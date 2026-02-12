"""SQLite database layer for HiveBoard event storage.

Single table, fully denormalized. Agent status is derived at query time,
never stored. This follows the canonical event schema from the spec.
"""

import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("HIVEBOARD_DB", os.path.join(os.path.dirname(__file__), "hiveboard.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    -- Identity
    event_id        TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    agent_type      TEXT,

    -- Time
    timestamp       TEXT NOT NULL,
    received_at     TEXT NOT NULL,

    -- Grouping
    environment     TEXT NOT NULL DEFAULT 'production',
    "group"         TEXT NOT NULL DEFAULT 'default',

    -- Task context (Layer 1+)
    task_id         TEXT,
    task_type       TEXT,
    task_run_id     TEXT,
    correlation_id  TEXT,

    -- Action nesting (Layer 1+)
    action_id       TEXT,
    parent_action_id TEXT,

    -- Classification
    event_type      TEXT NOT NULL,
    severity        TEXT,

    -- Outcome
    status          TEXT,
    duration_ms     INTEGER,

    -- Causal linkage
    parent_event_id TEXT,

    -- Content
    payload         TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(tenant_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(tenant_id, task_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(tenant_id, event_type);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(tenant_id, timestamp);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.close()


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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


def insert_events(events: list[dict]):
    """Insert a batch of fully-expanded events."""
    with get_db() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO events (
                event_id, tenant_id, agent_id, agent_type,
                timestamp, received_at,
                environment, "group",
                task_id, task_type, task_run_id, correlation_id,
                action_id, parent_action_id,
                event_type, severity,
                status, duration_ms,
                parent_event_id, payload
            ) VALUES (
                :event_id, :tenant_id, :agent_id, :agent_type,
                :timestamp, :received_at,
                :environment, :group,
                :task_id, :task_type, :task_run_id, :correlation_id,
                :action_id, :parent_action_id,
                :event_type, :severity,
                :status, :duration_ms,
                :parent_event_id, :payload
            )""",
            events,
        )
