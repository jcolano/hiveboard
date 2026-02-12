"""Alerting engine — evaluates rules after each ingestion batch.

Supports 6 condition types: agent_stuck, task_failed, error_rate,
duration_exceeded, heartbeat_lost, cost_threshold.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from shared.enums import AgentStatus, EventType
from shared.models import AlertHistoryRecord, Event

from backend.storage_json import JsonStorageBackend, derive_agent_status

logger = logging.getLogger(__name__)


async def evaluate_alerts(
    storage: JsonStorageBackend,
    tenant_id: str,
    new_events: list[Event],
) -> None:
    """Evaluate all enabled alert rules for this tenant against the new batch."""
    rules = await storage.list_alert_rules(tenant_id, is_enabled=True)
    if not rules:
        return

    now = datetime.now(timezone.utc)

    for rule in rules:
        # Cooldown check
        last_alert = await storage.get_last_alert_for_rule(
            tenant_id, rule.rule_id
        )
        if last_alert:
            elapsed = (now - last_alert.fired_at).total_seconds()
            if elapsed < rule.cooldown_seconds:
                continue

        fired = False
        snapshot: dict[str, Any] = {}
        related_agent_id: str | None = None
        related_task_id: str | None = None

        ctype = rule.condition_type
        config = rule.condition_config

        if ctype == "agent_stuck":
            fired, snapshot, related_agent_id = await _check_agent_stuck(
                storage, tenant_id, config, now
            )

        elif ctype == "task_failed":
            fired, snapshot, related_agent_id, related_task_id = (
                _check_task_failed(new_events, config)
            )

        elif ctype == "error_rate":
            fired, snapshot = await _check_error_rate(
                storage, tenant_id, config, now
            )

        elif ctype == "duration_exceeded":
            fired, snapshot, related_task_id = _check_duration_exceeded(
                new_events, config
            )

        elif ctype == "heartbeat_lost":
            fired, snapshot, related_agent_id = await _check_heartbeat_lost(
                storage, tenant_id, config, now
            )

        elif ctype == "cost_threshold":
            fired, snapshot = await _check_cost_threshold(
                storage, tenant_id, config, now
            )

        if fired:
            alert = AlertHistoryRecord(
                alert_id=str(uuid4()),
                tenant_id=tenant_id,
                rule_id=rule.rule_id,
                project_id=rule.project_id,
                fired_at=now,
                condition_snapshot=snapshot,
                actions_taken=_dispatch_actions(rule.actions, snapshot),
                related_agent_id=related_agent_id,
                related_task_id=related_task_id,
            )
            await storage.insert_alert(tenant_id, alert)
            logger.info(
                "Alert fired: rule=%s type=%s agent=%s",
                rule.name, ctype, related_agent_id,
            )


# ───────────────────────────────────────────────────────────────────
#  CONDITION EVALUATORS
# ───────────────────────────────────────────────────────────────────

async def _check_agent_stuck(
    storage: JsonStorageBackend,
    tenant_id: str,
    config: dict,
    now: datetime,
) -> tuple[bool, dict, str | None]:
    """Check if any agent is stuck."""
    agent_id = config.get("agent_id")
    threshold = config.get("stuck_threshold_seconds", 300)

    if agent_id:
        agents_to_check = []
        agent = await storage.get_agent(tenant_id, agent_id)
        if agent:
            agents_to_check = [agent]
    else:
        agents_to_check = await storage.list_agents(tenant_id)

    for agent in agents_to_check:
        status = derive_agent_status(agent, now)
        if status == AgentStatus.STUCK:
            hb_age = None
            if agent.last_heartbeat:
                hb_age = int((now - agent.last_heartbeat).total_seconds())
            return True, {
                "agent_id": agent.agent_id,
                "threshold_seconds": threshold,
                "heartbeat_age_seconds": hb_age,
            }, agent.agent_id

    return False, {}, None


def _check_task_failed(
    new_events: list[Event],
    config: dict,
) -> tuple[bool, dict, str | None, str | None]:
    """Check if any task_failed events are in the batch."""
    for e in new_events:
        if e.event_type == EventType.TASK_FAILED:
            return True, {
                "event_id": e.event_id,
                "task_id": e.task_id,
                "agent_id": e.agent_id,
            }, e.agent_id, e.task_id
    return False, {}, None, None


async def _check_error_rate(
    storage: JsonStorageBackend,
    tenant_id: str,
    config: dict,
    now: datetime,
) -> tuple[bool, dict]:
    """Check if error rate exceeds threshold in time window."""
    threshold_pct = config.get("threshold_percent", 50)
    window_minutes = config.get("window_minutes", 60)
    since = now - timedelta(minutes=window_minutes)

    # Get all action events in window
    actions_page = await storage.get_events(
        tenant_id,
        event_type="action_started,action_completed,action_failed",
        since=since, exclude_heartbeats=True, limit=200,
    )
    total = len(actions_page.data)
    if total == 0:
        return False, {}

    failed = sum(
        1 for e in actions_page.data if e.event_type == "action_failed"
    )
    rate = (failed / total) * 100

    if rate >= threshold_pct:
        return True, {
            "error_rate_percent": round(rate, 1),
            "threshold_percent": threshold_pct,
            "total_actions": total,
            "failed_actions": failed,
            "window_minutes": window_minutes,
        }
    return False, {}


def _check_duration_exceeded(
    new_events: list[Event],
    config: dict,
) -> tuple[bool, dict, str | None]:
    """Check if any task_completed exceeds duration threshold."""
    threshold_ms = config.get("threshold_ms", 60000)
    for e in new_events:
        if (
            e.event_type == EventType.TASK_COMPLETED
            and e.duration_ms
            and e.duration_ms > threshold_ms
        ):
            return True, {
                "task_id": e.task_id,
                "duration_ms": e.duration_ms,
                "threshold_ms": threshold_ms,
            }, e.task_id
    return False, {}, None


async def _check_heartbeat_lost(
    storage: JsonStorageBackend,
    tenant_id: str,
    config: dict,
    now: datetime,
) -> tuple[bool, dict, str | None]:
    """Check if a specific agent hasn't sent a heartbeat in the window."""
    agent_id = config.get("agent_id")
    window_seconds = config.get("window_seconds", 300)
    if not agent_id:
        return False, {}, None

    agent = await storage.get_agent(tenant_id, agent_id)
    if not agent:
        return False, {}, None

    if agent.last_heartbeat is None:
        return True, {
            "agent_id": agent_id,
            "window_seconds": window_seconds,
            "last_heartbeat": None,
        }, agent_id

    age = (now - agent.last_heartbeat).total_seconds()
    if age > window_seconds:
        return True, {
            "agent_id": agent_id,
            "window_seconds": window_seconds,
            "heartbeat_age_seconds": int(age),
        }, agent_id

    return False, {}, None


