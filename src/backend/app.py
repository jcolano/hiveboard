"""HiveBoard API Server — FastAPI application.

Run with: uvicorn backend.app:app --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.middleware import AuthMiddleware, RateLimitMiddleware
from backend.storage_json import JsonStorageBackend, derive_agent_status
from backend.llm_pricing import LlmPricingEngine
from shared.enums import (
    AgentStatus,
    EventType,
    MAX_AGENT_ID_CHARS,
    MAX_BATCH_BYTES,
    MAX_BATCH_EVENTS,
    MAX_ENVIRONMENT_CHARS,
    MAX_GROUP_CHARS,
    MAX_PAYLOAD_BYTES,
    MAX_TASK_ID_CHARS,
    SEVERITY_DEFAULTS,
    SEVERITY_BY_PAYLOAD_KIND,
    VALID_SEVERITIES,
)
from shared.models import (
    AgentRecord,
    AgentStats1h,
    AgentSummary,
    AlertRuleCreate,
    AlertRuleUpdate,
    CostSummary,
    ErrorResponse,
    Event,
    IngestError,
    IngestRequest,
    IngestResponse,
    Page,
    PaginationInfo,
    ProjectCreate,
    ProjectMergeRequest,
    ProjectUpdate,
)


# ═══════════════════════════════════════════════════════════════════════════
#  APP LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = JsonStorageBackend()
    await storage.initialize()
    app.state.storage = storage
    # Initialize LLM pricing engine
    pricing = LlmPricingEngine()
    await pricing.initialize()
    app.state.pricing = pricing
    # Bootstrap: create default tenant + key if none exist
    await _bootstrap_dev_tenant(storage)
    # Start WebSocket ping task
    from backend.websocket import ws_manager
    ping_task = asyncio.create_task(_ws_ping_loop())
    yield
    ping_task.cancel()
    await storage.close()


async def _ws_ping_loop():
    """Send WebSocket pings every 30 seconds."""
    from backend.websocket import ws_manager
    while True:
        await asyncio.sleep(30)
        await ws_manager.ping_all()


async def _bootstrap_dev_tenant(storage: JsonStorageBackend):
    """Create a dev tenant and API key on first run for easy testing.

    The dev key is read from HIVEBOARD_DEV_KEY env var.
    If unset, bootstrap is skipped (no hardcoded key in source).
    """
    raw_key = os.environ.get("HIVEBOARD_DEV_KEY")
    if not raw_key:
        return
    tenant = await storage.get_tenant("dev")
    if tenant is not None:
        return
    await storage.create_tenant("dev", "Development", "dev")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await storage.create_api_key(
        key_id="dev-key",
        tenant_id="dev",
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        key_type="live",
        label="Development API Key",
    )


app = FastAPI(
    title="HiveBoard API",
    version="0.1.0",
    description="Observability platform for AI agents",
    lifespan=lifespan,
)

# CORS — allow all origins for MVP
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware stack (order matters: rate limit wraps auth wraps routes)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)


# ═══════════════════════════════════════════════════════════════════════════
#  ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════════

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail if isinstance(exc.detail, str) else "error",
            "message": str(exc.detail),
            "status": exc.status_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    field_errors = []
    for err in exc.errors():
        field_errors.append({
            "field": ".".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        })
    return JSONResponse(
        status_code=400,
        content={
            "error": "validation_error",
            "message": "Request validation failed",
            "status": 400,
            "details": {"fields": field_errors},
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  HEALTH + DASHBOARD (B2.1.1 / B2.1.5)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Redirect to the Team 2 dashboard served from /static/."""
    static_dir = Path(__file__).parent.parent / "static"
    index = static_dir / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


# Mount Team 2's static dashboard files
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ═══════════════════════════════════════════════════════════════════════════
#  INGESTION ENDPOINT (B2.2)
# ═══════════════════════════════════════════════════════════════════════════

# Valid event types for validation
VALID_EVENT_TYPES = {et.value for et in EventType}

# Well-known payload kind required fields (advisory)
PAYLOAD_REQUIRED_FIELDS: dict[str, list[str]] = {
    "llm_call": ["name", "model"],
    "queue_snapshot": ["depth"],
    "todo": ["todo_id", "action"],
    "plan_created": ["steps"],
    "plan_step": ["step_index", "total_steps", "action"],
    "issue": ["severity"],
    "scheduled": ["items"],
}


