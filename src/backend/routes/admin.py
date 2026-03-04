"""Admin endpoints — rebuild aggregates, LLM pricing management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.llm_pricing import LlmPricingEngine

router = APIRouter(tags=["admin"])


@router.post("/v1/admin/rebuild-aggregates")
async def rebuild_aggregates_endpoint(request: Request):
    """Rebuild aggregate tables from raw events (admin)."""
    from backend.aggregator import rebuild_aggregates
    storage = request.app.state.storage
    result = await rebuild_aggregates(storage)
    return {"status": "rebuilt", "buckets": result}


@router.post("/v1/admin/rebuild-task-runs")
async def admin_rebuild_task_runs(request: Request):
    """Rebuild task_runs table from raw events (admin)."""
    from backend.aggregator import rebuild_task_runs
    storage = request.app.state.storage
    count = await rebuild_task_runs(storage)
    return {"status": "rebuilt", "task_runs": count}


@router.get("/v1/admin/pricing")
async def list_pricing(request: Request):
    pricing: LlmPricingEngine = request.app.state.pricing
    return {"data": await pricing.list_entries()}


@router.post("/v1/admin/pricing", status_code=201)
async def add_pricing(request: Request):
    body = await request.json()
    required = {"model_pattern", "provider", "input_per_m", "output_per_m"}
    if not required.issubset(body.keys()):
        raise HTTPException(400, f"Missing required fields: {required - body.keys()}")
    pricing: LlmPricingEngine = request.app.state.pricing
    entry = await pricing.add_entry(body)
    return entry


@router.put("/v1/admin/pricing/{pattern}")
async def update_pricing(pattern: str, request: Request):
    body = await request.json()
    pricing: LlmPricingEngine = request.app.state.pricing
    entry = await pricing.update_entry(pattern, body)
    if entry is None:
        raise HTTPException(404, f"Pricing pattern '{pattern}' not found")
    return entry


@router.delete("/v1/admin/pricing/{pattern}")
async def delete_pricing(pattern: str, request: Request):
    pricing: LlmPricingEngine = request.app.state.pricing
    deleted = await pricing.delete_entry(pattern)
    if not deleted:
        raise HTTPException(404, f"Pricing pattern '{pattern}' not found")
    return {"deleted": pattern}