async def _check_cost_threshold(
    storage: JsonStorageBackend,
    tenant_id: str,
    config: dict,
    now: datetime,
) -> tuple[bool, dict]:
    """Check if cost exceeds threshold in time window."""
    threshold_usd = config.get("threshold_usd", 10.0)
    window_hours = config.get("window_hours", 24)
    agent_id = config.get("agent_id")
    project_id = config.get("project_id")

    range_key = f"{window_hours}h" if window_hours <= 24 else "30d"
    cost = await storage.get_cost_summary(
        tenant_id,
        agent_id=agent_id,
        project_id=project_id,
        range=range_key,
    )

    if cost.total_cost >= threshold_usd:
        return True, {
            "total_cost_usd": round(cost.total_cost, 4),
            "threshold_usd": threshold_usd,
            "window_hours": window_hours,
            "call_count": cost.call_count,
        }
    return False, {}


# ───────────────────────────────────────────────────────────────────
#  ACTION DISPATCH
# ───────────────────────────────────────────────────────────────────

def _dispatch_actions(
    actions: list[dict[str, Any]],
    snapshot: dict,
) -> list[dict[str, Any]]:
    """Execute alert actions. Returns record of actions taken."""
    taken = []
    for action in actions:
        atype = action.get("type", "")

        if atype == "webhook":
            url = action.get("url", "")
            # MVP: log the webhook, don't actually POST
            logger.info("Alert webhook: POST %s with %s", url, snapshot)
            taken.append({
                "type": "webhook",
                "url": url,
                "status": "logged",
            })

        elif atype == "email":
            to = action.get("to", "")
            logger.info("Alert email: to=%s snapshot=%s", to, snapshot)
            taken.append({
                "type": "email",
                "to": to,
                "status": "logged",
            })

    return taken
