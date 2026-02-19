"""
AGENT_MANAGER
=============

Central orchestration point for all agent operations in loopCore.

The AgentManager is the top-level coordinator that creates agents, wires up their
dependencies, and manages their lifecycle. It's the main entry point for running
agents from the CLI, API, or scheduler.

Responsibilities
----------------
- **Agent creation**: Loads config, creates ToolRegistry, SkillLoader, MemoryManager,
  OutputManager, and wires them into an Agent instance.
- **Tool registry**: Per-agent ToolRegistry with only the tools listed in
  ``config.json → tools.enabled``. File tools get agent-specific path sandboxing.
- **Skill loading**: Each agent gets its own SkillLoader (global + private skills).
  Global skill loader is shared; per-agent loaders are cached.
- **Rate limiting**: Per-agent rate limits (requests/sec, requests/min, max concurrent,
  token budget).
- **Output management**: Saves run results and transcripts to
  ``data/AGENTS/{agent_id}/runs/``.

Per-Agent Component Isolation
------------------------------
Each agent operates in its own directory:
::

    data/AGENTS/{agent_id}/
    ├── config.json       # Agent settings (model, tools, prompts, limits)
    ├── skills/           # Private skills (override global, support vendor dirs)
    ├── memory/           # Sessions and long-term memory
    ├── credentials.json  # API keys for external services (loopColony, etc.)
    ├── tasks/            # Scheduled tasks with state and run history
    └── runs/             # Execution output (result.json + transcript.md)

Tool Registration Logic
-----------------------
``_create_tool_registry()`` registers all tools unconditionally (every tool
is sandboxed to the agent's own directories). New tools are automatically
available to every agent without config changes.

Entry Points
------------
::

    # CLI
    python -m loop_core.cli run main "Hello"

    # API
    POST http://localhost:8431/agents/main/run {"message": "Hello"}

    # Python
    manager = AgentManager()
    result = manager.run_agent("main", "Hello!")
"""

import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)

from .config import (
    ConfigManager, AgentConfig, GlobalConfig,
    get_config_manager, load_agent_config
)
from .tools.base import ToolRegistry
from .tools.file_tools import FileReadTool, FileWriteTool
from .tools.http_tools import HttpCallTool, WebFetchTool
from .tools.task_tools import (
    TaskCreateTool, TaskListTool, TaskGetTool,
    TaskUpdateTool, TaskDeleteTool, TaskTriggerTool, TaskRunsTool
)
from .tools.feed_tools import FeedPostTool
from .tools.state_tools import SaveTaskStateTool, GetTaskStateTool
from .tools.event_tools import CreateEventTool
from .tools.search_tools import WebSearchTool
from .tools.spreadsheet_tools import CsvExportTool, SpreadsheetCreateTool
from .tools.notification_tools import SendNotificationTool
from .tools.image_tools import ImageGenerateTool
from .tools.extract_tools import DocumentExtractTool
from .tools.email_tools import EmailSendTool
from .tools.ticket_crm_tools import TicketCreateCrmTool, TicketUpdateCrmTool
from .tools.crm_tools import CrmSearchTool, CrmWriteTool
from .tools.colony_tools import ColonyReadTool, ColonyWriteTool
from .tools.aggregate_tool import AggregateTool
from .tools.compute_tool import ComputeTool
from .tools.todo_tools import TodoAddTool, TodoListTool, TodoCompleteTool, TodoRemoveTool
from .tools.issue_tools import IssueReportTool
from .tools.delegation_tools import DelegateToAgentTool, DelegateToHumanTool
from .skills import SkillLoader
from .skills.registry import AgentSkillRegistry
from .memory import MemoryManager
from .output import OutputManager
from .agent import Agent, AgentResult
from .ratelimit import RateLimiter, RateLimitExceeded
from .observability import (
    set_current_task, clear_current_task,
    set_hiveloop_agent, clear_hiveloop_agent,
)


def _summarize_turns_for_save(turns: list) -> list:
    """Summarize Turn objects for persistence — same logic as api._summarize_turns."""
    result = []
    for t in turns:
        d = {
            "number": t.number,
            "timestamp": t.timestamp,
            "duration_ms": t.duration_ms,
            "tokens": t.tokens_used.to_dict(),
            "tools": [
                {"name": tc.name, "success": tc.result.success,
                 "error": tc.result.error if not tc.result.success else None}
                for tc in t.tool_calls
            ],
            "has_text": bool(t.llm_text),
        }
        if t.plan_step_index is not None:
            d["plan_step_index"] = t.plan_step_index
            d["plan_step_description"] = t.plan_step_description
        result.append(d)
    return result


