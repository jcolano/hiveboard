"""HiveLoop SDK — framework-agnostic agent instrumentation.

Usage:
    import hiveloop

    hb = hiveloop.init(api_key="hb_live_...", environment="production")
    agent = hb.agent("my-agent", type="sales", version="1.0.0")

    with agent.task("task-123", project="sales-pipeline") as task:
        task.llm_call("reason", "claude-sonnet-4-20250514", tokens_in=1200, tokens_out=350, cost=0.008)
        task.plan("Process lead", ["Score", "Enrich", "Route"])
        task.plan_step(0, "completed", "Scored lead 42", turns=2, tokens=3200)

    hiveloop.shutdown()
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path
from typing import Any, Callable

from ._agent import Agent, Task, HiveLoopError, SDK_VERSION, tool_payload
from ._transport import Transport

__all__ = [
    "init",
    "shutdown",
    "reset",
    "flush",
    "HiveBoard",
    "Agent",
    "Task",
    "HiveLoopError",
    "SDK_VERSION",
    "tool_payload",
]

logger = logging.getLogger("hiveloop")

_DEFAULT_ENDPOINT = "https://mlbackend.net/loophive"


def _resolve_endpoint() -> str:
    """Resolve the backend endpoint from config file or default.

    Search order:
      1. ./loophive.cfg  (current working directory)
      2. ~/.loophive/loophive.cfg  (user home)
      3. Default production URL
    """
    candidates = [
        Path.cwd() / "loophive.cfg",
        Path.home() / ".loophive" / "loophive.cfg",
    ]
    for path in candidates:
        if path.is_file():
            cfg = configparser.ConfigParser()
            cfg.read(path)
            ep = cfg.get("loophive", "endpoint", fallback=None)
            if ep:
                logger.debug("Endpoint resolved from %s: %s", path, ep)
                return ep.strip().rstrip("/")
    return _DEFAULT_ENDPOINT


# Module-level singleton
_instance: HiveBoard | None = None


class HiveBoard:
    """HiveLoop client — manages transport, agents, and global config."""

    def __init__(
        self,
        api_key: str,
        endpoint: str | None = None,
        environment: str = "production",
        group: str = "default",
        flush_interval: float = 5.0,
        batch_size: int = 100,
        max_queue_size: int = 10_000,
        debug: bool = False,
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint or _resolve_endpoint()
        self._environment = environment
        self._group = group
        self._debug = debug

        if debug:
            logging.getLogger("hiveloop").setLevel(logging.DEBUG)

        self._transport = Transport(
            endpoint=self._endpoint,
            api_key=api_key,
            flush_interval=flush_interval,
            batch_size=batch_size,
            max_queue_size=max_queue_size,
        )

        # Agent registry
        self._agents: dict[str, Agent] = {}

    def agent(
        self,
        agent_id: str,
        type: str = "general",
        version: str | None = None,
        framework: str = "custom",
        heartbeat_interval: float = 30.0,
        stuck_threshold: int = 300,
        heartbeat_payload: Callable[[], dict[str, Any] | None] | None = None,
        queue_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> Agent:
        """Create or retrieve an agent.

        Idempotent: same agent_id returns existing instance (updates metadata).
        """
        if agent_id in self._agents:
            existing = self._agents[agent_id]
            # Update metadata if different
            existing.agent_type = type
            existing.version = version
            existing.framework = framework
            existing._heartbeat_payload_cb = heartbeat_payload
            existing._queue_provider_cb = queue_provider
            return existing

        ag = Agent(
            agent_id=agent_id,
            transport=self._transport,
            agent_type=type,
            version=version,
            framework=framework,
            heartbeat_interval=heartbeat_interval,
            stuck_threshold=stuck_threshold,
            heartbeat_payload=heartbeat_payload,
            queue_provider=queue_provider,
            environment=self._environment,
            group=self._group,
        )
        self._agents[agent_id] = ag
        ag._register()
        return ag

    def get_agent(self, agent_id: str) -> Agent | None:
        """Look up a registered agent by ID."""
        return self._agents.get(agent_id)

    def flush(self) -> None:
        """Trigger an immediate flush of all queued events."""
        self._transport.flush()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shut down all agents and transport."""
        for ag in self._agents.values():
            ag._stop_heartbeat()
        self._transport.shutdown(timeout=timeout)


def init(
    api_key: str,
    environment: str = "production",
    group: str = "default",
    endpoint: str | None = None,
    flush_interval: float = 5.0,
    batch_size: int = 100,
    max_queue_size: int = 10_000,
    debug: bool = False,
) -> HiveBoard:
    """Initialize the HiveLoop SDK singleton.

    Validates api_key starts with 'hb_'. Subsequent calls log a warning
    and return the existing instance.
    """
    global _instance

    if not api_key.startswith("hb_"):
        raise HiveLoopError(
            f"Invalid API key format: must start with 'hb_' (got '{api_key[:10]}...')"
        )

    if _instance is not None:
        logger.warning(
            "hiveloop.init() called again — returning existing instance. "
            "Call hiveloop.reset() first to reinitialize."
        )
        return _instance

    _instance = HiveBoard(
        api_key=api_key,
        endpoint=endpoint,
        environment=environment,
        group=group,
        flush_interval=flush_interval,
        batch_size=batch_size,
        max_queue_size=max_queue_size,
        debug=debug,
    )
    return _instance


def shutdown(timeout: float = 5.0) -> None:
    """Shut down the HiveLoop SDK."""
    global _instance
    if _instance is not None:
        _instance.shutdown(timeout=timeout)


def reset() -> None:
    """Shut down and clear the singleton. Allows re-initialization."""
    global _instance
    if _instance is not None:
        _instance.shutdown(timeout=5.0)
        _instance = None


def flush() -> None:
    """Flush all queued events immediately."""
    if _instance is not None:
        _instance.flush()
