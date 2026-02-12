"""HiveBoard API server.

Part A of the spec — all endpoints under /v1.

Write path:
  POST /v1/ingest           — Batch event ingestion (envelope + events)

Read path:
  GET  /v1/agents           — Fleet overview (The Hive)
  GET  /v1/agents/{id}      — Single agent detail
  GET  /v1/tasks            — Task list
  GET  /v1/tasks/{id}/timeline — Task timeline with action tree
  GET  /v1/events           — Activity stream
  GET  /v1/metrics          — Aggregate metrics + timeseries

Dashboard:
  GET  /dashboard           — Serve the live dashboard HTML
"""

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from db import (
    init_db, insert_events, load_events, load_agents, upsert_agent,
    SEVERITY_DEFAULTS, VALID_EVENT_TYPES,
)

app = FastAPI(title="HiveBoard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# MVP: single hardcoded tenant. API key -> tenant mapping.
API_KEY_MAP = {
    "hb_dev_key": "tenant_dev",
    "hb_live_testkey123": "tenant_dev",
}


@app.on_event("startup")
def startup():
    init_db()


def _resolve_tenant(request: Request) -> str:
    """Derive tenant_id from API key. MVP: accept anything, default tenant."""
    auth = request.headers.get("Authorization", "")
    api_key = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else auth
    return API_KEY_MAP.get(api_key, "tenant_dev")


# ──────────────────────────────────────────────
#  INGESTION — POST /v1/ingest  (spec Section 3.1)
# ──────────────────────────────────────────────

@app.post("/v1/ingest")
async def ingest(request: Request):
    """Accept batch envelope per spec Section 3.1.

    Body shape:
    {
      "envelope": { "agent_id": "...", "agent_type": "...", ... },
      "events": [ { "event_id": "...", ... } ]
    }
    """
    tenant_id = _resolve_tenant(request)
    body = await request.json()
    now = datetime.now(timezone.utc).isoformat()

    envelope = body.get("envelope", {})
    raw_events = body.get("events", [])

    agent_id = envelope.get("agent_id")
    if not agent_id:
        return _error("invalid_batch", "envelope.agent_id is required.", 400)

    if len(raw_events) > 500:
        return _error("invalid_batch", "Batch exceeds maximum size of 500 events.", 400)

    if not raw_events:
        return {"accepted": 0, "rejected": 0, "errors": []}

    # Upsert agent profile (spec Section 3.3)
    latest_ts = max((e.get("timestamp", now) for e in raw_events), default=now)
    upsert_agent(agent_id, envelope, latest_ts)

    accepted = 0
    rejected = 0
    errors = []
    expanded = []

    for evt in raw_events:
        eid = evt.get("event_id")
        if not eid:
            rejected += 1
            errors.append({"event_id": None, "error": "missing_required_field", "message": "event_id is required"})
            continue

        event_type = evt.get("event_type")
        if not event_type:
            rejected += 1
            errors.append({"event_id": eid, "error": "missing_required_field", "message": "event_type is required"})
            continue

        if not evt.get("timestamp"):
            rejected += 1
            errors.append({"event_id": eid, "error": "missing_required_field", "message": "timestamp is required"})
            continue

        if event_type not in VALID_EVENT_TYPES:
            rejected += 1
            errors.append({"event_id": eid, "error": "invalid_event_type", "message": f"Unknown event_type: '{event_type}'"})
            continue

        severity = evt.get("severity") or SEVERITY_DEFAULTS.get(event_type, "info")

        expanded.append({
            "event_id": eid,
            "tenant_id": tenant_id,
            "agent_id": evt.get("agent_id") or agent_id,
            "agent_type": evt.get("agent_type") or envelope.get("agent_type", "general"),
            "timestamp": evt["timestamp"],
            "received_at": now,
            "environment": evt.get("environment") or envelope.get("environment", "production"),
            "group": evt.get("group") or envelope.get("group", "default"),
            "task_id": evt.get("task_id"),
            "task_type": evt.get("task_type"),
            "task_run_id": evt.get("task_run_id"),
            "correlation_id": evt.get("correlation_id"),
            "action_id": evt.get("action_id"),
            "parent_action_id": evt.get("parent_action_id"),
            "event_type": event_type,
            "severity": severity,
            "status": evt.get("status"),
            "duration_ms": evt.get("duration_ms"),
            "parent_event_id": evt.get("parent_event_id"),
            "payload": evt.get("payload"),
        })
        accepted += 1

    if expanded:
        insert_events(expanded)

    status_code = 200 if rejected == 0 else 207
    return _json({"accepted": accepted, "rejected": rejected, "errors": errors}, status_code)


# ──────────────────────────────────────────────
#  QUERY — GET /v1/agents  (spec Section 4.1)
# ──────────────────────────────────────────────

@app.get("/v1/agents")
def get_agents(
    environment: str | None = None,
    group: str | None = None,
    status: str | None = None,
    sort: str = "attention",
    limit: int = 50,
):
    """Fleet overview — derived agent status + 1h stats."""
    all_events = load_events()
    agent_profiles = load_agents()
    now = datetime.now(timezone.utc)

    # Collect per-agent data from events
    agent_data = {}  # agent_id -> {latest_evt, heartbeats, events_1h, ...}

    for evt in all_events:
        aid = evt.get("agent_id")
        if not aid:
            continue

        if aid not in agent_data:
            agent_data[aid] = {"latest": None, "last_heartbeat": None, "events_1h": []}

        d = agent_data[aid]
        ts = evt.get("timestamp", "")

        if d["latest"] is None or ts > d["latest"]["timestamp"]:
            d["latest"] = evt

        if evt.get("event_type") == "heartbeat":
            if d["last_heartbeat"] is None or ts > d["last_heartbeat"]:
                d["last_heartbeat"] = ts

        # Collect events from last hour for stats
        try:
            evt_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if (now - evt_time).total_seconds() <= 3600:
                d["events_1h"].append(evt)
        except (ValueError, TypeError):
            pass

    # Build response
    agents = []
    for aid, d in agent_data.items():
        evt = d["latest"]
        if not evt:
            continue

        profile = agent_profiles.get(aid, {})

        # Apply filters
        agent_env = evt.get("environment", "production")
        agent_group = evt.get("group", "default")
        if environment and agent_env != environment:
            continue
        if group and agent_group != group:
            continue

        derived_status = _derive_agent_status(evt, d["last_heartbeat"], now)
        if status and derived_status != status:
            continue

        # 1h stats
        stats = _compute_1h_stats(d["events_1h"])

        # Heartbeat age
        hb_age = None
        if d["last_heartbeat"]:
            try:
                hb_time = datetime.fromisoformat(d["last_heartbeat"].replace("Z", "+00:00"))
                hb_age = int((now - hb_time).total_seconds())
            except (ValueError, TypeError):
                pass

        # Current task
        current_task = evt.get("task_id") if derived_status == "processing" else None

        agents.append({
            "agent_id": aid,
            "agent_type": profile.get("agent_type") or evt.get("agent_type", "general"),
            "agent_version": profile.get("agent_version"),
            "framework": profile.get("framework", "custom"),
            "environment": agent_env,
            "group": agent_group,
            "derived_status": derived_status,
            "current_task_id": current_task,
            "last_heartbeat": d["last_heartbeat"],
            "heartbeat_age_seconds": hb_age,
            "is_stuck": derived_status == "stuck",
            "stuck_threshold_seconds": 300,
            "first_seen": profile.get("first_seen"),
            "last_seen": profile.get("last_seen") or evt.get("timestamp"),
            "stats_1h": stats,
        })

    # Sort
    if sort == "attention":
        priority = {"stuck": 0, "error": 1, "waiting_approval": 2, "processing": 3, "idle": 4}
        agents.sort(key=lambda a: priority.get(a["derived_status"], 5))
    elif sort == "name":
        agents.sort(key=lambda a: a["agent_id"])
    elif sort == "last_seen":
        agents.sort(key=lambda a: a.get("last_seen") or "", reverse=True)

    return {"data": agents[:limit], "pagination": {"cursor": None, "has_more": len(agents) > limit}}


# ──────────────────────────────────────────────
#  QUERY — GET /v1/agents/{agent_id}  (spec Section 4.2)
# ──────────────────────────────────────────────

@app.get("/v1/agents/{agent_id}")
def get_agent_detail(agent_id: str):
    """Single agent profile + derived state."""
    all_events = load_events()
    agent_profiles = load_agents()
    now = datetime.now(timezone.utc)

    profile = agent_profiles.get(agent_id)
    agent_events = [e for e in all_events if e.get("agent_id") == agent_id]

    if not agent_events and not profile:
        raise HTTPException(status_code=404, detail={
            "error": "agent_not_found",
            "message": f"No agent with id '{agent_id}' in this workspace.",
            "status": 404,
        })

    agent_events.sort(key=lambda e: e.get("timestamp", ""))

    latest = agent_events[-1] if agent_events else {}
    last_hb = None
    for e in reversed(agent_events):
        if e.get("event_type") == "heartbeat":
            last_hb = e.get("timestamp")
            break

    derived_status = _derive_agent_status(latest, last_hb, now) if latest else "idle"

    # 1h stats
    events_1h = []
    for e in agent_events:
        try:
            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
            if (now - ts).total_seconds() <= 3600:
                events_1h.append(e)
        except (ValueError, TypeError, KeyError):
            pass

    stats = _compute_1h_stats(events_1h)

    hb_age = None
    if last_hb:
        try:
            hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
            hb_age = int((now - hb_time).total_seconds())
        except (ValueError, TypeError):
            pass

    return {
        "agent_id": agent_id,
        "agent_type": (profile or {}).get("agent_type") or latest.get("agent_type", "general"),
        "agent_version": (profile or {}).get("agent_version"),
        "framework": (profile or {}).get("framework", "custom"),
        "environment": (profile or {}).get("environment") or latest.get("environment", "production"),
        "group": (profile or {}).get("group") or latest.get("group", "default"),
        "derived_status": derived_status,
        "current_task_id": latest.get("task_id") if derived_status == "processing" else None,
        "last_heartbeat": last_hb,
        "heartbeat_age_seconds": hb_age,
        "is_stuck": derived_status == "stuck",
        "stuck_threshold_seconds": 300,
        "first_seen": (profile or {}).get("first_seen"),
        "last_seen": (profile or {}).get("last_seen") or latest.get("timestamp"),
        "stats_1h": stats,
    }


# ──────────────────────────────────────────────
#  QUERY — GET /v1/tasks  (spec Section 4.3)
# ──────────────────────────────────────────────

@app.get("/v1/tasks")
def get_tasks(
    agent_id: str | None = None,
    task_type: str | None = None,
    status: str | None = None,
    environment: str | None = None,
    group: str | None = None,
    sort: str = "newest",
    limit: int = 50,
):
    """Task list derived from task lifecycle events."""
    all_events = load_events()

    # Group events by task_id
    task_events = {}
    for evt in all_events:
        tid = evt.get("task_id")
        if not tid:
            continue
        if agent_id and evt.get("agent_id") != agent_id:
            continue
        if environment and evt.get("environment") != environment:
            continue
        if group and evt.get("group") != group:
            continue
        task_events.setdefault(tid, []).append(evt)

    tasks = []
    for tid, events in task_events.items():
        events.sort(key=lambda e: e.get("timestamp", ""))
        first = events[0]

        if task_type and first.get("task_type") != task_type:
            continue

        event_types = {e.get("event_type") for e in events}
        derived_status = _derive_task_status(event_types)

        if status and derived_status != status:
            continue

        # Terminal event data
        duration_ms = None
        total_cost = 0.0
        has_cost = False
        completed_at = None
        action_count = 0
        error_count = 0
        has_escalation = "escalated" in event_types
        has_human = "approval_requested" in event_types or "approval_received" in event_types

        for e in events:
            et = e.get("event_type")
            if et in ("task_completed", "task_failed"):
                duration_ms = e.get("duration_ms")
                completed_at = e.get("timestamp")
            if et in ("action_started",):
                action_count += 1
            if et in ("action_failed", "task_failed"):
                error_count += 1
            # Sum cost from payloads
            payload = e.get("payload")
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, dict) and data.get("cost") is not None:
                    try:
                        total_cost += float(data["cost"])
                        has_cost = True
                    except (ValueError, TypeError):
                        pass

        tasks.append({
            "task_id": tid,
            "task_type": first.get("task_type"),
            "task_run_id": first.get("task_run_id"),
            "agent_id": first.get("agent_id"),
            "derived_status": derived_status,
            "started_at": first.get("timestamp"),
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "total_cost": round(total_cost, 4) if has_cost else None,
            "action_count": action_count,
            "error_count": error_count,
            "has_escalation": has_escalation,
            "has_human_intervention": has_human,
        })

    # Sort
    if sort == "newest":
        tasks.sort(key=lambda t: t.get("started_at") or "", reverse=True)
    elif sort == "oldest":
        tasks.sort(key=lambda t: t.get("started_at") or "")
    elif sort == "duration":
        tasks.sort(key=lambda t: t.get("duration_ms") or 0, reverse=True)
    elif sort == "cost":
        tasks.sort(key=lambda t: t.get("total_cost") or 0, reverse=True)

    return {"data": tasks[:limit], "pagination": {"cursor": None, "has_more": len(tasks) > limit}}


