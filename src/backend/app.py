"""HiveBoard API Server — FastAPI application.

Run with: uvicorn backend.app:app --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
    PRUNE_INTERVAL_SECONDS,
    SEVERITY_DEFAULTS,
    SEVERITY_BY_PAYLOAD_KIND,
    VALID_SEVERITIES,
)
from shared.models import (
    AcceptInviteRequest,
    AgentRecord,
    AgentStats1h,
    AgentSummary,
    AlertRuleCreate,
    AlertRuleUpdate,
    ApiKeyCreateRequest,
    CostSummary,
    ErrorResponse,
    Event,
    IngestError,
    IngestRequest,
    IngestResponse,
    InviteRequest,
    LoginRequest,
    LoginResponse,
    Page,
    PaginationInfo,
    PasswordChangeRequest,
    ProjectCreate,
    ProjectMergeRequest,
    ProjectUpdate,
    RegisterRequest,
    SendCodeRequest,
    UserCreate,
    UserSafe,
    UserUpdate,
    VerifyCodeRequest,
)


# ═══════════════════════════════════════════════════════════════════════════
#  APP LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = logging.getLogger("hiveboard.retention")
    storage = JsonStorageBackend()
    await storage.initialize()

    # Prune stale events before serving requests
    result = await storage.prune_events()
    if result["total_pruned"] > 0:
        logger.info(
            "Startup pruning: %d events removed (ttl=%d, cold=%d), %d remaining",
            result["total_pruned"],
            result["ttl_pruned"],
            result["cold_pruned"],
            len(storage._tables["events"]),
        )

    app.state.storage = storage
    # Initialize LLM pricing engine
    pricing = LlmPricingEngine()
    await pricing.initialize()
    app.state.pricing = pricing
    # Bootstrap: create default tenant + key if none exist
    await _bootstrap_dev_tenant(storage)
    # Start background tasks
    from backend.websocket import ws_manager
    ping_task = asyncio.create_task(_ws_ping_loop())
    prune_task = asyncio.create_task(_prune_loop(storage))
    yield
    prune_task.cancel()
    ping_task.cancel()
    await storage.close()


async def _ws_ping_loop():
    """Send WebSocket pings every 30 seconds."""
    from backend.websocket import ws_manager
    while True:
        await asyncio.sleep(30)
        await ws_manager.ping_all()


async def _prune_loop(storage: JsonStorageBackend):
    """Periodically prune expired and cold events."""
    logger = logging.getLogger("hiveboard.retention")
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            result = await storage.prune_events()
            total = result["total_pruned"]
            if total > 0:
                logger.info(
                    "Event pruning: %d removed (ttl=%d, cold=%d), %d remaining",
                    total,
                    result["ttl_pruned"],
                    result["cold_pruned"],
                    len(storage._tables["events"]),
                )
        except Exception:
            logger.exception("Event pruning failed")


async def _bootstrap_dev_tenant(storage: JsonStorageBackend):
    """Create a dev tenant, API key, and owner user on first run.

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
    # Bootstrap dev owner user
    from backend.auth import hash_password
    dev_password = os.environ.get("HIVEBOARD_DEV_PASSWORD", "admin")
    try:
        await storage.create_user(
            user_id="dev-owner",
            tenant_id="dev",
            email="admin@hiveboard.dev",
            password_hash=hash_password(dev_password),
            name="Dev Admin",
            role="owner",
        )
    except ValueError:
        pass  # Already exists


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
            sdk_version=body.envelope.sdk_version,
            environment=body.envelope.environment,
            group=body.envelope.group,
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
        runtime=agent.runtime,
        sdk_version=agent.sdk_version,
        environment=agent.environment,
        group=agent.group,
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


# --- B2.3.3b: GET /v1/pipeline (fleet-level) ---

@app.get("/v1/pipeline")
async def get_fleet_pipeline(
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    fleet = await storage.get_fleet_pipeline(tenant_id)
    return fleet.model_dump(mode="json")


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
#  AUTH & USER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

def _user_to_safe(user) -> dict:
    """Convert UserRecord to safe API response (no password_hash)."""
    return UserSafe(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_login_at=user.last_login_at,
        settings=user.settings,
    ).model_dump(mode="json")


def _require_role(request: Request, allowed_roles: list[str]):
    """Check JWT user has required role. API keys bypass role checks."""
    auth_type = getattr(request.state, "auth_type", None)
    if auth_type == "api_key":
        return  # API keys bypass role checks (backward compatible)
    user_role = getattr(request.state, "user_role", None)
    if user_role not in allowed_roles:
        raise HTTPException(403, {
            "error": "insufficient_permissions",
            "message": f"Role '{user_role}' not in allowed roles: {allowed_roles}",
            "status": 403,
        })


@app.post("/v1/auth/login")
async def login(body: LoginRequest, request: Request, tenant_id: str = Query(...)):
    """Email+password login. Returns JWT token."""
    from backend.auth import verify_password, create_token
    storage = request.app.state.storage
    user = await storage.get_user_by_email(tenant_id, body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Invalid email or password",
            "status": 401,
        })
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    # Update last_login_at
    await storage.update_user(
        user.tenant_id, user.user_id,
        last_login_at=datetime.now(timezone.utc),
    )
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )
    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
    ).model_dump(mode="json")


