"""
HUMAN DELEGATION
================

Dispatch logic for human delegation.

When an agent delegates a task to a human, this module handles the
"extra step": saving a form file and notifying the human through
the configured channel.

Form generation and rendering live in ``forms.py`` -- this module
is a consumer, not a generator.
"""

import logging
from pathlib import Path
from typing import Optional

from .forms import render_form_html

logger = logging.getLogger(__name__)


def dispatch_human_delegation(
    suspension_data: dict,
    delegation_id: str,
    agent_dir: Optional[str] = None,
) -> None:
    """Dispatch a human delegation via the configured channel.

    This is the "extra step" for human delegation -- after the agent's plan
    is suspended, we create the form and notify the human.

    Args:
        suspension_data: Full suspension data dict from the delegation tool
        delegation_id: The delegation ID
        agent_dir: Agent's data directory (for saving form file)
    """
    channel = suspension_data.get("channel", "loopcolony")
    form_schema = suspension_data.get("form_schema", {})
    human_name = suspension_data.get("human_name", suspension_data.get("waiting_on", "unknown"))

    logger.info(
        "Dispatching human delegation '%s' to '%s' via channel '%s'",
        delegation_id, human_name, channel,
    )

    # Always save the form HTML to the agent's directory for standalone access
    if agent_dir:
        forms_dir = Path(agent_dir) / "delegation_forms"
        forms_dir.mkdir(parents=True, exist_ok=True)
        html = render_form_html(
            form_schema,
            form_id=delegation_id,
            footer_text=f"Delegation ID: {delegation_id}",
            success_message="Your response has been recorded. The agent will resume its work.",
        )
        form_path = forms_dir / f"{delegation_id}.html"
        form_path.write_text(html, encoding="utf-8")
        logger.info("Saved delegation form to %s", form_path)

    # Channel-specific dispatch via channel adapters
    form_url = f"/delegation/{delegation_id}/form"
    try:
        from .channels import get_channel
        adapter = get_channel(channel)
        if adapter:
            adapter.send_delegation(suspension_data, form_url)
        else:
            logger.info(
                "No channel adapter for '%s', delegation '%s' form available at GET %s",
                channel, delegation_id, form_url,
            )
    except Exception as e:
        logger.warning("Channel dispatch failed for delegation '%s': %s", delegation_id, e)
