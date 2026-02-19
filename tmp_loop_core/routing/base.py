"""
OUTPUT ROUTING — Base Classes
==============================

Plugin interface for routing agent responses to external channels
(loopColony, Slack, email, etc.).

Each plugin implements ``OutputPlugin`` and handles delivery to one channel.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OutputRouteConfig:
    """Routing instructions attached to an AgentEvent."""
    channel: Optional[str] = None    # Plugin name, e.g. "loopcolony"
    to: Optional[str] = None         # Destination within channel, e.g. "post:123"
    deliver: bool = True             # False = skip delivery
    name: Optional[str] = None       # Label for logging


@dataclass
class DeliveryResult:
    """Result of a plugin delivery attempt."""
    success: bool
    channel: str
    destination: Optional[str] = None
    detail: Optional[str] = None


class OutputPlugin(ABC):
    """Abstract base for output routing plugins."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Unique channel identifier, e.g. 'loopcolony'."""
        ...

    @abstractmethod
    def deliver(
        self,
        agent_id: str,
        response_text: str,
        to: Optional[str],
        agents_dir: str,
    ) -> DeliveryResult:
        """
        Deliver an agent response to the external channel.

        Args:
            agent_id: The agent that produced the response.
            response_text: The LLM response text.
            to: Destination within the channel (plugin-specific).
            agents_dir: Path to data/AGENTS directory (for credentials).

        Returns:
            DeliveryResult with success/failure info.
        """
        ...
