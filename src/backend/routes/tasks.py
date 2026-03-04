"""Task endpoints — list tasks, task timeline."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from backend.routes.helpers import _parse_dt
from shared.models import TimelineSummary

router = APIRouter(tags=["tasks"])


@router.get("/v1/tasks")
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


@router.get("/v1/tasks/{task_id}/timeline")
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
    step_status: dict[int, dict] = {}
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