def _build_loop_result_data(result) -> Optional[Dict]:
    """Extract trace data from AgentResult.loop_result for persistence."""
    if not result.loop_result:
        return None
    lr = result.loop_result
    return {
        "execution_trace": lr.execution_trace if lr.execution_trace else [],
        "plan": lr.plan,
        "reflections": [r.to_dict() for r in lr.reflections] if lr.reflections else [],
        "turn_details": _summarize_turns_for_save(lr.turns) if lr.turns else [],
        "step_stats": lr.get_step_stats() if lr.execution_trace else [],
        "journal": lr.journal if lr.journal else [],
    }


# ============================================================================
# AGENT MANAGER
# ============================================================================

class AgentManager:
    """
    Manage agent lifecycle and coordination.

    The AgentManager provides a high-level interface for:
    - Creating agents from configuration
    - Running agents with messages
    - Managing sessions across agents
    - Storing and retrieving run outputs

    Per-agent structure:
    - Each agent has its own directory: data/AGENTS/{agent_id}/
    - Agent-specific components (SkillLoader, MemoryManager, OutputManager)
      are created when the agent is first accessed
    """

    def __init__(
        self,
        config_manager: Optional[ConfigManager] = None,
        llm_client: Any = None,
        auto_setup: bool = True
    ):
        """
        Initialize the agent manager.

        Args:
            config_manager: Optional pre-configured ConfigManager
            llm_client: Optional LLM client (will try to auto-detect if not provided)
            auto_setup: If True, automatically set up components from config
        """
        # Configuration
        self.config_manager = config_manager or get_config_manager()
        self.global_config = self.config_manager.load_global()

        # LLM client
        self.llm_client = llm_client
        if self.llm_client is None and auto_setup:
            self._setup_llm_client()

        # Global skill loader (for global skills only)
        self.global_skill_loader: Optional[SkillLoader] = None

        # Legacy components (for backward compatibility)
        self.skill_loader: Optional[SkillLoader] = None

        # Per-agent component caches
        self._agent_skill_loaders: Dict[str, SkillLoader] = {}
        self._agent_skill_registries: Dict[str, Optional[AgentSkillRegistry]] = {}
        self._agent_memory_managers: Dict[str, MemoryManager] = {}
        self._agent_output_managers: Dict[str, OutputManager] = {}

        # Rate limiter (uses config values)
        rate_cfg = self.global_config.rate_limits
        self.rate_limiter = RateLimiter(
            requests_per_second=rate_cfg.requests_per_second,
            requests_per_minute=rate_cfg.requests_per_minute,
            max_concurrent=rate_cfg.max_concurrent,
            token_budget_per_minute=rate_cfg.token_budget_per_minute
        )

        # Task scheduler reference (set via set_scheduler())
        self._scheduler: Any = None

        # Agent runtime reference (set via set_runtime())
        self._runtime: Any = None

        # HiveLoop observability handle (set via set_hiveloop())
        self._hiveloop: Any = None

        # Agent registry
        self._agents: Dict[str, Agent] = {}

        # Shared tool registry for admin panel (built once, read-only reference)
        self.tool_registry: Optional[ToolRegistry] = None

        # Auto-setup components
        if auto_setup:
            self._setup_components()

    def _setup_llm_client(self) -> None:
        """Try to set up LLM client automatically."""
        try:
            # Import the llm_client module
            import sys
            from pathlib import Path

            # Add src to path if needed
            src_path = Path(__file__).parent.parent
            if str(src_path) not in sys.path:
                sys.path.insert(0, str(src_path))

            from llm_client import get_default_client
            self.llm_client = get_default_client()
        except ImportError:
            logger.warning("Could not import llm_client module")
        except Exception as e:
            logger.error(f"Could not initialize LLM client: {e}")

    def _setup_components(self) -> None:
        """Set up framework components from configuration."""
        paths = self.global_config.paths

        # Global skill loader (for global skills only)
        try:
            self.global_skill_loader = SkillLoader(
                global_skills_dir=paths.get_global_skills_dir()
            )
            self.global_skill_loader.load_all()
            # Also set as skill_loader for backward compatibility
            self.skill_loader = self.global_skill_loader
        except Exception as e:
            logger.warning(f"Could not initialize global skill loader: {e}")

        # Trait compiler (loads trait definitions from config directory)
        self.trait_compiler = None
        try:
            from loop_core.traits import TraitCompiler
            traits_dir = Path(paths.config_dir) / "traits"
            if traits_dir.exists():
                self.trait_compiler = TraitCompiler(traits_dir)
                logger.info(
                    "Trait compiler initialized (enabled=%s, %d traits loaded)",
                    self.trait_compiler.is_enabled(),
                    len(self.trait_compiler.get_active_trait_refs()),
                )
        except Exception as e:
            logger.warning(f"Could not initialize trait compiler: {e}")

    def set_scheduler(self, scheduler: Any) -> None:
        """
        Set the task scheduler reference.

        This allows agents to use task management tools (schedule_create, schedule_list, etc.).
        Call this after creating the scheduler instance.

        Args:
            scheduler: TaskScheduler instance
        """
        self._scheduler = scheduler

    def get_scheduler(self) -> Optional[Any]:
        """Get the current scheduler reference."""
        return self._scheduler

    def set_runtime(self, runtime: Any) -> None:
        """
        Set the agent runtime reference.

        Args:
            runtime: AgentRuntime instance
        """
        self._runtime = runtime

    def get_runtime(self) -> Optional[Any]:
        """Get the current runtime reference."""
        return self._runtime

    def set_hiveloop(self, hb: Any) -> None:
        """
        Set the HiveLoop observability handle.

        Args:
            hb: HiveLoop instance from hiveloop.init()
        """
        self._hiveloop = hb

    def start_agent(self, agent_id: str) -> dict:
        """Start an agent via the runtime."""
        if self._runtime is None:
            return {"status": "error", "error": "Runtime not initialized"}
        return self._runtime.start_agent(agent_id)

    def stop_agent(self, agent_id: str) -> dict:
        """Stop an agent via the runtime."""
        if self._runtime is None:
            return {"status": "error", "error": "Runtime not initialized"}
        return self._runtime.stop_agent(agent_id)

    def get_agent_runtime_status(self, agent_id: str) -> dict:
        """Get runtime status for an agent."""
        if self._runtime is None:
            return {"active": False, "queue_depth": 0}
        return self._runtime.get_agent_status(agent_id)

    def _get_agent_skill_loader(self, agent_id: str) -> SkillLoader:
        """
        Get or create a SkillLoader for a specific agent.

        Loads both global and agent-private skills.
        """
        if agent_id not in self._agent_skill_loaders:
            paths = self.global_config.paths
            agent_dir = self.config_manager.get_agent_dir(agent_id)

            loader = SkillLoader(
                global_skills_dir=paths.get_global_skills_dir(),
                agent_skills_dir=agent_dir / "skills"
            )
            loader.load_all()
            self._agent_skill_loaders[agent_id] = loader

        return self._agent_skill_loaders[agent_id]

    def _get_agent_skill_registry(self, agent_id: str) -> Optional[AgentSkillRegistry]:
        """
        Get or create an AgentSkillRegistry for a specific agent.

        Returns None if the agent has no per-agent registry.json or if
        it uses the old SkillLoader format (has "id" instead of "name").
        """
        if agent_id not in self._agent_skill_registries:
            agent_dir = self.config_manager.get_agent_dir(agent_id)
            registry_path = agent_dir / "skills" / "registry.json"
            registry = AgentSkillRegistry.load(registry_path)
            if registry:
                registry.resolve_paths(
                    agent_skills_dir=agent_dir / "skills",
                    global_skills_dir=self.global_config.paths.get_global_skills_dir(),
                )
            self._agent_skill_registries[agent_id] = registry
        return self._agent_skill_registries[agent_id]

    def _get_agent_memory_manager(self, agent_id: str) -> MemoryManager:
        """
        Get or create a MemoryManager for a specific agent.

        Uses per-agent directory structure.
        """
        if agent_id not in self._agent_memory_managers:
            agent_dir = self.config_manager.get_agent_dir(agent_id)

            manager = MemoryManager(
                agent_dir=agent_dir,
                agent_id=agent_id
            )
            manager.load_topics()
            self._agent_memory_managers[agent_id] = manager

        return self._agent_memory_managers[agent_id]

    def _get_agent_output_manager(self, agent_id: str) -> OutputManager:
        """
        Get or create an OutputManager for a specific agent.

        Uses per-agent directory structure.
        """
        if agent_id not in self._agent_output_managers:
            agent_dir = self.config_manager.get_agent_dir(agent_id)

            manager = OutputManager(
                agent_dir=agent_dir,
                agent_id=agent_id
            )
            self._agent_output_managers[agent_id] = manager

        return self._agent_output_managers[agent_id]

    def _create_tool_registry(self, agent_config: AgentConfig) -> ToolRegistry:
        """Create a tool registry for an agent.

        All tools are registered unconditionally. Every tool is sandboxed
        to the agent's own directories, so there is no need to gate them.
        New tools are automatically available to every agent.
        """
        registry = ToolRegistry()
        paths = self.global_config.paths

        # Get per-agent directories
        agent_dir = self.config_manager.get_agent_dir(agent_config.agent_id)
        agent_id = agent_config.agent_id
        agent_memory_dir = str(agent_dir / "memory")
        agent_runs_dir = str(agent_dir / "runs")
        agent_skills_dir = str(agent_dir / "skills")

        # File read tool
        file_read_config = self.config_manager.get_tool_config("file_read")
        allowed_read = file_read_config.allowed_paths or [
            paths.skills_dir,
            agent_skills_dir,
            agent_memory_dir,
            agent_runs_dir,
            paths.sandbox_dir
        ]
        registry.register(FileReadTool(allowed_paths=allowed_read, agent_base_dir=str(agent_dir)))

        # File write tool
        file_write_config = self.config_manager.get_tool_config("file_write")
        allowed_write = file_write_config.allowed_paths or [
            agent_runs_dir,
            paths.sandbox_dir,
            agent_memory_dir
        ]
        registry.register(FileWriteTool(allowed_paths=allowed_write, agent_base_dir=str(agent_dir)))

        # HTTP call tool
        http_config = self.config_manager.get_tool_config("http_request")
        registry.register(HttpCallTool(default_timeout=http_config.timeout_seconds))

        # Web fetch tool
        web_config = self.config_manager.get_tool_config("webpage_fetch")
        registry.register(WebFetchTool(max_length=web_config.max_length))

        # Task tools (require scheduler to be set)
        if self._scheduler is not None:
            registry.register(TaskCreateTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskListTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskGetTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskUpdateTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskDeleteTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskTriggerTool(scheduler=self._scheduler, agent_id=agent_id))
            registry.register(TaskRunsTool(scheduler=self._scheduler, agent_id=agent_id))

        # Feed tools (allows agents to post to operator feed)
        registry.register(FeedPostTool(agent_id=agent_id))

        # State tools (persist data between task runs)
        registry.register(SaveTaskStateTool(agent_dir=str(agent_dir)))
        registry.register(GetTaskStateTool(agent_dir=str(agent_dir)))

        # Event tool (lets agents queue follow-up work)
        registry.register(CreateEventTool(agent_id=agent_id))

        # Search tools
        registry.register(WebSearchTool())

        # Spreadsheet tools
        registry.register(CsvExportTool(agent_dir=str(agent_dir)))
        registry.register(SpreadsheetCreateTool(agent_dir=str(agent_dir)))

        # Notification tools
        registry.register(SendNotificationTool())

        # Image tools
        registry.register(ImageGenerateTool(agent_dir=str(agent_dir)))

        # Extract tools
        registry.register(DocumentExtractTool(agent_dir=str(agent_dir)))

        # Email tools
        registry.register(EmailSendTool())

        # CRM Ticket tools
        registry.register(TicketCreateCrmTool())
        registry.register(TicketUpdateCrmTool())

        # CRM Data tools (generic read/write for all CRM entities)
        registry.register(CrmSearchTool())
        registry.register(CrmWriteTool())

        # Colony workspace tools (read/write for posts, tasks, notifications, etc.)
        registry.register(ColonyReadTool())
        registry.register(ColonyWriteTool())

        # Aggregation and computation tools (generic, not CRM-specific)
        registry.register(AggregateTool())
        registry.register(ComputeTool())

        # TO_DO queue tools (persistent per-agent task list)
        registry.register(TodoAddTool(agent_dir=str(agent_dir)))
        registry.register(TodoListTool(agent_dir=str(agent_dir)))
        registry.register(TodoCompleteTool(agent_dir=str(agent_dir)))
        registry.register(TodoRemoveTool(agent_dir=str(agent_dir)))

        # Issue reporting tool (persistent per-agent issues list)
        registry.register(IssueReportTool(agent_dir=str(agent_dir)))

        # Delegation tools (agent-to-agent and agent-to-human)
        registry.register(DelegateToAgentTool(
            agent_id=agent_id, config_manager=self.config_manager))
        from .config.loader import _find_project_root
        registry.register(DelegateToHumanTool(
            agent_id=agent_id, project_root=_find_project_root()))

        # Extension tools (drop-in .py files in EXTENSIONS/ directory)
        from .tools.extensions import load_extensions
        extensions_dir = paths.extensions_dir
        for tool in load_extensions(extensions_dir):
            if not registry.has(tool.name):
                registry.register(tool)
                source_file = getattr(tool, "_extension_source_file", "") or ""
                # Try to resolve source file from the module
                mod = sys.modules.get(type(tool).__module__)
                if mod and hasattr(mod, "__file__") and mod.__file__:
                    source_file = Path(mod.__file__).name
                registry.mark_as_extension(tool.name, source_file)
            else:
                print(f"[WARN] Extension tool '{tool.name}' skipped -- name conflicts with built-in")

        # Keep a shared reference for the admin panel GET /tools endpoint.
        # All agents register the same tools (definitions are identical), so
        # whichever agent is created first provides the reference registry.
        if self.tool_registry is None:
            self.tool_registry = registry

        return registry

    # ========================================================================
    # AGENT MANAGEMENT
    # ========================================================================

    def create_agent(self, agent_id: str, config: AgentConfig = None) -> Agent:
        """
        Create or retrieve an agent.

        Creates agent with per-agent directory structure:
        - SkillLoader: loads global + private skills
        - MemoryManager: uses agent's memory/sessions dirs
        - OutputManager: uses agent's runs dir

        Args:
            agent_id: Agent ID
            config: Optional agent configuration (loads from file if not provided)

        Returns:
            Agent instance
        """
        # Return existing agent if already created
        if agent_id in self._agents:
            return self._agents[agent_id]

        # Ensure agent directory structure exists
        self.config_manager.get_agent_dir(agent_id)

        # Load config if not provided
        if config is None:
            config = load_agent_config(agent_id)

        # Create tool registry
        tool_registry = self._create_tool_registry(config)

        # Get per-agent components
        skill_loader = self._get_agent_skill_loader(agent_id)
        skill_registry = self._get_agent_skill_registry(agent_id)
        memory_manager = self._get_agent_memory_manager(agent_id)
        output_manager = self._get_agent_output_manager(agent_id)

        # Determine reflection config: agent-specific > global
        reflection_config = config.reflection or self.global_config.reflection

        # Determine planning config: agent-specific > global
        planning_config = config.planning or self.global_config.planning

        # Create agent with per-agent components
        agent_dir = self.config_manager.get_agent_dir(agent_id)
        agent = Agent(
            agent_id=agent_id,
            config=config,
            llm_client=self.llm_client,
            tool_registry=tool_registry,
            skill_loader=skill_loader,
            skill_registry=skill_registry,
            memory_manager=memory_manager,
            output_manager=output_manager,
            reflection_config=reflection_config,
            planning_config=planning_config,
            agent_dir=str(agent_dir),
            trait_compiler=self.trait_compiler
        )

        # Pass HiveLoop prompt logging flag to the agentic loop
        agent.loop._hiveloop_log_prompts = self.global_config.hiveloop_log_prompts
        logger.debug(
            "Agent '%s' hiveloop_log_prompts=%s (global_config=%s)",
            agent_id, agent.loop._hiveloop_log_prompts, self.global_config.hiveloop_log_prompts,
        )

        # Register HiveLoop observability agent (idempotent)
        if self._hiveloop is not None:
            # Build queue_provider closure — reads runtime state lazily
            def _make_queue_provider(mgr, aid):
                def provider():
                    rt = mgr._runtime
                    if rt is None:
                        return {"depth": 0}
                    try:
                        with rt._lock:
                            st = rt._agents.get(aid)
                            if not st or not st.active:
                                return {"depth": 0}
                            return {
                                "depth": len(st.queue),
                                "oldest_age_seconds": (
                                    int((datetime.now(timezone.utc) - st.queue[0].timestamp).total_seconds())
                                    if st.queue else 0
                                ),
                                "items": [
                                    {
                                        "id": e.event_id,
                                        "priority": e.priority.name.lower(),
                                        "source": e.source,
                                        "summary": (e.title or e.message[:80]),
                                        "queued_at": e.timestamp.isoformat(),
                                    }
                                    for e in st.queue[:10]
                                ],
                                "processing": (
                                    {
                                        "id": st.current_event.event_id,
                                        "summary": (st.current_event.title or st.current_event.message[:80]),
                                        "started_at": st.current_event.timestamp.isoformat(),
                                    }
                                    if st.current_event else None
                                ),
                            }
                    except Exception:
                        return {"depth": 0}
                return provider

            # Build heartbeat_payload closure — returns agent health metrics every 30s
            def _make_heartbeat_payload(mgr, aid):
                _started_at = time.time()
                def payload():
                    ag = mgr._agents.get(aid)
                    rt = mgr._runtime
                    rt_state = rt._agents.get(aid) if rt else None
                    return {
                        "uptime_seconds": int(time.time() - _started_at),
                        "total_runs": rt_state.events_processed + rt_state.events_failed if rt_state else 0,
                        "total_tokens": getattr(ag, '_cumulative_tokens', 0) if ag else 0,
                        "total_cost": getattr(ag, '_cumulative_cost', 0.0) if ag else 0.0,
                        "consecutive_errors": rt_state.events_failed if rt_state else 0,
                        "active_skills": list(config.skills.keys()) if hasattr(config, 'skills') and config.skills else [],
                        "heartbeats_fired": rt_state.heartbeats_fired if rt_state else 0,
                        "heartbeats_skipped": rt_state.heartbeats_skipped if rt_state else 0,
                    }
                return payload

            agent._hiveloop = self._hiveloop.agent(
                agent_id=agent_id,
                type=config.role or "general",
                version=config.llm.model,
                framework="loopcore",
                heartbeat_interval=30,
                stuck_threshold=config.timeout_seconds,
                queue_provider=_make_queue_provider(self, agent_id),
                heartbeat_payload=_make_heartbeat_payload(self, agent_id),
            )

            # Gap #17: Emit config snapshot on agent registration
            try:
                agent._hiveloop.event("config_snapshot", payload={
                    "model": config.llm.model,
                    "phase2_model": getattr(config, 'phase2_model', None),
                    "temperature": getattr(config.llm, 'temperature', None),
                    "max_tokens": getattr(config.llm, 'max_tokens', None),
                    "max_turns": config.max_turns,
                    "timeout_seconds": config.timeout_seconds,
                    "role": config.role,
                    "skills": list(config.skills.keys()) if hasattr(config, 'skills') and config.skills else [],
                    "tools_enabled": list(config.tools.keys()) if hasattr(config, 'tools') and config.tools else [],
                    "planning_enabled": config.planning.enabled if hasattr(config, 'planning') and config.planning else False,
                    "reflection_enabled": config.reflection.enabled if hasattr(config, 'reflection') and config.reflection else False,
                    "learning_enabled": config.learning.enabled if hasattr(config, 'learning') and config.learning else False,
                })
            except Exception:
                pass

        # Register agent
        self._agents[agent_id] = agent
        return agent

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """
        Get an existing agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            Agent or None if not found
        """
        if agent_id not in self._agents:
            # Try to create it
            try:
                return self.create_agent(agent_id)
            except Exception:
                return None
        return self._agents.get(agent_id)

    def list_agents(self) -> List[str]:
        """List all configured agent IDs."""
        return self.config_manager.list_agents()

    def list_active_agents(self) -> List[str]:
        """List IDs of currently active (instantiated) agents."""
        return list(self._agents.keys())

    def remove_agent(self, agent_id: str) -> bool:
        """
        Remove an agent from the registry.

        Args:
            agent_id: Agent ID

        Returns:
            True if removed, False if not found
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._agent_skill_registries.pop(agent_id, None)
            self._agent_skill_loaders.pop(agent_id, None)
            self._agent_memory_managers.pop(agent_id, None)
            self._agent_output_managers.pop(agent_id, None)
            return True
        return False

    # ========================================================================
    # RUN METHODS
    # ========================================================================

    def run_agent(
        self,
        agent_id: str,
        message: str,
        session_id: Optional[str] = None,
        save_output: bool = True,
        event_context: Optional[Dict] = None,
        turn_callback: Callable[[int, List[str], int], None] = None,
        cancel_check: Callable[[], bool] = None,
        phase2_model: Optional[str] = None,
    ) -> AgentResult:
        """
        Run an agent with a message.

        Args:
            agent_id: Agent ID
            message: User message
            session_id: Optional session ID
            save_output: Whether to save output to disk
            event_context: Optional event metadata (source, priority, triggered_skills, etc.)
            turn_callback: Optional callback called after each turn
                          Args: (turn_number, tools_called_this_turn, total_tokens)
            cancel_check: Optional callable returning True when execution should stop
            phase2_model: Model for Phase 2 (None = same as Phase 1)

        Returns:
            AgentResult
        """
        # Check rate limit
        client_id = session_id or agent_id
        if not self.rate_limiter.acquire(client_id):
            retry_after = self.rate_limiter.get_retry_after()
            return AgentResult(
                agent_id=agent_id,
                session_id=session_id or "",
                status="error",
                final_response=None,
                turns=0,
                tools_called=[],
                total_tokens=0,
                total_duration_ms=0,
                error=f"Rate limit exceeded. Retry after {retry_after:.1f}s"
            )

        try:
            # Get or create agent
            agent = self.get_agent(agent_id)
            if agent is None:
                return AgentResult(
                    agent_id=agent_id,
                    session_id=session_id or "",
                    status="error",
                    final_response=None,
                    turns=0,
                    tools_called=[],
                    total_tokens=0,
                    total_duration_ms=0,
                    error=f"Agent not found: {agent_id}"
                )

            # Check LLM client
            if self.llm_client is None or not getattr(self.llm_client, 'is_initialized', False):
                return AgentResult(
                    agent_id=agent_id,
                    session_id=session_id or "",
                    status="error",
                    final_response=None,
                    turns=0,
                    tools_called=[],
                    total_tokens=0,
                    total_duration_ms=0,
                    error="LLM client not initialized"
                )

            # Determine event source for task type
            _event_source = (event_context or {}).get("source", "api")
            _task_type = _event_source.split(":")[0]  # "heartbeat", "webhook", "human", "task"

            # Wrap execution in HiveLoop task if available
            _hiveloop_agent = getattr(agent, "_hiveloop", None)
            _hiveloop_task = None
            _hiveloop_ctx = None
            if _hiveloop_agent is not None:
                _event_id = (event_context or {}).get("event_id")
                _task_id = f"{agent_id}-{_event_id}" if _event_id else f"{agent_id}-{uuid.uuid4().hex[:8]}"
                try:
                    _hiveloop_ctx = _hiveloop_agent.task(
                        _task_id,
                        project="loopcolony",
                        type=_task_type,
                    )
                    _hiveloop_task = _hiveloop_ctx.__enter__()
                    set_current_task(_hiveloop_task)
                    set_hiveloop_agent(_hiveloop_agent)
                except Exception:
                    logger.debug("HiveLoop task init failed", exc_info=True)
                    _hiveloop_ctx = None

            try:
                # Extract skill_id from event_context (set by runtime for per-skill events)
                skill_id = (event_context or {}).get("skill_id")

                # Run the agent
                result = agent.run(
                    message=message, session_id=session_id,
                    event_context=event_context,
                    turn_callback=turn_callback, cancel_check=cancel_check,
                    phase2_model=phase2_model,
                    skill_id=skill_id,
                )
            finally:
                clear_current_task()
                clear_hiveloop_agent()
                if _hiveloop_ctx is not None:
                    try:
                        _hiveloop_ctx.__exit__(None, None, None)
                    except Exception:
                        logger.debug("HiveLoop task exit failed", exc_info=True)

            # Record token usage for rate limiting
            if result.total_tokens > 0:
                self.rate_limiter.record_tokens(result.total_tokens)
        finally:
            # Always release the rate limit slot
            self.rate_limiter.release(client_id)

        # Save output if requested - use agent's output_manager
        # NOTE: This writes to data/AGENTS/{agent_id}/runs/{date}/run_{N}/
        # and has NO auto-cleanup. Runs accumulate indefinitely.
        # Task runs (scheduler) have separate cleanup (last 50 per task).
        if save_output:
            # Get the agent's output manager
            agent_output_manager = self._get_agent_output_manager(agent_id)

            conversation = []
            if result.loop_result and result.loop_result.turns:
                for turn in result.loop_result.turns:
                    if turn.llm_text:
                        conversation.append({
                            "role": "assistant",
                            "content": turn.llm_text,
                            "tool_calls": [
                                {"name": tc.name, "id": tc.id}
                                for tc in turn.tool_calls
                            ] if turn.tool_calls else []
                        })

            agent_output_manager.save_run(
                agent_id=agent_id,
                session_id=session_id or "",
                message=message,
                response=result.final_response,
                status=result.status,
                turns=result.turns,
                tools_called=result.tools_called,
                total_tokens=result.total_tokens,
                duration_ms=result.total_duration_ms,
                error=result.error,
                conversation=conversation,
                loop_result_data=_build_loop_result_data(result),
            )

        return result

    def run_with_skill(
        self,
        agent_id: str,
        message: str,
        skill_id: str,
        session_id: Optional[str] = None,
        event_context: Optional[Dict] = None,
    ) -> AgentResult:
        """Run an agent with a specific skill activated.

        Delegates to run_agent() with skill_id in event_context.
        Kept for backward compatibility (CLI, API, runtime callers).
        """
        ctx = dict(event_context or {})
        ctx["skill_id"] = skill_id
        return self.run_agent(
            agent_id=agent_id,
            message=message,
            session_id=session_id,
            event_context=ctx,
        )

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    def list_sessions(self, agent_id: str = None) -> List[Dict[str, Any]]:
        """
        List sessions, optionally filtered by agent.

        If agent_id is specified, uses that agent's memory manager.
        Otherwise, aggregates from all agents.

        Args:
            agent_id: Optional agent ID filter

        Returns:
            List of session summaries
        """
        if agent_id:
            # Get sessions from specific agent's memory manager
            memory_manager = self._get_agent_memory_manager(agent_id)
            return memory_manager.list_sessions(agent_id=agent_id)
        else:
            # Aggregate from all agents
            all_sessions = []
            for aid in self.list_agents():
                try:
                    memory_manager = self._get_agent_memory_manager(aid)
                    sessions = memory_manager.list_sessions(agent_id=aid)
                    all_sessions.extend(sessions)
                except Exception:
                    continue

            # Sort by updated_at descending
            all_sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
            return all_sessions

    def get_session(self, session_id: str, agent_id: str = None) -> Optional[Any]:
        """
        Get a session by ID.

        If agent_id is provided, searches only that agent's sessions.
        Otherwise, searches all agents.
        """
        if agent_id:
            memory_manager = self._get_agent_memory_manager(agent_id)
            return memory_manager.load_session(session_id)
        else:
            # Search all agents for the session
            for aid in self.list_agents():
                try:
                    memory_manager = self._get_agent_memory_manager(aid)
                    session = memory_manager.load_session(session_id)
                    if session:
                        return session
                except Exception:
                    continue
        return None

    def delete_session(self, session_id: str, agent_id: str = None) -> bool:
        """
        Delete a session.

        If agent_id is provided, deletes from that agent's sessions.
        Otherwise, searches all agents for the session.
        """
        if agent_id:
            memory_manager = self._get_agent_memory_manager(agent_id)
            return memory_manager.delete_session(session_id)
        else:
            # Search all agents for the session
            for aid in self.list_agents():
                try:
                    memory_manager = self._get_agent_memory_manager(aid)
                    if memory_manager.delete_session(session_id):
                        return True
                except Exception:
                    continue
        return False

    # ========================================================================
    # OUTPUT MANAGEMENT
    # ========================================================================

    def list_runs(
        self,
        agent_id: str = None,
        date: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List runs with optional filtering.

        If agent_id is specified, uses that agent's output manager.
        Otherwise, aggregates from all agents.

        Args:
            agent_id: Filter by agent ID
            date: Filter by date (YYYY-MM-DD)
            limit: Maximum runs to return

        Returns:
            List of run summaries
        """
        if agent_id:
            # Get runs from specific agent's output manager
            output_manager = self._get_agent_output_manager(agent_id)
            return output_manager.list_runs(agent_id=agent_id, date=date, limit=limit)
        else:
            # Aggregate from all agents
            all_runs = []
            for aid in self.list_agents():
                try:
                    output_manager = self._get_agent_output_manager(aid)
                    runs = output_manager.list_runs(date=date, limit=limit)
                    all_runs.extend(runs)
                except Exception:
                    continue

            # Sort by timestamp and limit
            all_runs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return all_runs[:limit]

    def get_run(self, agent_id: str, date: str, run_id: str) -> Optional[Any]:
        """Get a specific run."""
        output_manager = self._get_agent_output_manager(agent_id)
        return output_manager.load_run(agent_id, date, run_id)

    def get_transcript(self, agent_id: str, date: str, run_id: str) -> Optional[str]:
        """Get the transcript for a run."""
        output_manager = self._get_agent_output_manager(agent_id)
        return output_manager.get_transcript(agent_id, date, run_id)

    # ========================================================================
    # INFO METHODS
    # ========================================================================

    def get_status(self) -> Dict[str, Any]:
        """Get manager status."""
        # Count memory topics across all active agents
        total_memory_topics = 0
        for agent_id in self._agent_memory_managers:
            try:
                total_memory_topics += len(self._agent_memory_managers[agent_id].list_topics())
            except Exception:
                pass

        return {
            "llm_initialized": self.llm_client is not None and getattr(self.llm_client, 'is_initialized', False),
            "llm_provider": getattr(self.llm_client, 'provider', None) if self.llm_client else None,
            "configured_agents": self.list_agents(),
            "active_agents": self.list_active_agents(),
            "skills_loaded": len(self.skill_loader.list_skills()) if self.skill_loader else 0,
            "memory_topics": total_memory_topics
        }

    def get_agent_info(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an agent (includes runtime status)."""
        agent = self.get_agent(agent_id)
        if agent:
            info = agent.get_info()
            runtime_status = self.get_agent_runtime_status(agent_id)
            info["active"] = runtime_status.get("active", False)
            info["queue_depth"] = runtime_status.get("queue_depth", 0)
            return info
        return None


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_agent_manager: Optional[AgentManager] = None


def get_agent_manager(
    config_manager: Optional[ConfigManager] = None,
    llm_client: Any = None
) -> AgentManager:
    """Get or create the global agent manager."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager(
            config_manager=config_manager,
            llm_client=llm_client
        )
    return _agent_manager


def run_agent(agent_id: str, message: str, session_id: str = None) -> AgentResult:
    """Convenience function to run an agent."""
    manager = get_agent_manager()
    return manager.run_agent(agent_id, message, session_id)
