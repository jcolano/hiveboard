"""Project endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.routes.helpers import _agent_to_summary
from shared.models import ProjectCreate, ProjectMergeRequest, ProjectUpdate

router = APIRouter(tags=["projects"])


@router.get("/v1/projects")
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


@router.post("/v1/projects")
async def create_project(
    body: ProjectCreate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    try:
        project = await storage.create_project(tenant_id, body)
    except ValueError:
        raise HTTPException(409, {
            "error": "slug_exists",
            "message": f"A project with slug '{body.slug}' already exists",
            "status": 409,
        })
    return JSONResponse(
        content=project.model_dump(mode="json"), status_code=201,
    )


@router.get("/v1/projects/{project_id}")
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


@router.put("/v1/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    try:
        project = await storage.update_project(tenant_id, project_id, body)
    except ValueError:
        raise HTTPException(409, {
            "error": "slug_exists",
            "message": f"A project with slug '{body.slug}' already exists",
            "status": 409,
        })
    if project is None:
        raise HTTPException(404, {"error": "not_found", "message": "Project not found", "status": 404})
    return project.model_dump(mode="json")


@router.delete("/v1/projects/{project_id}")
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


@router.post("/v1/projects/{project_id}/archive")
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


@router.post("/v1/projects/{project_id}/unarchive")
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


@router.post("/v1/projects/{project_id}/merge")
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


@router.get("/v1/projects/{project_id}/agents")
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


@router.post("/v1/projects/{project_id}/agents")
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


@router.delete("/v1/projects/{project_id}/agents/{agent_id}")
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
