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
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from db import init_db, insert_events, get_db, SEVERITY_DEFAULTS

app = FastAPI(title="HiveBoard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# For MVP, a single hardcoded tenant. API key → tenant mapping.
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

        # Serialize payload to JSON string
        payload = evt.get("payload")
        payload_str = json.dumps(payload) if payload is not None else None

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
            "payload": payload_str,
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
    with get_db() as conn:
        # Get distinct agents with their latest event
        rows = conn.execute("""
            SELECT
                e.agent_id,
                e.agent_type,
                e.event_type AS last_event_type,
                e.task_id AS current_task,
                e.timestamp AS last_event_time,
                hb.last_heartbeat
            FROM events e
            INNER JOIN (
                SELECT agent_id, MAX(timestamp) AS max_ts
                FROM events
                GROUP BY agent_id
            ) latest ON e.agent_id = latest.agent_id AND e.timestamp = latest.max_ts
            LEFT JOIN (
                SELECT agent_id, MAX(timestamp) AS last_heartbeat
                FROM events
                WHERE event_type = 'heartbeat'
                GROUP BY agent_id
            ) hb ON e.agent_id = hb.agent_id
            ORDER BY e.agent_id
        """).fetchall()

    now = datetime.now(timezone.utc)
    agents = []
    for row in rows:
        # Compute status from last event type
        last_type = row["last_event_type"]
        if last_type in ("task_started", "action_started"):
            status = "processing"
        elif last_type == "task_completed":
            status = "idle"
        elif last_type == "task_failed":
            status = "error"
        elif last_type == "approval_requested":
            status = "waiting_approval"
        elif last_type == "escalated":
            status = "waiting_approval"
        elif last_type in ("heartbeat", "agent_registered"):
            status = "idle"
        else:
            status = "processing"

        # Stuck detection: no heartbeat in 5 minutes
        hb_age_seconds = None
        if row["last_heartbeat"]:
            try:
                hb_time = datetime.fromisoformat(row["last_heartbeat"].replace("Z", "+00:00"))
                hb_age_seconds = (now - hb_time).total_seconds()
                if hb_age_seconds > 300:
                    status = "stuck"
            except (ValueError, TypeError):
                pass

        agents.append({
            "id": row["agent_id"],
            "type": row["agent_type"],
            "status": status,
            "task": row["current_task"] if status == "processing" else row["current_task"],
            "hb": int(hb_age_seconds) if hb_age_seconds is not None else None,
        })

    return agents


@app.get("/api/tasks")
def get_tasks(agent_id: str | None = None, limit: int = 50):
    """Task list derived from task lifecycle events."""
    with get_db() as conn:
        query = """
            SELECT
                task_id,
                agent_id,
                task_type,
                MAX(CASE WHEN event_type = 'task_completed' THEN 'completed'
                         WHEN event_type = 'task_failed' THEN 'failed'
                         WHEN event_type = 'escalated' THEN 'escalated'
                         WHEN event_type = 'approval_requested' THEN 'waiting'
                         ELSE 'processing' END) AS status,
                MAX(CASE WHEN event_type IN ('task_completed', 'task_failed')
                    THEN duration_ms END) AS duration_ms,
                MIN(timestamp) AS started_at,
                MAX(timestamp) AS last_event_at,
                MAX(CASE WHEN event_type IN ('task_completed', 'task_failed')
                    THEN json_extract(payload, '$.data.cost')
                    END) AS cost
            FROM events
            WHERE task_id IS NOT NULL
        """
        params = []
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)

        query += " GROUP BY task_id ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

    tasks = []
    for row in rows:
        dur_ms = row["duration_ms"]
        if dur_ms is not None:
            duration_str = f"{dur_ms / 1000:.1f}s" if dur_ms < 60000 else f"{dur_ms / 60000:.1f}m"
        else:
            duration_str = None

        tasks.append({
            "id": row["task_id"],
            "agent": row["agent_id"],
            "type": row["task_type"],
            "status": row["status"],
            "duration": duration_str,
            "duration_ms": dur_ms,
            "cost": row["cost"],
            "time": row["started_at"],
        })

    return tasks


@app.get("/api/timeline/{task_id}")
def get_timeline(task_id: str):
    """All events for a task, ordered chronologically for timeline rendering."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                event_id, agent_id, event_type, timestamp,
                duration_ms, status, severity, payload,
                action_id, parent_action_id, parent_event_id
            FROM events
            WHERE task_id = ?
            ORDER BY timestamp ASC
        """, (task_id,)).fetchall()

    nodes = []
    for row in rows:
        payload = None
        if row["payload"]:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Map event_type to visual type for the timeline
        event_type = row["event_type"]
        visual_type = _event_type_to_visual(event_type, row["status"])

        # Determine if this is an error branch node
        is_branch = event_type in ("retry_started",) and row["parent_event_id"] is not None
        is_branch_start = event_type in ("action_failed", "task_failed") and row["status"] == "failure"

        dur_ms = row["duration_ms"]
        dur_str = None
        if dur_ms is not None:
            dur_str = f"{dur_ms / 1000:.1f}s" if dur_ms < 60000 else f"{dur_ms / 60000:.1f}m"

        label = _event_label(event_type, payload, row["status"])

        nodes.append({
            "label": label,
            "time": row["timestamp"],
            "type": visual_type,
            "dur": dur_str,
            "detail": payload or {},
            "tags": payload.get("tags", []) if payload else [],
            "isBranch": is_branch,
            "isBranchStart": is_branch_start,
        })

    return nodes


@app.get("/api/events")
def get_events(agent_id: str | None = None, event_type: str | None = None, limit: int = 50):
    """Recent events for the activity stream."""
    with get_db() as conn:
        query = "SELECT * FROM events WHERE 1=1"
        params = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

    events = []
    for row in rows:
        payload = None
        if row["payload"]:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                pass

        summary = ""
        if payload:
            summary = payload.get("summary", "")

        events.append({
            "type": row["event_type"],
            "agent": row["agent_id"],
            "task": row["task_id"],
            "summary": summary,
            "time": row["timestamp"],
            "severity": row["severity"],
        })

    return events


@app.get("/api/summary")
def get_summary():
    """Aggregate counts for the summary bar."""
    agents = get_agents()
    status_counts = {}
    for a in agents:
        status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1

    with get_db() as conn:
        # Success rate: completed / (completed + failed) in last hour
        row = conn.execute("""
            SELECT
                COUNT(CASE WHEN event_type = 'task_completed' THEN 1 END) AS completed,
                COUNT(CASE WHEN event_type = 'task_failed' THEN 1 END) AS failed
            FROM events
            WHERE event_type IN ('task_completed', 'task_failed')
              AND timestamp >= datetime('now', '-1 hour')
        """).fetchone()

        completed = row["completed"] or 0
        failed = row["failed"] or 0
        success_rate = round(completed / (completed + failed) * 100) if (completed + failed) > 0 else None

        # Average duration of completed tasks in last hour
        avg_row = conn.execute("""
            SELECT AVG(duration_ms) as avg_dur
            FROM events
            WHERE event_type = 'task_completed'
              AND duration_ms IS NOT NULL
              AND timestamp >= datetime('now', '-1 hour')
        """).fetchone()
        avg_dur = avg_row["avg_dur"]
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
    import os
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
    if payload and payload.get("summary"):
        return payload["summary"]
    if payload and payload.get("action_name"):
        suffix = ""
        if event_type == "action_failed":
            suffix = " \u2717"
        elif event_type == "action_completed":
            suffix = ""
        return payload["action_name"] + suffix
    # Fallback to event_type
    return event_type
