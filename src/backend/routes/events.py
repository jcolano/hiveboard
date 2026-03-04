"""Event, metrics, cost, and LLM call endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from backend.routes.helpers import _parse_dt

router = APIRouter(tags=["events"])


@router.get("/v1/events")
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


@router.get("/v1/metrics")
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


@router.get("/v1/cost")
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


@router.get("/v1/cost/calls")
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


@router.get("/v1/cost/timeseries")
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


@router.get("/v1/llm-calls")
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
