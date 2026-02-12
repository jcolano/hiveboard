"""HiveBoard API server.

POST /events      — Batch event ingestion (envelope format from spec)
GET  /api/agents  — Derived agent list with computed status
GET  /api/tasks   — Task list with latest status/duration/cost
GET  /api/events  — Activity stream (recent events)
GET  /api/timeline/{task_id} — Timeline nodes for a specific task
GET  /api/summary  — Aggregate counts for the summary bar
GET  /dashboard    — Serve the live dashboard
"""

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from db import init_db, insert_events, load_events, SEVERITY_DEFAULTS

app = FastAPI(title="HiveBoard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# For MVP, a single hardcoded tenant. API key -> tenant mapping.
API_KEY_MAP = {
    "hb_dev_key": "tenant_dev",
}


@app.on_event("startup")
def startup():
    init_db()


# ──────────────────────────────────────────────
#  EVENT INGESTION
# ──────────────────────────────────────────────

@app.post("/events")
async def ingest_events(request: Request):
    """Accept batch envelope per spec Section 3.1.

    Envelope shape:
    {
      "agent_id": "lead-qualifier",
      "agent_type": "sales",
      "environment": "production",
      "group": "default",
      "events": [
        { "event_id": "...", "timestamp": "...", "event_type": "heartbeat", ... }
      ]
    }
    """
    # Derive tenant from API key
    auth = request.headers.get("Authorization", "")
    api_key = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else auth
    tenant_id = API_KEY_MAP.get(api_key)
    if not tenant_id:
        # MVP: accept all keys, assign default tenant
        tenant_id = "tenant_dev"

    body = await request.json()
    now = datetime.now(timezone.utc).isoformat()

    # Extract envelope fields
    envelope_agent_id = body.get("agent_id")
    envelope_agent_type = body.get("agent_type")
    envelope_environment = body.get("environment", "production")
    envelope_group = body.get("group", "default")

    raw_events = body.get("events", [])
    if not raw_events:
        return {"accepted": 0}

    expanded = []
    for evt in raw_events:
        event_id = evt.get("event_id") or str(uuid.uuid4())
        event_type = evt.get("event_type", "custom")

        # Severity auto-default (spec Section 9)
        severity = evt.get("severity") or SEVERITY_DEFAULTS.get(event_type, "info")

        # Store payload as dict directly (JSON file, no serialization needed)
        payload = evt.get("payload")

        expanded.append({
            "event_id": event_id,
            "tenant_id": tenant_id,
            "agent_id": evt.get("agent_id") or envelope_agent_id,
            "agent_type": evt.get("agent_type") or envelope_agent_type,
            "timestamp": evt.get("timestamp", now),
            "received_at": now,
            "environment": evt.get("environment") or envelope_environment,
            "group": evt.get("group") or envelope_group,
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
            "payload": payload,
        })

    insert_events(expanded)
    return {"accepted": len(expanded)}


# ──────────────────────────────────────────────
#  QUERY ENDPOINTS
# ──────────────────────────────────────────────

@app.get("/api/agents")
def get_agents():
    """Derived agent list with computed status (spec Section 8).

    Status rules:
    - Most recent event_type determines status
    - Stuck = no heartbeat in 5 minutes
    """
    all_events = load_events()
    now = datetime.now(timezone.utc)

    # Group events by agent, find latest event and latest heartbeat
    agent_latest = {}  # agent_id -> latest event
    agent_heartbeats = {}  # agent_id -> latest heartbeat timestamp

    for evt in all_events:
        aid = evt.get("agent_id")
        if not aid:
            continue
        ts = evt.get("timestamp", "")

        if aid not in agent_latest or ts > agent_latest[aid]["timestamp"]:
            agent_latest[aid] = evt

        if evt.get("event_type") == "heartbeat":
            if aid not in agent_heartbeats or ts > agent_heartbeats[aid]:
                agent_heartbeats[aid] = ts

    agents = []
    for aid, evt in agent_latest.items():
        last_type = evt.get("event_type", "")

        if last_type in ("task_started", "action_started"):
            status = "processing"
        elif last_type == "task_completed":
            status = "idle"
        elif last_type == "task_failed":
            status = "error"
        elif last_type in ("approval_requested", "escalated"):
            status = "waiting_approval"
        elif last_type in ("heartbeat", "agent_registered"):
            status = "idle"
        else:
            status = "processing"

        # Stuck detection: no heartbeat in 5 minutes
        hb_age_seconds = None
        hb_ts = agent_heartbeats.get(aid)
        if hb_ts:
            try:
                hb_time = datetime.fromisoformat(hb_ts.replace("Z", "+00:00"))
                hb_age_seconds = (now - hb_time).total_seconds()
                if hb_age_seconds > 300:
                    status = "stuck"
            except (ValueError, TypeError):
                pass

        agents.append({
            "id": aid,
            "type": evt.get("agent_type"),
            "status": status,
            "task": evt.get("task_id"),
            "hb": int(hb_age_seconds) if hb_age_seconds is not None else None,
        })

    return agents


@app.get("/api/tasks")
def get_tasks(agent_id: str | None = None, limit: int = 50):
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
        task_events.setdefault(tid, []).append(evt)

    tasks = []
    for tid, events in task_events.items():
        events.sort(key=lambda e: e.get("timestamp", ""))

        # Derive status from event types present
        event_types = {e.get("event_type") for e in events}
        if "task_completed" in event_types:
            status = "completed"
        elif "task_failed" in event_types:
            status = "failed"
        elif "escalated" in event_types:
            status = "escalated"
        elif "approval_requested" in event_types:
            status = "waiting"
        else:
            status = "processing"

        # Duration and cost from terminal events
        duration_ms = None
        cost = None
        for e in events:
            if e.get("event_type") in ("task_completed", "task_failed"):
                duration_ms = e.get("duration_ms")
                payload = e.get("payload")
                if isinstance(payload, dict):
                    cost = payload.get("data", {}).get("cost") if isinstance(payload.get("data"), dict) else payload.get("cost")

        dur_str = None
        if duration_ms is not None:
            dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms < 60000 else f"{duration_ms / 60000:.1f}m"

        first = events[0]
        tasks.append({
            "id": tid,
            "agent": first.get("agent_id"),
            "type": first.get("task_type"),
            "status": status,
            "duration": dur_str,
            "duration_ms": duration_ms,
            "cost": cost,
            "time": first.get("timestamp"),
        })

    # Sort by time descending, limit
    tasks.sort(key=lambda t: t.get("time", ""), reverse=True)
    return tasks[:limit]


@app.get("/api/timeline/{task_id}")
def get_timeline(task_id: str):
    """All events for a task, ordered chronologically for timeline rendering."""
    all_events = load_events()

    task_events = [e for e in all_events if e.get("task_id") == task_id]
    task_events.sort(key=lambda e: e.get("timestamp", ""))

    nodes = []
    for evt in task_events:
        event_type = evt.get("event_type", "")
        payload = evt.get("payload")
        status = evt.get("status")

        visual_type = _event_type_to_visual(event_type, status)

        is_branch = event_type == "retry_started" and evt.get("parent_event_id") is not None
        is_branch_start = event_type in ("action_failed", "task_failed") and status == "failure"

        dur_ms = evt.get("duration_ms")
        dur_str = None
        if dur_ms is not None:
            dur_str = f"{dur_ms / 1000:.1f}s" if dur_ms < 60000 else f"{dur_ms / 60000:.1f}m"

        label = _event_label(event_type, payload, status)

        tags = []
        if isinstance(payload, dict):
            tags = payload.get("tags", [])

        nodes.append({
            "label": label,
            "time": evt.get("timestamp"),
            "type": visual_type,
            "dur": dur_str,
            "detail": payload if isinstance(payload, dict) else {},
            "tags": tags,
            "isBranch": is_branch,
            "isBranchStart": is_branch_start,
        })

    return nodes


@app.get("/api/events")
def get_events(agent_id: str | None = None, event_type: str | None = None, limit: int = 50):
    """Recent events for the activity stream."""
    all_events = load_events()

    filtered = all_events
    if agent_id:
        filtered = [e for e in filtered if e.get("agent_id") == agent_id]
    if event_type:
        filtered = [e for e in filtered if e.get("event_type") == event_type]

    # Sort by timestamp descending (most recent first)
    filtered.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    filtered = filtered[:limit]

    events = []
    for evt in filtered:
        payload = evt.get("payload")
        summary = ""
        if isinstance(payload, dict):
            summary = payload.get("summary", "")

        events.append({
            "type": evt.get("event_type"),
            "agent": evt.get("agent_id"),
            "task": evt.get("task_id"),
            "summary": summary,
            "time": evt.get("timestamp"),
            "severity": evt.get("severity"),
        })

    return events


@app.get("/api/summary")
def get_summary():
    """Aggregate counts for the summary bar."""
    agents = get_agents()
    status_counts = {}
    for a in agents:
        status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1

    all_events = load_events()
    now = datetime.now(timezone.utc)

    # Success rate: completed / (completed + failed) in last hour
    completed = 0
    failed = 0
    durations = []
    for evt in all_events:
        et = evt.get("event_type")
        if et not in ("task_completed", "task_failed"):
            continue
        # Check if within last hour
        try:
            ts = datetime.fromisoformat(evt["timestamp"].replace("Z", "+00:00"))
            if (now - ts).total_seconds() > 3600:
                continue
        except (ValueError, TypeError, KeyError):
            continue

        if et == "task_completed":
            completed += 1
            if evt.get("duration_ms") is not None:
                durations.append(evt["duration_ms"])
        elif et == "task_failed":
            failed += 1

    success_rate = round(completed / (completed + failed) * 100) if (completed + failed) > 0 else None
    avg_dur = sum(durations) / len(durations) if durations else None
    avg_dur_str = f"{avg_dur / 1000:.1f}s" if avg_dur else None

    return {
        "total_agents": len(agents),
        "processing": status_counts.get("processing", 0),
        "waiting": status_counts.get("waiting_approval", 0),
        "stuck": status_counts.get("stuck", 0),
        "errors": status_counts.get("error", 0),
        "success_rate": success_rate,
        "avg_duration": avg_dur_str,
    }


# Serve dashboard
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "docs", "hiveboard-v2.html")
    with open(dashboard_path) as f:
        return HTMLResponse(f.read())


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def _event_type_to_visual(event_type: str, status: str | None) -> str:
    mapping = {
        "agent_registered": "system",
        "heartbeat": "system",
        "task_started": "system",
        "task_completed": "success",
        "task_failed": "error",
        "action_started": "action",
        "action_completed": "action",
        "action_failed": "error",
        "retry_started": "retry",
        "escalated": "warning",
        "approval_requested": "human",
        "approval_received": "human",
        "custom": "action",
    }
    return mapping.get(event_type, "system")


def _event_label(event_type: str, payload: dict | None, status: str | None) -> str:
    if isinstance(payload, dict):
        if payload.get("summary"):
            return payload["summary"]
        if payload.get("action_name"):
            suffix = ""
            if event_type == "action_failed":
                suffix = " \u2717"
            return payload["action_name"] + suffix
    return event_type
