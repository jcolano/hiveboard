"""Alert rule and alert history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.routes.helpers import _parse_dt
from shared.models import AlertRuleCreate, AlertRuleUpdate

router = APIRouter(tags=["alerts"])


@router.get("/v1/alerts/rules")
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


@router.post("/v1/alerts/rules")
async def create_alert_rule(
    body: AlertRuleCreate,
    request: Request,
):
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id
    rule = await storage.create_alert_rule(tenant_id, body)
    return JSONResponse(content=rule.model_dump(mode="json"), status_code=201)


@router.put("/v1/alerts/rules/{rule_id}")
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


@router.delete("/v1/alerts/rules/{rule_id}")
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


@router.get("/v1/alerts/history")
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
