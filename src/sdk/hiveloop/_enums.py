"""SDK-local copy of enums and constants needed by the hiveloop package.

This vendored subset allows the SDK to work as a standalone pip package
without requiring the monorepo ``shared`` package at runtime.

Source of truth: src/shared/enums.py
"""

from enum import StrEnum


# ---------------------------------------------------------------------------
# Event Types — Event Schema Spec Section 5
# ---------------------------------------------------------------------------

class EventType(StrEnum):
    # Layer 0 — Agent Lifecycle
    AGENT_REGISTERED = "agent_registered"
    HEARTBEAT = "heartbeat"

    # Layer 1 — Structured Execution
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    ACTION_STARTED = "action_started"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"

    # Layer 2 — Narrative Telemetry
    RETRY_STARTED = "retry_started"
    ESCALATED = "escalated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RECEIVED = "approval_received"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Severity — Event Schema Spec Section 4.6 / 9
# ---------------------------------------------------------------------------

class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


SEVERITY_DEFAULTS: dict[str, Severity] = {
    EventType.HEARTBEAT: Severity.DEBUG,
    EventType.AGENT_REGISTERED: Severity.INFO,
    EventType.TASK_STARTED: Severity.INFO,
    EventType.TASK_COMPLETED: Severity.INFO,
    EventType.TASK_FAILED: Severity.ERROR,
    EventType.ACTION_STARTED: Severity.INFO,
    EventType.ACTION_COMPLETED: Severity.INFO,
    EventType.ACTION_FAILED: Severity.ERROR,
    EventType.RETRY_STARTED: Severity.WARN,
    EventType.ESCALATED: Severity.WARN,
    EventType.APPROVAL_REQUESTED: Severity.INFO,
    EventType.APPROVAL_RECEIVED: Severity.INFO,
    EventType.CUSTOM: Severity.INFO,
}


# ---------------------------------------------------------------------------
# Well-Known Payload Kinds — Event Schema Spec Section 6
# ---------------------------------------------------------------------------

class PayloadKind(StrEnum):
    LLM_CALL = "llm_call"
    QUEUE_SNAPSHOT = "queue_snapshot"
    TODO = "todo"
    SCHEDULED = "scheduled"
    PLAN_CREATED = "plan_created"
    PLAN_STEP = "plan_step"
    ISSUE = "issue"


# ---------------------------------------------------------------------------
# Field Size Limits — Event Schema Spec Section 10
# ---------------------------------------------------------------------------

MAX_PAYLOAD_BYTES = 32 * 1024       # 32 KB
MAX_SUMMARY_CHARS = 512
MAX_AGENT_ID_CHARS = 256
MAX_TASK_ID_CHARS = 256
MAX_ENVIRONMENT_CHARS = 64
MAX_GROUP_CHARS = 128
MAX_BATCH_EVENTS = 500