@app.post("/v1/ingest")
async def ingest(body: IngestRequest, request: Request):
    """The critical write path — 10-step ingestion pipeline."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Step 2: Validate batch constraints
    if len(body.events) > MAX_BATCH_EVENTS:
        raise HTTPException(400, f"Batch exceeds max {MAX_BATCH_EVENTS} events")

    errors: list[IngestError] = []
    warnings: list[dict[str, str]] = []
    accepted_events: list[Event] = []
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Track agent metadata from the batch
    has_heartbeat = False
    last_event_type = None
    last_task_id = None
    last_project_id = None
    project_ids_seen: set[str] = set()

    for raw in body.events:
        # Step 3: Per-event validation
        if not raw.event_id:
            errors.append(IngestError(
                error="missing_field", message="event_id is required",
            ))
            continue
        if not raw.timestamp:
            errors.append(IngestError(
                event_id=raw.event_id, error="missing_field",
                message="timestamp is required",
            ))
            continue
        if raw.event_type not in VALID_EVENT_TYPES:
            errors.append(IngestError(
                event_id=raw.event_id, error="invalid_event_type",
                message=f"Unknown event_type: {raw.event_type}",
            ))
            continue

        # Field size limits
        agent_id = raw.agent_id or body.envelope.agent_id
        if len(agent_id) > MAX_AGENT_ID_CHARS:
            errors.append(IngestError(
                event_id=raw.event_id, error="field_too_long",
                message=f"agent_id exceeds {MAX_AGENT_ID_CHARS} chars",
            ))
            continue
        if raw.task_id and len(raw.task_id) > MAX_TASK_ID_CHARS:
            errors.append(IngestError(
                event_id=raw.event_id, error="field_too_long",
                message=f"task_id exceeds {MAX_TASK_ID_CHARS} chars",
            ))
            continue

        # Payload size check
        if raw.payload:
            payload_size = len(json.dumps(raw.payload))
            if payload_size > MAX_PAYLOAD_BYTES:
                errors.append(IngestError(
                    event_id=raw.event_id, error="payload_too_large",
                    message=f"payload exceeds {MAX_PAYLOAD_BYTES} bytes",
                ))
                continue

        # Step 3b: Payload convention validation (advisory — warn but don't reject)
        if raw.payload and isinstance(raw.payload, dict):
            kind = raw.payload.get("kind")
            if kind and kind in PAYLOAD_REQUIRED_FIELDS:
                data = raw.payload.get("data", {})
                if isinstance(data, dict):
                    for field in PAYLOAD_REQUIRED_FIELDS[kind]:
                        if field not in data:
                            warnings.append({
                                "event_id": raw.event_id,
                                "warning": f"payload.kind={kind} recommends data.{field}",
                            })

        # Step 4: Expand envelope
        env_str = body.envelope.environment or "production"
        env_override = env_str
        if len(env_override) > MAX_ENVIRONMENT_CHARS:
            warnings.append({
                "event_id": raw.event_id,
                "warning": f"environment truncated from {len(env_override)} to {MAX_ENVIRONMENT_CHARS} chars",
            })
            env_override = env_override[:MAX_ENVIRONMENT_CHARS]
        grp = body.envelope.group or "default"
        if len(grp) > MAX_GROUP_CHARS:
            warnings.append({
                "event_id": raw.event_id,
                "warning": f"group truncated from {len(grp)} to {MAX_GROUP_CHARS} chars",
            })
            grp = grp[:MAX_GROUP_CHARS]

        # Severity auto-defaults
        severity = raw.severity
        if severity and severity not in VALID_SEVERITIES:
            warnings.append({
                "event_id": raw.event_id,
                "warning": f"Unknown severity '{severity}', defaulting to auto",
            })
            severity = None
        if not severity:
            severity = SEVERITY_DEFAULTS.get(raw.event_type, "info")
            # Payload kind overrides
            if raw.payload and isinstance(raw.payload, dict):
                pk = raw.payload.get("kind")
                if pk and pk in SEVERITY_BY_PAYLOAD_KIND:
                    severity = SEVERITY_BY_PAYLOAD_KIND[pk]

        # Step 5: Validate or auto-create project
        project_id = raw.project_id
        if project_id:
            proj = await storage.get_project(tenant_id, project_id)
            if proj is None:
                # Auto-create project for unknown slug (Issue #9)
                project_count = await storage.count_projects(tenant_id)
                if project_count >= 50:
                    # Tenant at project limit — route to default project
                    default_proj = await storage.get_project(tenant_id, "default")
                    if default_proj:
                        project_id = default_proj.project_id
                    warnings.append({
                        "event_id": raw.event_id,
                        "warning": f"Project limit (50) reached; routed to default project",
                        "project_slug": raw.project_id,
                    })
                else:
                    # Auto-create the project with the slug
                    slug = raw.project_id
                    new_proj = await storage.create_project(
                        tenant_id,
                        ProjectCreate(name=slug, slug=slug),
                    )
                    # Mark as auto-created
                    await storage.update_project(
                        tenant_id, new_proj.project_id,
                        ProjectUpdate(),
                    )
                    # Set auto_created flag directly
                    async with storage._locks["projects"]:
                        for row in storage._tables["projects"]:
                            if row["project_id"] == new_proj.project_id:
                                row["auto_created"] = True
                                storage._persist("projects")
                                break
                    project_id = new_proj.project_id
                    warnings.append({
                        "event_id": raw.event_id,
                        "warning": f"Auto-created project '{slug}'",
                        "project_slug": slug,
                    })
            else:
                # Resolve slug to project_id if get_project matched by slug
                project_id = proj.project_id

        # Step 5b: LLM cost estimation (Issue #15)
        enriched_payload = raw.payload
        if isinstance(enriched_payload, dict) and enriched_payload.get("kind") == "llm_call":
            pricing: LlmPricingEngine = request.app.state.pricing
            enriched_payload = pricing.process_llm_event(enriched_payload)

        event = Event(
            event_id=raw.event_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_type=raw.agent_type or body.envelope.agent_type,
            project_id=project_id,
            timestamp=raw.timestamp,
            received_at=now_iso,
            environment=env_override,
            group=grp,
            task_id=raw.task_id,
            task_type=raw.task_type,
            task_run_id=raw.task_run_id,
            correlation_id=raw.correlation_id,
            action_id=raw.action_id,
            parent_action_id=raw.parent_action_id,
            event_type=raw.event_type,
            severity=severity,
            status=raw.status,
            duration_ms=raw.duration_ms,
            parent_event_id=raw.parent_event_id,
            payload=enriched_payload,
        )
        accepted_events.append(event)

        # Track metadata
        if raw.event_type == "heartbeat":
            has_heartbeat = True
        if raw.task_id:
            last_task_id = raw.task_id
        if project_id:
            last_project_id = project_id
            project_ids_seen.add(project_id)

    # W3: Sort accepted events by timestamp for correct last_event_type
    accepted_events.sort(key=lambda e: e.timestamp)
    if accepted_events:
        last_event_type = accepted_events[-1].event_type

    # Step 6: Batch insert
    ingestion_key_type = getattr(request.state, "key_type", "live")
    inserted = 0
    if accepted_events:
        inserted = await storage.insert_events(accepted_events, key_type=ingestion_key_type)

    # Step 7: Agent cache update
    agent_record = None
    if accepted_events:
        last_ts = max(
            _parse_dt(e.timestamp) for e in accepted_events
        ) or now
        agent_record = await storage.upsert_agent(
            tenant_id,
            body.envelope.agent_id,
            agent_type=body.envelope.agent_type or "general",
            agent_version=body.envelope.agent_version,
            framework=body.envelope.framework,
            runtime=body.envelope.runtime,
            last_seen=last_ts,
            last_heartbeat=last_ts if has_heartbeat else None,
            last_event_type=last_event_type,
            last_task_id=last_task_id,
            last_project_id=last_project_id,
        )

    # Step 8: Project-agent junction
    for pid in project_ids_seen:
        await storage.upsert_project_agent(
            tenant_id, pid, body.envelope.agent_id
        )

    # Step 9: WebSocket broadcast
    from backend.websocket import ws_manager
    if accepted_events:
        event_dicts = [e.model_dump(mode="json") for e in accepted_events]
        await ws_manager.broadcast_events(tenant_id, event_dicts)

        # F11: Check for agent status change and broadcast
        if agent_record:
            new_status = derive_agent_status(agent_record)
            previous_status = agent_record.previous_status

            if previous_status and previous_status != new_status.value:
                hb_age = None
                if agent_record.last_heartbeat:
                    hb_age = int((datetime.now(timezone.utc) - agent_record.last_heartbeat).total_seconds())
                await ws_manager.broadcast_agent_status_change(
                    tenant_id, agent_record.agent_id,
                    previous_status, new_status.value,
                    agent_record.last_task_id, agent_record.last_project_id,
                    hb_age,
                )

            if new_status == AgentStatus.STUCK:
                await ws_manager.broadcast_agent_stuck(
                    tenant_id, agent_record.agent_id,
                    agent_record.last_heartbeat.isoformat() if agent_record.last_heartbeat else None,
                    agent_record.stuck_threshold_seconds,
                    agent_record.last_task_id, agent_record.last_project_id,
                )
            else:
                ws_manager.clear_stuck(tenant_id, agent_record.agent_id)

    # Step 10: Alert evaluation
    from backend.alerting import evaluate_alerts
    if accepted_events:
        await evaluate_alerts(storage, tenant_id, accepted_events)

    response = IngestResponse(
        accepted=len(accepted_events),
        rejected=len(errors),
        errors=errors,
    )

    status_code = 200 if not errors else 207
    result = response.model_dump(mode="json")
    if warnings:
        result["warnings"] = warnings
    return JSONResponse(content=result, status_code=status_code)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  QUERY ENDPOINTS — Priority 1 (B2.3)
#  These power Team 2's dashboard: agents, tasks, events, projects
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_ts(iso_str: str | None) -> str | None:
    """W10: Normalize timestamps to end with Z instead of +00:00."""
    if iso_str is None:
        return None
    return iso_str.replace("+00:00", "Z")


async def _agent_to_summary(agent: AgentRecord, now: datetime, storage=None) -> AgentSummary:
    """Convert agent record to API response with derived status."""
    status = derive_agent_status(agent, now)
    hb_age = None
    if agent.last_heartbeat:
        hb_age = int((now - agent.last_heartbeat).total_seconds())

    stats = AgentStats1h()
    if storage:
        stats = await storage.compute_agent_stats_1h(agent.tenant_id, agent.agent_id)

    return AgentSummary(
        agent_id=agent.agent_id,
        agent_type=agent.agent_type,
        agent_version=agent.agent_version,
        framework=agent.framework,
        environment="production",
        group="default",
        derived_status=status.value,
        current_task_id=agent.last_task_id,
        current_project_id=agent.last_project_id,
        last_heartbeat=_normalize_ts(agent.last_heartbeat.isoformat()) if agent.last_heartbeat else None,
        heartbeat_age_seconds=hb_age,
        is_stuck=(status == AgentStatus.STUCK),
        stuck_threshold_seconds=agent.stuck_threshold_seconds,
        first_seen=_normalize_ts(agent.first_seen.isoformat()) if agent.first_seen else None,
        last_seen=_normalize_ts(agent.last_seen.isoformat()) if agent.last_seen else None,
        stats_1h=stats,
    )


# --- B2.3.1: GET /v1/agents ---

@app.get("/v1/agents")
async def list_agents(
    request: Request,
    project_id: str | None = None,
    environment: str | None = None,
    group: str | None = None,
    status: str | None = None,
    sort: str = "last_seen",
    limit: int = Query(default=50, le=200),
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    now = datetime.now(timezone.utc)

    agents = await storage.list_agents(
        tenant_id, project_id=project_id, environment=environment,
        group=group, limit=limit,
    )

    summaries = [await _agent_to_summary(a, now, storage) for a in agents]

    # Filter by derived status
    if status:
        summaries = [s for s in summaries if s.derived_status == status]

    # Sort
    if sort == "attention":
        priority = {
            "stuck": 0, "error": 1, "waiting_approval": 2,
            "processing": 3, "idle": 4,
        }
        summaries.sort(key=lambda s: priority.get(s.derived_status, 5))
    elif sort == "name":
        summaries.sort(key=lambda s: s.agent_id)

    return {"data": [s.model_dump(mode="json") for s in summaries]}


# --- B2.3.2: GET /v1/agents/{agent_id} ---

@app.get("/v1/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    now = datetime.now(timezone.utc)

    agent = await storage.get_agent(tenant_id, agent_id)
    if agent is None:
        raise HTTPException(404, {"error": "not_found", "message": "Agent not found", "status": 404})

    summary = await _agent_to_summary(agent, now, storage)
    return summary.model_dump(mode="json")


# --- B2.3.3: GET /v1/agents/{agent_id}/pipeline ---

@app.get("/v1/agents/{agent_id}/pipeline")
async def get_agent_pipeline(
    agent_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    pipeline = await storage.get_pipeline(tenant_id, agent_id)
    return pipeline.model_dump(mode="json")


# --- B2.3.4: GET /v1/tasks ---

@app.get("/v1/tasks")
async def list_tasks(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_type: str | None = None,
    status: str | None = None,
    environment: str | None = None,
    since: str | None = None,
    until: str | None = None,
    sort: str = "newest",
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    since_dt = _parse_dt(since) if since else None
    until_dt = _parse_dt(until) if until else None
    page = await storage.list_tasks(
        tenant_id, agent_id=agent_id, project_id=project_id,
        task_type=task_type, status=status, environment=environment,
        since=since_dt, until=until_dt,
        sort=sort, limit=limit, cursor=cursor,
    )
    return page.model_dump(mode="json")


# --- B2.3.5: GET /v1/tasks/{task_id}/timeline ---

@app.get("/v1/tasks/{task_id}/timeline")
async def get_task_timeline(
    task_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    events = await storage.get_task_events(tenant_id, task_id)
    if not events:
        raise HTTPException(404, {"error": "not_found", "message": "Task not found", "status": 404})

    event_dicts = [e.model_dump(mode="json") for e in events]
    event_types = {e.event_type for e in events}
    first = events[0]
    last = events[-1]

    # Derive status
    from backend.storage_json import _derive_task_status
    derived = _derive_task_status(event_types)

    # Duration and completion
    duration_ms = None
    completed_at = None
    total_cost = 0.0
    for e in events:
        if e.event_type in ("task_completed", "task_failed"):
            duration_ms = e.duration_ms
            completed_at = e.timestamp
        if e.payload and isinstance(e.payload, dict) and e.payload.get("kind") == "llm_call":
            data = e.payload.get("data", {})
            if isinstance(data, dict):
                total_cost += data.get("cost", 0) or 0

    # F6: Plan overlay — build plan from plan_created and plan_step payloads
    plan = None
    plan_steps: list[dict] = []
    plan_goal: str | None = None
    plan_completed = 0
    plan_total = 0
    # Track per-step status from plan_step events
    step_status: dict[int, dict] = {}  # step_index → {action, timestamp}
    for e in events:
        if e.payload and isinstance(e.payload, dict):
            kind = e.payload.get("kind")
            data = e.payload.get("data", {})
            if kind == "plan_created" and isinstance(data, dict):
                plan_steps = data.get("steps", [])
                plan_goal = e.payload.get("summary")
                plan_total = len(plan_steps)
            elif kind == "plan_step" and isinstance(data, dict):
                plan_total = data.get("total_steps", plan_total)
                idx = data.get("step_index")
                action = data.get("action")
                if idx is not None and action:
                    step_status[idx] = {"action": action, "timestamp": e.timestamp}
                if action == "completed":
                    plan_completed += 1
    # Enrich plan steps with status from plan_step events
    if plan_steps:
        for step in plan_steps:
            idx = step.get("index")
            if idx is not None and idx in step_status:
                ss = step_status[idx]
                step["action"] = ss["action"]
                if ss["action"] == "completed":
                    step["completed_at"] = ss["timestamp"]
                elif ss["action"] == "started":
                    step["started_at"] = ss["timestamp"]
    if plan_steps or plan_total > 0:
        plan = {
            "goal": plan_goal,
            "steps": plan_steps,
            "progress": {"completed": plan_completed, "total": plan_total},
        }

    # F5: Build action tree with name, status, duration_ms
    actions: dict[str, dict] = {}
    for e in events:
        if e.event_type in ("action_started", "action_completed", "action_failed"):
            aid = e.action_id
            if aid and aid not in actions:
                actions[aid] = {
                    "action_id": aid,
                    "parent_action_id": e.parent_action_id,
                    "name": None,
                    "status": None,
                    "duration_ms": None,
                    "events": [],
                    "children": [],
                }
            if aid:
                actions[aid]["events"].append(e.model_dump(mode="json"))
                if e.event_type == "action_started":
                    # Extract name from payload — SDK puts action_name at top level
                    if e.payload and isinstance(e.payload, dict):
                        name = e.payload.get("action_name")
                        if not name:
                            data = e.payload.get("data", {})
                            if isinstance(data, dict):
                                name = data.get("action_name")
                        if not name:
                            name = e.payload.get("summary")
                        actions[aid]["name"] = name
                elif e.event_type == "action_completed":
                    actions[aid]["status"] = e.status or "completed"
                    actions[aid]["duration_ms"] = e.duration_ms
                elif e.event_type == "action_failed":
                    actions[aid]["status"] = e.status or "failed"
                    actions[aid]["duration_ms"] = e.duration_ms

    # Nest children
    roots: list[dict] = []
    for aid, action in actions.items():
        parent = action.get("parent_action_id")
        if parent and parent in actions:
            actions[parent]["children"].append(action)
        else:
            roots.append(action)

    # Build error chains
    error_chains: list[dict] = []
    error_events = [
        e for e in events
        if e.event_type in ("retry_started", "escalated") and e.parent_event_id
    ]
    for e in error_events:
        chain = {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "parent_event_id": e.parent_event_id,
            "timestamp": e.timestamp,
            "payload": e.payload,
        }
        error_chains.append(chain)

    from shared.models import TimelineSummary
    timeline = TimelineSummary(
        task_id=task_id,
        task_run_id=first.task_run_id,
        agent_id=first.agent_id,
        project_id=first.project_id,
        task_type=first.task_type,
        derived_status=derived,
        started_at=first.timestamp,
        completed_at=completed_at,
        duration_ms=duration_ms,
        total_cost=total_cost if total_cost > 0 else None,
        events=event_dicts,
        action_tree=roots,
        error_chains=error_chains,
        plan=plan,
    )
    return timeline.model_dump(mode="json")


# --- B2.3.6: GET /v1/events ---

@app.get("/v1/events")
async def list_events(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    environment: str | None = None,
    group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    exclude_heartbeats: bool = True,
    payload_kind: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    since_dt = _parse_dt(since) if since else None
    until_dt = _parse_dt(until) if until else None

    page = await storage.get_events(
        tenant_id,
        project_id=project_id,
        agent_id=agent_id,
        task_id=task_id,
        event_type=event_type,
        severity=severity,
        environment=environment,
        group=group,
        since=since_dt,
        until=until_dt,
        exclude_heartbeats=exclude_heartbeats,
        payload_kind=payload_kind,
        limit=limit,
        cursor=cursor,
    )
    return page.model_dump(mode="json")


# --- B2.3.7: GET /v1/metrics ---

@app.get("/v1/metrics")
async def get_metrics(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    environment: str | None = None,
    metric: str | None = None,
    group_by: str | None = None,
    range: str = "1h",
    interval: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    metrics = await storage.get_metrics(
        tenant_id,
        agent_id=agent_id,
        project_id=project_id,
        environment=environment,
        metric=metric,
        group_by=group_by,
        range=range,
        interval=interval,
    )
    return metrics.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════
#  COST ENDPOINTS (B2.3.8 – B2.3.11)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/cost")
async def get_cost(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    range: str = "24h",
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    cost = await storage.get_cost_summary(
        tenant_id, agent_id=agent_id, project_id=project_id, range=range,
    )
    return cost.model_dump(mode="json")


@app.get("/v1/cost/calls")
async def get_cost_calls(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    page = await storage.get_cost_calls(
        tenant_id, agent_id=agent_id, project_id=project_id, model=model,
        since=_parse_dt(since), until=_parse_dt(until),
        limit=limit, cursor=cursor,
    )
    return page.model_dump(mode="json")


@app.get("/v1/cost/timeseries")
async def get_cost_timeseries(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    range: str = "24h",
    interval: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    buckets = await storage.get_cost_timeseries(
        tenant_id, agent_id=agent_id, project_id=project_id,
        range=range, interval=interval,
    )
    return {"data": [b.model_dump(mode="json") for b in buckets]}


@app.get("/v1/llm-calls")
async def list_llm_calls(
    request: Request,
    project_id: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    page = await storage.get_cost_calls(
        tenant_id, agent_id=agent_id, project_id=project_id, model=model,
        since=_parse_dt(since), until=_parse_dt(until),
        limit=limit, cursor=cursor,
    )
    # Add totals wrapper
    total_cost = sum(r.cost or 0 for r in page.data)
    total_tokens_in = sum(r.tokens_in or 0 for r in page.data)
    total_tokens_out = sum(r.tokens_out or 0 for r in page.data)
    return {
        "data": [r.model_dump(mode="json") for r in page.data],
        "pagination": page.pagination.model_dump(mode="json"),
        "totals": {
            "cost": total_cost,
            "tokens_in": total_tokens_in,
            "tokens_out": total_tokens_out,
            "call_count": len(page.data),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN — LLM PRICING (Issue #15)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/admin/pricing")
async def list_pricing(request: Request):
    pricing: LlmPricingEngine = request.app.state.pricing
    return {"data": await pricing.list_entries()}


@app.post("/v1/admin/pricing", status_code=201)
async def add_pricing(request: Request):
    body = await request.json()
    required = {"model_pattern", "provider", "input_per_m", "output_per_m"}
    if not required.issubset(body.keys()):
        raise HTTPException(400, f"Missing required fields: {required - body.keys()}")
    pricing: LlmPricingEngine = request.app.state.pricing
    entry = await pricing.add_entry(body)
    return entry


@app.put("/v1/admin/pricing/{pattern}")
async def update_pricing(pattern: str, request: Request):
    body = await request.json()
    pricing: LlmPricingEngine = request.app.state.pricing
    entry = await pricing.update_entry(pattern, body)
    if entry is None:
        raise HTTPException(404, f"Pricing pattern '{pattern}' not found")
    return entry


@app.delete("/v1/admin/pricing/{pattern}")
async def delete_pricing(pattern: str, request: Request):
    pricing: LlmPricingEngine = request.app.state.pricing
    deleted = await pricing.delete_entry(pattern)
    if not deleted:
        raise HTTPException(404, f"Pricing pattern '{pattern}' not found")
    return {"deleted": pattern}


# ═══════════════════════════════════════════════════════════════════════════
#  PROJECT ENDPOINTS (B2.3.12 – B2.3.21)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/projects")
async def list_projects(
    request: Request,
    include_archived: bool = False,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    projects = await storage.list_projects(
        tenant_id, include_archived=include_archived,
    )
    result = []
    for p in projects:
        d = p.model_dump(mode="json")
        d["event_count"] = await storage.count_project_events(tenant_id, p.project_id)
        result.append(d)
    return {"data": result}


@app.post("/v1/projects")
async def create_project(
    body: ProjectCreate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    project = await storage.create_project(tenant_id, body)
    return JSONResponse(
        content=project.model_dump(mode="json"), status_code=201,
    )


@app.get("/v1/projects/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    project = await storage.get_project(tenant_id, project_id)
    if project is None:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    return project.model_dump(mode="json")


@app.put("/v1/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    project = await storage.update_project(tenant_id, project_id, body)
    if project is None:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    return project.model_dump(mode="json")


@app.delete("/v1/projects/{project_id}")
async def delete_project(
    project_id: str,
    request: Request,
    reassign_to: str | None = Query(default=None, description="Slug/ID of project to reassign events to (default: 'default')"),
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    project = await storage.get_project(tenant_id, project_id)
    if project is None:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    # W7: Protect default project
    if project.slug == "default":
        raise HTTPException(400, {"error": "cannot_delete_default", "message": "Cannot delete the default project", "status": 400})

    # Reassign events to target project (default: "default" project)
    target_slug = reassign_to or "default"
    target = await storage.get_project(tenant_id, target_slug)
    events_moved = 0
    if target and target.project_id != project.project_id:
        events_moved = await storage.reassign_events(
            tenant_id, project.project_id, target.project_id
        )

    await storage.archive_project(tenant_id, project.project_id)
    return {"status": "deleted", "events_reassigned": events_moved, "reassigned_to": target_slug}


@app.post("/v1/projects/{project_id}/archive")
async def archive_project(
    project_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.archive_project(tenant_id, project_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    return {"status": "archived"}


@app.post("/v1/projects/{project_id}/unarchive")
async def unarchive_project(
    project_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.unarchive_project(tenant_id, project_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    return {"status": "unarchived"}


@app.post("/v1/projects/{project_id}/merge")
async def merge_project(
    project_id: str,
    body: ProjectMergeRequest,
    request: Request,
):
    """Merge source project into target: reassign all events, then archive source."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Resolve source project (by id or slug)
    source = await storage.get_project(tenant_id, project_id)
    if source is None:
        raise HTTPException(404, {"error": "not_found", "message": "Source project not found", "status": 404})

    # Resolve target project (by slug)
    target = await storage.get_project(tenant_id, body.target_slug)
    if target is None:
        raise HTTPException(404, {"error": "not_found", "message": f"Target project '{body.target_slug}' not found", "status": 404})

    if source.project_id == target.project_id:
        raise HTTPException(400, {"error": "invalid_merge", "message": "Cannot merge a project into itself", "status": 400})

    # Reassign all events from source to target
    moved = await storage.reassign_events(tenant_id, source.project_id, target.project_id)

    # Reassign project_agents junction entries
    async with storage._locks["project_agents"]:
        for row in storage._tables["project_agents"]:
            if (
                row["tenant_id"] == tenant_id
                and row["project_id"] == source.project_id
            ):
                row["project_id"] = target.project_id
        storage._persist("project_agents")

    # Archive the source project
    await storage.archive_project(tenant_id, source.project_id)

    return {
        "status": "merged",
        "source_slug": source.slug,
        "target_slug": target.slug,
        "events_moved": moved,
    }


