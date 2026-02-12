"""HiveLoop transport layer — thread-safe batched HTTP transport.

Handles event queuing, background flushing, batch envelope construction,
retry with exponential backoff, and graceful shutdown.

Critical invariant: Transport never raises exceptions to the caller.
All failures are logged and events dropped silently. The SDK must
never interfere with the instrumented application.
"""

from __future__ import annotations

import atexit
import collections
import json
import logging
import threading
import time
from typing import Any

import requests

from shared.enums import MAX_BATCH_EVENTS

logger = logging.getLogger("hiveloop.transport")

# Retry configuration
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.0
_BACKOFF_CAP = 60.0


class _QueueItem:
    """An event paired with its agent envelope metadata."""

    __slots__ = ("event", "envelope")

    def __init__(self, event: dict[str, Any], envelope: dict[str, Any]) -> None:
        self.event = event
        self.envelope = envelope


class Transport:
    """Thread-safe batched HTTP transport for HiveLoop events.

    Events are enqueued by SDK methods, buffered in a bounded deque,
    and flushed to the ingest endpoint by a background daemon thread.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        flush_interval: float = 5.0,
        batch_size: int = 100,
        max_queue_size: int = 10_000,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._flush_interval = flush_interval
        self._batch_size = min(batch_size, MAX_BATCH_EVENTS)
        self._shutdown = False

        # Thread-safe bounded queue — oldest events dropped when full
        self._queue: collections.deque[_QueueItem] = collections.deque(
            maxlen=max_queue_size
        )
        self._lock = threading.Lock()

        # Signal to wake the flush thread early
        self._flush_event = threading.Event()

        # HTTP session (reused for connection pooling)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        )

        # Start background flush thread
        self._thread = threading.Thread(
            target=self._flush_loop, name="hiveloop-flush", daemon=True
        )
        self._thread.start()

        # Register atexit shutdown
        atexit.register(lambda: self.shutdown(timeout=5.0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, event: dict[str, Any], envelope: dict[str, Any]) -> None:
        """Add an event to the queue. Non-blocking, never raises."""
        if self._shutdown:
            return
        try:
            prev_len = len(self._queue)
            with self._lock:
                self._queue.append(_QueueItem(event, envelope))
            # Log if we hit capacity (deque silently drops oldest)
            if prev_len >= (self._queue.maxlen or 0):
                logger.warning(
                    "Event queue full (%d). Oldest events are being dropped.",
                    self._queue.maxlen,
                )
            # Trigger flush if we've accumulated enough events
            if len(self._queue) >= self._batch_size:
                self._flush_event.set()
        except Exception:
            logger.debug("Failed to enqueue event", exc_info=True)

    def flush(self) -> None:
        """Trigger an immediate flush. Blocks until the flush cycle completes."""
        if self._shutdown:
            return
        self._flush_event.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Graceful shutdown: flush remaining events and close session."""
        if self._shutdown:
            return
        self._shutdown = True

        # Wake flush thread so it can exit
        self._flush_event.set()

        # Wait for flush thread to finish
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

        # Final synchronous drain
        self._drain_all()

        # Close HTTP session
        try:
            self._session.close()
        except Exception:
            logger.debug("Error closing HTTP session", exc_info=True)

    # ------------------------------------------------------------------
    # Background flush loop
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Background thread: periodically drains the queue."""
        while not self._shutdown:
            self._flush_event.wait(timeout=self._flush_interval)
            self._flush_event.clear()
            if self._shutdown:
                break
            self._drain_all()

    def _drain_all(self) -> None:
        """Drain the queue completely, flushing in batch_size chunks."""
        while True:
            items = self._drain_batch()
            if not items:
                break
            batches = self._group_by_agent(items)
            for envelope, events in batches.items():
                self._send_batch(json.loads(envelope), events)

    def _drain_batch(self) -> list[_QueueItem]:
        """Pop up to batch_size items from the queue."""
        items: list[_QueueItem] = []
        with self._lock:
            for _ in range(self._batch_size):
                if not self._queue:
                    break
                items.append(self._queue.popleft())
        return items

    # ------------------------------------------------------------------
    # Batch construction
    # ------------------------------------------------------------------

    def _group_by_agent(
        self, items: list[_QueueItem]
    ) -> dict[str, list[dict[str, Any]]]:
        """Group events by agent envelope (serialized as JSON key)."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            key = json.dumps(item.envelope, sort_keys=True)
            groups.setdefault(key, []).append(item.event)
        return groups

    # ------------------------------------------------------------------
    # HTTP send with retry
    # ------------------------------------------------------------------

    def _send_batch(
        self, envelope: dict[str, Any], events: list[dict[str, Any]]
    ) -> bool:
        """POST a batch to /v1/ingest with retry and backoff.

        Returns True on success, False on permanent failure.
        """
        url = f"{self._endpoint}/v1/ingest"
        body = {"envelope": envelope, "events": events}

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.post(url, json=body, timeout=30)

                if resp.status_code in (200, 207):
                    # Log any rejected events from partial success
                    if resp.status_code == 207:
                        try:
                            data = resp.json()
                            if data.get("rejected", 0) > 0:
                                logger.warning(
                                    "Batch partially rejected: %d accepted, %d rejected. Errors: %s",
                                    data.get("accepted", 0),
                                    data.get("rejected", 0),
                                    data.get("errors", []),
                                )
                        except Exception:
                            pass
                    return True

                if resp.status_code == 429:
                    # Rate limited — use server-provided retry delay
                    retry_after = self._parse_retry_after(resp)
                    logger.warning(
                        "Rate limited (429). Retrying after %.1fs.", retry_after
                    )
                    time.sleep(retry_after)
                    continue

                if resp.status_code == 400:
                    # Permanently invalid — don't retry, drop batch
                    logger.error(
                        "Batch rejected (400): %s. Dropping %d events.",
                        resp.text[:500],
                        len(events),
                    )
                    return False

                if resp.status_code >= 500:
                    # Server error — retry with backoff
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        "Server error (%d). Retry %d/%d in %.1fs.",
                        resp.status_code,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                # Unexpected status code — treat as permanent failure
                logger.error(
                    "Unexpected status %d: %s. Dropping %d events.",
                    resp.status_code,
                    resp.text[:500],
                    len(events),
                )
                return False

            except requests.ConnectionError:
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "Connection error. Retry %d/%d in %.1fs.",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
                continue
            except Exception:
                logger.error("Unexpected error sending batch", exc_info=True)
                return False

        logger.error(
            "Exhausted %d retries. Dropping %d events.", _MAX_RETRIES, len(events)
        )
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """Exponential backoff: 1s, 2s, 4s, 8s, 16s — capped at 60s."""
        return min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float:
        """Extract retry delay from 429 response."""
        try:
            data = resp.json()
            details = data.get("details", {})
            if details and "retry_after_seconds" in details:
                return float(details["retry_after_seconds"])
        except Exception:
            pass
        # Fallback: check Retry-After header
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return 2.0  # Default fallback
