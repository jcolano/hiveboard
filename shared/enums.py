"""HiveBoard enumerations and constants.

Single source of truth for all constants derived from the specs:
- Event Schema Spec v2 (13 event types, 7 payload kinds, severity levels)
- Data Model Spec v5 (table constraints, field limits)
- API + SDK Spec v3 (API key types, rate limits)
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


VALID_SEVERITIES = {s.value for s in Severity}


# Auto-defaults by event type (Section 9)
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

# Severity overrides by payload kind (Section 9, v2)
SEVERITY_BY_PAYLOAD_KIND: dict[str, Severity] = {
    "llm_call": Severity.INFO,
    "queue_snapshot": Severity.DEBUG,
    # "todo" with action=failed -> WARN (handled in code, not here)
    # "plan_step" with action=failed -> ERROR (handled in code, not here)
    # "issue" -> derived from data.severity (handled in code, not here)
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
# API Key Types — API Spec Section 2.2
# ---------------------------------------------------------------------------

class KeyType(StrEnum):
    LIVE = "live"
    TEST = "test"
    READ = "read"


# Prefix → KeyType mapping
KEY_PREFIX_MAP: dict[str, KeyType] = {
    "hb_live_": KeyType.LIVE,
    "hb_test_": KeyType.TEST,
    "hb_read_": KeyType.READ,
}


# ---------------------------------------------------------------------------
# Derived Agent Status — Data Model Spec Section 5
# ---------------------------------------------------------------------------

class AgentStatus(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    WAITING_APPROVAL = "waiting_approval"
    ERROR = "error"
    STUCK = "stuck"


# ---------------------------------------------------------------------------
# Derived Task Status — Data Model Spec Section 5
# ---------------------------------------------------------------------------

class TaskStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    WAITING = "waiting"


# ---------------------------------------------------------------------------
# Issue Severity — Event Schema Spec Section 6.9
# ---------------------------------------------------------------------------

class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Alert Condition Types — API Spec Section 6
# ---------------------------------------------------------------------------

class AlertConditionType(StrEnum):
    AGENT_STUCK = "agent_stuck"
    TASK_DURATION = "task_duration"
    ERROR_RATE = "error_rate"
    CUSTOM_EVENT = "custom_event"
    HEARTBEAT_MISSING = "heartbeat_missing"
    COST_THRESHOLD = "cost_threshold"


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
MAX_BATCH_BYTES = 1 * 1024 * 1024   # 1 MB


# ---------------------------------------------------------------------------
# Rate Limits — API Spec Section 2.5
# ---------------------------------------------------------------------------

RATE_LIMIT_INGEST = 100          # requests/second per key
RATE_LIMIT_QUERY = 30            # requests/second per key
MAX_WEBSOCKET_CONNECTIONS = 5    # per key


# ---------------------------------------------------------------------------
# Tenant Plan Limits — Data Model Spec Section 3.1
# ---------------------------------------------------------------------------

class TenantPlan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


PLAN_LIMITS: dict[str, dict] = {
    TenantPlan.FREE: {
        "events_per_month": 500_000,
        "max_agents": 5,
        "max_projects": 3,
        "retention_days": 7,
        "rate_limit": 10,
    },
    TenantPlan.PRO: {
        "events_per_month": 10_000_000,
        "max_agents": 50,
        "max_projects": 20,
        "retention_days": 30,
        "rate_limit": 100,
    },
    TenantPlan.ENTERPRISE: {
        "events_per_month": None,  # unlimited
        "max_agents": None,
        "max_projects": None,
        "retention_days": 90,
        "rate_limit": 500,
    },
}


# ---------------------------------------------------------------------------
# Time Range / Interval Constants — API Spec Section 4.6
# ---------------------------------------------------------------------------

RANGE_SECONDS: dict[str, int] = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}

AUTO_INTERVAL: dict[str, str] = {
    "1h": "5m",
    "6h": "15m",
    "24h": "1h",
    "7d": "6h",
    "30d": "1d",
}

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}