# ──────────────────────────────────────────────
#  QUERY — GET /v1/tasks/{task_id}/timeline  (spec Section 4.4)
# ──────────────────────────────────────────────

@app.get("/v1/tasks/{task_id}/timeline")
def get_timeline(task_id: str):
    """Full event sequence + action tree for a task."""
    all_events = load_events()
    task_events = [e for e in all_events if e.get("task_id") == task_id]

    if not task_events:
        raise HTTPException(status_code=404, detail={
            "error": "task_not_found",
            "message": f"No task with id '{task_id}' in this workspace.",
            "status": 404,
        })

    task_events.sort(key=lambda e: e.get("timestamp", ""))
    first = task_events[0]

    event_types = {e.get("event_type") for e in task_events}
    derived_status = _derive_task_status(event_types)

    # Terminal data
    duration_ms = None
    completed_at = None
    total_cost = 0.0
    has_cost = False

    for e in task_events:
        if e.get("event_type") in ("task_completed", "task_failed"):
            duration_ms = e.get("duration_ms")
            completed_at = e.get("timestamp")
        payload = e.get("payload")
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and data.get("cost") is not None:
                try:
                    total_cost += float(data["cost"])
                    has_cost = True
                except (ValueError, TypeError):
                    pass

    # Build flat event list
    events_out = []
    for e in task_events:
        events_out.append({
            "event_id": e.get("event_id"),
            "event_type": e.get("event_type"),
            "timestamp": e.get("timestamp"),
            "severity": e.get("severity"),
            "status": e.get("status"),
            "duration_ms": e.get("duration_ms"),
            "action_id": e.get("action_id"),
            "parent_action_id": e.get("parent_action_id"),
            "parent_event_id": e.get("parent_event_id"),
            "payload": e.get("payload"),
        })

    # Build action tree from action_started/completed/failed events
    action_tree = _build_action_tree(task_events)

    # Build error chains from parent_event_id links
    error_chains = _build_error_chains(task_events)

    return {
        "task_id": task_id,
        "task_run_id": first.get("task_run_id"),
        "agent_id": first.get("agent_id"),
        "task_type": first.get("task_type"),
        "derived_status": derived_status,
        "started_at": first.get("timestamp"),
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "total_cost": round(total_cost, 4) if has_cost else None,
        "events": events_out,
        "action_tree": action_tree,
        "error_chains": error_chains,
    }


