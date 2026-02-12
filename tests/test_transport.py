"""Tests for the HiveLoop transport layer.

Covers: batching, flush on timer, flush on shutdown, retry on 5xx,
no retry on 400, queue overflow, manual flush.
"""

from __future__ import annotations

import time

import hiveloop
from hiveloop._transport import Transport


def _make_event(i: int) -> dict:
    return {
        "event_id": f"evt-{i:04d}",
        "timestamp": "2026-02-10T14:00:00.000Z",
        "event_type": "custom",
        "payload": {"summary": f"Event {i}"},
    }


ENVELOPE = {
    "agent_id": "test-agent",
    "agent_type": "test",
    "agent_version": "1.0.0",
    "framework": "custom",
    "runtime": "python-3.12",
    "sdk_version": "hiveloop-0.1.0",
    "environment": "test",
    "group": "default",
}


class TestBatching:
    """Events are grouped and shipped in batches."""

    def test_events_batched_and_posted(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,  # disable timer flush
            batch_size=10,
        )
        # Enqueue 10 events — should trigger flush at batch_size
        for i in range(10):
            transport.enqueue(_make_event(i), ENVELOPE)

        time.sleep(0.5)  # Give flush thread time to process
        transport.shutdown()

        assert len(mock_server.all_events()) == 10

    def test_multiple_batches(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,
            batch_size=5,
        )
        for i in range(12):
            transport.enqueue(_make_event(i), ENVELOPE)

        time.sleep(0.5)
        transport.shutdown()

        assert len(mock_server.all_events()) == 12

    def test_events_grouped_by_agent(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=0.1,
            batch_size=100,
        )
        env_a = {**ENVELOPE, "agent_id": "agent-a"}
        env_b = {**ENVELOPE, "agent_id": "agent-b"}

        transport.enqueue(_make_event(1), env_a)
        transport.enqueue(_make_event(2), env_b)
        transport.enqueue(_make_event(3), env_a)

        time.sleep(0.5)
        transport.shutdown()

        # Should have 2 batches (one per agent)
        assert len(mock_server.batches) == 2
        agent_ids = {b["envelope"]["agent_id"] for b in mock_server.batches}
        assert agent_ids == {"agent-a", "agent-b"}


class TestFlush:
    """Timer and manual flush work."""

    def test_timer_flush(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=0.2,
            batch_size=1000,  # High batch size so timer is the trigger
        )
        transport.enqueue(_make_event(1), ENVELOPE)
        time.sleep(0.5)
        transport.shutdown()

        assert len(mock_server.all_events()) == 1

    def test_manual_flush(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,
            batch_size=1000,
        )
        transport.enqueue(_make_event(1), ENVELOPE)
        transport.flush()
        time.sleep(0.3)
        transport.shutdown()

        assert len(mock_server.all_events()) == 1

    def test_shutdown_flushes_remaining(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,
            batch_size=1000,
        )
        for i in range(5):
            transport.enqueue(_make_event(i), ENVELOPE)

        transport.shutdown()

        assert len(mock_server.all_events()) == 5


class TestRetry:
    """Retry logic for server errors."""

    def test_retry_on_500(self, mock_server):
        mock_server.enqueue_error(500)  # First request fails
        # Second request succeeds (default 200)

        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=0.1,
            batch_size=1,
        )
        transport.enqueue(_make_event(1), ENVELOPE)
        time.sleep(3)  # Allow time for retry + backoff
        transport.shutdown()

        # Should have 2 requests (1 failed + 1 success)
        assert len(mock_server.batches) == 2
        assert len(mock_server.all_events()) == 2  # Same event sent twice

    def test_no_retry_on_400(self, mock_server):
        mock_server.enqueue_error(400, {"error": "bad_request", "message": "Invalid", "status": 400})

        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=0.1,
            batch_size=1,
        )
        transport.enqueue(_make_event(1), ENVELOPE)
        time.sleep(1)
        transport.shutdown()

        # Only 1 request — no retry for 400
        assert len(mock_server.batches) == 1

    def test_429_uses_retry_after(self, mock_server):
        mock_server.enqueue_error(
            429,
            {
                "error": "rate_limit_exceeded",
                "message": "Too many requests",
                "status": 429,
                "details": {"retry_after_seconds": 0.1},
            },
        )

        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=0.1,
            batch_size=1,
        )
        transport.enqueue(_make_event(1), ENVELOPE)
        time.sleep(2)
        transport.shutdown()

        # Should have retried after rate limit
        assert len(mock_server.batches) >= 2


class TestQueueOverflow:
    """Queue drops oldest events when full."""

    def test_overflow_drops_oldest(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,
            batch_size=1000,
            max_queue_size=5,
        )
        # Enqueue 10 events into a queue of size 5
        for i in range(10):
            transport.enqueue(_make_event(i), ENVELOPE)

        transport.shutdown()

        events = mock_server.all_events()
        assert len(events) == 5
        # Should have the LAST 5 events (oldest dropped)
        ids = [e["event_id"] for e in events]
        assert ids == [f"evt-{i:04d}" for i in range(5, 10)]


class TestShutdownBehavior:
    """Post-shutdown enqueue is a no-op."""

    def test_enqueue_after_shutdown_is_noop(self, mock_server):
        transport = Transport(
            endpoint=mock_server.url,
            api_key="hb_test_abc123",
            flush_interval=60,
            batch_size=1000,
        )
        transport.shutdown()

        transport.enqueue(_make_event(1), ENVELOPE)
        time.sleep(0.2)

        assert len(mock_server.all_events()) == 0