@app.post("/v1/auth/register", status_code=201)
async def register(body: RegisterRequest, request: Request):
    """Register a new tenant + owner user + default project + API key."""
    from backend.auth import generate_api_key
    storage = request.app.state.storage
    logger = logging.getLogger("hiveboard.auth")

    # Check email not already registered
    existing = await storage.get_user_by_email_global(body.email)
    if existing:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered",
            "status": 409,
        })

    # Check for pending invite
    for row in storage._tables.get("invites", []):
        if (
            row["email"].lower() == body.email.lower()
            and not row.get("is_accepted", False)
        ):
            from backend.storage_json import _parse_dt as _sj_parse_dt, _now_utc
            exp = _sj_parse_dt(row["expires_at"])
            if exp and exp > _now_utc():
                raise HTTPException(409, {
                    "error": "pending_invite",
                    "message": "You have a pending invite. Use accept-invite instead.",
                    "status": 409,
                })

    # Generate slug from tenant_name
    slug = body.tenant_name.lower().replace(" ", "-")
    tenant_id = str(uuid4())
    user_id = str(uuid4())

    # Create tenant (auto-creates default project)
    tenant = await storage.create_tenant(tenant_id, body.tenant_name, slug)

    # Create owner user
    user = await storage.create_user(
        user_id=user_id,
        tenant_id=tenant_id,
        email=body.email,
        password_hash="",
        name=body.name,
        role="owner",
    )

    # Generate API key
    raw_key, key_hash, key_prefix = generate_api_key("live")
    key_id = str(uuid4())
    await storage.create_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        key_type="live",
        label="Default API Key",
        created_by_user_id=user_id,
    )

    logger.info("New registration: %s (tenant: %s)", body.email, slug)

    return JSONResponse(
        status_code=201,
        content={
            "user": _user_to_safe(user),
            "tenant": {"tenant_id": tenant_id, "name": body.tenant_name, "slug": slug},
            "api_key": raw_key,
        },
    )


@app.post("/v1/auth/send-code")
async def send_code(body: SendCodeRequest, request: Request):
    """Send a login code to an email address."""
    from backend.auth import generate_login_code
    from shared.enums import AUTH_CODE_EXPIRY_SECONDS, AUTH_CODE_RATE_LIMIT, AUTH_CODE_RATE_WINDOW
    storage = request.app.state.storage
    logger = logging.getLogger("hiveboard.auth")

    # Rate limit
    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=AUTH_CODE_RATE_WINDOW)
    recent = await storage.count_recent_codes(body.email, since)
    if recent >= AUTH_CODE_RATE_LIMIT:
        raise HTTPException(429, {
            "error": "rate_limit_exceeded",
            "message": "Too many login codes requested. Try again later.",
            "status": 429,
        })

    # Lookup user (but don't reveal if they exist)
    user = await storage.get_user_by_email_global(body.email)

    raw_code = None
    if user:
        raw_code_val, code_hash = generate_login_code()
        raw_code = raw_code_val
        code_id = str(uuid4())
        expires_at = now + timedelta(seconds=AUTH_CODE_EXPIRY_SECONDS)
        await storage.create_auth_code(code_id, body.email, code_hash, expires_at)
        logger.info("Login code for %s: %s", body.email, raw_code)

    response = {
        "message": "If registered, a code has been sent.",
        "expires_in": AUTH_CODE_EXPIRY_SECONDS,
    }
    # MVP: return code in response (no real email service)
    if raw_code:
        response["code"] = raw_code

    return response