# ──────────────────────────────────────────────
#  QUERY — GET /v1/events  (spec Section 4.5)
# ──────────────────────────────────────────────

@app.get("/v1/events")
def get_events(
    agent_id: str | None = None,
    task_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    environment: str | None = None,
    group: str | None = None,
    exclude_heartbeats: bool = True,
    limit: int = 50,
):
    """Activity stream — reverse-chronological events."""
    all_events = load_events()

    filtered = all_events
    if exclude_heartbeats:
        filtered = [e for e in filtered if e.get("event_type") != "heartbeat"]
    if agent_id:
        filtered = [e for e in filtered if e.get("agent_id") == agent_id]
    if task_id:
        filtered = [e for e in filtered if e.get("task_id") == task_id]
    if event_type:
        types = set(event_type.split(","))
        filtered = [e for e in filtered if e.get("event_type") in types]
    if severity:
        severities = set(severity.split(","))
        filtered = [e for e in filtered if e.get("severity") in severities]
    if environment:
        filtered = [e for e in filtered if e.get("environment") == environment]
    if group:
        filtered = [e for e in filtered if e.get("group") == group]

    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    page = filtered[:limit]

    data = []
    for evt in page:
        data.append({
            "event_id": evt.get("event_id"),
            "agent_id": evt.get("agent_id"),
            "agent_type": evt.get("agent_type"),
            "task_id": evt.get("task_id"),
            "event_type": evt.get("event_type"),
            "timestamp": evt.get("timestamp"),
            "severity": evt.get("severity"),
            "status": evt.get("status"),
            "duration_ms": evt.get("duration_ms"),
            "payload": evt.get("payload"),
        })

    return {"data": data, "pagination": {"cursor": None, "has_more": len(filtered) > limit}}


