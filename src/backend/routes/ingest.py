"""Ingest endpoint — the critical write path."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.llm_pricing import LlmPricingEngine
from backend.storage_json import JsonStorageBackend, derive_agent_status
from backend.routes.helpers import _get_broadcaster, _parse_dt
from shared.enums import (
    AgentStatus,
    EventType,
    MAX_AGENT_ID_CHARS,
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
    Event,
    IngestError,
    IngestRequest,
    IngestResponse,
    ProjectCreate,
    ProjectUpdate,
)

router = APIRouter(tags=["ingest"])
logger = logging.getLogger(__name__)

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


@router.post("/v1/ingest")
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
                        "warning": "Project limit (50) reached; routed to default project",
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

    # Step 6: Agent cache update — MUST run before insert_events
    # so internal_id exists for event partitioning
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

    # Step 7: Batch insert events
    ingestion_key_type = getattr(request.state, "key_type", "live")
    inserted = 0
    if accepted_events:
        inserted = await storage.insert_events(accepted_events, key_type=ingestion_key_type)

    # Step 7b: Update running aggregates
    if accepted_events:
        from backend.aggregator import (
            update_agent_hourly, update_model_hourly,
            get_or_create_bucket, _hour_key,
        )
        for ev in accepted_events:
            hour = _hour_key(ev.timestamp)
            ab = get_or_create_bucket(
                storage._tables["agent_hourly"],
                tenant_id, "agent_id", ev.agent_id, hour,
            )
            update_agent_hourly(ab, ev)

            ev_payload = ev.payload or {}
            if isinstance(ev_payload, dict) and ev_payload.get("kind") == "llm_call":
                ev_model = (ev_payload.get("data") or {}).get("model", "unknown")
                mb = get_or_create_bucket(
                    storage._tables["model_hourly"],
                    tenant_id, "model", ev_model, hour,
                )
                update_model_hourly(mb, ev)

        storage._persist("agent_hourly")
        storage._persist("model_hourly")

    # Step 7c: Update task_runs
    if accepted_events:
        from backend.aggregator import get_or_create_task_run, update_task_run
        modified_task_runs = False
        for ev in accepted_events:
            if ev.task_id and ev.task_run_id:
                run_bucket = get_or_create_task_run(
                    storage._tables["task_runs"], tenant_id, ev.task_id, ev.task_run_id,
                )
                update_task_run(run_bucket, ev)
                modified_task_runs = True
        if modified_task_runs:
            storage._persist("task_runs")

    # Step 8: Project-agent junction
    for pid in project_ids_seen:
        await storage.upsert_project_agent(
            tenant_id, pid, body.envelope.agent_id
        )

    # Step 9: WebSocket broadcast (uses bridge in production, ws_manager locally)
    broadcaster = _get_broadcaster(request.app)
    if accepted_events:
        event_dicts = [e.model_dump(mode="json") for e in accepted_events]
        await broadcaster.broadcast_events(tenant_id, event_dicts)

        # F11: Check for agent status change and broadcast
        if agent_record:
            new_status = derive_agent_status(agent_record)
            previous_status = agent_record.previous_status

            if previous_status and previous_status != new_status.value:
                hb_age = None
                if agent_record.last_heartbeat:
                    hb_age = int((datetime.now(timezone.utc) - agent_record.last_heartbeat).total_seconds())
                await broadcaster.broadcast_agent_status_change(
                    tenant_id, agent_record.agent_id,
                    previous_status, new_status.value,
                    agent_record.last_task_id, agent_record.last_project_id,
                    hb_age,
                )

            if new_status == AgentStatus.STUCK:
                await broadcaster.broadcast_agent_stuck(
                    tenant_id, agent_record.agent_id,
                    agent_record.last_heartbeat.isoformat() if agent_record.last_heartbeat else None,
                    agent_record.stuck_threshold_seconds,
                    agent_record.last_task_id, agent_record.last_project_id,
                )
            else:
                broadcaster.clear_stuck(tenant_id, agent_record.agent_id)

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
