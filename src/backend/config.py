"""Configuration loader for HiveBoard.

Reads from config.json in the project root. Falls back to environment
variables for backward compatibility, then to defaults.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG: dict | None = None

def _load() -> dict:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    config_path = Path(__file__).resolve().parent.parent.parent / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            _CONFIG = json.load(f)
    else:
        _CONFIG = {}
    return _CONFIG


def get(key: str, default=None):
    """Get a config value. Checks config.json first, then env var HIVEBOARD_{KEY}, then default."""
    cfg = _load()
    if key in cfg:
        return cfg[key]
    env_key = f"HIVEBOARD_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return default


def reload():
    """Force reload config from disk (useful for tests)."""
    global _CONFIG
    _CONFIG = None