# ──────────────────────────────────────────────
#  QUERY — GET /v1/metrics  (spec Section 4.6)
# ──────────────────────────────────────────────

RANGE_SECONDS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}
AUTO_INTERVAL = {"1h": "5m", "6h": "15m", "24h": "1h", "7d": "6h", "30d": "1d"}
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}


@app.get("/v1/metrics")
def get_metrics(
    agent_id: str | None = None,
    environment: str | None = None,
    group: str | None = None,
    range: str = "1h",
    interval: str | None = None,
):
    """Aggregate metrics + timeseries buckets."""
    range_sec = RANGE_SECONDS.get(range, 3600)
    interval_key = interval or AUTO_INTERVAL.get(range, "5m")
    interval_sec = INTERVAL_SECONDS.get(interval_key, 300)

    all_events = load_events()
    now = datetime.now(timezone.utc)

    # Filter events within range
    in_range = []
    for evt in all_events:
        if agent_id and evt.get("agent_id") != agent_id:
            continue
        if environment and evt.get("environment") != environment:
            continue
        if group and evt.get("group") != group:
            continue
        try:
            ts = datetime.fromisoformat(evt["timestamp"].replace("Z", "+00:00"))
            if (now - ts).total_seconds() <= range_sec:
                in_range.append((evt, ts))
        except (ValueError, TypeError, KeyError):
            continue

    # Summary
    completed = 0
    failed = 0
    escalated = 0
    stuck = 0
    total_dur = 0.0
    dur_count = 0
    total_cost = 0.0
    has_cost = False

    for evt, _ in in_range:
        et = evt.get("event_type")
        if et == "task_completed":
            completed += 1
            if evt.get("duration_ms") is not None:
                total_dur += evt["duration_ms"]
                dur_count += 1
        elif et == "task_failed":
            failed += 1
        elif et == "escalated":
            escalated += 1

        payload = evt.get("payload")
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and data.get("cost") is not None:
                try:
                    total_cost += float(data["cost"])
                    has_cost = True
                except (ValueError, TypeError):
                    pass

    total_tasks = completed + failed
    success_rate = round(completed / total_tasks, 3) if total_tasks > 0 else None
    avg_dur = round(total_dur / dur_count) if dur_count > 0 else None

    summary = {
        "total_tasks": total_tasks,
        "completed": completed,
        "failed": failed,
        "escalated": escalated,
        "stuck": stuck,
        "success_rate": success_rate,
        "avg_duration_ms": avg_dur,
        "total_cost": round(total_cost, 4) if has_cost else None,
        "avg_cost_per_task": round(total_cost / total_tasks, 4) if has_cost and total_tasks > 0 else None,
    }

    # Timeseries buckets
    timeseries = _build_timeseries(in_range, now, range_sec, interval_sec)

    return {
        "range": range,
        "interval": interval_key,
        "summary": summary,
        "timeseries": timeseries,
    }


