"""HiveLoop — Agent observability SDK for HiveBoard.

Usage:
    import hiveloop

    hb = hiveloop.init(api_key="hb_live_...", environment="production")
    agent = hb.agent("my-agent", type="sales")

    with agent.task("task-123", type="lead_processing") as task:
        result = do_work()
        task.event("scored", payload={"summary": "Lead scored 42"})

    hiveloop.shutdown()

Spec Part B, Sections 8–14.
"""

import logging

from ._agent import Agent, Task, HiveLoopError, HiveLoopConfigError
from ._transport import Transport

__all__ = [
    "init", "shutdown", "reset",
    "HiveBoard", "Agent", "Task",
    "HiveLoopError", "HiveLoopConfigError",
]

logger = logging.getLogger("hiveloop")

_instance: "HiveBoard | None" = None


def init(
    api_key: str,
    environment: str = "production",
    group: str = "default",
    endpoint: str = "http://localhost:8000",
    flush_interval: float = 5.0,
    batch_size: int = 100,
    max_queue_size: int = 10000,
    debug: bool = False,
) -> "HiveBoard":
    """Initialize the HiveLoop client singleton. Spec Section 8.2."""
    global _instance

    if _instance is not None:
        logger.warning("hiveloop.init() called again — returning existing instance. Call hiveloop.reset() first to reinitialize.")
        return _instance

    if not api_key or not api_key.startswith("hb_"):
        raise HiveLoopConfigError("api_key must start with 'hb_'")

    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")

    _instance = HiveBoard(
        api_key=api_key,
        environment=environment,
        group=group,
        endpoint=endpoint,
        flush_interval=flush_interval,
        batch_size=batch_size,
        max_queue_size=max_queue_size,
        debug=debug,
    )
    return _instance


def shutdown(timeout: float = 5.0):
    """Graceful shutdown — flush remaining events, stop threads. Spec Section 13.4."""
    global _instance
    if _instance is not None:
        _instance._shutdown(timeout)


def reset():
    """Shutdown + clear singleton. For testing. Spec Section 8.3."""
    global _instance
    if _instance is not None:
        _instance._shutdown(timeout=5.0)
        _instance = None


class HiveBoard:
    """Client instance — manages agents and transport. Spec Section 16.2."""

    def __init__(self, api_key: str, environment: str, group: str, endpoint: str,
                 flush_interval: float, batch_size: int, max_queue_size: int, debug: bool):
        self._api_key = api_key
        self._environment = environment
        self._group = group
        self._debug = debug

        self._transport = Transport(
            endpoint=endpoint,
            api_key=api_key,
            flush_interval=flush_interval,
            batch_size=batch_size,
            max_queue_size=max_queue_size,
            debug=debug,
        )

        self._agents: dict[str, Agent] = {}

    def agent(self, agent_id: str, type: str = "general", version: str | None = None,
              framework: str = "custom", heartbeat_interval: int = 30,
              stuck_threshold: int = 300) -> Agent:
        """Register or retrieve an agent. Spec Section 9.1.

        Calling with the same agent_id returns the existing instance.
        """
        if agent_id in self._agents:
            existing = self._agents[agent_id]
            # Update metadata if different
            existing.type = type
            existing.version = version
            existing.framework = framework
            existing._envelope["agent_type"] = type
            existing._envelope["agent_version"] = version
            existing._envelope["framework"] = framework
            return existing

        a = Agent(self, agent_id, type=type, version=version, framework=framework,
                  heartbeat_interval=heartbeat_interval, stuck_threshold=stuck_threshold)
        self._agents[agent_id] = a
        return a

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve a registered agent by ID. Returns None if not found."""
        return self._agents.get(agent_id)

    def flush(self):
        """Force immediate flush of queued events."""
        self._transport.flush()

    def _shutdown(self, timeout: float = 5.0):
        """Stop heartbeats and flush."""
        for a in self._agents.values():
            a.stop_heartbeat()
        self._transport.shutdown(timeout=timeout)
