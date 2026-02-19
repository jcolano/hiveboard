"""
CHANNEL ADAPTERS
================

Pluggable notification channels for human delegation.

When an agent delegates a task to a human, the delegation is dispatched
through a channel adapter that handles notification delivery:

- **loopcolony**: Creates a task + DM in loopColony (default)
- **email**: Sends email with form link (requires SMTP/API config)
- **standalone**: Generates a self-contained HTML file
- **webhook**: POSTs delegation payload to a configured URL (Slack, etc.)

Usage:
    from loop_core.channels import get_channel
    channel = get_channel("loopcolony")
    channel.send_delegation(delegation_data, form_url)
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    """Base class for delegation notification channels."""

    @abstractmethod
    def send_delegation(self, delegation_data: dict, form_url: str) -> bool:
        """Notify a human about a new delegation.

        Args:
            delegation_data: Full suspension data dict
            form_url: URL to the delegation form

        Returns:
            True if notification was sent successfully.
        """

    @abstractmethod
    def send_reminder(self, delegation_data: dict, reminder_number: int) -> bool:
        """Send a reminder about a pending delegation.

        Args:
            delegation_data: Full suspension data dict
            reminder_number: Which reminder this is (1, 2, 3...)

        Returns:
            True if reminder was sent successfully.
        """


def get_channel(channel_name: str) -> Optional[ChannelAdapter]:
    """Get a channel adapter by name.

    Args:
        channel_name: One of "loopcolony", "email", "standalone", "webhook"

    Returns:
        ChannelAdapter instance, or None if not available.
    """
    channel_name = (channel_name or "loopcolony").lower()

    if channel_name == "loopcolony":
        from .loopcolony import LoopColonyChannel
        return LoopColonyChannel()
    elif channel_name == "email":
        from .email import EmailChannel
        return EmailChannel()
    elif channel_name == "standalone":
        from .standalone import StandaloneHTMLChannel
        return StandaloneHTMLChannel()
    elif channel_name == "webhook":
        from .webhook import WebhookChannel
        return WebhookChannel()
    else:
        logger.warning("Unknown channel '%s', falling back to loopcolony", channel_name)
        from .loopcolony import LoopColonyChannel
        return LoopColonyChannel()
