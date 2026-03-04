"""Insights endpoints (section 6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request

from backend.routes.helpers import (
    _build_comparison,
    _filter_hourly_buckets,
    _merge_dict_counters,
)

router = APIRouter(tags=["insights"])


# --- 6.1: GET /v1/insights/agents ---

@router.get("/v1/insights/agents")
async def insights_agents(
    request: Request,
    range: str = "24h",
    project_id: str | None = None,
    sort: str = "cost",
):
    from shared.models import (
        InsightsAgentDetail, InsightsAgentsResponse,
        InsightsFleetTotals,
    )
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    buckets = _filter_hourly_buckets(
        storage._tables["agent_hourly"], tenant_id, range,
    )

    # If project_id filter, get agent_ids assigned to that project
    allowed_agents: set[str] | None = None
    if project_id:
        allowed_agents = {
            row["agent_id"]
            for row in storage._tables["project_agents"]
            if row["tenant_id"] == tenant_id and row["project_id"] == project_id
        }

    # Group by agent_id and sum counters
    agent_agg: dict[str, dict] = {}
    for b in buckets:
        aid = b.get("agent_id", "")
        if allowed_agents is not None and aid not in allowed_agents:
            continue
        if aid not in agent_agg:
            agent_agg[aid] = {"agent_id": aid}
        agg = agent_agg[aid]
        for field in (
            "tasks_completed", "tasks_failed", "tasks_started",
            "actions_started", "actions_completed", "actions_failed",
            "llm_call_count", "llm_tokens_in", "llm_tokens_out",
            "task_duration_sum_ms", "task_duration_count",
            "retries", "escalations", "issues_reported", "issues_resolved",
            "event_count",
        ):
            agg[field] = agg.get(field, 0) + b.get(field, 0)
        agg["llm_cost"] = agg.get("llm_cost", 0) + b.get("llm_cost", 0)
        # Merge nested dicts
        for dict_key in ("errors_by_type", "errors_by_category", "models",
                         "actions_by_name", "calls_by_name", "tasks_by_type"):
            src = b.get(dict_key, {})
            if src:
                tgt = agg.setdefault(dict_key, {})
                _merge_dict_counters(tgt, src)

    # Build response
    agents_list = []
    for aid, agg in agent_agg.items():
        tc = agg.get("tasks_completed", 0)
        tf = agg.get("tasks_failed", 0)
        total_tasks = tc + tf
        sr = round(tc / total_tasks * 100, 1) if total_tasks > 0 else None
        dur_count = agg.get("task_duration_count", 0)
        dur_sum = agg.get("task_duration_sum_ms", 0)
        avg_dur = int(dur_sum / dur_count) if dur_count > 0 else None

        # top_models: sorted by cost desc, top 5
        models_dict = agg.get("models", {})
        top_models = sorted(
            [{"model": m, **v} for m, v in models_dict.items()],
            key=lambda x: x.get("cost", 0), reverse=True,
        )[:5]

        # top_actions: sorted by total (started) desc, top 5
        actions_dict = agg.get("actions_by_name", {})
        top_actions = sorted(
            [{"name": n, **v} for n, v in actions_dict.items()],
            key=lambda x: x.get("started", 0), reverse=True,
        )[:5]

        # top_llm_calls: sorted by cost desc, top 5
        calls_dict = agg.get("calls_by_name", {})
        top_calls = sorted(
            [{"name": n, **v} for n, v in calls_dict.items()],
            key=lambda x: x.get("cost_sum", 0), reverse=True,
        )[:5]

        # Compute error count from errors_by_type
        ebt = agg.get("errors_by_type", {})
        error_count = sum(ebt.values()) if ebt else (tf + agg.get("actions_failed", 0))

        agents_list.append(InsightsAgentDetail(
            agent_id=aid,
            tasks_completed=tc,
            tasks_failed=tf,
            success_rate=sr,
            avg_task_duration_ms=avg_dur,
            llm_call_count=agg.get("llm_call_count", 0),
            llm_cost=round(agg.get("llm_cost", 0), 6),
            llm_tokens_in=agg.get("llm_tokens_in", 0),
            llm_tokens_out=agg.get("llm_tokens_out", 0),
            error_count=error_count,
            errors_by_type=agg.get("errors_by_type", {}),
            errors_by_category=agg.get("errors_by_category", {}),
            top_models=top_models,
            top_actions=top_actions,
            top_llm_calls=top_calls,
            tasks_by_type=agg.get("tasks_by_type", {}),
        ))

    # Sort
    sort_keys = {
        "cost": lambda a: a.llm_cost,
        "tasks": lambda a: a.tasks_completed + a.tasks_failed,
        "errors": lambda a: a.error_count,
        "llm_calls": lambda a: a.llm_call_count,
    }
    agents_list.sort(key=sort_keys.get(sort, sort_keys["cost"]), reverse=True)

    # Fleet totals
    fleet = InsightsFleetTotals(
        total_cost=round(sum(a.llm_cost for a in agents_list), 6),
        total_tasks=sum(a.tasks_completed + a.tasks_failed for a in agents_list),
        total_errors=sum(a.error_count for a in agents_list),
        total_llm_calls=sum(a.llm_call_count for a in agents_list),
    )

    # Comparisons
    agents_dicts = [a.model_dump(mode="json") for a in agents_list]
    comparisons = {}
    if agents_dicts:
        comparisons["cost"] = _build_comparison(
            [{"agent_id": a["agent_id"], "cost": a["llm_cost"]} for a in agents_dicts], "cost")
        comparisons["tasks"] = _build_comparison(
            [{"agent_id": a["agent_id"], "tasks": a["tasks_completed"] + a["tasks_failed"]} for a in agents_dicts], "tasks")
        comparisons["errors"] = _build_comparison(
            [{"agent_id": a["agent_id"], "errors": a["error_count"]} for a in agents_dicts], "errors")

    return InsightsAgentsResponse(
        range=range,
        agents=agents_list,
        fleet_totals=fleet,
        comparisons=comparisons,
    ).model_dump(mode="json")


# --- 6.2: GET /v1/insights/models ---

@router.get("/v1/insights/models")
async def insights_models(
    request: Request,
    range: str = "24h",
    agent_id: str | None = None,
):
    from shared.models import InsightsModelDetail, InsightsModelsResponse, InsightsFleetTotals
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    buckets = _filter_hourly_buckets(
        storage._tables["model_hourly"], tenant_id, range,
    )

    # If agent_id filter, only include buckets with that agent
    model_agg: dict[str, dict] = {}
    for b in buckets:
        mdl = b.get("model", "unknown")
        agents_in_bucket = b.get("agents", {})

        # If filtering by agent, only count that agent's contribution
        if agent_id:
            if agent_id not in agents_in_bucket:
                continue
            agent_data = agents_in_bucket[agent_id]
            if mdl not in model_agg:
                model_agg[mdl] = {"model": mdl}
            agg = model_agg[mdl]
            agg["call_count"] = agg.get("call_count", 0) + agent_data.get("calls", 0)
            agg["tokens_in"] = agg.get("tokens_in", 0) + agent_data.get("tokens_in", 0)
            agg["tokens_out"] = agg.get("tokens_out", 0) + agent_data.get("tokens_out", 0)
            agg["cost"] = agg.get("cost", 0) + agent_data.get("cost", 0)
            # No duration data per-agent, use overall proportionally
            agg["duration_sum_ms"] = agg.get("duration_sum_ms", 0) + b.get("duration_sum_ms", 0)
            agg["duration_count"] = agg.get("duration_count", 0) + b.get("duration_count", 0)
            agents_dict = agg.setdefault("agents", {})
            a = agents_dict.setdefault(agent_id, {})
            for k in ("calls", "cost", "tokens_in", "tokens_out"):
                a[k] = a.get(k, 0) + agent_data.get(k, 0)
        else:
            if mdl not in model_agg:
                model_agg[mdl] = {"model": mdl}
            agg = model_agg[mdl]
            for field in ("call_count", "tokens_in", "tokens_out", "duration_sum_ms", "duration_count"):
                agg[field] = agg.get(field, 0) + b.get(field, 0)
            agg["cost"] = agg.get("cost", 0) + b.get("cost", 0)

            # Track max tokens_in
            if b.get("max_tokens_in", 0) > agg.get("max_tokens_in", 0):
                agg["max_tokens_in"] = b["max_tokens_in"]
                agg["max_tokens_in_agent"] = b.get("max_tokens_in_agent", "")
                agg["max_tokens_in_name"] = b.get("max_tokens_in_name", "")

            # Merge agents and calls_by_name
            for dict_key in ("agents", "calls_by_name"):
                src = b.get(dict_key, {})
                if src:
                    tgt = agg.setdefault(dict_key, {})
                    _merge_dict_counters(tgt, src)

    models_list = []
    for mdl, agg in model_agg.items():
        dur_count = agg.get("duration_count", 0)
        dur_sum = agg.get("duration_sum_ms", 0)
        avg_dur = int(dur_sum / dur_count) if dur_count > 0 else None

        agents_dict = agg.get("agents", {})
        agents_using = sorted(
            [{"agent_id": a, **v} for a, v in agents_dict.items()],
            key=lambda x: x.get("cost", 0), reverse=True,
        )[:10]

        calls_dict = agg.get("calls_by_name", {})
        top_calls = sorted(
            [{"name": n, **v} for n, v in calls_dict.items()],
            key=lambda x: x.get("cost_sum", 0), reverse=True,
        )[:10]

        models_list.append(InsightsModelDetail(
            model=mdl,
            call_count=agg.get("call_count", 0),
            tokens_in=agg.get("tokens_in", 0),
            tokens_out=agg.get("tokens_out", 0),
            cost=round(agg.get("cost", 0), 6),
            avg_duration_ms=avg_dur,
            max_tokens_in=agg.get("max_tokens_in", 0),
            max_tokens_in_agent=agg.get("max_tokens_in_agent", ""),
            max_tokens_in_name=agg.get("max_tokens_in_name", ""),
            agents_using=agents_using,
            top_calls=top_calls,
        ))

    models_list.sort(key=lambda m: m.cost, reverse=True)

    fleet = InsightsFleetTotals(
        total_cost=round(sum(m.cost for m in models_list), 6),
        total_llm_calls=sum(m.call_count for m in models_list),
    )

    return InsightsModelsResponse(
        range=range, models=models_list, fleet_totals=fleet,
    ).model_dump(mode="json")


# --- 6.3: GET /v1/insights/timeseries ---

@router.get("/v1/insights/timeseries")
async def insights_timeseries(
    request: Request,
    range: str = "24h",
    agent_id: str | None = None,
    metric: str = "cost",
):
    from shared.models import (
        InsightsTimeseriesBucket, InsightsTimeseriesResponse,
        InsightsTimeseriesSummary,
    )
    from shared.enums import RANGE_SECONDS
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    buckets = _filter_hourly_buckets(
        storage._tables["agent_hourly"], tenant_id, range,
        key_field="agent_id" if agent_id else None,
        key_value=agent_id,
    )

    # Map metric to bucket field
    metric_map = {
        "cost": "llm_cost",
        "tasks": lambda b: b.get("tasks_completed", 0) + b.get("tasks_failed", 0),
        "errors": lambda b: sum(b.get("errors_by_type", {}).values()) if b.get("errors_by_type") else (b.get("tasks_failed", 0) + b.get("actions_failed", 0)),
        "llm_calls": "llm_call_count",
        "tokens": lambda b: b.get("llm_tokens_in", 0) + b.get("llm_tokens_out", 0),
    }

    # Group by hour, sum values
    hourly: dict[str, float] = {}
    for b in buckets:
        hour = b.get("hour", "")
        extractor = metric_map.get(metric, "llm_cost")
        if callable(extractor):
            val = extractor(b)
        else:
            val = b.get(extractor, 0)
        hourly[hour] = hourly.get(hour, 0) + val

    # Fill zero-value gaps
    range_secs = RANGE_SECONDS.get(range, 86400)
    now = datetime.now(timezone.utc)
    start = now - timedelta(seconds=range_secs)
    start_hour = start.replace(minute=0, second=0, microsecond=0)
    end_hour = now.replace(minute=0, second=0, microsecond=0)

    all_hours = []
    current = start_hour
    while current <= end_hour:
        h = current.strftime("%Y-%m-%dT%H:%M:%SZ")
        all_hours.append(h)
        current += timedelta(hours=1)

    ts_buckets = []
    for h in all_hours:
        ts_buckets.append(InsightsTimeseriesBucket(
            hour=h, value=round(hourly.get(h, 0), 6),
        ))

    # Summary
    values = [b.value for b in ts_buckets]
    total = sum(values)
    avg = total / len(values) if values else 0
    peak_val = max(values) if values else 0
    trough_val = min(values) if values else 0
    peak_idx = values.index(peak_val) if values else 0
    trough_idx = values.index(trough_val) if values else 0

    summary = InsightsTimeseriesSummary(
        total=round(total, 6),
        avg_per_hour=round(avg, 6),
        peak_hour=ts_buckets[peak_idx].hour if ts_buckets else "",
        peak_value=round(peak_val, 6),
        trough_hour=ts_buckets[trough_idx].hour if ts_buckets else "",
        trough_value=round(trough_val, 6),
    )

    return InsightsTimeseriesResponse(
        range=range, agent_id=agent_id, metric=metric,
        buckets=ts_buckets, summary=summary,
    ).model_dump(mode="json")


# --- 6.4: GET /v1/insights/errors ---

@router.get("/v1/insights/errors")
async def insights_errors(
    request: Request,
    range: str = "24h",
    agent_id: str | None = None,
):
    from shared.models import (
        InsightsErrorAgent, InsightsErrorsResponse,
        InsightsTimeseriesBucket,
    )
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    buckets = _filter_hourly_buckets(
        storage._tables["agent_hourly"], tenant_id, range,
        key_field="agent_id" if agent_id else None,
        key_value=agent_id,
    )

    # Aggregate by agent
    agent_errors: dict[str, dict] = {}
    by_type_global: dict[str, int] = {}
    by_category_global: dict[str, int] = {}
    by_task_type_global: dict[str, int] = {}
    by_action_global: dict[str, int] = {}
    hourly_errors: dict[str, int] = {}

    for b in buckets:
        aid = b.get("agent_id", "")
        hour = b.get("hour", "")

        ebt = b.get("errors_by_type", {})
        ebc = b.get("errors_by_category", {})
        ebtt = b.get("errors_by_task_type", {})
        eba = b.get("errors_by_action", {})
        tf = b.get("tasks_failed", 0)
        af = b.get("actions_failed", 0)
        error_count = sum(ebt.values()) if ebt else (tf + af)

        if error_count > 0 or tf > 0 or af > 0:
            if aid not in agent_errors:
                agent_errors[aid] = {
                    "agent_id": aid,
                    "error_count": 0,
                    "task_failure_count": 0,
                    "action_failure_count": 0,
                    "by_type": {},
                    "by_category": {},
                    "by_task_type": {},
                    "by_action": {},
                }
            ae = agent_errors[aid]
            ae["error_count"] += error_count
            ae["task_failure_count"] += tf
            ae["action_failure_count"] += af
            _merge_dict_counters(ae["by_type"], ebt)
            _merge_dict_counters(ae["by_category"], ebc)
            _merge_dict_counters(ae["by_task_type"], ebtt)
            _merge_dict_counters(ae["by_action"], eba)

        _merge_dict_counters(by_type_global, ebt)
        _merge_dict_counters(by_category_global, ebc)
        _merge_dict_counters(by_task_type_global, ebtt)
        _merge_dict_counters(by_action_global, eba)
        hourly_errors[hour] = hourly_errors.get(hour, 0) + error_count

    total_errors = sum(ae["error_count"] for ae in agent_errors.values())

    by_agent = sorted(
        [InsightsErrorAgent(**ae) for ae in agent_errors.values()],
        key=lambda a: a.error_count, reverse=True,
    )

    # Error timeseries
    from shared.enums import RANGE_SECONDS
    range_secs = RANGE_SECONDS.get(range, 86400)
    now = datetime.now(timezone.utc)
    start_hour = (now - timedelta(seconds=range_secs)).replace(minute=0, second=0, microsecond=0)
    end_hour = now.replace(minute=0, second=0, microsecond=0)
    error_ts = []
    current = start_hour
    while current <= end_hour:
        h = current.strftime("%Y-%m-%dT%H:%M:%SZ")
        error_ts.append(InsightsTimeseriesBucket(hour=h, value=hourly_errors.get(h, 0)))
        current += timedelta(hours=1)

    return InsightsErrorsResponse(
        range=range,
        total_errors=total_errors,
        by_agent=by_agent,
        by_type_global=by_type_global,
        by_category_global=by_category_global,
        by_task_type_global=by_task_type_global,
        by_action_global=by_action_global,
        error_timeseries=error_ts,
    ).model_dump(mode="json")


# --- 6.5: GET /v1/insights/prompts ---

@router.get("/v1/insights/prompts")
async def insights_prompts(
    request: Request,
    range: str = "24h",
    agent_id: str | None = None,
    sort: str = "cost",
):
    from shared.models import InsightsPromptDetail, InsightsPromptsResponse
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    # Use agent_hourly for per-agent call breakdown
    agent_buckets = _filter_hourly_buckets(
        storage._tables["agent_hourly"], tenant_id, range,
        key_field="agent_id" if agent_id else None,
        key_value=agent_id,
    )

    # Also use model_hourly for cross-referencing primary model
    model_buckets = _filter_hourly_buckets(
        storage._tables["model_hourly"], tenant_id, range,
    )

    # Merge calls_by_name across agent buckets, track which agents use each call
    call_agg: dict[str, dict] = {}
    call_agents: dict[str, set] = {}

    for b in agent_buckets:
        aid = b.get("agent_id", "")
        cbn = b.get("calls_by_name", {})
        for name, stats in cbn.items():
            if name not in call_agg:
                call_agg[name] = {}
            agg = call_agg[name]
            agg["count"] = agg.get("count", 0) + stats.get("count", 0)
            agg["tokens_in_sum"] = agg.get("tokens_in_sum", 0) + stats.get("tokens_in_sum", 0)
            agg["tokens_out_sum"] = agg.get("tokens_out_sum", 0) + stats.get("tokens_out_sum", 0)
            agg["cost_sum"] = agg.get("cost_sum", 0) + stats.get("cost_sum", 0)
            call_agents.setdefault(name, set()).add(aid)

        # Track max tokens_in per call name from bucket-level data
        llm_max = b.get("llm_max_tokens_in", 0)
        llm_max_name = b.get("llm_max_tokens_in_name", "")
        if llm_max_name and llm_max_name in call_agg:
            if llm_max > call_agg[llm_max_name].get("max_tokens_in", 0):
                call_agg[llm_max_name]["max_tokens_in"] = llm_max

    # Determine primary model per call name from model_hourly
    call_model_costs: dict[str, dict[str, float]] = {}
    for b in model_buckets:
        mdl = b.get("model", "unknown")
        cbn = b.get("calls_by_name", {})
        for name, stats in cbn.items():
            call_model_costs.setdefault(name, {})
            call_model_costs[name][mdl] = call_model_costs[name].get(mdl, 0) + stats.get("cost_sum", 0)

    calls_list = []
    biggest_prompt: dict = {}
    max_tokens = 0

    for name, agg in call_agg.items():
        count = agg.get("count", 0)
        ti_sum = agg.get("tokens_in_sum", 0)
        avg_ti = int(ti_sum / count) if count > 0 else 0
        max_ti = agg.get("max_tokens_in", 0)

        # Primary model: the one with highest cost for this call name
        primary_model = ""
        if name in call_model_costs:
            models = call_model_costs[name]
            if models:
                primary_model = max(models, key=models.get)

        detail = InsightsPromptDetail(
            name=name,
            total_count=count,
            avg_tokens_in=avg_ti,
            max_tokens_in=max_ti,
            total_tokens_in=ti_sum,
            total_tokens_out=agg.get("tokens_out_sum", 0),
            total_cost=round(agg.get("cost_sum", 0), 6),
            agents_using=sorted(call_agents.get(name, set())),
            primary_model=primary_model,
        )
        calls_list.append(detail)

        if max_ti > max_tokens:
            max_tokens = max_ti
            biggest_prompt = {
                "name": name, "max_tokens_in": max_ti,
                "primary_model": primary_model,
            }

    sort_keys = {
        "cost": lambda c: c.total_cost,
        "tokens": lambda c: c.total_tokens_in,
        "calls": lambda c: c.total_count,
    }
    calls_list.sort(key=sort_keys.get(sort, sort_keys["cost"]), reverse=True)

    return InsightsPromptsResponse(
        range=range, calls=calls_list, biggest_prompt=biggest_prompt,
    ).model_dump(mode="json")


# --- 6.6: GET /v1/insights/actions ---

@router.get("/v1/insights/actions")
async def insights_actions(
    request: Request,
    range: str = "24h",
    agent_id: str | None = None,
    group_by: str = "name",
):
    from shared.models import InsightsActionDetail, InsightsActionsResponse
    from shared.enums import RANGE_SECONDS
    storage = request.app.state.storage
    tenant_id = request.state.tenant_id

    buckets = _filter_hourly_buckets(
        storage._tables["agent_hourly"], tenant_id, range,
        key_field="agent_id" if agent_id else None,
        key_value=agent_id,
    )

    # Aggregate actions_by_name across buckets
    action_agg: dict[str, dict] = {}
    action_agents: dict[str, dict[str, int]] = {}
    action_hourly: dict[str, dict[str, dict[str, int]]] = {}  # action_name -> {hour: {started, completed, failed}}

    for b in buckets:
        aid = b.get("agent_id", "")
        hour = b.get("hour", "")
        abn = b.get("actions_by_name", {})
        for name, stats in abn.items():
            if name not in action_agg:
                action_agg[name] = {}
            agg = action_agg[name]
            for field in ("started", "completed", "failed", "duration_sum_ms", "duration_count"):
                agg[field] = agg.get(field, 0) + stats.get(field, 0)

            # Track agents using
            action_agents.setdefault(name, {})
            action_agents[name][aid] = action_agents[name].get(aid, 0) + stats.get("started", 0)

            # Track hourly counts for peak detection and heatmap
            action_hourly.setdefault(name, {})
            h_entry = action_hourly[name].setdefault(hour, {"started": 0, "completed": 0, "failed": 0})
            h_entry["started"] += stats.get("started", 0)
            h_entry["completed"] += stats.get("completed", 0)
            h_entry["failed"] += stats.get("failed", 0)

    # Count unique hours in range for hourly_avg
    range_secs = RANGE_SECONDS.get(range, 86400)
    total_hours = max(range_secs / 3600, 1)

    actions_list = []
    for name, agg in action_agg.items():
        started = agg.get("started", 0)
        completed = agg.get("completed", 0)
        failed = agg.get("failed", 0)
        total_done = completed + failed
        sr = round(completed / total_done * 100, 1) if total_done > 0 else None
        hourly_avg = round(started / total_hours, 2)

        # Peak hour
        hourly = action_hourly.get(name, {})
        peak_hour = ""
        peak_count = 0
        if hourly:
            peak_hour = max(hourly, key=lambda h: hourly[h]["started"])
            peak_count = hourly[peak_hour]["started"]

        dur_sum = agg.get("duration_sum_ms", 0)
        dur_count = agg.get("duration_count", 0)
        avg_duration_ms = int(dur_sum / dur_count) if dur_count > 0 else None

        # Build zero-gap-filled hourly_buckets for heatmap
        now = datetime.now(timezone.utc)
        start_hour = (now - timedelta(seconds=range_secs)).replace(minute=0, second=0, microsecond=0)
        end_hour = now.replace(minute=0, second=0, microsecond=0)
        action_buckets: list[dict[str, Any]] = []
        cur = start_hour
        while cur <= end_hour:
            h_str = cur.strftime("%Y-%m-%dT%H:%M:%SZ")
            h_data = hourly.get(h_str, {})
            action_buckets.append({
                "hour": h_str,
                "started": h_data.get("started", 0),
                "completed": h_data.get("completed", 0),
                "failed": h_data.get("failed", 0),
            })
            cur += timedelta(hours=1)

        actions_list.append(InsightsActionDetail(
            name=name,
            total_started=started,
            total_completed=completed,
            total_failed=failed,
            success_rate=sr,
            agents_using=action_agents.get(name, {}),
            hourly_avg=hourly_avg,
            peak_hour=peak_hour,
            peak_count=peak_count,
            avg_duration_ms=avg_duration_ms,
            hourly_buckets=action_buckets,
        ))

    actions_list.sort(key=lambda a: a.total_started, reverse=True)

    return InsightsActionsResponse(
        range=range, actions=actions_list,
    ).model_dump(mode="json")
