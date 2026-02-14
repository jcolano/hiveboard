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
    AgentStats1h,
    AgentSummary,
    AlertHistoryRecord,
    AlertRuleCreate,
    AlertRuleRecord,
    AlertRuleUpdate,
    ApiKeyInfo,
    ApiKeyRecord,
    CostSummary,
    CostTimeBucket,
    Event,
    FleetPipelineState,
    InviteRecord,
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
    UserRecord,
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

    async def get_tenant_by_slug(self, slug: str) -> TenantRecord | None:
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
        created_by_user_id: str | None = None,
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
    #  USERS
    # ───────────────────────────────────────────────────────────────────

    async def create_user(
        self,
        user_id: str,
        tenant_id: str,
        email: str,
        password_hash: str,
        name: str,
        role: str = "member",
    ) -> UserRecord:
        """Create a new user. Raises ValueError if email already exists globally."""
        ...

    async def get_user(
        self, tenant_id: str, user_id: str
    ) -> UserRecord | None:
        """Get user by ID.

        Maps to: SELECT ... FROM users
                 WHERE tenant_id = ? AND user_id = ?
        """
        ...

    async def get_user_by_email(
        self, tenant_id: str, email: str
    ) -> UserRecord | None:
        """Get active user by email (for login).

        Maps to: SELECT ... FROM users
                 WHERE tenant_id = ? AND email = ? AND is_active = 1
        """
        ...

    async def list_users(
        self,
        tenant_id: str,
        *,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserRecord]:
        """List users with optional filters.

        Maps to: SELECT ... FROM users WHERE tenant_id = ? [AND role = ?] [AND is_active = ?]
        """
        ...

    async def update_user(
        self,
        tenant_id: str,
        user_id: str,
        *,
        email: str | None = None,
        name: str | None = None,
        role: str | None = None,
        password_hash: str | None = None,
        settings: dict | None = None,
        last_login_at: datetime | None = None,
    ) -> UserRecord | None:
        """Update user fields. Returns None if not found.

        Maps to: UPDATE users SET ... WHERE tenant_id = ? AND user_id = ?
        """
        ...

    async def deactivate_user(
        self, tenant_id: str, user_id: str
    ) -> bool:
        """Soft-delete: set is_active=False. Returns True if found.

        Maps to: UPDATE users SET is_active = 0 WHERE tenant_id = ? AND user_id = ?
        """
        ...

    async def reactivate_user(
        self, tenant_id: str, user_id: str
    ) -> bool:
        """Restore a deactivated user. Returns True if found.

        Maps to: UPDATE users SET is_active = 1 WHERE tenant_id = ? AND user_id = ?
        """
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

    async def unarchive_project(self, tenant_id: str, project_id: str) -> bool:
        """Returns True if project was found and unarchived."""
        ...

    async def count_projects(self, tenant_id: str) -> int:
        """Count non-archived projects for a tenant.

        Maps to: SELECT COUNT(*) FROM projects
                 WHERE tenant_id = ? AND is_archived = 0
        """
        ...

    async def count_project_events(
        self, tenant_id: str, project_id: str
    ) -> int:
        """Count events belonging to a project.

        Maps to: SELECT COUNT(*) FROM events
                 WHERE tenant_id = ? AND project_id = ?
        """
        ...

    async def reassign_events(
        self,
        tenant_id: str,
        from_project_id: str,
        to_project_id: str,
    ) -> int:
        """Move all events from one project to another. Returns count moved.

        Maps to: UPDATE events SET project_id = ?
                 WHERE tenant_id = ? AND project_id = ?
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  AGENTS
    # ───────────────────────────────────────────────────────────────────

    async def compute_agent_stats_1h(
        self,
        tenant_id: str,
        agent_id: str,
    ) -> AgentStats1h:
        """Compute rolling 1-hour stats for an agent.

        Maps to: SELECT COUNT(*), AVG(...) FROM events
                 WHERE tenant_id = ? AND agent_id = ?
                   AND timestamp > NOW() - INTERVAL 1 HOUR
        """
        ...

    async def upsert_agent(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        agent_type: str = "general",
        agent_version: str | None = None,
        framework: str | None = "custom",
        runtime: str | None = None,
        sdk_version: str | None = None,
        environment: str = "production",
        group: str = "default",
        last_seen: datetime,
        last_heartbeat: datetime | None = None,
        last_event_type: str | None = None,
        last_task_id: str | None = None,
        last_project_id: str | None = None,
        stuck_threshold_seconds: int = 300,
    ) -> AgentRecord:
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

    async def insert_events(self, events: list[Event], *, key_type: str | None = None) -> int:
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
        payload_kind: str | None = None,
        key_type: str | None = None,
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
        since: datetime | None = None,
        until: datetime | None = None,
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
    ) -> list[CostTimeBucket]:
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

    async def get_fleet_pipeline(
        self,
        tenant_id: str,
    ) -> FleetPipelineState:
        """Fleet-wide pipeline aggregation across all agents.

        Iterates all agents, calls get_pipeline() for each, aggregates totals.

        Maps to: SELECT agent_id, ... FROM events
                 WHERE tenant_id = ? AND event_type = 'custom'
                   AND json_extract(payload, '$.kind') IN (...)
                 GROUP BY agent_id
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

    # ───────────────────────────────────────────────────────────────────
    #  GLOBAL EMAIL LOOKUP
    # ───────────────────────────────────────────────────────────────────

    async def get_user_by_email_global(
        self, email: str
    ) -> UserRecord | None:
        """Find user across all tenants (active only).

        Maps to: SELECT ... FROM users
                 WHERE email = ? AND is_active = 1
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  INVITES
    # ───────────────────────────────────────────────────────────────────

    async def create_invite(
        self,
        invite_id: str,
        tenant_id: str,
        email: str,
        role: str,
        name: str | None,
        invite_token_hash: str,
        created_by_user_id: str,
        expires_at: datetime,
    ) -> InviteRecord:
        """Store an invite.

        Maps to: INSERT INTO invites (...)
        """
        ...

    async def get_invite_by_token_hash(
        self, invite_token_hash: str
    ) -> InviteRecord | None:
        """Lookup pending + unexpired invite by token hash.

        Maps to: SELECT ... FROM invites
                 WHERE invite_token_hash = ? AND is_accepted = 0
                   AND expires_at > NOW()
        """
        ...

    async def get_pending_invite(
        self, tenant_id: str, email: str
    ) -> InviteRecord | None:
        """Check for existing pending invite.

        Maps to: SELECT ... FROM invites
                 WHERE tenant_id = ? AND email = ?
                   AND is_accepted = 0 AND expires_at > NOW()
        """
        ...

    async def mark_invite_accepted(self, invite_id: str) -> bool:
        """Mark invite as accepted.

        Maps to: UPDATE invites SET is_accepted = 1, accepted_at = NOW()
                 WHERE invite_id = ?
        """
        ...

    async def list_invites(
        self,
        tenant_id: str,
        *,
        is_accepted: bool | None = None,
    ) -> list[InviteRecord]:
        """List tenant's invites with optional filter.

        Maps to: SELECT ... FROM invites WHERE tenant_id = ? [AND is_accepted = ?]
        """
        ...

    # ───────────────────────────────────────────────────────────────────
    #  API KEY — USER FILTERED
    # ───────────────────────────────────────────────────────────────────

    async def list_api_keys_by_user(
        self, tenant_id: str, user_id: str
    ) -> list[ApiKeyRecord]:
        """Keys created by a specific user.

        Maps to: SELECT ... FROM api_keys
                 WHERE tenant_id = ? AND created_by_user_id = ?
        """
        ...
