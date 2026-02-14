"""HiveBoardLogHandler — forward Python log records to HiveBoard as issues."""

from __future__ import annotations

import logging
from typing import Any

from shared.enums import MAX_SUMMARY_CHARS

# Map Python log levels to HiveBoard severity strings.
_LEVEL_MAP: dict[int, str] = {
    logging.WARNING: "medium",
    logging.ERROR: "high",
    logging.CRITICAL: "critical",
}


class HiveBoardLogHandler(logging.Handler):
    """A :class:`logging.Handler` that forwards WARNING+ records to
    :meth:`Agent.report_issue`.

    Usage::

        from hiveloop.contrib.log_handler import HiveBoardLogHandler

        agent = hb.agent("my-agent", ...)
        logging.getLogger("my_app").addHandler(HiveBoardLogHandler(agent))
    """

    def __init__(
        self,
        agent: Any,
        level: int = logging.WARNING,
        category: str = "log",
    ) -> None:
        super().__init__(level=level)
        self._agent = agent
        self._category = category

    def emit(self, record: logging.LogRecord) -> None:
        """Forward *record* to :meth:`Agent.report_issue`.

        Never raises — inherits the SDK safety contract.
        """
        try:
            severity = _LEVEL_MAP.get(record.levelno)
            if severity is None:
                # For levels above CRITICAL or between standard levels,
                # pick the closest lower match.
                for lvl in sorted(_LEVEL_MAP, reverse=True):
                    if record.levelno >= lvl:
                        severity = _LEVEL_MAP[lvl]
                        break
                if severity is None:
                    return  # below WARNING — shouldn't happen given default level

            message = self.format(record) if self.formatter else record.getMessage()
            message = message[:MAX_SUMMARY_CHARS]

            issue_id = f"log-{record.name}-{record.levelname}"

            context: dict[str, Any] = {
                "logger": record.name,
                "filename": record.filename,
                "lineno": record.lineno,
                "funcName": record.funcName,
            }

            self._agent.report_issue(
                summary=message,
                severity=severity,
                issue_id=issue_id,
                category=self._category,
                context=context,
            )
        except Exception:
            # Safety contract: never raise from observability code.
            pass
