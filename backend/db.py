"""JSON file storage layer for HiveBoard (MVP).

Two JSON files:
  - events.json  — the canonical event store (append-only, deduplicated)
  - agents.json  — agent profile cache (upserted on every ingest)

Agent status is derived at query time from the event stream.
Agent profiles are a convenience cache per spec Section 3.3.
"""

import json
import os
import threading

DATA_DIR = os.environ.get("HIVEBOARD_DATA", os.path.join(os.path.dirname(__file__), "data"))
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
AGENTS_FILE = os.path.join(DATA_DIR, "agents.json")

_lock = threading.Lock()


# --- Severity auto-defaults (Event Schema Spec Section 9) ---

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

VALID_EVENT_TYPES = set(SEVERITY_DEFAULTS.keys())


def init_db():
    """Ensure data directory and JSON files exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(EVENTS_FILE):
        _write_json(EVENTS_FILE, [])
    if not os.path.exists(AGENTS_FILE):
        _write_json(AGENTS_FILE, {})


# --- Generic JSON helpers ---

def _read_json(path, default=None):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else []


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# --- Events ---

def load_events() -> list[dict]:
    """Read all events."""
    with _lock:
        return _read_json(EVENTS_FILE, [])


def insert_events(new_events: list[dict]):
    """Append a batch of events. Deduplicates by event_id."""
    with _lock:
        existing = _read_json(EVENTS_FILE, [])
        seen_ids = {e["event_id"] for e in existing}
        for evt in new_events:
            if evt["event_id"] not in seen_ids:
                existing.append(evt)
                seen_ids.add(evt["event_id"])
        _write_json(EVENTS_FILE, existing)


# --- Agent Profiles (cache, spec Section 3.3) ---

def load_agents() -> dict:
    """Read agent profiles. Returns {agent_id: profile_dict}."""
    with _lock:
        return _read_json(AGENTS_FILE, {})


def upsert_agent(agent_id: str, envelope: dict, timestamp: str):
    """Update agent profile from ingest envelope.

    Called on every ingest. On first agent_registered event, sets first_seen.
    Always updates last_seen and metadata fields.
    """
    with _lock:
        agents = _read_json(AGENTS_FILE, {})
        existing = agents.get(agent_id, {})

        existing["agent_id"] = agent_id
        existing["agent_type"] = envelope.get("agent_type") or existing.get("agent_type", "general")
        existing["agent_version"] = envelope.get("agent_version") or existing.get("agent_version")
        existing["framework"] = envelope.get("framework") or existing.get("framework", "custom")
        existing["runtime"] = envelope.get("runtime") or existing.get("runtime")
        existing["environment"] = envelope.get("environment") or existing.get("environment", "production")
        existing["group"] = envelope.get("group") or existing.get("group", "default")
        existing["last_seen"] = timestamp
        if "first_seen" not in existing:
            existing["first_seen"] = timestamp

        agents[agent_id] = existing
        _write_json(AGENTS_FILE, agents)
