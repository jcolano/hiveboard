"""Agent and pipeline endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.routes.helpers import _agent_to_summary, _now_utc, _require_role
from shared.models import Event

router = APIRouter(tags=["agents"])


@router.get("/v1/agents")
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


@router.get("/v1/agents/{agent_id}")
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


@router.delete("/v1/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent and its project-agent associations."""
    _require_role(request, ["owner", "admin"])
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    deleted = await storage.delete_agent(tenant_id, agent_id)
    if not deleted:
        raise HTTPException(404, {
            "error": "not_found",
            "message": f"Agent '{agent_id}' not found",
            "status": 404,
        })
    return JSONResponse(content=None, status_code=204)


@router.get("/v1/agents/{agent_id}/pipeline")
async def get_agent_pipeline(
    agent_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    pipeline = await storage.get_pipeline(tenant_id, agent_id)
    return pipeline.model_dump(mode="json")


@router.post("/v1/agents/{agent_id}/issues/{issue_id}/resolve")
async def resolve_issue(
    agent_id: str,
    issue_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    now = _now_utc()
    evt = Event(
        event_id=f"sys-resolve-{uuid4().hex[:12]}",
        tenant_id=tenant_id,
        agent_id=agent_id,
        timestamp=now.isoformat(),
        received_at=now.isoformat(),
        event_type="custom",
        payload={
            "kind": "issue",
            "summary": issue_id,
            "data": {
                "issue_id": issue_id,
                "action": "resolved",
            },
        },
    )
    await storage.insert_events([evt])
    return {"resolved": issue_id}


@router.post("/v1/agents/{agent_id}/issues/resolve-all")
async def resolve_all_issues(
    agent_id: str,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    pipeline = await storage.get_pipeline(tenant_id, agent_id)
    now = _now_utc()
    events = []
    for iss in pipeline.issues:
        iid = iss.get("issue_id") or iss.get("summary") or ""
        events.append(Event(
            event_id=f"sys-resolve-{uuid4().hex[:12]}",
            tenant_id=tenant_id,
            agent_id=agent_id,
            timestamp=now.isoformat(),
            received_at=now.isoformat(),
            event_type="custom",
            payload={
                "kind": "issue",
                "summary": iid,
                "data": {
                    "issue_id": iid,
                    "action": "resolved",
                },
            },
        ))
    resolved = 0
    if events:
        resolved = await storage.insert_events(events)
    return {"resolved": resolved}


@router.get("/v1/pipeline")
async def get_fleet_pipeline(
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    fleet = await storage.get_fleet_pipeline(tenant_id)
    return fleet.model_dump(mode="json")