@app.get("/v1/projects/{project_id}/agents")
async def list_project_agents(
    project_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    now = datetime.now(timezone.utc)
    agents = await storage.list_agents(tenant_id, project_id=project_id)
    summaries = [await _agent_to_summary(a, now, storage) for a in agents]
    return {"data": [s.model_dump(mode="json") for s in summaries]}


@app.post("/v1/projects/{project_id}/agents")
async def add_agent_to_project(
    project_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    body = await request.json()
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "agent_id is required")
    await storage.upsert_project_agent(tenant_id, project_id, agent_id)
    return JSONResponse(content={"status": "added"}, status_code=201)


@app.delete("/v1/projects/{project_id}/agents/{agent_id}")
async def remove_agent_from_project(
    project_id: str,
    agent_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    async with storage._locks["project_agents"]:
        before = len(storage._tables["project_agents"])
        storage._tables["project_agents"] = [
            r for r in storage._tables["project_agents"]
            if not (
                r["tenant_id"] == tenant_id
                and r["project_id"] == project_id
                and r["agent_id"] == agent_id
            )
        ]
        if len(storage._tables["project_agents"]) < before:
            storage._persist("project_agents")
    return {"status": "removed"}


# ═══════════════════════════════════════════════════════════════════════════
#  ALERT ENDPOINTS (B2.3.22 – B2.3.26)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/alerts/rules")
async def list_alert_rules(
    request: Request,
    project_id: str | None = None,
    is_enabled: bool | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    rules = await storage.list_alert_rules(
        tenant_id, project_id=project_id, is_enabled=is_enabled,
    )
    return {"data": [r.model_dump(mode="json") for r in rules]}


@app.post("/v1/alerts/rules")
async def create_alert_rule(
    body: AlertRuleCreate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    rule = await storage.create_alert_rule(tenant_id, body)
    return JSONResponse(content=rule.model_dump(mode="json"), status_code=201)


@app.put("/v1/alerts/rules/{rule_id}")
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    rule = await storage.update_alert_rule(tenant_id, rule_id, body)
    if rule is None:
        raise HTTPException(404, {"error": "not_found", "message": "Alert rule not found", "status": 404})
    return rule.model_dump(mode="json")


@app.delete("/v1/alerts/rules/{rule_id}")
async def delete_alert_rule(
    rule_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.delete_alert_rule(tenant_id, rule_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "Alert rule not found", "status": 404})
    return {"status": "deleted"}


@app.get("/v1/alerts/history")
async def list_alert_history(
    request: Request,
    rule_id: str | None = None,
    project_id: str | None = None,
    since: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    page = await storage.list_alert_history(
        tenant_id, rule_id=rule_id, project_id=project_id,
        since=_parse_dt(since), limit=limit, cursor=cursor,
    )
    return page.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════════
#  WEBSOCKET ENDPOINT (B2.4)
# ═══════════════════════════════════════════════════════════════════════════

@app.websocket("/v1/stream")
async def websocket_stream(ws: WebSocket):
    """Real-time event and agent status streaming."""
    from backend.websocket import ws_manager

    # Auth via query param
    token = ws.query_params.get("token", "")
    if not token:
        await ws.close(code=4001, reason="Missing token parameter")
        return

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    storage = ws.app.state.storage
    info = await storage.authenticate(key_hash)
    if info is None:
        await ws.close(code=4001, reason="Invalid API key")
        return

    conn = await ws_manager.accept(ws, info.tenant_id, info.key_id)
    if conn is None:
        return  # Connection limit exceeded — already closed

    try:
        while True:
            data = await ws.receive_json()
            conn.missed_pongs = 0  # Any message resets pong counter
            await ws_manager.handle_message(conn, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(conn)
    except Exception:
        ws_manager.disconnect(conn)
