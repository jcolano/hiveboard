"""
OUTPUT ROUTER
==============

Dispatches agent responses to registered output plugins.
"""

import logging
from typing import Dict, Optional

from .base import DeliveryResult, OutputPlugin, OutputRouteConfig

logger = logging.getLogger(__name__)


class OutputRouter:
    """Routes agent responses to external channels via plugins."""

    def __init__(self, agents_dir: str):
        self._agents_dir = agents_dir
        self._plugins: Dict[str, OutputPlugin] = {}

    def register(self, plugin: OutputPlugin) -> None:
        """Register a plugin by its channel name."""
        self._plugins[plugin.channel_name] = plugin
        logger.info(f"Output plugin registered: {plugin.channel_name}")

    def route(
        self,
        agent_id: str,
        response_text: str,
        routing: OutputRouteConfig,
    ) -> Optional[DeliveryResult]:
        """
        Route a response to the appropriate plugin.

        Returns None if routing is skipped (no channel, deliver=False, etc.).
        """
        if routing is None or not routing.deliver or not routing.channel:
            return None

        plugin = self._plugins.get(routing.channel)
        if plugin is None:
            logger.warning(f"No plugin for channel '{routing.channel}'")
            return DeliveryResult(
                success=False,
                channel=routing.channel,
                destination=routing.to,
                detail=f"No plugin registered for channel '{routing.channel}'",
            )

        try:
            result = plugin.deliver(
                agent_id=agent_id,
                response_text=response_text,
                to=routing.to,
                agents_dir=self._agents_dir,
            )
            if result.success:
                logger.info(f"Routed to {routing.channel}: {result.detail}")
            else:
                logger.warning(f"Delivery failed for {routing.channel}: {result.detail}")
            return result
        except Exception as e:
            logger.error(f"Plugin error ({routing.channel}): {e}")
            return DeliveryResult(
                success=False,
                channel=routing.channel,
                destination=routing.to,
                detail=f"Plugin exception: {e}",
            )
