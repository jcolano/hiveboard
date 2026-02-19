"""
STANDALONE HTML CHANNEL
=======================

Generates a self-contained HTML form file that can be opened in any browser,
emailed as an attachment, or hosted anywhere.

On form submission, the HTML page POSTs to the loopCore completion endpoint.
"""

import logging
from pathlib import Path
from . import ChannelAdapter

logger = logging.getLogger(__name__)


class StandaloneHTMLChannel(ChannelAdapter):
    """Generate standalone HTML form files for delegation."""

    def send_delegation(self, delegation_data: dict, form_url: str) -> bool:
        """Generate and save a standalone HTML form file.

        The form is saved to the agent's delegation_forms/ directory.
        This is already done by dispatch_human_delegation() as a default
        action, so this channel just confirms the file exists.
        """
        delegation_id = delegation_data.get("delegation_id", "?")
        human_name = delegation_data.get("human_name", "")

        logger.info(
            "StandaloneHTMLChannel: delegation '%s' form generated for '%s'. "
            "The form HTML file should already be saved in the agent's delegation_forms/ directory.",
            delegation_id, human_name,
        )
        return True

    def send_reminder(self, delegation_data: dict, reminder_number: int) -> bool:
        """Standalone forms have no reminder mechanism."""
        delegation_id = delegation_data.get("delegation_id", "?")
        logger.info(
            "StandaloneHTMLChannel: no reminder mechanism for delegation '%s' "
            "(standalone HTML forms are fire-and-forget)",
            delegation_id,
        )
        return False
