"""StorageBackend Protocol — the contract between API and persistence.

╔══════════════════════════════════════════════════════════════════════════╗
║  DESIGN NOTE — PRODUCTION TARGET IS MS SQL SERVER                      ║
║                                                                        ║
║  The JSON file engine is the MVP implementation, but every method      ║
║  signature MUST be designed so it maps cleanly to SQL.                 ║
║                                                                        ║
║  DO:   Use explicit, filterable parameters:                            ║
║        get_events(tenant_id, project_id=None, agent_id=None, ...)     ║
║        Each parameter maps to a WHERE clause.                          ║
║                                                                        ║
║  DON'T: Return raw JSON expecting Python-side filtering, or accept    ║
║         opaque filter dicts that the SQL adapter must reverse-engineer.║
║                                                                        ║
║  RULE OF THUMB: If a method can't be implemented as a single SQL      ║
║  query (with JOINs/aggregations as needed), redesign the signature.   ║
╚══════════════════════════════════════════════════════════════════════════╝

Each method's return type uses the Pydantic models from shared.models.
This file defines the abstract interface — implementations live in:
  - backend/storage_json.py  (MVP: JSON files)
  - backend/storage_mssql.py (Production: MS SQL Server)

The same test suite (tests/test_storage.py) runs against both implementations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    AgentRecord,
    AgentSummary,
    AlertHistoryRecord,
    AlertRuleCreate,
    AlertRuleRecord,
    AlertRuleUpdate,
    ApiKeyInfo,
    ApiKeyRecord,
    CostSummary,
    Event,
    LlmCallRecord,
    MetricsResponse,
    Page,
    PipelineState,
    ProjectAgentRecord,
    ProjectCreate,
    ProjectRecord,
    ProjectUpdate,
    TaskSummary,
    TenantRecord,
    TimelineSummary,
    TimeseriesBucket,
)


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract persistence interface for HiveBoard.

    Every method uses explicit, SQL-friendly parameters. No raw JSON blobs
    in signatures. Each optional filter parameter maps to a WHERE clause.
    """

    # ───────────────────────────────────────────────────────────────────
    #  LIFECYCLE
    # ───────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create tables / files / indexes. Called once at startup."""
        ...

    async def close(self) -> None:
        """Release connections / file handles. Called on shutdown."""
        ...

    # ───────────────────────────────────────────────────────────────────
    #  TENANTS
    # ───────────────────────────────────────────────────────────────────

    async def create_tenant(
        self,
        tenant_id: str,
        name: str,
        slug: str,
        plan: str = "free",
    ) -> TenantRecord:
        ...

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        ...

    # ───────────────────────────────────────────────────────────────────
    #  API KEYS
    # ───────────────────────────────────────────────────────────────────

    async def create_api_key(
        self,
        key_id: str,
        tenant_id: str,
        key_hash: str,
        key_prefix: str,
        key_type: str,
        label: str | None = None,
    ) -> ApiKeyRecord:
        ...

    async def authenticate(self, key_hash: str) -> ApiKeyInfo | None:
        """Look up an active API key by its SHA-256 hash.

        Returns None if no matching active key exists.
        Maps to: SELECT tenant_id, key_type FROM api_keys
                 WHERE key_hash = ? AND is_active = 1
        """
        ...

    async def touch_api_key(self, key_id: str) -> None:
        """Update last_used_at. Fire-and-forget on each request."""
        ...

    async def list_api_keys(self, tenant_id: str) -> list[ApiKeyRecord]:
        ...

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> bool:
        """Returns True if key was found and revoked."""
        ...

    # ───────────────────────────────────────────────────────────────────
    #  PROJECTS
    # ───────────────────────────────────────────────────────────────────

    async def create_project(
        self, tenant_id: str, project: ProjectCreate
    ) -> ProjectRecord:
        ...

    async def get_project(
        self, tenant_id: str, project_id: str
    ) -> ProjectRecord | None:
        ...

    async def list_projects(
        self,
        tenant_id: str,
        *,
        include_archived: bool = False,
    ) -> list[ProjectRecord]:
        ...

    async def update_project(
        self, tenant_id: str, project_id: str, updates: ProjectUpdate
    ) -> ProjectRecord | None:
        """Returns None if project not found."""
        ...

    async def archive_project(self, tenant_id: str, project_id: str) -> bool:
        """Returns True if project was found and archived."""
        ...

    # ───────────────────────────────────────────────────────────────────
    #  AGENTS
    # ───────────────────────────────────────────────────────────────────

    async def upsert_agent(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        agent_type: str = "general",
        agent_version: str | None = None,
        framework: str | None = "custom",
        runtime: str | None = None,
        last_seen: datetime,
        last_heartbeat: datetime | None = None,
        last_event_type: str | None = None,
        last_task_id: str | None = None,
        last_project_id: str | None = None,
        stuck_threshold_seconds: int = 300,
    ) -> None:
        """Create or update agent profile cache.

        Called on every ingestion batch. Sets first_seen on creation.
        Maps to: INSERT ... ON CONFLICT UPDATE (upsert).
        """
        ...

    async def get_agent(
        self, tenant_id: str, agent_id: str
    ) -> AgentRecord | None:
        ...

    async def list_agents(
        self,
        tenant_id: str,
        *,
        environment: str | None = None,
        group: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentRecord]:
        """List agent profiles.

        When project_id is set, returns only agents assigned to that project
        (via project_agents junction table).
        Maps to: SELECT ... FROM agents [JOIN project_agents] WHERE ...
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  PROJECT-AGENT JUNCTION
    # ───────────────────────────────────────────────────────────────────

    async def upsert_project_agent(
        self, tenant_id: str, project_id: str, agent_id: str
    ) -> None:
        """Auto-populate junction on task event ingestion.

        Maps to: INSERT OR IGNORE INTO project_agents ...
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  EVENT INGESTION
    # ───────────────────────────────────────────────────────────────────

    async def insert_events(self, events: list[Event]) -> int:
        """Batch insert events. Deduplicates by (tenant_id, event_id).

        Returns the number of events actually inserted (after dedup).
        Maps to: INSERT ... ON CONFLICT IGNORE (batch).
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  EVENT QUERIES
    # ───────────────────────────────────────────────────────────────────

    async def get_events(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        environment: str | None = None,
        group: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exclude_heartbeats: bool = True,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[Event]:
        """Activity stream — reverse-chronological filtered events.

        Maps to: SELECT ... FROM events WHERE ... ORDER BY timestamp DESC
        """
        ...

    async def get_task_events(
        self,
        tenant_id: str,
        task_id: str,
    ) -> list[Event]:
        """All events for a task, chronologically ordered.

        Maps to: SELECT ... FROM events
                 WHERE tenant_id = ? AND task_id = ?
                 ORDER BY timestamp ASC
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  TASK QUERIES (derived from events)
    # ───────────────────────────────────────────────────────────────────

    async def list_tasks(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        environment: str | None = None,
        sort: str = "newest",
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[TaskSummary]:
        """Task list derived from task lifecycle events.

        Maps to: SELECT task_id, ... FROM events
                 WHERE ... GROUP BY task_id
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  METRICS (aggregated from events)
    # ───────────────────────────────────────────────────────────────────

    async def get_metrics(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        environment: str | None = None,
        metric: str | None = None,
        group_by: str | None = None,
        range: str = "1h",
        interval: str | None = None,
    ) -> MetricsResponse:
        """Aggregate metrics with timeseries buckets.

        Maps to: SELECT date_bucket(...), COUNT(*), AVG(...)
                 FROM events WHERE ... GROUP BY bucket
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  COST (aggregated from llm_call payloads)
    # ───────────────────────────────────────────────────────────────────

    async def get_cost_summary(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        range: str = "24h",
    ) -> CostSummary:
        """Cost breakdown by agent and model.

        Maps to: SELECT agent_id, json_extract(payload, '$.data.model'),
                 SUM(json_extract(payload, '$.data.cost'))
                 FROM events WHERE ... AND json_extract(payload, '$.kind') = 'llm_call'
                 GROUP BY ...
        """
        ...

    async def get_cost_calls(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[LlmCallRecord]:
        """Individual LLM calls, most recent first.

        Maps to: SELECT ... FROM events
                 WHERE ... AND json_extract(payload, '$.kind') = 'llm_call'
                 ORDER BY timestamp DESC
        """
        ...

    async def get_cost_timeseries(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        range: str = "24h",
        interval: str | None = None,
    ) -> list[TimeseriesBucket]:
        """Cost timeseries bucketed by interval."""
        ...

    # ───────────────────────────────────────────────────────────────────
    #  PIPELINE (derived from well-known payload kinds)
    # ───────────────────────────────────────────────────────────────────

    async def get_pipeline(
        self,
        tenant_id: str,
        agent_id: str,
    ) -> PipelineState:
        """Agent work pipeline: queue, TODOs, scheduled, issues.

        Queries latest custom events with well-known payload kinds
        for the given agent.

        Maps to: SELECT ... FROM events
                 WHERE tenant_id = ? AND agent_id = ?
                   AND event_type = 'custom'
                   AND json_extract(payload, '$.kind') IN (...)
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  ALERT RULES
    # ───────────────────────────────────────────────────────────────────

    async def create_alert_rule(
        self, tenant_id: str, rule: AlertRuleCreate
    ) -> AlertRuleRecord:
        ...

    async def get_alert_rule(
        self, tenant_id: str, rule_id: str
    ) -> AlertRuleRecord | None:
        ...

    async def list_alert_rules(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        is_enabled: bool | None = None,
    ) -> list[AlertRuleRecord]:
        ...

    async def update_alert_rule(
        self, tenant_id: str, rule_id: str, updates: AlertRuleUpdate
    ) -> AlertRuleRecord | None:
        ...

    async def delete_alert_rule(self, tenant_id: str, rule_id: str) -> bool:
        ...

    # ───────────────────────────────────────────────────────────────────
    #  ALERT HISTORY
    # ───────────────────────────────────────────────────────────────────

    async def insert_alert(
        self, tenant_id: str, alert: AlertHistoryRecord
    ) -> None:
        ...

    async def list_alert_history(
        self,
        tenant_id: str,
        *,
        rule_id: str | None = None,
        project_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[AlertHistoryRecord]:
        ...

    async def get_last_alert_for_rule(
        self, tenant_id: str, rule_id: str
    ) -> AlertHistoryRecord | None:
        """For cooldown checking — get most recent alert for a rule.

        Maps to: SELECT ... FROM alert_history
                 WHERE rule_id = ? ORDER BY fired_at DESC LIMIT 1
        """
        ...
