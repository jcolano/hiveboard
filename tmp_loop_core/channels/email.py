"""
EMAIL CHANNEL
=============

Sends delegation notifications via email with form link.
Requires email configuration in config.json or team.json contact settings.
"""

import logging
from . import ChannelAdapter

logger = logging.getLogger(__name__)


class EmailChannel(ChannelAdapter):
    """Send delegation notifications via email."""

    def send_delegation(self, delegation_data: dict, form_url: str) -> bool:
        """Send an email to the human with the delegation task and form link."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")
        task_desc = delegation_data.get("delegation_task", "")
        deadline = delegation_data.get("deadline", "")

        # Look for email in team.json contact info
        # For now, log the intent -- email sending requires SMTP config
        logger.info(
            "EmailChannel: would send email to '%s' for delegation '%s'. "
            "Task: %s. Form: %s. Deadline: %s",
            human_name, delegation_id, task_desc[:80], form_url, deadline,
        )

        # TODO: Implement actual email sending when SMTP config is available
        # subject = f"Task Delegation: {task_desc[:60]}"
        # body = f"""
        # Hi {human_name},
        #
        # An agent has delegated a task to you:
        #
        # {task_desc}
        #
        # Please complete the form at: {form_url}
        # Deadline: {deadline}
        # """
        # send_email(to=email, subject=subject, body=body)

        return False  # Not yet implemented

    def send_reminder(self, delegation_data: dict, reminder_number: int) -> bool:
        """Send an email reminder."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")

        logger.info(
            "EmailChannel: would send reminder #%d email to '%s' for delegation '%s'",
            reminder_number, human_name, delegation_id,
        )
        return False  # Not yet implemented
