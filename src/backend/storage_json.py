"""JSON File Storage Backend — MVP implementation of StorageBackend.

One JSON file per table, in-memory working set, write-through persistence.
Thread-safe via asyncio.Lock per file.

Files:
  tenants.json, api_keys.json, projects.json, agents.json,
  project_agents.json, events.json, alert_rules.json, alert_history.json

NOTE: This is the MVP backend for development velocity.  Once the simulator
is running continuously, events.json will grow fast (~35K events/day with
10 agents).  The MS SQL Server adapter is a practical necessity for real
testing.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from shared.enums import (
    AgentStatus,
    AUTO_INTERVAL,
    EventType,
    INTERVAL_SECONDS,
    RANGE_SECONDS,
    Severity,
    SEVERITY_DEFAULTS,
    TaskStatus,
)
from shared.models import (
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
    LlmCallRecord,
    MetricsResponse,
    MetricsSummary,
    Page,
    PaginationInfo,
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


# ═══════════════════════════════════════════════════════════════════════════
#  SHARED UTILITY — Agent Status Derivation
# ═══════════════════════════════════════════════════════════════════════════
# Single implementation, called from storage queries, REST responses, and
# WebSocket broadcasts.  Three implementations of the same cascade is a
# bug factory — this is the one source of truth.

def derive_agent_status(
    agent: AgentRecord,
    now: datetime | None = None,
) -> AgentStatus:
    """Derive agent status using the priority cascade.

    1. stuck:            heartbeat/activity age > stuck_threshold_seconds
    2. error:            last_event_type in (task_failed, action_failed)
    3. waiting_approval: last_event_type = approval_requested
    4. processing:       last_event_type in (task_started, action_started)
    5. idle:             everything else
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # 1. Stuck check — use last_heartbeat if available, fall back to last_seen
    # An agent that recently sent events but no heartbeat yet is NOT stuck
    reference_time = agent.last_heartbeat or agent.last_seen
    if reference_time is None:
        return AgentStatus.STUCK
    age = (now - reference_time).total_seconds()
    if age > agent.stuck_threshold_seconds:
        return AgentStatus.STUCK

    # 2. Error
    if agent.last_event_type in (
        EventType.TASK_FAILED,
        EventType.ACTION_FAILED,
    ):
        return AgentStatus.ERROR

    # 3. Waiting approval
    if agent.last_event_type == EventType.APPROVAL_REQUESTED:
        return AgentStatus.WAITING_APPROVAL

    # 4. Processing
    if agent.last_event_type in (
        EventType.TASK_STARTED,
        EventType.ACTION_STARTED,
    ):
        return AgentStatus.PROCESSING

    # 5. Idle
    return AgentStatus.IDLE


def _derive_task_status(event_types: set[str]) -> str:
    """Derive task status from the set of event types present.

    1. task_completed → completed
    2. task_failed → failed
    3. escalated (and not completed/failed) → escalated
    4. approval_requested without approval_received → waiting
    5. Otherwise → processing
    """
    if EventType.TASK_COMPLETED in event_types:
        return TaskStatus.COMPLETED
    if EventType.TASK_FAILED in event_types:
        return TaskStatus.FAILED
    if EventType.ESCALATED in event_types:
        return TaskStatus.ESCALATED
    if (
        EventType.APPROVAL_REQUESTED in event_types
        and EventType.APPROVAL_RECEIVED not in event_types
    ):
        return TaskStatus.WAITING
    return TaskStatus.PROCESSING


def _parse_dt(s: str | datetime | None) -> datetime | None:
    """Parse an ISO 8601 string to a timezone-aware datetime."""
    if s is None:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
#  JSON FILE STORAGE BACKEND
# ═══════════════════════════════════════════════════════════════════════════

TABLE_FILES = [
    "tenants",
    "api_keys",
    "projects",
    "agents",
    "project_agents",
    "events",
    "alert_rules",
    "alert_history",
]


