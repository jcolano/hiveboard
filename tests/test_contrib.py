"""Tests for Category B convenience layer: tool_payload() and HiveBoardLogHandler."""

from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock

import hiveloop
from hiveloop import tool_payload
from hiveloop.contrib.log_handler import HiveBoardLogHandler


# ---------------------------------------------------------------------------
# TestToolPayload
# ---------------------------------------------------------------------------

class TestToolPayload:
    """tool_payload() builder function."""

    def test_defaults(self):
        """Minimal call returns success-only dict."""
        p = tool_payload()
        assert p == {"success": True}

    def test_success_false(self):
        p = tool_payload(success=False, error="timeout")
        assert p["success"] is False
        assert p["error"] == "timeout"

    def test_none_fields_stripped(self):
        """Fields left at None default are not present in the output."""
        p = tool_payload(args={"q": "hi"})
        assert "error" not in p
        assert "duration_ms" not in p
        assert "tool_category" not in p
        assert "http_status" not in p
        assert "result_size_bytes" not in p

    def test_all_fields_populated(self):
        p = tool_payload(
            args={"q": "hello"},
            result="found 3 items",
            success=True,
            error=None,
            duration_ms=120,
            tool_category="crm",
            http_status=200,
            result_size_bytes=4096,
        )
        assert p["args"] == {"q": "hello"}
        assert p["result"] == "found 3 items"
        assert p["success"] is True
        assert p["duration_ms"] == 120
        assert p["tool_category"] == "crm"
        assert p["http_status"] == 200
        assert p["result_size_bytes"] == 4096
        # error was None so it should be absent
        assert "error" not in p

    def test_args_truncation(self):
        long_value = "x" * 1000
        p = tool_payload(args={"key": long_value}, args_max_len=500)
        assert len(p["args"]["key"]) == 500

    def test_args_no_truncation_when_short(self):
        """Values shorter than max are kept as-is (original type preserved)."""
        p = tool_payload(args={"count": 42})
        # int value is kept (not stringified) since str(42) is short
        assert p["args"]["count"] == 42

    def test_result_truncation(self):
        long_result = "r" * 2000
        p = tool_payload(result=long_result, result_max_len=1000)
        assert len(p["result"]) == 1000

    def test_result_no_truncation_when_short(self):
        p = tool_payload(result="ok")
        assert p["result"] == "ok"

    def test_custom_max_lengths(self):
        p = tool_payload(
            args={"k": "a" * 50},
            result="b" * 50,
            args_max_len=10,
            result_max_len=20,
        )
        assert len(p["args"]["k"]) == 10
        assert len(p["result"]) == 20

    def test_empty_args(self):
        p = tool_payload(args={})
        assert p["args"] == {}

    def test_result_non_string_converted(self):
        """Non-string results are stringified then truncated."""
        p = tool_payload(result={"items": [1, 2, 3]}, result_max_len=10)
        assert isinstance(p["result"], str)
        assert len(p["result"]) == 10


# ---------------------------------------------------------------------------
# TestLogHandler
# ---------------------------------------------------------------------------

class TestLogHandler:
    """HiveBoardLogHandler integration tests."""

    def test_warning_maps_to_medium(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("log-agent", heartbeat_interval=0)

        logger = logging.getLogger("test.warning")
        handler = HiveBoardLogHandler(agent)
        logger.addHandler(handler)
        try:
            logger.warning("disk space low")
        finally:
            logger.removeHandler(handler)

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        issues = [
            e for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "issue"
        ]
        assert len(issues) == 1
        data = issues[0]["payload"]["data"]
        assert data["severity"] == "medium"
        assert data["action"] == "reported"
        assert data["category"] == "log"
        assert data["issue_id"] == "log-test.warning-WARNING"
        assert "disk space low" in issues[0]["payload"]["summary"]

    def test_error_maps_to_high(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("log-agent", heartbeat_interval=0)

        logger = logging.getLogger("test.error")
        handler = HiveBoardLogHandler(agent)
        logger.addHandler(handler)
        try:
            logger.error("connection refused")
        finally:
            logger.removeHandler(handler)

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        issues = [
            e for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "issue"
        ]
        assert len(issues) == 1
        assert issues[0]["payload"]["data"]["severity"] == "high"
        assert issues[0]["payload"]["data"]["issue_id"] == "log-test.error-ERROR"

    def test_critical_maps_to_critical(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("log-agent", heartbeat_interval=0)

        logger = logging.getLogger("test.critical")
        handler = HiveBoardLogHandler(agent)
        logger.addHandler(handler)
        try:
            logger.critical("out of memory")
        finally:
            logger.removeHandler(handler)

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        issues = [
            e for e in events
            if e.get("event_type") == "custom"
            and e.get("payload", {}).get("kind") == "issue"
        ]
        assert len(issues) == 1
        assert issues[0]["payload"]["data"]["severity"] == "critical"

    def test_below_threshold_ignored(self):
        """INFO logs should not be forwarded."""
        agent = MagicMock()
        logger = logging.getLogger("test.info")
        logger.setLevel(logging.DEBUG)
        handler = HiveBoardLogHandler(agent)
        logger.addHandler(handler)
        try:
            logger.info("just info")
        finally:
            logger.removeHandler(handler)

        agent.report_issue.assert_not_called()

    def test_message_truncation(self):
        """Messages longer than MAX_SUMMARY_CHARS are truncated."""
        agent = MagicMock()
        handler = HiveBoardLogHandler(agent)

        long_msg = "x" * 1000
        record = logging.LogRecord(
            name="test.trunc",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg=long_msg,
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        agent.report_issue.assert_called_once()
        call_kwargs = agent.report_issue.call_args
        assert len(call_kwargs.kwargs["summary"]) == 512

    def test_context_includes_record_info(self):
        """Context dict contains logger name, filename, lineno, funcName."""
        agent = MagicMock()
        handler = HiveBoardLogHandler(agent)

        record = logging.LogRecord(
            name="myapp.module",
            level=logging.WARNING,
            pathname="module.py",
            lineno=42,
            msg="something happened",
            args=(),
            exc_info=None,
        )
        record.funcName = "do_work"
        handler.emit(record)

        agent.report_issue.assert_called_once()
        ctx = agent.report_issue.call_args.kwargs["context"]
        assert ctx["logger"] == "myapp.module"
        assert ctx["filename"] == "module.py"
        assert ctx["lineno"] == 42
        assert ctx["funcName"] == "do_work"

    def test_custom_category(self):
        """Custom category is passed through."""
        agent = MagicMock()
        handler = HiveBoardLogHandler(agent, category="custom_log")

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="err",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        assert agent.report_issue.call_args.kwargs["category"] == "custom_log"

    def test_emit_never_raises(self):
        """Even if report_issue raises, emit() swallows the error."""
        agent = MagicMock()
        agent.report_issue.side_effect = RuntimeError("boom")
        handler = HiveBoardLogHandler(agent)

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="err",
            args=(),
            exc_info=None,
        )
        # Should not raise
        handler.emit(record)
