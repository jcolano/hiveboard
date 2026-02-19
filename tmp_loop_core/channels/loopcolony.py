"""
LOOPCOLONY CHANNEL
==================

Creates loopColony tasks and sends DMs for human delegation notifications.
"""

import logging
from . import ChannelAdapter

logger = logging.getLogger(__name__)


class LoopColonyChannel(ChannelAdapter):
    """Dispatch delegations via loopColony tasks and DMs."""

    def send_delegation(self, delegation_data: dict, form_url: str) -> bool:
        """Create a loopColony task assigned to the human."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", delegation_data.get("waiting_on", "unknown"))
        task_desc = delegation_data.get("delegation_task", "")

        try:
            from loop_colony.db.json_db import get_db
            import uuid
            from datetime import datetime, timezone

            db = get_db()
            human_id = delegation_data.get("waiting_on", "")

            # Find the human member in loopColony
            members = db.agents.find(name=human_name) or db.agents.find(agent_id=human_id)
            if not members:
                logger.info("LoopColonyChannel: human '%s' not found in DB", human_name)
                return False

            member = members[0]
            task_data = {
                "id": f"task_{uuid.uuid4().hex[:12]}",
                "title": f"[Delegation] {task_desc[:80]}",
                "content": f"{task_desc}\n\n---\nForm: {form_url}\nDeadline: {delegation_data.get('deadline', '')}",
                "assignee_id": member.get("id", ""),
                "workspace_id": member.get("workspace_id", ""),
                "priority": "high",
                "status": "pending",
                "due_date": delegation_data.get("deadline", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "delegation_id": delegation_id,
                    "form_url": form_url,
                    "source": "agent_delegation",
                },
            }
            db.tasks.insert(task_data)
            logger.info("LoopColonyChannel: task '%s' created for delegation '%s'", task_data["id"], delegation_id)
            return True
        except Exception as e:
            logger.info("LoopColonyChannel: task creation failed for '%s': %s", delegation_id, e)
            return False

    def send_reminder(self, delegation_data: dict, reminder_number: int) -> bool:
        """Send a DM reminder to the human via loopColony."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")
        task_desc = delegation_data.get("delegation_task", "")
        deadline = delegation_data.get("deadline", "")

        logger.info(
            "LoopColonyChannel: reminder #%d for delegation '%s' to '%s'",
            reminder_number, delegation_id, human_name,
        )

        # Best-effort DM creation
        try:
            from loop_colony.db.json_db import get_db
            db = get_db()

            human_id = delegation_data.get("waiting_on", "")
            members = db.agents.find(name=human_name) or db.agents.find(agent_id=human_id)
            if not members:
                return False

            # Create a notification
            import uuid
            from datetime import datetime, timezone

            notif = {
                "id": f"notif_{uuid.uuid4().hex[:12]}",
                "recipient_id": members[0].get("id", ""),
                "workspace_id": members[0].get("workspace_id", ""),
                "type": "delegation_reminder",
                "title": f"Reminder: {task_desc[:60]}",
                "content": f"Reminder #{reminder_number}: This task is due {deadline}.",
                "read": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {"delegation_id": delegation_id},
            }
            db.notifications.insert(notif)
            return True
        except Exception as e:
            logger.info("LoopColonyChannel: reminder failed for '%s': %s", delegation_id, e)
            return False
