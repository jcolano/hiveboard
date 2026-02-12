"""Tests for heartbeat payload and queue provider callbacks.

Covers: heartbeat_payload callback, queue_provider callback,
callback exception handling.
"""

from __future__ import annotations

import time

import hiveloop


class TestHeartbeatPayloadCallback:
    """heartbeat_payload callback is invoked and used."""

    def test_callback_provides_payload(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )

        def hb_payload():
            return {
                "kind": "heartbeat_status",
                "summary": "Healthy",
                "data": {"queue_depth": 2, "uptime_seconds": 100},
            }

        agent = hb.agent(
            "hb-cb-agent",
            heartbeat_interval=0.2,
            heartbeat_payload=hb_payload,
        )

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        heartbeats = [e for e in events if e["event_type"] == "heartbeat"]
        assert len(heartbeats) >= 1
        # At least one heartbeat should have our payload
        with_payload = [h for h in heartbeats if h.get("payload") is not None]
        assert len(with_payload) >= 1
        assert with_payload[0]["payload"]["kind"] == "heartbeat_status"

    def test_callback_exception_still_emits_heartbeat(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )

        def bad_callback():
            raise RuntimeError("callback crash")

        agent = hb.agent(
            "hb-err-agent",
            heartbeat_interval=0.2,
            heartbeat_payload=bad_callback,
        )

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        heartbeats = [e for e in events if e["event_type"] == "heartbeat"]
        # Heartbeat should still be emitted despite callback failure
        assert len(heartbeats) >= 1


class TestQueueProviderCallback:
    """queue_provider callback emits separate queue_snapshot events."""

    def test_queue_provider_emits_snapshot(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )

        def queue_cb():
            return {"depth": 5, "oldest_age_seconds": 30}

        agent = hb.agent(
            "qp-agent",
            heartbeat_interval=0.2,
            queue_provider=queue_cb,
        )

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        qs = [
            e
            for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "queue_snapshot"
        ]
        assert len(qs) >= 1
        assert qs[0]["payload"]["data"]["depth"] == 5

    def test_queue_provider_exception_skips_snapshot(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )

        def bad_queue_cb():
            raise ValueError("queue crash")

        agent = hb.agent(
            "qp-err-agent",
            heartbeat_interval=0.2,
            queue_provider=bad_queue_cb,
        )

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        # Heartbeat should still work
        heartbeats = [e for e in events if e["event_type"] == "heartbeat"]
        assert len(heartbeats) >= 1
        # No queue snapshots (callback failed)
        qs = [
            e
            for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "queue_snapshot"
        ]
        assert len(qs) == 0

    def test_both_callbacks_together(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )

        def hb_payload():
            return {"kind": "heartbeat_status", "data": {"status": "ok"}}

        def queue_cb():
            return {"depth": 2, "oldest_age_seconds": 10}

        agent = hb.agent(
            "both-cb-agent",
            heartbeat_interval=0.2,
            heartbeat_payload=hb_payload,
            queue_provider=queue_cb,
        )

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        heartbeats = [e for e in events if e["event_type"] == "heartbeat"]
        qs = [
            e
            for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "queue_snapshot"
        ]
        assert len(heartbeats) >= 1
        assert len(qs) >= 1
