"""Pre-Aggregated Insights Engine — Aggregator Module.

Maintains 2 denormalized aggregate tables (agent_hourly, model_hourly)
that are updated incrementally at ingestion time.  Query endpoints read
pre-computed rows — no scanning.

Tables:
  agent_hourly.json — one row per (tenant_id, agent_id, hour)
  model_hourly.json — one row per (tenant_id, model, hour)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ───────────────────────────────────────────────────────────────────────
#  HELPERS
# ───────────────────────────────────────────────────────────────────────

def _hour_key(timestamp_str: str) -> str:
    """Truncate ISO timestamp to hour boundary."""
    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    truncated = dt.replace(minute=0, second=0, microsecond=0)
    return truncated.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_or_create_bucket(
    table: list[dict[str, Any]],
    tenant_id: str,
    key_field: str,
    key_value: str,
    hour: str,
) -> dict[str, Any]:
    """Find or create a bucket row in the given table."""
    for row in table:
        if (
            row.get("tenant_id") == tenant_id
            and row.get(key_field) == key_value
            and row.get("hour") == hour
        ):
            return row
    new_bucket: dict[str, Any] = {
        "tenant_id": tenant_id,
        key_field: key_value,
        "hour": hour,
    }
    table.append(new_bucket)
    return new_bucket


# ───────────────────────────────────────────────────────────────────────
#  NESTED DICT HELPERS
# ───────────────────────────────────────────────────────────────────────

def _inc(bucket: dict, key: str, amount: int | float = 1) -> None:
    """Increment a numeric field in a bucket, initializing to 0 if absent."""
    bucket[key] = bucket.get(key, 0) + amount


def _inc_nested(bucket: dict, dict_key: str, name: str, field: str, amount: int | float = 1) -> None:
    """Increment a field inside a nested dict entry."""
    d = bucket.setdefault(dict_key, {})
    entry = d.setdefault(name, {})
    entry[field] = entry.get(field, 0) + amount


# ───────────────────────────────────────────────────────────────────────
#  AGENT HOURLY UPDATE
# ───────────────────────────────────────────────────────────────────────

def update_agent_hourly(bucket: dict[str, Any], event: Any) -> None:
    """Increment an agent_hourly bucket from a single Event.

    The event is a Pydantic Event model instance (attributes, not dict).
    payload is a dict (event.payload).
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _inc(bucket, "event_count")
    bucket["last_updated"] = now_iso

    et = event.event_type
    payload = event.payload or {}
    payload_kind = payload.get("kind") if isinstance(payload, dict) else None
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}

    # ── Task events ──
    if et == "task_started":
        _inc(bucket, "tasks_started")
        task_type = event.task_type
        if task_type:
            _inc_nested(bucket, "tasks_by_type", task_type, "started")

    elif et == "task_completed":
        _inc(bucket, "tasks_completed")
        _inc(bucket, "task_duration_count")
        if event.duration_ms:
            _inc(bucket, "task_duration_sum_ms", event.duration_ms)
        task_type = event.task_type
        if task_type:
            _inc_nested(bucket, "tasks_by_type", task_type, "completed")

    elif et == "task_failed":
        _inc(bucket, "tasks_failed")
        _inc(bucket, "task_duration_count")
        if event.duration_ms:
            _inc(bucket, "task_duration_sum_ms", event.duration_ms)
        task_type = event.task_type
        if task_type:
            _inc_nested(bucket, "tasks_by_type", task_type, "failed")

    # ── Action events ──
    elif et == "action_started":
        _inc(bucket, "actions_started")
        action_name = _extract_action_name(payload)
        if action_name:
            _inc_nested(bucket, "actions_by_name", action_name, "started")

    elif et == "action_completed":
        _inc(bucket, "actions_completed")
        action_name = _extract_action_name(payload)
        if action_name:
            _inc_nested(bucket, "actions_by_name", action_name, "completed")

    elif et == "action_failed":
        _inc(bucket, "actions_failed")
        action_name = _extract_action_name(payload)
        if action_name:
            _inc_nested(bucket, "actions_by_name", action_name, "failed")

    # ── Operational events ──
    elif et == "retry_started":
        _inc(bucket, "retries")

    elif et == "escalated":
        _inc(bucket, "escalations")

    elif et == "approval_requested":
        _inc(bucket, "approvals_requested")

    elif et == "approval_received":
        _inc(bucket, "approvals_received")

    # ── LLM call (payload kind, not event type) ──
    if payload_kind == "llm_call":
        tokens_in = data.get("tokens_in", 0) or 0
        tokens_out = data.get("tokens_out", 0) or 0
        cost = data.get("cost", 0) or 0
        model = data.get("model", "unknown")
        call_name = data.get("name", "unknown")

        _inc(bucket, "llm_call_count")
        _inc(bucket, "llm_tokens_in", tokens_in)
        _inc(bucket, "llm_tokens_out", tokens_out)
        _inc(bucket, "llm_cost", cost)

        # Track max tokens_in
        if tokens_in > bucket.get("llm_max_tokens_in", 0):
            bucket["llm_max_tokens_in"] = tokens_in
            bucket["llm_max_tokens_in_name"] = call_name

        # Per-model breakdown
        models = bucket.setdefault("models", {})
        m = models.setdefault(model, {})
        m["calls"] = m.get("calls", 0) + 1
        m["cost"] = m.get("cost", 0) + cost
        m["tokens_in"] = m.get("tokens_in", 0) + tokens_in
        m["tokens_out"] = m.get("tokens_out", 0) + tokens_out

        # Per-call-name breakdown
        calls = bucket.setdefault("calls_by_name", {})
        c = calls.setdefault(call_name, {})
        c["count"] = c.get("count", 0) + 1
        c["tokens_in_sum"] = c.get("tokens_in_sum", 0) + tokens_in
        c["tokens_out_sum"] = c.get("tokens_out_sum", 0) + tokens_out
        c["cost_sum"] = c.get("cost_sum", 0) + cost

    # ── Issue tracking (payload kind) ──
    if payload_kind == "issue":
        action = data.get("action", "reported")
        if action == "resolved":
            _inc(bucket, "issues_resolved")
        else:
            _inc(bucket, "issues_reported")
            category = data.get("category")
            if category:
                ebc = bucket.setdefault("errors_by_category", {})
                ebc[category] = ebc.get(category, 0) + 1

    # ── Error tracking ──
    if et in ("task_failed", "action_failed"):
        error_type = data.get("error_type") or data.get("exception_type")
        if error_type:
            ebt = bucket.setdefault("errors_by_type", {})
            ebt[error_type] = ebt.get(error_type, 0) + 1


