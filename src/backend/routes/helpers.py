"""Shared route helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.storage_json import derive_agent_status
from shared.enums import AgentStatus
from shared.models import AgentRecord, AgentStats1h, AgentSummary, UserSafe


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


def _normalize_ts(iso_str: str | None) -> str | None:
    """W10: Normalize timestamps to end with Z instead of +00:00."""
    if iso_str is None:
        return None
    return iso_str.replace("+00:00", "Z")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
        last_event_type=agent.last_event_type,
        last_event_at=_normalize_ts(agent.last_seen.isoformat()) if agent.last_seen else None,
        stats_1h=stats,
    )


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
        return
    user_role = getattr(request.state, "user_role", None)
    if user_role not in allowed_roles:
        raise HTTPException(403, {
            "error": "insufficient_permissions",
            "message": f"Role '{user_role}' not in allowed roles: {allowed_roles}",
            "status": 403,
        })


def _get_broadcaster(app_instance):
    """Return the active broadcaster (bridge in production, ws_manager locally)."""
    if getattr(app_instance.state, "ws_mode", "local") == "bridge":
        return app_instance.state.ws_bridge
    from backend.websocket import ws_manager
    return ws_manager


def _filter_hourly_buckets(
    table: list[dict], tenant_id: str, range_str: str,
    key_field: str | None = None, key_value: str | None = None,
) -> list[dict]:
    """Filter hourly aggregate buckets by tenant, range, and optional key."""
    from datetime import timedelta
    from shared.enums import RANGE_SECONDS
    range_secs = RANGE_SECONDS.get(range_str, 86400)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=range_secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for row in table:
        if row.get("tenant_id") != tenant_id:
            continue
        if row.get("hour", "") < cutoff:
            continue
        if key_field and key_value and row.get(key_field) != key_value:
            continue
        results.append(row)
    return results


def _merge_dict_counters(target: dict, source: dict) -> None:
    """Merge a source dict of counters into target."""
    for k, v in source.items():
        if isinstance(v, dict):
            sub = target.setdefault(k, {})
            for sk, sv in v.items():
                sub[sk] = sub.get(sk, 0) + sv
        else:
            target[k] = target.get(k, 0) + v


def _build_comparison(agents_data: list[dict], metric_key: str) -> dict:
    """Build comparison stats for a metric across agents."""
    from shared.models import InsightsComparison
    if not agents_data:
        return InsightsComparison().model_dump(mode="json")
    values = [(a["agent_id"], a.get(metric_key, 0)) for a in agents_data]
    values.sort(key=lambda x: x[1], reverse=True)
    max_agent, max_val = values[0]
    min_agent, min_val = values[-1]
    avg_val = sum(v for _, v in values) / len(values) if values else 0
    return InsightsComparison(
        max_agent=max_agent, min_agent=min_agent,
        max_value=max_val, min_value=min_val, avg_value=round(avg_val, 4),
        max_vs_avg=round(max_val / avg_val, 2) if avg_val > 0 else 0,
        max_vs_min=round(max_val / min_val, 2) if min_val > 0 else 0,
    ).model_dump(mode="json")