@app.post("/v1/auth/verify-code")
async def verify_code(body: VerifyCodeRequest, request: Request):
    """Verify a login code and return a JWT."""
    from backend.auth import create_token
    from shared.enums import AUTH_CODE_MAX_ATTEMPTS
    storage = request.app.state.storage

    # Lookup user
    user = await storage.get_user_by_email_global(body.email)
    if user is None:
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Invalid email or code",
            "status": 401,
        })

    # Get active code
    code_rec = await storage.get_active_auth_code(body.email)
    if code_rec is None:
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "No active code. Request a new one.",
            "status": 401,
        })

    # Check attempts
    if code_rec.attempts >= AUTH_CODE_MAX_ATTEMPTS:
        raise HTTPException(401, {
            "error": "code_expired",
            "message": "Too many attempts. Request a new code.",
            "status": 401,
        })

    # Compare hashes
    provided_hash = hashlib.sha256(body.code.encode()).hexdigest()
    if provided_hash != code_rec.code_hash:
        await storage.increment_auth_code_attempts(code_rec.code_id)
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Invalid code",
            "status": 401,
        })

    # Success
    await storage.mark_auth_code_used(code_rec.code_id)
    await storage.update_user(
        user.tenant_id, user.user_id,
        last_login_at=datetime.now(timezone.utc),
    )
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )
    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
    ).model_dump(mode="json")


@app.post("/v1/auth/accept-invite")
async def accept_invite(body: AcceptInviteRequest, request: Request):
    """Accept an invite and join a tenant."""
    from backend.auth import create_token
    storage = request.app.state.storage

    # Hash token and lookup invite
    token_hash = hashlib.sha256(body.invite_token.encode()).hexdigest()
    invite = await storage.get_invite_by_token_hash(token_hash)
    if invite is None:
        raise HTTPException(404, {
            "error": "not_found",
            "message": "Invite not found or expired",
            "status": 404,
        })

    # Check email not already registered
    existing = await storage.get_user_by_email_global(invite.email)
    if existing:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered",
            "status": 409,
        })

    # Create user in invite's tenant
    user_id = str(uuid4())
    user = await storage.create_user(
        user_id=user_id,
        tenant_id=invite.tenant_id,
        email=invite.email,
        password_hash="",
        name=body.name,
        role=invite.role,
    )

    # Mark invite accepted
    await storage.mark_invite_accepted(invite.invite_id)

    # Create JWT
    token, expires_in = create_token(user.user_id, user.tenant_id, user.role)
    safe = UserSafe(
        user_id=user.user_id, tenant_id=user.tenant_id,
        email=user.email, name=user.name, role=user.role,
        is_active=user.is_active, created_at=user.created_at,
        updated_at=user.updated_at, last_login_at=user.last_login_at,
        settings=user.settings,
    )
    return LoginResponse(
        token=token, expires_in=expires_in, user=safe,
    ).model_dump(mode="json")


@app.post("/v1/auth/invite", status_code=201)
async def invite_user(body: InviteRequest, request: Request):
    """Owner/admin invites a user by email."""
    from backend.auth import generate_invite_token
    from shared.enums import INVITE_EXPIRY_SECONDS
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    logger = logging.getLogger("hiveboard.auth")

    # Role escalation check
    caller_role = getattr(request.state, "user_role", None)
    auth_type = getattr(request.state, "auth_type", None)
    if body.role in ("owner", "admin"):
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can invite as owner or admin",
                "status": 403,
            })

    # Check email not already in this tenant
    existing_in_tenant = await storage.get_user_by_email(tenant_id, body.email)
    if existing_in_tenant:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email already registered in this organization",
            "status": 409,
        })

    # Check email not registered elsewhere
    existing_global = await storage.get_user_by_email_global(body.email)
    if existing_global:
        raise HTTPException(409, {
            "error": "email_exists",
            "message": "Email registered with another organization",
            "status": 409,
        })

    # Check no pending invite
    pending = await storage.get_pending_invite(tenant_id, body.email)
    if pending:
        raise HTTPException(400, {
            "error": "invite_exists",
            "message": "A pending invite already exists for this email",
            "status": 400,
        })

    # Generate invite token
    raw_token, token_hash = generate_invite_token()
    invite_id = str(uuid4())
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=INVITE_EXPIRY_SECONDS)

    caller_user_id = getattr(request.state, "user_id", None) or "api_key"
    invite = await storage.create_invite(
        invite_id=invite_id,
        tenant_id=tenant_id,
        email=body.email,
        role=body.role,
        name=body.name,
        invite_token_hash=token_hash,
        created_by_user_id=caller_user_id,
        expires_at=expires_at,
    )

    logger.info("Invite created for %s (token: %s)", body.email, raw_token)

    return JSONResponse(
        status_code=201,
        content={
            "invite_id": invite_id,
            "email": body.email,
            "role": body.role,
            "tenant_id": tenant_id,
            "expires_at": invite.expires_at.isoformat() if hasattr(invite.expires_at, 'isoformat') else str(invite.expires_at),
            "invite_token": raw_token,
        },
    )