def _extract_action_name(payload: dict) -> str | None:
    """Extract action name from payload (SDK puts it at payload.action_name)."""
    if not isinstance(payload, dict):
        return None
    name = payload.get("action_name")
    if not name:
        data = payload.get("data", {})
        if isinstance(data, dict):
            name = data.get("action_name")
    if not name:
        name = payload.get("summary")
    return name


# ───────────────────────────────────────────────────────────────────────
#  MODEL HOURLY UPDATE
# ───────────────────────────────────────────────────────────────────────

def update_model_hourly(bucket: dict[str, Any], event: Any) -> None:
    """Increment a model_hourly bucket from an llm_call event."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bucket["last_updated"] = now_iso

    payload = event.payload or {}
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}

    tokens_in = data.get("tokens_in", 0) or 0
    tokens_out = data.get("tokens_out", 0) or 0
    cost = data.get("cost", 0) or 0
    duration_ms = data.get("duration_ms", 0) or 0
    call_name = data.get("name", "unknown")
    agent_id = event.agent_id

    _inc(bucket, "call_count")
    _inc(bucket, "tokens_in", tokens_in)
    _inc(bucket, "tokens_out", tokens_out)
    _inc(bucket, "cost", cost)
    _inc(bucket, "duration_sum_ms", duration_ms)
    _inc(bucket, "duration_count")

    # Track max tokens_in
    if tokens_in > bucket.get("max_tokens_in", 0):
        bucket["max_tokens_in"] = tokens_in
        bucket["max_tokens_in_agent"] = agent_id
        bucket["max_tokens_in_name"] = call_name

    # Per-agent breakdown
    agents = bucket.setdefault("agents", {})
    a = agents.setdefault(agent_id, {})
    a["calls"] = a.get("calls", 0) + 1
    a["cost"] = a.get("cost", 0) + cost
    a["tokens_in"] = a.get("tokens_in", 0) + tokens_in
    a["tokens_out"] = a.get("tokens_out", 0) + tokens_out

    # Per-call-name breakdown
    calls = bucket.setdefault("calls_by_name", {})
    c = calls.setdefault(call_name, {})
    c["count"] = c.get("count", 0) + 1
    c["cost_sum"] = c.get("cost_sum", 0) + cost


# ───────────────────────────────────────────────────────────────────────
#  REBUILD FROM RAW EVENTS
# ───────────────────────────────────────────────────────────────────────

async def rebuild_aggregates(storage: Any) -> dict[str, int]:
    """Rebuild both aggregate tables from raw events.

    Clears existing aggregates and re-processes all events.
    Returns {"agent_hourly": N, "model_hourly": M} bucket counts.
    """
    from shared.models import Event

    # Clear tables
    storage._tables["agent_hourly"] = []
    storage._tables["model_hourly"] = []

    for row in storage._tables["events"]:
        ev = Event(**row)
        tenant_id = ev.tenant_id
        hour = _hour_key(ev.timestamp)

        # Agent hourly
        bucket = get_or_create_bucket(
            storage._tables["agent_hourly"],
            tenant_id, "agent_id", ev.agent_id, hour,
        )
        update_agent_hourly(bucket, ev)

        # Model hourly (only for llm_call events)
        payload = ev.payload or {}
        if isinstance(payload, dict) and payload.get("kind") == "llm_call":
            model = (payload.get("data") or {}).get("model", "unknown")
            bucket = get_or_create_bucket(
                storage._tables["model_hourly"],
                tenant_id, "model", model, hour,
            )
            update_model_hourly(bucket, ev)

    storage._persist("agent_hourly")
    storage._persist("model_hourly")

    return {
        "agent_hourly": len(storage._tables["agent_hourly"]),
        "model_hourly": len(storage._tables["model_hourly"]),
    }
