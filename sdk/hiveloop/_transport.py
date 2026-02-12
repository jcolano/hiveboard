"""Transport layer — event queue + background flush thread.

Spec Sections 13.1–13.4: thread-safe deque, periodic flush, retry with
exponential backoff, graceful shutdown via atexit.
"""

import atexit
import collections
import json
import logging
import sys
import threading
import time

import requests

logger = logging.getLogger("hiveloop.transport")

SDK_VERSION = "hiveloop-0.1.0"


class Transport:
    """Batched HTTP transport for events → POST /v1/ingest."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        flush_interval: float = 5.0,
        batch_size: int = 100,
        max_queue_size: int = 10000,
        debug: bool = False,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.flush_interval = flush_interval
        self.batch_size = min(batch_size, 500)  # server cap
        self.debug = debug

        self._queue = collections.deque(maxlen=max_queue_size)
        self._lock = threading.Lock()
        self._shutdown = False
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

        # Background flush thread
        self._flush_event = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

        # Graceful shutdown
        atexit.register(self.shutdown, timeout=5.0)

    def enqueue(self, event: dict, envelope: dict):
        """Add an event to the queue. Triggers flush if batch is full."""
        if self._shutdown:
            return
        with self._lock:
            self._queue.append((event, envelope))
        if len(self._queue) >= self.batch_size:
            self._flush_event.set()

    def flush(self):
        """Force an immediate flush."""
        self._flush_event.set()

    def shutdown(self, timeout: float = 5.0):
        """Stop threads and do a final synchronous flush."""
        if self._shutdown:
            return
        self._shutdown = True
        self._flush_event.set()
        self._thread.join(timeout=timeout)
        # Final flush of anything remaining
        self._do_flush()
        self._session.close()

    def _flush_loop(self):
        """Background thread: flush on timer or when signaled."""
        while not self._shutdown:
            self._flush_event.wait(timeout=self.flush_interval)
            self._flush_event.clear()
            self._do_flush()

    def _do_flush(self):
        """Drain queue and send batches."""
        while True:
            batch = self._drain(self.batch_size)
            if not batch:
                break
            self._send_batch(batch)

    def _drain(self, n: int) -> list[tuple[dict, dict]]:
        """Pop up to n items from the queue."""
        items = []
        with self._lock:
            for _ in range(min(n, len(self._queue))):
                items.append(self._queue.popleft())
        return items

    def _send_batch(self, batch: list[tuple[dict, dict]]):
        """Send a batch to POST /v1/ingest with retry."""
        if not batch:
            return

        # Group by agent_id (each batch goes to one agent's envelope)
        by_agent = {}
        for event, envelope in batch:
            aid = envelope.get("agent_id", "unknown")
            by_agent.setdefault(aid, (envelope, []))
            by_agent[aid][1].append(event)

        for aid, (envelope, events) in by_agent.items():
            payload = {"envelope": envelope, "events": events}

            for attempt in range(5):
                try:
                    url = f"{self.endpoint}/v1/ingest"
                    resp = self._session.post(url, json=payload, timeout=10)

                    if resp.status_code in (200, 207):
                        if self.debug:
                            result = resp.json()
                            logger.debug("Flushed %d events for %s (accepted=%s)",
                                         len(events), aid, result.get("accepted"))
                        return

                    if resp.status_code == 400:
                        # Permanent failure — don't retry
                        logger.warning("Batch rejected (400) for %s: %s", aid, resp.text)
                        return

                    if resp.status_code == 429:
                        retry_after = 2
                        try:
                            retry_after = resp.json().get("details", {}).get("retry_after_seconds", 2)
                        except Exception:
                            pass
                        time.sleep(retry_after)
                        continue

                    # 5xx — retry with backoff
                    backoff = min(2 ** attempt, 60)
                    logger.warning("Server error %d for %s, retrying in %ds", resp.status_code, aid, backoff)
                    time.sleep(backoff)

                except requests.RequestException as e:
                    backoff = min(2 ** attempt, 60)
                    if self.debug:
                        logger.debug("Connection error for %s: %s, retrying in %ds", aid, e, backoff)
                    time.sleep(backoff)

            logger.warning("Failed to send batch for %s after 5 attempts, dropping %d events", aid, len(events))
