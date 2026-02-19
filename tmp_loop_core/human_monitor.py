"""
HUMAN DELEGATION MONITOR
========================

Independent daemon thread that monitors pending human delegations.

Responsibilities:
- Periodically scan all agent directories for suspended human delegations
- Send reminders when interval is due
- Handle timeouts when deadline expires (fires callback to resume agent)

NOT tied to any agent's heartbeat -- this is a standalone monitor thread
managed by AgentRuntime.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class HumanDelegationMonitor:
    """Monitor for pending human delegations with reminders and timeouts."""

    def __init__(self, agents_dir, runtime, check_interval: int = 300):
        """
        Args:
            agents_dir: Path to data/AGENTS directory
            runtime: AgentRuntime instance (for firing callbacks)
            check_interval: Seconds between checks (default 5 minutes)
        """
        self._agents_dir = Path(agents_dir)
        self._runtime = runtime
        self._check_interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Start the monitor daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="human-delegation-monitor",
        )
        self._thread.start()
        logger.info("HumanDelegationMonitor started (interval=%ds)", self._check_interval)

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("HumanDelegationMonitor stopped")

    def _loop(self) -> None:
        """Main loop: check delegations, wait, repeat."""
        while not self._stop.is_set():
            try:
                self._check_all_delegations()
            except Exception:
                logger.exception("Human delegation monitor error")
            self._stop.wait(self._check_interval)

    def _check_all_delegations(self) -> None:
        """Scan all agent directories for pending human delegations."""
        if not self._agents_dir.exists():
            return

        for agent_dir in self._agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            suspended_file = agent_dir / "suspended_delegation.json"
            if not suspended_file.exists():
                continue
            try:
                data = json.loads(suspended_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if data.get("type") != "human":
                continue

            self._process_delegation(agent_dir, data)

    def _process_delegation(self, agent_dir: Path, data: dict) -> None:
        """Process a single human delegation: check timeout, send reminders."""
        now = datetime.now(timezone.utc)
        delegation_id = data.get("delegation_id", "?")

        # Parse deadline
        deadline_str = data.get("deadline", "")
        try:
            deadline = datetime.fromisoformat(deadline_str)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.warning("Invalid deadline for delegation '%s': %s", delegation_id, deadline_str)
            return

        # Timeout check
        if now >= deadline:
            self._handle_timeout(agent_dir, data)
            return

        # Reminder check
        reminder_config = data.get("reminder_config", {})
        if self._reminder_due(now, reminder_config, deadline):
            self._send_reminder(data, reminder_config)
            self._update_reminder_state(agent_dir, data)

    def _reminder_due(self, now: datetime, config: dict, deadline: datetime) -> bool:
        """Check if a reminder should be sent now."""
        max_reminders = config.get("max_reminders", 3)
        sent = config.get("reminders_sent", 0)
        if sent >= max_reminders:
            return False

        interval_hours = config.get("interval_hours", 6)
        last_reminder = config.get("last_reminder_at")

        if last_reminder:
            try:
                last_dt = datetime.fromisoformat(last_reminder)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                hours_since = (now - last_dt).total_seconds() / 3600
                return hours_since >= interval_hours
            except (ValueError, TypeError):
                return True
        else:
            # No reminder sent yet -- check if enough time has passed since delegation
            # Send first reminder after interval_hours
            return True

    def _send_reminder(self, data: dict, config: dict) -> None:
        """Send a reminder about a pending delegation via channel adapter."""
        delegation_id = data.get("delegation_id", "?")
        human_name = data.get("human_name", data.get("waiting_on", "unknown"))
        task = data.get("delegation_task", "")
        deadline = data.get("deadline", "")
        sent = config.get("reminders_sent", 0)

        logger.info(
            "Sending reminder #%d for delegation '%s' to '%s': %s (deadline: %s)",
            sent + 1, delegation_id, human_name, task[:80], deadline,
        )

        # Dispatch via channel adapter
        try:
            from .channels import get_channel
            channel = data.get("channel", "loopcolony")
            adapter = get_channel(channel)
            if adapter:
                adapter.send_reminder(data, sent + 1)
        except Exception as e:
            logger.warning("Reminder dispatch failed for delegation '%s': %s", delegation_id, e)

    def _update_reminder_state(self, agent_dir: Path, data: dict) -> None:
        """Update the reminder counter and timestamp in the suspension file."""
        config = data.get("reminder_config", {})
        config["reminders_sent"] = config.get("reminders_sent", 0) + 1
        config["last_reminder_at"] = datetime.now(timezone.utc).isoformat()
        data["reminder_config"] = config

        suspended_file = agent_dir / "suspended_delegation.json"
        try:
            suspended_file.write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to update reminder state: %s", e)

    def _handle_timeout(self, agent_dir: Path, data: dict) -> None:
        """Handle a timed-out human delegation -- fire timeout callback."""
        delegation_id = data.get("delegation_id", "?")
        agent_id = agent_dir.name
        human_id = data.get("waiting_on", "unknown")
        human_name = data.get("human_name", human_id)
        deadline_hours = data.get("deadline_hours", "?")

        logger.warning(
            "Human delegation '%s' TIMED OUT: '%s' did not respond within %sh",
            delegation_id, human_name, deadline_hours,
        )

        # Fire timeout callback to resume the agent
        self._runtime._fire_delegation_callback(
            delegation_id=delegation_id,
            callback_agent=agent_id,
            status="timeout",
            result=f"Human '{human_name}' did not respond before the {deadline_hours}h deadline.",
            from_entity=f"human:{human_id}",
        )

        # Clean up the suspension file
        from .delegation import delete_suspended_delegation
        delete_suspended_delegation(str(agent_dir))
