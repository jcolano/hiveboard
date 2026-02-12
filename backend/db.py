"""JSON file storage layer for HiveBoard event storage (MVP).

Uses a single events.json file as the data store. Agent status is derived
at query time, never stored. This follows the canonical event schema from
the spec, using JSON files for easy inspection and iteration.
"""

import json
import os
import threading

DATA_DIR = os.environ.get("HIVEBOARD_DATA", os.path.join(os.path.dirname(__file__), "data"))
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")

_lock = threading.Lock()


# --- Severity auto-defaults (Section 9 of spec) ---

SEVERITY_DEFAULTS = {
    "heartbeat": "debug",
    "agent_registered": "info",
    "task_started": "info",
    "task_completed": "info",
    "task_failed": "error",
    "action_started": "info",
    "action_completed": "info",
    "action_failed": "error",
    "retry_started": "warn",
    "escalated": "warn",
    "approval_requested": "info",
    "approval_received": "info",
    "custom": "info",
}


def init_db():
    """Ensure data directory and events file exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(EVENTS_FILE):
        _write_events([])


def _read_events() -> list[dict]:
    try:
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_events(events: list[dict]):
    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=2)


def load_events() -> list[dict]:
    """Read all events. Used by query endpoints."""
    with _lock:
        return _read_events()


def insert_events(new_events: list[dict]):
    """Append a batch of fully-expanded events. Deduplicates by event_id."""
    with _lock:
        existing = _read_events()
        seen_ids = {e["event_id"] for e in existing}
        for evt in new_events:
            if evt["event_id"] not in seen_ids:
                existing.append(evt)
                seen_ids.add(evt["event_id"])
        _write_events(existing)
