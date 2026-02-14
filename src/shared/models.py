"""HiveBoard Pydantic models — the shared contract between teams.

All data structures derived from:
- Event Schema Spec v2 (event shape, payload conventions)
- Data Model Spec v5 (table schemas)
- API + SDK Spec v3 (request/response shapes, WebSocket messages)

These models are the source of truth for serialization, validation, and
documentation. Both Team 1 (Backend) and Team 2 (SDK + Dashboard) build
against these types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════════════
#  PAYLOAD DATA MODELS — Event Schema Spec Section 6
# ═══════════════════════════════════════════════════════════════════════════

# --- 6.3 llm_call ---

class LlmCallData(BaseModel):
    """payload.data for kind='llm_call'."""
    name: str                                   # Logical call identifier
    model: str                                  # Model identifier
    tokens_in: int | None = None                # Input tokens
    tokens_out: int | None = None               # Output tokens
    cost: float | None = None                   # Cost in USD
    duration_ms: int | None = None              # Call latency
    prompt_preview: str | None = None           # First N chars of prompt
    response_preview: str | None = None         # First N chars of response
    metadata: dict[str, Any] | None = None      # Arbitrary additional context


# --- 6.4 queue_snapshot ---

class QueueItem(BaseModel):
    id: str | None = None
    priority: str | None = None                 # high / normal / low
    source: str | None = None                   # human / webhook / heartbeat / scheduled
    summary: str | None = None
    queued_at: str | None = None                # ISO 8601

class QueueProcessing(BaseModel):
    id: str | None = None
    summary: str | None = None
    started_at: str | None = None               # ISO 8601
    elapsed_ms: int | None = None

class QueueSnapshotData(BaseModel):
    """payload.data for kind='queue_snapshot'."""
    depth: int                                  # Number of items in queue
    oldest_age_seconds: int | None = None
    items: list[QueueItem] | None = None
    processing: QueueProcessing | None = None


# --- 6.5 todo ---

class TodoData(BaseModel):
    """payload.data for kind='todo'."""
    todo_id: str                                # Stable identifier
    action: str                                 # created / completed / failed / dismissed / deferred
    priority: str | None = None                 # high / normal / low
    source: str | None = None                   # Origin
    context: str | None = None                  # Additional context
    due_by: str | None = None                   # ISO 8601


# --- 6.6 scheduled ---

class ScheduledItem(BaseModel):
    id: str | None = None
    name: str | None = None
    next_run: str | None = None                 # ISO 8601
    interval: str | None = None                 # 5m / 1h / daily / weekly / null
    enabled: bool | None = None
    last_status: str | None = None              # success / failure / skipped / null

class ScheduledData(BaseModel):
    """payload.data for kind='scheduled'."""
    items: list[ScheduledItem]


# --- 6.7 plan_created ---

class PlanStep(BaseModel):
    index: int                                  # Zero-based
    description: str

class PlanCreatedData(BaseModel):
    """payload.data for kind='plan_created'."""
    steps: list[PlanStep]
    revision: int | None = None                 # 0 for initial, increments on replan


# --- 6.8 plan_step ---

class PlanStepData(BaseModel):
    """payload.data for kind='plan_step'."""
    step_index: int                             # Zero-based
    total_steps: int
    action: str                                 # started / completed / failed / skipped
    turns: int | None = None
    tokens: int | None = None
    plan_revision: int | None = None


# --- 6.9 issue ---

class IssueData(BaseModel):
    """payload.data for kind='issue'."""
    severity: str                               # critical / high / medium / low
    issue_id: str | None = None                 # Stable ID; if absent, server deduplicates by summary hash
    category: str | None = None                 # permissions / connectivity / configuration / data_quality / rate_limit / other
    context: dict[str, Any] | None = None       # Related details
    action: str | None = None                   # reported (default) / resolved / dismissed
    occurrence_count: int | None = None


# --- Universal payload envelope ---

class Payload(BaseModel):
    """Universal payload structure (Section 6.1).

    Well-known payloads have kind set; generic payloads may omit it.
    """
    kind: str | None = None
    summary: str | None = None
    data: dict[str, Any] | None = None
    tags: list[str] | None = None


# ═══════════════════════════════════════════════════════════════════════════
#  BATCH ENVELOPE — Event Schema Spec Section 3.1
# ═══════════════════════════════════════════════════════════════════════════

class BatchEnvelope(BaseModel):
    """Envelope sent once per ingest batch. Agent metadata."""
    agent_id: str
    agent_type: str | None = None
    agent_version: str | None = None
    framework: str | None = None
    runtime: str | None = None
    sdk_version: str | None = None
    environment: str = "production"
    group: str = "default"


# ═══════════════════════════════════════════════════════════════════════════
#  EVENT — Event Schema Spec Section 4 (canonical stored schema)
# ═══════════════════════════════════════════════════════════════════════════

class Event(BaseModel):
    """Canonical stored event — fully denormalized after envelope expansion."""
    # Identity (Section 4.1)
    event_id: str
    tenant_id: str
    agent_id: str
    agent_type: str | None = None

    # Project context (null for agent-level events)
    project_id: str | None = None

    # Time (Section 4.2)
    timestamp: str                              # ISO 8601
    received_at: str                            # ISO 8601, server-set

    # Grouping (Section 4.3)
    environment: str = "production"
    group: str = "default"

    # Task context (Section 4.4)
    task_id: str | None = None
    task_type: str | None = None
    task_run_id: str | None = None
    correlation_id: str | None = None

    # Action nesting (Section 4.5)
    action_id: str | None = None
    parent_action_id: str | None = None

    # Classification (Section 4.6)
    event_type: str                             # One of 13 EventType values
    severity: str = "info"                      # debug / info / warn / error

    # Outcome (Section 4.7)
    status: str | None = None
    duration_ms: int | None = None

    # Causal linkage (Section 4.8)
    parent_event_id: str | None = None

    # Content (Section 4.9)
    payload: dict[str, Any] | None = None       # 32KB max


class IngestEvent(BaseModel):
    """Event as sent by the SDK in a batch (before server enrichment).

    Missing fields (tenant_id, received_at, agent_id if absent) are filled
    from the envelope and server context.
    """
    event_id: str
    timestamp: str
    event_type: str
    project_id: str | None = None
    agent_id: str | None = None                 # Overrides envelope if present
    agent_type: str | None = None
    task_id: str | None = None
    task_type: str | None = None
    task_run_id: str | None = None
    correlation_id: str | None = None
    action_id: str | None = None
    parent_action_id: str | None = None
    severity: str | None = None                 # Auto-defaulted if absent
    status: str | None = None
    duration_ms: int | None = None
    parent_event_id: str | None = None
    payload: dict[str, Any] | None = None


class IngestRequest(BaseModel):
    """POST /v1/ingest request body."""
    envelope: BatchEnvelope
    events: list[IngestEvent]


class IngestError(BaseModel):
    event_id: str | None = None
    error: str
    message: str


class IngestWarning(BaseModel):
    event_id: str | None = None
    warning: str
    project_slug: str | None = None


class IngestResponse(BaseModel):
    """POST /v1/ingest response."""
    accepted: int
    rejected: int
    errors: list[IngestError] = Field(default_factory=list)
    warnings: list[IngestWarning] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  TABLE RECORD MODELS — Data Model Spec Section 3
# ═══════════════════════════════════════════════════════════════════════════

class TenantRecord(BaseModel):
    tenant_id: str
    name: str
    slug: str
    plan: str = "free"
    created_at: datetime
    updated_at: datetime
    settings: dict[str, Any] = Field(default_factory=dict)


class ApiKeyRecord(BaseModel):
    key_id: str
    tenant_id: str
    key_hash: str
    key_prefix: str
    key_type: str                               # live / test / read
    label: str | None = None
    created_by_user_id: str | None = None       # User who created this key
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = True


class ApiKeyInfo(BaseModel):
    """Minimal info returned from authentication lookup."""
    key_id: str
    tenant_id: str
    key_type: str


# ═══════════════════════════════════════════════════════════════════════════
#  USER MODELS — Auth System
# ═══════════════════════════════════════════════════════════════════════════

class UserRecord(BaseModel):
    """Stored user record."""
    user_id: str
    tenant_id: str
    email: str
    password_hash: str
    name: str
    role: str                                   # owner / admin / member / viewer
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class UserCreate(BaseModel):
    """POST body for creating a user."""
    email: str
    password: str
    name: str
    role: str = "member"


class UserUpdate(BaseModel):
    """PUT body for updating a user."""
    email: str | None = None
    name: str | None = None
    role: str | None = None
    settings: dict[str, Any] | None = None


class UserInfo(BaseModel):
    """Minimal JWT auth lookup result."""
    user_id: str
    tenant_id: str
    role: str


class UserSafe(BaseModel):
    """API response — all UserRecord fields except password_hash."""
    user_id: str
    tenant_id: str
    email: str
    name: str
    role: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    """POST body for login."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """Login response with JWT token."""
    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserSafe


class PasswordChangeRequest(BaseModel):
    """POST body for password change."""
    current_password: str
    new_password: str


# ═══════════════════════════════════════════════════════════════════════════
#  AUTH FLOW MODELS — Registration, Invites
# ═══════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    """POST /v1/auth/register body."""
    email: str
    password: str
    name: str
    tenant_name: str


class AcceptInviteRequest(BaseModel):
    """POST /v1/auth/accept-invite body."""
    invite_token: str
    name: str
    password: str


class InviteRequest(BaseModel):
    """POST /v1/auth/invite body."""
    email: str
    role: str = "member"
    name: str | None = None


class InviteRecord(BaseModel):
    """Stored invite."""
    invite_id: str
    tenant_id: str
    email: str
    role: str
    name: str | None = None
    invite_token_hash: str
    created_by_user_id: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    is_accepted: bool = False


class ApiKeyCreateRequest(BaseModel):
    """POST /v1/api-keys body."""
    label: str
    key_type: str = "live"


class ProjectRecord(BaseModel):
    project_id: str
    tenant_id: str
    name: str
    slug: str
    description: str | None = None
    environment: str = "production"
    settings: dict[str, Any] = Field(default_factory=dict)
    is_archived: bool = False
    auto_created: bool = False
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    environment: str = "production"
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    environment: str | None = None
    settings: dict[str, Any] | None = None


class ProjectMergeRequest(BaseModel):
    """POST /v1/projects/{slug}/merge request body."""
    target_slug: str


class AgentRecord(BaseModel):
    """Agent profile cache — Data Model Spec Section 3.4."""
    agent_id: str
    tenant_id: str
    agent_type: str = "general"
    agent_version: str | None = None
    framework: str | None = "custom"
    runtime: str | None = None
    sdk_version: str | None = None
    environment: str = "production"
    group: str = "default"
    first_seen: datetime
    last_seen: datetime
    last_heartbeat: datetime | None = None
    last_event_type: str | None = None
    last_task_id: str | None = None
    last_project_id: str | None = None
    stuck_threshold_seconds: int = 300
    is_registered: bool = True
    previous_status: str | None = None


class ProjectAgentRecord(BaseModel):
    tenant_id: str
    project_id: str
    agent_id: str
    added_at: datetime
    role: str = "member"


class AlertRuleRecord(BaseModel):
    rule_id: str
    tenant_id: str
    project_id: str | None = None
    name: str
    condition_type: str                         # One of 6 AlertConditionType values
    condition_config: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    cooldown_seconds: int = 300
    is_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class AlertRuleCreate(BaseModel):
    name: str
    project_id: str | None = None
    condition_type: str
    condition_config: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    cooldown_seconds: int = 300


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    condition_config: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    cooldown_seconds: int | None = None
    is_enabled: bool | None = None


class AlertHistoryRecord(BaseModel):
    alert_id: str
    tenant_id: str
    rule_id: str
    project_id: str | None = None
    fired_at: datetime
    condition_snapshot: dict[str, Any] = Field(default_factory=dict)
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    related_agent_id: str | None = None
    related_task_id: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
#  API RESPONSE MODELS — API Spec Sections 2, 4
# ═══════════════════════════════════════════════════════════════════════════

class PaginationInfo(BaseModel):
    cursor: str | None = None
    has_more: bool = False


class Page(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""
    data: list[Any]
    pagination: PaginationInfo = Field(default_factory=PaginationInfo)


class ErrorResponse(BaseModel):
    """Standard error shape — API Spec Section 2.4."""
    error: str
    message: str
    status: int
    details: dict[str, Any] | None = None


class RateLimitHeaders(BaseModel):
    limit: int
    remaining: int
    reset: int                                  # Unix timestamp


# ═══════════════════════════════════════════════════════════════════════════
#  DERIVED STATE MODELS — computed from events, not stored
# ═══════════════════════════════════════════════════════════════════════════

class AgentStats1h(BaseModel):
    """Rolling 1-hour stats for an agent — API Spec Section 4.1."""
    tasks_completed: int = 0
    tasks_failed: int = 0
    success_rate: float | None = None
    avg_duration_ms: int | None = None
    total_cost: float | None = None
    throughput: int = 0                         # tasks completed in window
    queue_depth: int = 0                        # from latest queue_snapshot
    active_issues: int = 0                      # from pipeline issues


class AgentSummary(BaseModel):
    """Agent as returned by GET /v1/agents — API Spec Section 4.1."""
    agent_id: str
    agent_type: str
    agent_version: str | None = None
    framework: str | None = None
    runtime: str | None = None
    sdk_version: str | None = None
    environment: str
    group: str
    derived_status: str                         # idle / processing / waiting_approval / error / stuck
    current_task_id: str | None = None
    current_project_id: str | None = None
    last_heartbeat: str | None = None           # ISO 8601
    heartbeat_age_seconds: int | None = None
    is_stuck: bool = False
    stuck_threshold_seconds: int = 300
    first_seen: str | None = None
    last_seen: str | None = None
    stats_1h: AgentStats1h = Field(default_factory=AgentStats1h)


class TaskSummary(BaseModel):
    """Task as returned by GET /v1/tasks — API Spec Section 4.3."""
    task_id: str
    task_type: str | None = None
    task_run_id: str | None = None
    agent_id: str
    project_id: str | None = None
    derived_status: str                         # processing / completed / failed / escalated / waiting
    started_at: str                             # ISO 8601
    completed_at: str | None = None
    duration_ms: int | None = None
    total_cost: float | None = None
    action_count: int = 0
    error_count: int = 0
    has_escalation: bool = False
    has_human_intervention: bool = False
    llm_call_count: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class TimelineSummary(BaseModel):
    """Task timeline response — API Spec Section 4.4."""
    task_id: str
    task_run_id: str | None = None
    agent_id: str
    project_id: str | None = None
    task_type: str | None = None
    derived_status: str
    started_at: str
    completed_at: str | None = None
    duration_ms: int | None = None
    total_cost: float | None = None
    events: list[dict[str, Any]]
    action_tree: list[dict[str, Any]]
    error_chains: list[dict[str, Any]]
    plan: dict[str, Any] | None = None


class MetricsSummary(BaseModel):
    """Aggregate metrics — API Spec Section 4.6."""
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    escalated: int = 0
    stuck: int = 0
    success_rate: float | None = None
    avg_duration_ms: int | None = None
    total_cost: float | None = None
    avg_cost_per_task: float | None = None


class TimeseriesBucket(BaseModel):
    timestamp: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_duration_ms: int | None = None
    cost: float = 0
    error_count: int = 0
    throughput: int = 0


class MetricsResponse(BaseModel):
    range: str
    interval: str
    summary: MetricsSummary
    timeseries: list[TimeseriesBucket]
    groups: list[dict[str, Any]] | None = None


class CostSummary(BaseModel):
    """GET /v1/cost response — API Spec Section 4.7."""
    total_cost: float
    call_count: int
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    by_agent: list[dict[str, Any]] = Field(default_factory=list)
    by_model: list[dict[str, Any]] = Field(default_factory=list)
    reported_cost: float = 0.0     # sum of developer-provided costs
    estimated_cost: float = 0.0    # sum of server-estimated costs


class CostTimeBucket(BaseModel):
    timestamp: str
    cost: float = 0
    call_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class LlmCallRecord(BaseModel):
    """Individual LLM call — GET /v1/cost/calls or /v1/llm-calls."""
    event_id: str
    agent_id: str
    project_id: str | None = None
    task_id: str | None = None
    timestamp: str
    name: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: float | None = None
    duration_ms: int | None = None
    cost_source: str | None = None          # "reported", "estimated", or null
    cost_model_matched: str | None = None   # pricing pattern matched (estimated only)
    prompt_preview: str | None = None
    response_preview: str | None = None


class PipelineState(BaseModel):
    """GET /v1/agents/{id}/pipeline response — API Spec Section 4.9."""
    agent_id: str
    queue: dict[str, Any] | None = None         # Latest queue_snapshot
    todos: list[dict[str, Any]] = Field(default_factory=list)
    scheduled: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[dict[str, Any]] = Field(default_factory=list)


class AgentPipelineSummary(BaseModel):
    """Per-agent summary within the fleet pipeline view."""
    agent_id: str
    queue_depth: int = 0
    active_todos: int = 0
    active_issues: int = 0
    scheduled_count: int = 0


class FleetPipelineState(BaseModel):
    """GET /v1/pipeline response — fleet-level pipeline aggregation."""
    totals: dict[str, int] = Field(default_factory=lambda: {
        "queue_depth": 0,
        "active_issues": 0,
        "active_todos": 0,
        "scheduled_count": 0,
    })
    agents: list[AgentPipelineSummary] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  WEBSOCKET MESSAGE MODELS — API Spec Section 5
# ═══════════════════════════════════════════════════════════════════════════

class WsSubscribeRequest(BaseModel):
    """Client → Server subscription message."""
    action: str = "subscribe"                   # subscribe / unsubscribe / ping
    channels: list[str] = Field(default_factory=list)  # events / agents
    filters: dict[str, Any] | None = None

class WsSubscribedResponse(BaseModel):
    """Server → Client subscription confirmation."""
    type: str = "subscribed"
    channels: list[str]
    filters: dict[str, Any]

class WsEventNew(BaseModel):
    """Server → Client: new event ingested (channel=events)."""
    type: str = "event.new"
    data: dict[str, Any]

class WsAgentStatusChanged(BaseModel):
    """Server → Client: agent status changed (channel=agents)."""
    type: str = "agent.status_changed"
    data: dict[str, Any]

class WsAgentStuck(BaseModel):
    """Server → Client: agent stuck alert (channel=agents)."""
    type: str = "agent.stuck"
    data: dict[str, Any]

class WsPong(BaseModel):
    type: str = "pong"
    server_time: str