@app.post("/v1/auth/change-password")
async def change_password(body: PasswordChangeRequest, request: Request):
    """Change password for the currently authenticated JWT user."""
    from backend.auth import verify_password, hash_password
    auth_type = getattr(request.state, "auth_type", None)
    if auth_type != "jwt":
        raise HTTPException(403, {
            "error": "jwt_required",
            "message": "Password change requires JWT authentication",
            "status": 403,
        })
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_id = request.state.user_id
    user = await storage.get_user(tenant_id, user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(401, {
            "error": "authentication_failed",
            "message": "Current password is incorrect",
            "status": 401,
        })
    await storage.update_user(
        tenant_id, user_id,
        password_hash=hash_password(body.new_password),
    )
    return {"status": "password_changed"}


# ═══════════════════════════════════════════════════════════════════════════
#  API KEY CRUD ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/api-keys")
async def list_api_keys_endpoint(request: Request):
    """List API keys. Owner/admin see all; others see own keys only."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_role = getattr(request.state, "user_role", None)
    user_id = getattr(request.state, "user_id", None)
    auth_type = getattr(request.state, "auth_type", None)

    if auth_type == "api_key" or user_role in ("owner", "admin"):
        keys = await storage.list_api_keys(tenant_id)
    else:
        keys = await storage.list_api_keys_by_user(tenant_id, user_id) if user_id else []

    # Omit key_hash, show metadata
    result = []
    for k in keys:
        result.append({
            "key_id": k.key_id,
            "key_prefix": k.key_prefix,
            "key_type": k.key_type,
            "label": k.label,
            "created_by_user_id": k.created_by_user_id,
            "created_at": k.created_at.isoformat() if hasattr(k.created_at, 'isoformat') else str(k.created_at),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at and hasattr(k.last_used_at, 'isoformat') else str(k.last_used_at) if k.last_used_at else None,
            "is_active": k.is_active,
        })
    return {"data": result}


@app.post("/v1/api-keys", status_code=201)
async def create_api_key_endpoint(body: ApiKeyCreateRequest, request: Request):
    """Create a new API key."""
    from backend.auth import generate_api_key
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_id = getattr(request.state, "user_id", None)
    user_role = getattr(request.state, "user_role", None)

    # Validate key_type based on role
    if user_role == "viewer" and body.key_type != "read":
        raise HTTPException(403, {
            "error": "insufficient_permissions",
            "message": "Viewers can only create read keys",
            "status": 403,
        })

    raw_key, key_hash, key_prefix = generate_api_key(body.key_type)
    key_id = str(uuid4())
    rec = await storage.create_api_key(
        key_id=key_id,
        tenant_id=tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        key_type=body.key_type,
        label=body.label,
        created_by_user_id=user_id,
    )

    return JSONResponse(
        status_code=201,
        content={
            "key_id": key_id,
            "key_prefix": key_prefix,
            "key_type": body.key_type,
            "label": body.label,
            "raw_key": raw_key,
            "created_at": rec.created_at.isoformat() if hasattr(rec.created_at, 'isoformat') else str(rec.created_at),
        },
    )


@app.delete("/v1/api-keys/{key_id}")
async def revoke_api_key_endpoint(key_id: str, request: Request):
    """Revoke an API key."""
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user_role = getattr(request.state, "user_role", None)
    user_id = getattr(request.state, "user_id", None)
    auth_type = getattr(request.state, "auth_type", None)

    # Non-owner/admin can only revoke own keys
    if auth_type == "jwt" and user_role not in ("owner", "admin"):
        # Check if key belongs to user
        user_keys = await storage.list_api_keys_by_user(tenant_id, user_id) if user_id else []
        if not any(k.key_id == key_id for k in user_keys):
            raise HTTPException(403, {
                "error": "insufficient_permissions",
                "message": "Can only revoke your own keys",
                "status": 403,
            })

    ok = await storage.revoke_api_key(tenant_id, key_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "API key not found", "status": 404})
    return {"status": "revoked"}


# ═══════════════════════════════════════════════════════════════════════════
#  INVITE MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/v1/invites")
async def list_invites_endpoint(request: Request):
    """List pending invites for tenant (owner/admin only)."""
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    invites = await storage.list_invites(tenant_id)
    result = []
    for inv in invites:
        result.append({
            "invite_id": inv.invite_id,
            "email": inv.email,
            "role": inv.role,
            "name": inv.name,
            "is_accepted": inv.is_accepted,
            "created_at": inv.created_at.isoformat() if hasattr(inv.created_at, 'isoformat') else str(inv.created_at),
            "expires_at": inv.expires_at.isoformat() if hasattr(inv.expires_at, 'isoformat') else str(inv.expires_at),
            "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at and hasattr(inv.accepted_at, 'isoformat') else str(inv.accepted_at) if inv.accepted_at else None,
        })
    return {"data": result}


@app.delete("/v1/invites/{invite_id}")
async def cancel_invite(invite_id: str, request: Request):
    """Cancel a pending invite (owner/admin only)."""
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Find and remove the invite
    async with storage._locks["invites"]:
        before = len(storage._tables["invites"])
        storage._tables["invites"] = [
            r for r in storage._tables["invites"]
            if not (
                r["tenant_id"] == tenant_id
                and r["invite_id"] == invite_id
                and not r.get("is_accepted", False)
            )
        ]
        if len(storage._tables["invites"]) < before:
            storage._persist("invites")
            return {"status": "cancelled"}
    raise HTTPException(404, {"error": "not_found", "message": "Invite not found", "status": 404})


@app.get("/v1/users")
async def list_users(
    request: Request,
    role: str | None = None,
    is_active: bool | None = None,
):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    users = await storage.list_users(tenant_id, role=role, is_active=is_active)
    return {"data": [_user_to_safe(u) for u in users]}


@app.post("/v1/users", status_code=201)
async def create_user(body: UserCreate, request: Request):
    from backend.auth import hash_password
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Role escalation protection: only owner can create owner/admin
    if body.role in ("owner", "admin"):
        caller_role = getattr(request.state, "user_role", None)
        auth_type = getattr(request.state, "auth_type", None)
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can create owner or admin users",
                "status": 403,
            })

    user_id = str(uuid4())
    pw_hash = hash_password(body.password) if body.password else ""
    try:
        user = await storage.create_user(
            user_id=user_id,
            tenant_id=tenant_id,
            email=body.email,
            password_hash=pw_hash,
            name=body.name,
            role=body.role,
        )
    except ValueError as e:
        raise HTTPException(409, {
            "error": "duplicate_email",
            "message": str(e),
            "status": 409,
        })
    return JSONResponse(content=_user_to_safe(user), status_code=201)


@app.get("/v1/users/me")
async def get_current_user(request: Request):
    """Get current user profile (JWT only)."""
    auth_type = getattr(request.state, "auth_type", None)
    if auth_type != "jwt":
        raise HTTPException(403, {
            "error": "jwt_required",
            "message": "This endpoint requires JWT authentication",
            "status": 403,
        })
    storage = request.app.state.storage
    user = await storage.get_user(request.state.tenant_id, request.state.user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@app.get("/v1/users/{user_id}")
async def get_user_endpoint(user_id: str, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    user = await storage.get_user(tenant_id, user_id)
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@app.put("/v1/users/{user_id}")
async def update_user_endpoint(user_id: str, body: UserUpdate, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Role escalation protection
    if body.role in ("owner", "admin"):
        caller_role = getattr(request.state, "user_role", None)
        auth_type = getattr(request.state, "auth_type", None)
        if auth_type == "jwt" and caller_role != "owner":
            raise HTTPException(403, {
                "error": "role_escalation",
                "message": "Only owners can assign owner or admin roles",
                "status": 403,
            })

    kwargs = {}
    if body.email is not None:
        kwargs["email"] = body.email
    if body.name is not None:
        kwargs["name"] = body.name
    if body.role is not None:
        kwargs["role"] = body.role
    if body.settings is not None:
        kwargs["settings"] = body.settings

    try:
        user = await storage.update_user(tenant_id, user_id, **kwargs)
    except ValueError as e:
        raise HTTPException(409, {
            "error": "duplicate_email",
            "message": str(e),
            "status": 409,
        })
    if user is None:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return _user_to_safe(user)


@app.delete("/v1/users/{user_id}")
async def deactivate_user_endpoint(user_id: str, request: Request):
    """Soft-delete a user (deactivate). Can't self-deactivate."""
    _require_role(request, ["owner", "admin"])
    # Block self-deactivation
    caller_user_id = getattr(request.state, "user_id", None)
    if caller_user_id == user_id:
        raise HTTPException(400, {
            "error": "self_deactivation",
            "message": "Cannot deactivate your own account",
            "status": 400,
        })
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.deactivate_user(tenant_id, user_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "User not found", "status": 404})
    return {"status": "deactivated"}


@app.post("/v1/users/{user_id}/reactivate")
async def reactivate_user_endpoint(user_id: str, request: Request):
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    ok = await storage.reactivate_user(tenant_id, user_id)
    if not ok:
        raise HTTPException(404, {"error": "not_found", "message": "User not found or already active", "status": 404})
    return {"status": "reactivated"}


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
