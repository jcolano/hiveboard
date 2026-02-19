"""
WEBHOOK CHANNEL
===============

Generic webhook adapter for delegation notifications.

POSTs delegation data to a configured URL. Can be used for Slack,
Microsoft Teams, Discord, or any system that accepts webhooks.

Slack-specific: formats as Block Kit message with action button pointing
to the form URL.
"""

import json
import logging
from . import ChannelAdapter

logger = logging.getLogger(__name__)


class WebhookChannel(ChannelAdapter):
    """Send delegation notifications via generic webhook."""

    def send_delegation(self, delegation_data: dict, form_url: str) -> bool:
        """POST delegation payload to the configured webhook URL."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")
        task_desc = delegation_data.get("delegation_task", "")
        deadline = delegation_data.get("deadline", "")

        # Look for webhook URL in team.json contact settings
        # For now, log the intent
        logger.info(
            "WebhookChannel: would POST delegation '%s' for '%s' to webhook. "
            "Task: %s. Form: %s",
            delegation_id, human_name, task_desc[:80], form_url,
        )

        # TODO: When webhook URL is configured:
        # payload = {
        #     "delegation_id": delegation_id,
        #     "human": human_name,
        #     "task": task_desc,
        #     "form_url": form_url,
        #     "deadline": deadline,
        # }
        # For Slack:
        # payload = {
        #     "blocks": [
        #         {"type": "header", "text": {"type": "plain_text", "text": f"Task Delegation: {task_desc[:60]}"}},
        #         {"type": "section", "text": {"type": "mrkdwn", "text": f"*For:* {human_name}\n*Deadline:* {deadline}\n\n{task_desc}"}},
        #         {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open Form"}, "url": form_url}]},
        #     ]
        # }
        # requests.post(webhook_url, json=payload, timeout=10)

        return False  # Not yet implemented

    def send_reminder(self, delegation_data: dict, reminder_number: int) -> bool:
        """POST a reminder to the webhook."""
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")

        logger.info(
            "WebhookChannel: would POST reminder #%d for delegation '%s' to '%s'",
            reminder_number, delegation_id, human_name,
        )
        return False  # Not yet implemented
