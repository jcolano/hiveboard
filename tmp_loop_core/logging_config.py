"""
Centralized logging configuration for loopCore.

Configures the ``loop_core`` parent logger so every child logger
(loop_core.loop, loop_core.agent, …) inherits handlers and level
automatically.
"""

import logging
import logging.handlers
from pathlib import Path

_logging_configured = False


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure loopCore logging with console and optional file output.

    Args:
        level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to log file.
            - ``None``  → default path ``data/loopCore/LOGS/loopcore.log``
            - ``"none"`` → disable file logging
            - any other string → use as explicit file path
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    parent_logger = logging.getLogger("loop_core")
    parent_logger.setLevel(numeric_level)
    parent_logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # -- Console handler (always on) --
    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(fmt)
    parent_logger.addHandler(console)

    # -- File handler --
    if isinstance(log_file, str) and log_file.lower() == "none":
        return  # explicitly disabled

    if log_file is None:
        # Default path: <project_root>/data/loopCore/LOGS/loopcore.log
        from loop_core.config.loader import _find_project_root

        log_dir = _find_project_root() / "data" / "loopCore" / "LOGS"
        log_dir.mkdir(parents=True, exist_ok=True)
        resolved_path = str(log_dir / "loopcore.log")
    else:
        resolved_path = log_file
        Path(resolved_path).parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        resolved_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(fmt)
    parent_logger.addHandler(file_handler)