# ──────────────────────────────────────────────
#  DASHBOARD
# ──────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "docs", "hiveboard-v2.html")
    with open(dashboard_path) as f:
        return HTMLResponse(f.read())


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def _json(data, status_code=200):
    from fastapi.responses import JSONResponse
    return JSONResponse(content=data, status_code=status_code)


def _error(code: str, message: str, status: int):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={"error": code, "message": message, "status": status},
        status_code=status,
    )


def _derive_agent_status(latest_event: dict, last_heartbeat: str | None, now: datetime) -> str:
    """Spec Section 4.1 — priority cascade for derived_status."""
    # Priority 1: stuck (no heartbeat in 5 min)
    if last_heartbeat:
        try:
            hb_time = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
            if (now - hb_time).total_seconds() > 300:
                return "stuck"
        except (ValueError, TypeError):
            pass

    last_type = latest_event.get("event_type", "")

    # Priority 2: error
    if last_type in ("task_failed", "action_failed"):
        return "error"
    # Priority 3: waiting approval
    if last_type == "approval_requested":
        return "waiting_approval"
    # Priority 4: processing
    if last_type in ("task_started", "action_started"):
        return "processing"
    # Priority 5: idle
    return "idle"


def _derive_task_status(event_types: set) -> str:
    """Spec Section 4.3 — task status from event types present."""
    if "task_completed" in event_types:
        return "completed"
    if "task_failed" in event_types:
        return "failed"
    if "escalated" in event_types:
        return "escalated"
    if "approval_requested" in event_types and "approval_received" not in event_types:
        return "waiting"
    return "processing"


