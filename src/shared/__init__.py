"""HiveBoard shared types — the contract between teams.

This package is the single source of truth for:
- Enumerations and constants (enums.py)
- Pydantic data models (models.py)
- StorageBackend protocol (storage.py)
"""

from .enums import (
    EventType,
    Severity,
    PayloadKind,
    KeyType,
    AgentStatus,
    TaskStatus,
    IssueSeverity,
    AlertConditionType,
    TenantPlan,
    SEVERITY_DEFAULTS,
    SEVERITY_BY_PAYLOAD_KIND,
)
from .models import (
    Event,
    IngestEvent,
    IngestRequest,
    IngestResponse,
    BatchEnvelope,
    Payload,
    LlmCallData,
    QueueSnapshotData,
    TodoData,
    ScheduledData,
    PlanCreatedData,
    PlanStepData,
    IssueData,
    TenantRecord,
    ApiKeyRecord,
    ApiKeyInfo,
    ProjectRecord,
    AgentRecord,
    AlertRuleRecord,
    AlertHistoryRecord,
    AgentSummary,
    TaskSummary,
    TimelineSummary,
    MetricsResponse,
    CostSummary,
    LlmCallRecord,
    PipelineState,
    Page,
    ErrorResponse,
)
from .storage import StorageBackend