class JsonStorageBackend:
    """MVP storage — one JSON file per table, in-memory + write-through."""

    def __init__(self, data_dir: str | Path | None = None):
        self._data_dir = Path(
            data_dir or os.environ.get("HIVEBOARD_DATA", "data")
        )
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ───────────────────────────────────────────────────────────────────
    #  LIFECYCLE
    # ───────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        for name in TABLE_FILES:
            self._locks[name] = asyncio.Lock()
            fp = self._data_dir / f"{name}.json"
            if fp.exists():
                with open(fp, "r", encoding="utf-8") as f:
                    self._tables[name] = json.load(f)
            else:
                self._tables[name] = []
                self._persist(name)

    async def close(self) -> None:
        for name in TABLE_FILES:
            if name in self._tables:
                self._persist(name)

    def _persist(self, table: str) -> None:
        fp = self._data_dir / f"{table}.json"
        tmp = fp.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._tables[table], f, indent=2, default=str)
        os.replace(tmp, fp)
        # Restrict file permissions (no-op on Windows)
        try:
            os.chmod(fp, 0o600)
        except OSError:
            pass

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
        now = _now_utc()
        rec = TenantRecord(
            tenant_id=tenant_id,
            name=name,
            slug=slug,
            plan=plan,
            created_at=now,
            updated_at=now,
        )
        async with self._locks["tenants"]:
            self._tables["tenants"].append(rec.model_dump(mode="json"))
            self._persist("tenants")

        # Auto-create "default" project
        await self.create_project(
            tenant_id,
            ProjectCreate(name="Default", slug="default"),
        )
        return rec

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        for row in self._tables["tenants"]:
            if row["tenant_id"] == tenant_id:
                return TenantRecord(**row)
        return None

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
        now = _now_utc()
        rec = ApiKeyRecord(
            key_id=key_id,
            tenant_id=tenant_id,
            key_hash=key_hash,
            key_prefix=key_prefix,
            key_type=key_type,
            label=label,
            created_at=now,
        )
        async with self._locks["api_keys"]:
            self._tables["api_keys"].append(rec.model_dump(mode="json"))
            self._persist("api_keys")
        return rec

    async def authenticate(self, key_hash: str) -> ApiKeyInfo | None:
        for row in self._tables["api_keys"]:
            if row["key_hash"] == key_hash and row.get("is_active", True):
                return ApiKeyInfo(
                    key_id=row["key_id"],
                    tenant_id=row["tenant_id"],
                    key_type=row["key_type"],
                )
        return None

    async def touch_api_key(self, key_id: str) -> None:
        async with self._locks["api_keys"]:
            for row in self._tables["api_keys"]:
                if row["key_id"] == key_id:
                    row["last_used_at"] = _now_utc().isoformat()
                    break
            self._persist("api_keys")

    async def list_api_keys(self, tenant_id: str) -> list[ApiKeyRecord]:
        return [
            ApiKeyRecord(**row)
            for row in self._tables["api_keys"]
            if row["tenant_id"] == tenant_id
        ]

    async def revoke_api_key(self, tenant_id: str, key_id: str) -> bool:
        async with self._locks["api_keys"]:
            for row in self._tables["api_keys"]:
                if (
                    row["key_id"] == key_id
                    and row["tenant_id"] == tenant_id
                    and row.get("is_active", True)
                ):
                    row["is_active"] = False
                    row["revoked_at"] = _now_utc().isoformat()
                    self._persist("api_keys")
                    return True
        return False

    # ───────────────────────────────────────────────────────────────────
    #  PROJECTS
    # ───────────────────────────────────────────────────────────────────

    async def create_project(
        self, tenant_id: str, project: ProjectCreate
    ) -> ProjectRecord:
        now = _now_utc()
        rec = ProjectRecord(
            project_id=str(uuid4()),
            tenant_id=tenant_id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            environment=project.environment,
            settings=project.settings,
            created_at=now,
            updated_at=now,
        )
        async with self._locks["projects"]:
            self._tables["projects"].append(rec.model_dump(mode="json"))
            self._persist("projects")
        return rec

    async def get_project(
        self, tenant_id: str, project_id: str
    ) -> ProjectRecord | None:
        for row in self._tables["projects"]:
            if row["tenant_id"] == tenant_id and row["project_id"] == project_id:
                return ProjectRecord(**row)
        # Fallback: match by slug (SDK sends slug as project_id)
        for row in self._tables["projects"]:
            if row["tenant_id"] == tenant_id and row.get("slug") == project_id:
                return ProjectRecord(**row)
        return None

    async def list_projects(
        self,
        tenant_id: str,
        *,
        include_archived: bool = False,
    ) -> list[ProjectRecord]:
        results = []
        for row in self._tables["projects"]:
            if row["tenant_id"] != tenant_id:
                continue
            if not include_archived and row.get("is_archived", False):
                continue
            results.append(ProjectRecord(**row))
        return results

    async def update_project(
        self, tenant_id: str, project_id: str, updates: ProjectUpdate
    ) -> ProjectRecord | None:
        async with self._locks["projects"]:
            for row in self._tables["projects"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["project_id"] == project_id
                ):
                    patch = updates.model_dump(exclude_none=True)
                    row.update(patch)
                    row["updated_at"] = _now_utc().isoformat()
                    self._persist("projects")
                    return ProjectRecord(**row)
        return None

    async def archive_project(self, tenant_id: str, project_id: str) -> bool:
        async with self._locks["projects"]:
            for row in self._tables["projects"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["project_id"] == project_id
                ):
                    row["is_archived"] = True
                    row["updated_at"] = _now_utc().isoformat()
                    self._persist("projects")
                    return True
        return False

    async def unarchive_project(self, tenant_id: str, project_id: str) -> bool:
        async with self._locks["projects"]:
            for row in self._tables["projects"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["project_id"] == project_id
                ):
                    row["is_archived"] = False
                    row["updated_at"] = _now_utc().isoformat()
                    self._persist("projects")
                    return True
        return False

    async def count_projects(self, tenant_id: str) -> int:
        return sum(
            1 for row in self._tables["projects"]
            if row["tenant_id"] == tenant_id
            and not row.get("is_archived", False)
        )

    async def count_project_events(
        self, tenant_id: str, project_id: str
    ) -> int:
        return sum(
            1 for row in self._tables["events"]
            if row["tenant_id"] == tenant_id
            and row.get("project_id") == project_id
        )

    async def reassign_events(
        self,
        tenant_id: str,
        from_project_id: str,
        to_project_id: str,
    ) -> int:
        count = 0
        async with self._locks["events"]:
            for row in self._tables["events"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row.get("project_id") == from_project_id
                ):
                    row["project_id"] = to_project_id
                    count += 1
            if count > 0:
                self._persist("events")
        return count

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
    ) -> AgentRecord:
        async with self._locks["agents"]:
            existing = None
            for row in self._tables["agents"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["agent_id"] == agent_id
                ):
                    existing = row
                    break

            if existing is None:
                # Create new
                rec = AgentRecord(
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    agent_type=agent_type,
                    agent_version=agent_version,
                    framework=framework,
                    runtime=runtime,
                    first_seen=last_seen,
                    last_seen=last_seen,
                    last_heartbeat=last_heartbeat,
                    last_event_type=last_event_type,
                    last_task_id=last_task_id,
                    last_project_id=last_project_id,
                    stuck_threshold_seconds=stuck_threshold_seconds,
                )
                self._tables["agents"].append(rec.model_dump(mode="json"))
            else:
                # Compute previous status before updating
                prev_agent = AgentRecord(**existing)
                prev_status = derive_agent_status(prev_agent).value

                # Update with COALESCE semantics
                existing["last_seen"] = last_seen.isoformat()
                existing["previous_status"] = prev_status
                if agent_type is not None:
                    existing["agent_type"] = agent_type
                if agent_version is not None:
                    existing["agent_version"] = agent_version
                if framework is not None:
                    existing["framework"] = framework
                if runtime is not None:
                    existing["runtime"] = runtime
                if last_heartbeat is not None:
                    existing["last_heartbeat"] = last_heartbeat.isoformat()
                if last_event_type is not None:
                    existing["last_event_type"] = last_event_type
                if last_task_id is not None:
                    existing["last_task_id"] = last_task_id
                if last_project_id is not None:
                    existing["last_project_id"] = last_project_id
                existing["stuck_threshold_seconds"] = stuck_threshold_seconds
                rec = AgentRecord(**existing)

            self._persist("agents")
            return rec

    async def get_agent(
        self, tenant_id: str, agent_id: str
    ) -> AgentRecord | None:
        for row in self._tables["agents"]:
            if (
                row["tenant_id"] == tenant_id
                and row["agent_id"] == agent_id
            ):
                return AgentRecord(**row)
        return None

    async def list_agents(
        self,
        tenant_id: str,
        *,
        environment: str | None = None,
        group: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentRecord]:
        # If project_id, get agent_ids from junction table
        project_agent_ids: set[str] | None = None
        if project_id:
            project_agent_ids = {
                row["agent_id"]
                for row in self._tables["project_agents"]
                if (
                    row["tenant_id"] == tenant_id
                    and row["project_id"] == project_id
                )
            }

        results = []
        for row in self._tables["agents"]:
            if row["tenant_id"] != tenant_id:
                continue
            if project_agent_ids is not None and row["agent_id"] not in project_agent_ids:
                continue
            results.append(AgentRecord(**row))

        # Sort by last_seen descending
        results.sort(key=lambda a: a.last_seen, reverse=True)
        return results[:limit]

    async def compute_agent_stats_1h(
        self,
        tenant_id: str,
        agent_id: str,
    ) -> AgentStats1h:
        now = _now_utc()
        since = datetime.fromtimestamp(
            now.timestamp() - 3600, tz=timezone.utc
        )
        events = self._filter_events(
            tenant_id,
            agent_id=agent_id,
            since=since,
            exclude_heartbeats=True,
        )

        tasks_completed = 0
        tasks_failed = 0
        durations: list[int] = []
        total_cost = 0.0

        for e in events:
            if e["event_type"] == "task_completed":
                tasks_completed += 1
                if e.get("duration_ms"):
                    durations.append(e["duration_ms"])
            elif e["event_type"] == "task_failed":
                tasks_failed += 1
            p = e.get("payload")
            if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                data = p.get("data", {})
                if isinstance(data, dict):
                    total_cost += data.get("cost", 0) or 0

        total_tasks = tasks_completed + tasks_failed
        success_rate = (
            (tasks_completed / total_tasks * 100) if total_tasks > 0 else None
        )
        avg_duration = (
            int(sum(durations) / len(durations)) if durations else None
        )

        # Compute queue_depth and active_issues from pipeline data
        pipeline = await self.get_pipeline(tenant_id, agent_id)
        queue_depth = 0
        if pipeline.queue and isinstance(pipeline.queue, dict):
            queue_depth = pipeline.queue.get("depth", 0)
        active_issues = len(pipeline.issues)

        return AgentStats1h(
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            success_rate=success_rate,
            avg_duration_ms=avg_duration,
            total_cost=total_cost if total_cost > 0 else None,
            throughput=tasks_completed,
            queue_depth=queue_depth,
            active_issues=active_issues,
        )

    # ───────────────────────────────────────────────────────────────────
    #  PROJECT-AGENT JUNCTION
    # ───────────────────────────────────────────────────────────────────

    async def upsert_project_agent(
        self, tenant_id: str, project_id: str, agent_id: str
    ) -> None:
        async with self._locks["project_agents"]:
            for row in self._tables["project_agents"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["project_id"] == project_id
                    and row["agent_id"] == agent_id
                ):
                    return  # Already exists
            rec = ProjectAgentRecord(
                tenant_id=tenant_id,
                project_id=project_id,
                agent_id=agent_id,
                added_at=_now_utc(),
            )
            self._tables["project_agents"].append(rec.model_dump(mode="json"))
            self._persist("project_agents")

    # ───────────────────────────────────────────────────────────────────
    #  EVENT INGESTION
    # ───────────────────────────────────────────────────────────────────

    async def insert_events(self, events: list[Event], *, key_type: str | None = None) -> int:
        async with self._locks["events"]:
            existing_keys = {
                (row["tenant_id"], row["event_id"])
                for row in self._tables["events"]
            }
            inserted = 0
            for evt in events:
                key = (evt.tenant_id, evt.event_id)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                row = evt.model_dump(mode="json")
                if key_type:
                    row["key_type"] = key_type
                self._tables["events"].append(row)
                inserted += 1
            if inserted > 0:
                self._persist("events")
        return inserted

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
        rows = self._filter_events(
            tenant_id,
            project_id=project_id,
            agent_id=agent_id,
            task_id=task_id,
            event_type=event_type,
            severity=severity,
            environment=environment,
            group=group,
            since=since,
            until=until,
            exclude_heartbeats=exclude_heartbeats,
            payload_kind=payload_kind,
            key_type=key_type,
        )
        # Reverse chronological
        rows.sort(key=lambda r: r["timestamp"], reverse=True)

        # Cursor-based pagination: cursor is the event_id of the last item
        if cursor:
            start_idx = None
            for i, r in enumerate(rows):
                if r["event_id"] == cursor:
                    start_idx = i + 1
                    break
            if start_idx is not None:
                rows = rows[start_idx:]
            else:
                rows = []

        page_rows = rows[: limit]
        has_more = len(rows) > limit
        next_cursor = page_rows[-1]["event_id"] if has_more and page_rows else None

        return Page[Event](
            data=[Event(**r) for r in page_rows],
            pagination=PaginationInfo(
                cursor=next_cursor,
                has_more=has_more,
            ),
        )

    async def get_task_events(
        self,
        tenant_id: str,
        task_id: str,
    ) -> list[Event]:
        rows = [
            r for r in self._tables["events"]
            if r["tenant_id"] == tenant_id and r.get("task_id") == task_id
        ]
        rows.sort(key=lambda r: r["timestamp"])
        return [Event(**r) for r in rows]

    def _filter_events(
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
    ) -> list[dict[str, Any]]:
        """Filter events in memory — mirrors a SQL WHERE clause."""
        results = []
        for row in self._tables["events"]:
            if row["tenant_id"] != tenant_id:
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            if agent_id and row.get("agent_id") != agent_id:
                continue
            if task_id and row.get("task_id") != task_id:
                continue
            if event_type:
                # Support comma-separated types
                types = [t.strip() for t in event_type.split(",")]
                if row["event_type"] not in types:
                    continue
            if severity:
                severities = [s.strip() for s in severity.split(",")]
                if row.get("severity", "info") not in severities:
                    continue
            if environment and row.get("environment") != environment:
                continue
            if group and row.get("group") != group:
                continue
            if exclude_heartbeats and row["event_type"] == "heartbeat":
                continue
            if payload_kind:
                p = row.get("payload")
                if not p or not isinstance(p, dict) or p.get("kind") != payload_kind:
                    continue
            if key_type:
                row_key_type = row.get("key_type")
                if key_type == "test":
                    pass  # test keys see all events
                elif key_type == "live" and row_key_type == "test":
                    continue  # live keys don't see test events
            if since:
                ts = _parse_dt(row["timestamp"])
                if ts and ts < since:
                    continue
            if until:
                ts = _parse_dt(row["timestamp"])
                if ts and ts > until:
                    continue
            results.append(row)
        return results

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
        # Group events by task_id
        task_events: dict[str, list[dict]] = {}
        for row in self._tables["events"]:
            if row["tenant_id"] != tenant_id:
                continue
            tid = row.get("task_id")
            if not tid:
                continue
            if agent_id and row.get("agent_id") != agent_id:
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            if task_type and row.get("task_type") != task_type:
                continue
            if environment and row.get("environment") != environment:
                continue
            task_events.setdefault(tid, []).append(row)

        # Build TaskSummary for each
        summaries: list[TaskSummary] = []
        for tid, events in task_events.items():
            events.sort(key=lambda e: e["timestamp"])
            event_types = {e["event_type"] for e in events}
            derived = _derive_task_status(event_types)

            if status and derived != status:
                continue

            first = events[0]
            last = events[-1]

            # F4: since/until filter on task started_at
            started_at_dt = _parse_dt(first["timestamp"])
            if since and started_at_dt and started_at_dt < since:
                continue
            if until and started_at_dt and started_at_dt > until:
                continue

            # Duration from task_completed/task_failed event
            duration_ms = None
            completed_at = None
            for e in events:
                if e["event_type"] in ("task_completed", "task_failed"):
                    duration_ms = e.get("duration_ms")
                    completed_at = e["timestamp"]

            # Cost + token counts: sum from llm_call payloads (F3)
            total_cost = 0.0
            total_tokens_in = 0
            total_tokens_out = 0
            llm_call_count = 0
            for e in events:
                p = e.get("payload")
                if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                    data = p.get("data", {})
                    if isinstance(data, dict):
                        total_cost += data.get("cost", 0) or 0
                        total_tokens_in += data.get("tokens_in", 0) or 0
                        total_tokens_out += data.get("tokens_out", 0) or 0
                        llm_call_count += 1

            # Counts
            action_count = sum(
                1 for e in events
                if e["event_type"] in ("action_started",)
            )
            error_count = sum(
                1 for e in events
                if e["event_type"] in ("action_failed", "task_failed")
            )

            summaries.append(TaskSummary(
                task_id=tid,
                task_type=first.get("task_type"),
                task_run_id=first.get("task_run_id"),
                agent_id=first["agent_id"],
                project_id=first.get("project_id"),
                derived_status=derived,
                started_at=first["timestamp"],
                completed_at=completed_at,
                duration_ms=duration_ms,
                total_cost=total_cost if total_cost > 0 else None,
                action_count=action_count,
                error_count=error_count,
                has_escalation=EventType.ESCALATED in event_types,
                has_human_intervention=(
                    EventType.APPROVAL_REQUESTED in event_types
                    or EventType.APPROVAL_RECEIVED in event_types
                ),
                llm_call_count=llm_call_count,
                total_tokens_in=total_tokens_in,
                total_tokens_out=total_tokens_out,
            ))

        # Sort
        if sort == "newest":
            summaries.sort(key=lambda s: s.started_at, reverse=True)
        elif sort == "oldest":
            summaries.sort(key=lambda s: s.started_at)
        elif sort == "duration":
            summaries.sort(
                key=lambda s: s.duration_ms or 0, reverse=True
            )
        elif sort == "cost":
            summaries.sort(
                key=lambda s: s.total_cost or 0, reverse=True
            )

        # Cursor pagination
        if cursor:
            start_idx = None
            for i, s in enumerate(summaries):
                if s.task_id == cursor:
                    start_idx = i + 1
                    break
            if start_idx is not None:
                summaries = summaries[start_idx:]
            else:
                summaries = []

        page = summaries[:limit]
        has_more = len(summaries) > limit
        next_cursor = page[-1].task_id if has_more and page else None

        return Page[TaskSummary](
            data=page,
            pagination=PaginationInfo(cursor=next_cursor, has_more=has_more),
        )

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
        now = _now_utc()
        range_secs = RANGE_SECONDS.get(range, 3600)
        since = datetime.fromtimestamp(
            now.timestamp() - range_secs, tz=timezone.utc
        )
        interval = interval or AUTO_INTERVAL.get(range, "5m")
        interval_secs = INTERVAL_SECONDS.get(interval, 300)

        # Gather task events in range
        events = self._filter_events(
            tenant_id,
            agent_id=agent_id,
            project_id=project_id,
            environment=environment,
            since=since,
            exclude_heartbeats=True,
        )

        # Group by task_id to compute task-level metrics
        task_events: dict[str, list[dict]] = {}
        for e in events:
            tid = e.get("task_id")
            if tid:
                task_events.setdefault(tid, []).append(e)

        total_tasks = len(task_events)
        completed = 0
        failed = 0
        escalated = 0
        durations: list[int] = []
        total_cost = 0.0

        for tid, tevts in task_events.items():
            etypes = {e["event_type"] for e in tevts}
            st = _derive_task_status(etypes)
            if st == TaskStatus.COMPLETED:
                completed += 1
            elif st == TaskStatus.FAILED:
                failed += 1
            elif st == TaskStatus.ESCALATED:
                escalated += 1
            for e in tevts:
                if e.get("duration_ms") and e["event_type"] in (
                    "task_completed", "task_failed"
                ):
                    durations.append(e["duration_ms"])
                p = e.get("payload")
                if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                    data = p.get("data", {})
                    if isinstance(data, dict):
                        total_cost += data.get("cost", 0) or 0

        # Stuck agents
        agents = [
            AgentRecord(**a) for a in self._tables["agents"]
            if a["tenant_id"] == tenant_id
        ]
        stuck = sum(
            1 for a in agents
            if derive_agent_status(a, now) == AgentStatus.STUCK
        )

        success_rate = (
            (completed / total_tasks * 100) if total_tasks > 0 else None
        )
        avg_duration = (
            int(sum(durations) / len(durations)) if durations else None
        )
        avg_cost = (
            total_cost / total_tasks if total_tasks > 0 and total_cost > 0
            else None
        )

        summary = MetricsSummary(
            total_tasks=total_tasks,
            completed=completed,
            failed=failed,
            escalated=escalated,
            stuck=stuck,
            success_rate=success_rate,
            avg_duration_ms=avg_duration,
            total_cost=total_cost if total_cost > 0 else None,
            avg_cost_per_task=avg_cost,
        )

        # Timeseries buckets
        buckets: list[TimeseriesBucket] = []
        bucket_start = since.timestamp()
        bucket_end = now.timestamp()
        t = bucket_start
        while t < bucket_end:
            bucket_since = datetime.fromtimestamp(t, tz=timezone.utc)
            bucket_until = datetime.fromtimestamp(
                t + interval_secs, tz=timezone.utc
            )

            # Events in this bucket
            bucket_events = [
                e for e in events
                if bucket_since <= (_parse_dt(e["timestamp"]) or bucket_since) < bucket_until
            ]

            # Task metrics in bucket
            btask_events: dict[str, list[dict]] = {}
            for e in bucket_events:
                tid = e.get("task_id")
                if tid:
                    btask_events.setdefault(tid, []).append(e)

            b_completed = 0
            b_failed = 0
            b_durations: list[int] = []
            b_cost = 0.0
            b_errors = 0

            for tevts in btask_events.values():
                for e in tevts:
                    if e["event_type"] == "task_completed":
                        b_completed += 1
                        if e.get("duration_ms"):
                            b_durations.append(e["duration_ms"])
                    elif e["event_type"] == "task_failed":
                        b_failed += 1
                    if e["event_type"] in ("action_failed", "task_failed"):
                        b_errors += 1
                    p = e.get("payload")
                    if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                        data = p.get("data", {})
                        if isinstance(data, dict):
                            b_cost += data.get("cost", 0) or 0

            buckets.append(TimeseriesBucket(
                timestamp=bucket_since.isoformat(),
                tasks_completed=b_completed,
                tasks_failed=b_failed,
                avg_duration_ms=(
                    int(sum(b_durations) / len(b_durations))
                    if b_durations else None
                ),
                cost=b_cost,
                error_count=b_errors,
                throughput=b_completed,
            ))
            t += interval_secs

        # F10: group_by support
        groups = None
        if group_by and group_by in ("agent", "model"):
            grouped: dict[str, dict] = {}
            for e in events:
                if group_by == "agent":
                    key = e.get("agent_id", "unknown")
                else:
                    p = e.get("payload")
                    if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                        data = p.get("data", {})
                        key = data.get("model", "unknown") if isinstance(data, dict) else "unknown"
                    else:
                        continue

                if key not in grouped:
                    grouped[key] = {
                        group_by: key,
                        "tasks_completed": 0,
                        "tasks_failed": 0,
                        "total_cost": 0.0,
                    }
                if e["event_type"] == "task_completed":
                    grouped[key]["tasks_completed"] += 1
                elif e["event_type"] == "task_failed":
                    grouped[key]["tasks_failed"] += 1
                p = e.get("payload")
                if p and isinstance(p, dict) and p.get("kind") == "llm_call":
                    data = p.get("data", {})
                    if isinstance(data, dict):
                        grouped[key]["total_cost"] += data.get("cost", 0) or 0

            groups = list(grouped.values())

        return MetricsResponse(
            range=range,
            interval=interval,
            summary=summary,
            timeseries=buckets,
            groups=groups,
        )

    # ───────────────────────────────────────────────────────────────────
    #  COST (aggregated from llm_call payloads)
    # ───────────────────────────────────────────────────────────────────

    def _get_llm_call_events(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        model: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """Get events with payload.kind='llm_call'."""
        results = []
        for row in self._tables["events"]:
            if row["tenant_id"] != tenant_id:
                continue
            if agent_id and row.get("agent_id") != agent_id:
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            p = row.get("payload")
            if not p or not isinstance(p, dict) or p.get("kind") != "llm_call":
                continue
            data = p.get("data", {})
            if not isinstance(data, dict):
                continue
            if model and data.get("model") != model:
                continue
            if since:
                ts = _parse_dt(row["timestamp"])
                if ts and ts < since:
                    continue
            if until:
                ts = _parse_dt(row["timestamp"])
                if ts and ts > until:
                    continue
            results.append(row)
        return results

    async def get_cost_summary(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        range: str = "24h",
    ) -> CostSummary:
        now = _now_utc()
        range_secs = RANGE_SECONDS.get(range, 86400)
        since = datetime.fromtimestamp(
            now.timestamp() - range_secs, tz=timezone.utc
        )
        rows = self._get_llm_call_events(
            tenant_id, agent_id=agent_id, project_id=project_id, since=since
        )

        total_cost = 0.0
        total_tokens_in = 0
        total_tokens_out = 0
        by_agent: dict[str, dict] = {}
        by_model: dict[str, dict] = {}

        for row in rows:
            data = row["payload"]["data"]
            cost = data.get("cost", 0) or 0
            t_in = data.get("tokens_in", 0) or 0
            t_out = data.get("tokens_out", 0) or 0
            total_cost += cost
            total_tokens_in += t_in
            total_tokens_out += t_out
            aid = row["agent_id"]
            mdl = data.get("model", "unknown")

            if aid not in by_agent:
                by_agent[aid] = {"agent_id": aid, "cost": 0, "call_count": 0, "tokens_in": 0, "tokens_out": 0}
            by_agent[aid]["cost"] += cost
            by_agent[aid]["call_count"] += 1
            by_agent[aid]["tokens_in"] += t_in
            by_agent[aid]["tokens_out"] += t_out

            if mdl not in by_model:
                by_model[mdl] = {"model": mdl, "cost": 0, "call_count": 0, "tokens_in": 0, "tokens_out": 0}
            by_model[mdl]["cost"] += cost
            by_model[mdl]["call_count"] += 1
            by_model[mdl]["tokens_in"] += t_in
            by_model[mdl]["tokens_out"] += t_out

        return CostSummary(
            total_cost=total_cost,
            call_count=len(rows),
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
            by_agent=list(by_agent.values()),
            by_model=list(by_model.values()),
        )

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
        rows = self._get_llm_call_events(
            tenant_id,
            agent_id=agent_id,
            project_id=project_id,
            model=model,
            since=since,
            until=until,
        )
        rows.sort(key=lambda r: r["timestamp"], reverse=True)

        if cursor:
            start_idx = None
            for i, r in enumerate(rows):
                if r["event_id"] == cursor:
                    start_idx = i + 1
                    break
            if start_idx is not None:
                rows = rows[start_idx:]
            else:
                rows = []

        page_rows = rows[:limit]
        has_more = len(rows) > limit
        next_cursor = (
            page_rows[-1]["event_id"] if has_more and page_rows else None
        )

        records = []
        for r in page_rows:
            data = r["payload"]["data"]
            records.append(LlmCallRecord(
                event_id=r["event_id"],
                agent_id=r["agent_id"],
                project_id=r.get("project_id"),
                task_id=r.get("task_id"),
                timestamp=r["timestamp"],
                name=data.get("name", "unknown"),
                model=data.get("model", "unknown"),
                tokens_in=data.get("tokens_in"),
                tokens_out=data.get("tokens_out"),
                cost=data.get("cost"),
                duration_ms=data.get("duration_ms"),
            ))

        return Page[LlmCallRecord](
            data=records,
            pagination=PaginationInfo(cursor=next_cursor, has_more=has_more),
        )

    async def get_cost_timeseries(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        project_id: str | None = None,
        range: str = "24h",
        interval: str | None = None,
    ) -> list[CostTimeBucket]:
        now = _now_utc()
        range_secs = RANGE_SECONDS.get(range, 86400)
        since = datetime.fromtimestamp(
            now.timestamp() - range_secs, tz=timezone.utc
        )
        interval = interval or AUTO_INTERVAL.get(range, "1h")
        interval_secs = INTERVAL_SECONDS.get(interval, 3600)

        rows = self._get_llm_call_events(
            tenant_id, agent_id=agent_id, project_id=project_id, since=since
        )

        buckets: list[CostTimeBucket] = []
        t = since.timestamp()
        end = now.timestamp()
        while t < end:
            bucket_since = datetime.fromtimestamp(t, tz=timezone.utc)
            bucket_until = datetime.fromtimestamp(
                t + interval_secs, tz=timezone.utc
            )
            b_cost = 0.0
            b_count = 0
            b_tokens_in = 0
            b_tokens_out = 0
            for r in rows:
                ts = _parse_dt(r["timestamp"])
                if ts and bucket_since <= ts < bucket_until:
                    data = r["payload"]["data"]
                    b_cost += data.get("cost", 0) or 0
                    b_tokens_in += data.get("tokens_in", 0) or 0
                    b_tokens_out += data.get("tokens_out", 0) or 0
                    b_count += 1
            buckets.append(CostTimeBucket(
                timestamp=bucket_since.isoformat(),
                cost=b_cost,
                call_count=b_count,
                tokens_in=b_tokens_in,
                tokens_out=b_tokens_out,
            ))
            t += interval_secs

        return buckets

    # ───────────────────────────────────────────────────────────────────
    #  PIPELINE (derived from well-known payload kinds)
    # ───────────────────────────────────────────────────────────────────

    async def get_pipeline(
        self,
        tenant_id: str,
        agent_id: str,
    ) -> PipelineState:
        custom_events = [
            r for r in self._tables["events"]
            if (
                r["tenant_id"] == tenant_id
                and r.get("agent_id") == agent_id
                and r["event_type"] == "custom"
                and isinstance(r.get("payload"), dict)
                and r["payload"].get("kind") in (
                    "queue_snapshot", "todo", "scheduled", "issue"
                )
            )
        ]

        # Queue: latest queue_snapshot
        queue = None
        queue_events = [
            e for e in custom_events
            if e["payload"]["kind"] == "queue_snapshot"
        ]
        if queue_events:
            queue_events.sort(key=lambda e: e["timestamp"], reverse=True)
            latest = queue_events[0]
            queue = dict(latest["payload"].get("data", {}))
            queue["snapshot_at"] = latest["timestamp"]

        # TODOs: group by todo_id, take latest action, filter active
        todo_events = [
            e for e in custom_events if e["payload"]["kind"] == "todo"
        ]
        todos_by_id: dict[str, dict] = {}
        for e in sorted(todo_events, key=lambda e: e["timestamp"]):
            data = e["payload"].get("data", {})
            todo_id = data.get("todo_id")
            if todo_id:
                todos_by_id[todo_id] = {
                    "todo_id": todo_id,
                    "action": data.get("action"),
                    "priority": data.get("priority"),
                    "source": data.get("source"),
                    "context": data.get("context"),
                    "due_by": data.get("due_by"),
                    "timestamp": e["timestamp"],
                }
        active_todos = [
            t for t in todos_by_id.values()
            if t.get("action") not in ("completed", "dismissed")
        ]

        # Scheduled: latest scheduled event
        sched_events = [
            e for e in custom_events
            if e["payload"]["kind"] == "scheduled"
        ]
        scheduled = []
        if sched_events:
            sched_events.sort(key=lambda e: e["timestamp"], reverse=True)
            latest = sched_events[0]
            scheduled = latest["payload"].get("data", {}).get("items", [])

        # Issues: group by issue_id (or summary), take latest, filter active
        issue_events = [
            e for e in custom_events if e["payload"]["kind"] == "issue"
        ]
        issues_by_id: dict[str, dict] = {}
        for e in sorted(issue_events, key=lambda e: e["timestamp"]):
            data = e["payload"].get("data", {})
            issue_id = data.get("issue_id") or e["payload"].get("summary", "")
            issues_by_id[issue_id] = {
                "issue_id": issue_id,
                "severity": data.get("severity"),
                "category": data.get("category"),
                "context": data.get("context"),
                "action": data.get("action", "reported"),
                "occurrence_count": data.get("occurrence_count"),
                "summary": e["payload"].get("summary"),
                "timestamp": e["timestamp"],
            }
        active_issues = [
            iss for iss in issues_by_id.values()
            if iss.get("action") not in ("resolved",)
        ]

        return PipelineState(
            agent_id=agent_id,
            queue=queue,
            todos=active_todos,
            scheduled=scheduled,
            issues=active_issues,
        )

    # ───────────────────────────────────────────────────────────────────
    #  ALERT RULES
    # ───────────────────────────────────────────────────────────────────

    async def create_alert_rule(
        self, tenant_id: str, rule: AlertRuleCreate
    ) -> AlertRuleRecord:
        now = _now_utc()
        rec = AlertRuleRecord(
            rule_id=str(uuid4()),
            tenant_id=tenant_id,
            project_id=rule.project_id,
            name=rule.name,
            condition_type=rule.condition_type,
            condition_config=rule.condition_config,
            filters=rule.filters,
            actions=rule.actions,
            cooldown_seconds=rule.cooldown_seconds,
            created_at=now,
            updated_at=now,
        )
        async with self._locks["alert_rules"]:
            self._tables["alert_rules"].append(rec.model_dump(mode="json"))
            self._persist("alert_rules")
        return rec

    async def get_alert_rule(
        self, tenant_id: str, rule_id: str
    ) -> AlertRuleRecord | None:
        for row in self._tables["alert_rules"]:
            if (
                row["tenant_id"] == tenant_id
                and row["rule_id"] == rule_id
            ):
                return AlertRuleRecord(**row)
        return None

    async def list_alert_rules(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        is_enabled: bool | None = None,
    ) -> list[AlertRuleRecord]:
        results = []
        for row in self._tables["alert_rules"]:
            if row["tenant_id"] != tenant_id:
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            if is_enabled is not None and row.get("is_enabled", True) != is_enabled:
                continue
            results.append(AlertRuleRecord(**row))
        return results

    async def update_alert_rule(
        self, tenant_id: str, rule_id: str, updates: AlertRuleUpdate
    ) -> AlertRuleRecord | None:
        async with self._locks["alert_rules"]:
            for row in self._tables["alert_rules"]:
                if (
                    row["tenant_id"] == tenant_id
                    and row["rule_id"] == rule_id
                ):
                    patch = updates.model_dump(exclude_none=True)
                    row.update(patch)
                    row["updated_at"] = _now_utc().isoformat()
                    self._persist("alert_rules")
                    return AlertRuleRecord(**row)
        return None

    async def delete_alert_rule(self, tenant_id: str, rule_id: str) -> bool:
        async with self._locks["alert_rules"]:
            before = len(self._tables["alert_rules"])
            self._tables["alert_rules"] = [
                r for r in self._tables["alert_rules"]
                if not (
                    r["tenant_id"] == tenant_id and r["rule_id"] == rule_id
                )
            ]
            if len(self._tables["alert_rules"]) < before:
                self._persist("alert_rules")
                return True
        return False

    # ───────────────────────────────────────────────────────────────────
    #  ALERT HISTORY
    # ───────────────────────────────────────────────────────────────────

    async def insert_alert(
        self, tenant_id: str, alert: AlertHistoryRecord
    ) -> None:
        async with self._locks["alert_history"]:
            self._tables["alert_history"].append(
                alert.model_dump(mode="json")
            )
            self._persist("alert_history")

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
        rows = []
        for row in self._tables["alert_history"]:
            if row["tenant_id"] != tenant_id:
                continue
            if rule_id and row.get("rule_id") != rule_id:
                continue
            if project_id and row.get("project_id") != project_id:
                continue
            if since:
                fired = _parse_dt(row.get("fired_at"))
                if fired and fired < since:
                    continue
            rows.append(row)

        rows.sort(key=lambda r: r.get("fired_at", ""), reverse=True)

        if cursor:
            start_idx = None
            for i, r in enumerate(rows):
                if r["alert_id"] == cursor:
                    start_idx = i + 1
                    break
            if start_idx is not None:
                rows = rows[start_idx:]
            else:
                rows = []

        page_rows = rows[:limit]
        has_more = len(rows) > limit
        next_cursor = (
            page_rows[-1]["alert_id"] if has_more and page_rows else None
        )

        return Page[AlertHistoryRecord](
            data=[AlertHistoryRecord(**r) for r in page_rows],
            pagination=PaginationInfo(cursor=next_cursor, has_more=has_more),
        )

    async def get_last_alert_for_rule(
        self, tenant_id: str, rule_id: str
    ) -> AlertHistoryRecord | None:
        matches = [
            r for r in self._tables["alert_history"]
            if r["tenant_id"] == tenant_id and r["rule_id"] == rule_id
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get("fired_at", ""), reverse=True)
        return AlertHistoryRecord(**matches[0])