def _compute_1h_stats(events_1h: list[dict]) -> dict:
    """Compute 1-hour stats for an agent."""
    completed = 0
    failed = 0
    total_dur = 0.0
    dur_count = 0
    total_cost = 0.0
    has_cost = False

    for e in events_1h:
        et = e.get("event_type")
        if et == "task_completed":
            completed += 1
            if e.get("duration_ms") is not None:
                total_dur += e["duration_ms"]
                dur_count += 1
        elif et == "task_failed":
            failed += 1

        payload = e.get("payload")
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and data.get("cost") is not None:
                try:
                    total_cost += float(data["cost"])
                    has_cost = True
                except (ValueError, TypeError):
                    pass

    total = completed + failed
    return {
        "tasks_completed": completed,
        "tasks_failed": failed,
        "success_rate": round(completed / total, 3) if total > 0 else None,
        "avg_duration_ms": round(total_dur / dur_count) if dur_count > 0 else None,
        "total_cost": round(total_cost, 4) if has_cost else None,
        "throughput": completed,
    }


def _build_action_tree(task_events: list[dict]) -> list[dict]:
    """Build hierarchical action tree from action events."""
    actions = {}  # action_id -> node
    for e in task_events:
        aid = e.get("action_id")
        if not aid:
            continue
        et = e.get("event_type")
        payload = e.get("payload")

        if aid not in actions:
            actions[aid] = {
                "action_id": aid,
                "action_name": payload.get("action_name") if isinstance(payload, dict) else None,
                "parent_action_id": e.get("parent_action_id"),
                "started_at": None,
                "duration_ms": None,
                "status": None,
                "children": [],
            }

        node = actions[aid]
        if et == "action_started":
            node["started_at"] = e.get("timestamp")
            if isinstance(payload, dict) and payload.get("action_name"):
                node["action_name"] = payload["action_name"]
        elif et == "action_completed":
            node["duration_ms"] = e.get("duration_ms")
            node["status"] = "success"
        elif et == "action_failed":
            node["duration_ms"] = e.get("duration_ms")
            node["status"] = "failure"

    # Build tree: attach children to parents
    roots = []
    for aid, node in actions.items():
        parent_id = node["parent_action_id"]
        if parent_id and parent_id in actions:
            actions[parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


def _build_error_chains(task_events: list[dict]) -> list[dict]:
    """Build error chains from parent_event_id links."""
    # Find events that have parent_event_id
    children = {}  # parent_event_id -> [event_ids]
    event_ids = set()
    for e in task_events:
        eid = e.get("event_id")
        event_ids.add(eid)
        pid = e.get("parent_event_id")
        if pid:
            children.setdefault(pid, []).append(eid)

    # Find chain roots: events that are parents but not children
    all_parents = set(children.keys())
    all_children = set()
    for kids in children.values():
        all_children.update(kids)

    roots = all_parents - all_children
    chains = []
    for root in roots:
        chain = [root]
        current = root
        while current in children:
            nexts = children[current]
            chain.extend(nexts)
            current = nexts[0] if len(nexts) == 1 else None
            if current is None:
                break
        if len(chain) > 1:
            chains.append({"original_event_id": root, "chain": chain})

    return chains


def _build_timeseries(events_with_ts, now, range_sec, interval_sec):
    """Build timeseries buckets for metrics."""
    from datetime import timedelta

    start = now - timedelta(seconds=range_sec)
    buckets = []
    bucket_start = start

    while bucket_start < now:
        bucket_end = bucket_start + timedelta(seconds=interval_sec)
        bucket_events = [
            e for e, ts in events_with_ts
            if bucket_start <= ts < bucket_end
        ]

        completed = sum(1 for e in bucket_events if e.get("event_type") == "task_completed")
        failed = sum(1 for e in bucket_events if e.get("event_type") == "task_failed")
        durs = [e["duration_ms"] for e in bucket_events if e.get("event_type") == "task_completed" and e.get("duration_ms")]
        errors = sum(1 for e in bucket_events if e.get("event_type") in ("task_failed", "action_failed"))

        cost = 0.0
        for e in bucket_events:
            p = e.get("payload")
            if isinstance(p, dict):
                d = p.get("data")
                if isinstance(d, dict) and d.get("cost") is not None:
                    try:
                        cost += float(d["cost"])
                    except (ValueError, TypeError):
                        pass

        buckets.append({
            "timestamp": bucket_start.isoformat(),
            "tasks_completed": completed,
            "tasks_failed": failed,
            "avg_duration_ms": round(sum(durs) / len(durs)) if durs else None,
            "cost": round(cost, 4) if cost > 0 else 0,
            "error_count": errors,
            "throughput": completed,
        })

        bucket_start = bucket_end

    return buckets
