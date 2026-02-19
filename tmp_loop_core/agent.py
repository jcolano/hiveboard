"""
AGENT
=====

Single agent wrapper for the Agentic Loop Framework.

An Agent bundles together all the components needed to execute a task:
configuration, tools, skills, memory, and the agentic loop itself.

Components (wired by AgentManager)
-----------------------------------
- **config**: AgentConfig — system prompt, model, max_turns, enabled_tools
- **tool_registry**: ToolRegistry — sandboxed per-agent (only enabled tools)
- **skill_loader**: SkillLoader — loads global + agent-private skills
- **memory_manager**: MemoryManager — session persistence + long-term memory
- **loop**: AgenticLoop — core execution engine (prompt → LLM → tool → repeat)
- **reflection/planning/learning**: Optional capabilities (configured per-agent)

Execution Flow (agent.run)
--------------------------
1. Check for user directives (memory commands like "remember X")
2. Load or create session (conversation history)
3. If ``skill_id`` is provided, load that single skill's content inline
   via ``build_single_skill_prompt()`` or ``build_skill_content_prompt()``
4. Build memory prompt (relevant memories with recency/relevance boost)
5. Execute AgenticLoop (prompt → LLM → tool calls → repeat until done)
6. Scan response for facts to remember (TurnScanner)
7. Update session with new messages
8. On session end: review conversation for long-term memories

Skill Loading
--------------
When ``skill_id`` is passed to ``run()``, only that single skill's content
is loaded inline into the system prompt (no all-skills loading, ever).
When ``skill_id`` is absent, no skills are loaded into the system prompt
(tools are always available regardless). ``run_with_skill()`` is a thin
redirect to ``run(skill_id=...)`` kept for backward compatibility.

Per-Agent Directory Structure
------------------------------
::

    data/AGENTS/{agent_id}/
    ├── config.json       # Model, tools, prompts, limits
    ├── skills/           # Private skills (can override global)
    ├── memory/           # Sessions + long-term memory store
    ├── credentials.json  # External service credentials
    ├── tasks/            # Scheduled tasks
    └── runs/             # Execution history

Usage::

    agent = Agent(agent_id="main", config=config, llm_client=client,
                  tool_registry=registry, skill_loader=loader,
                  memory_manager=memory)
    result = agent.run("Check my loopColony tasks", session_id="sess_001")
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

from .config import AgentConfig, ReflectionConfig, PlanningConfig, LearningConfig, get_config_manager
from .tools.base import ToolRegistry, ToolResult
from .tools.file_tools import FileReadTool, FileWriteTool
from .tools.http_tools import HttpCallTool, WebFetchTool
from .skills import SkillLoader
from .memory import MemoryManager
from .memory.decision import (
    UserDirectiveHandler,
    SessionEndReviewer,
    MemoryDecay,
    TurnScanner,
    is_session_end_command,
    parse_memory_command,
)
from .loop import AgenticLoop, LoopResult
from .tools.todo_tools import get_pending_todos_prompt
from .tools.issue_tools import get_open_issues_prompt
from .observability import get_current_task, get_hiveloop_agent, estimate_cost

logger = logging.getLogger(__name__)


# ============================================================================
# HEARTBEAT SUMMARY (cross-heartbeat context)
# ============================================================================

@dataclass
class HeartbeatSummary:
    """Summary of a single heartbeat run, persisted for cross-heartbeat context."""
    timestamp: str
    skills_triggered: List[str]
    turn_count: int
    status: str
    summary_lines: List[str]  # One line per turn, from Haiku
    total_tokens: int

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "skills_triggered": self.skills_triggered,
            "turn_count": self.turn_count,
            "status": self.status,
            "summary_lines": self.summary_lines,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "HeartbeatSummary":
        return cls(
            timestamp=data.get("timestamp", ""),
            skills_triggered=data.get("skills_triggered", []),
            turn_count=data.get("turn_count", 0),
            status=data.get("status", ""),
            summary_lines=data.get("summary_lines", []),
            total_tokens=data.get("total_tokens", 0),
        )


def _load_heartbeat_history(agent_dir: str) -> List[HeartbeatSummary]:
    """Load heartbeat history from disk. Returns [] on missing/corrupt."""
    path = Path(agent_dir) / "heartbeat_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [HeartbeatSummary.from_dict(entry) for entry in data]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("Could not load heartbeat_history.json: %s", e)
        return []


def _save_heartbeat_history(
    agent_dir: str, history: List[HeartbeatSummary], max_entries: int = 50
) -> None:
    """Save heartbeat history to disk with rolling cap."""
    path = Path(agent_dir) / "heartbeat_history.json"
    # Cap to max_entries
    if len(history) > max_entries:
        history = history[-max_entries:]
    try:
        path.write_text(
            json.dumps([h.to_dict() for h in history], indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Could not save heartbeat_history.json: %s", e)


def _summarize_heartbeat_journal(
    llm_client, journal: List[Dict], skills_triggered: List[str]
) -> List[str]:
    """Call Haiku to condense journal into single-line summaries.

    Falls back to mechanical extraction from phase1_decision entries on failure.
    """
    # Build journal text for Haiku
    journal_lines = []
    for entry in journal:
        event = entry.get("event", "")
        turn = entry.get("turn", "?")
        if event == "phase1_decision":
            tool = entry.get("tool") or "none"
            intent = (entry.get("intent") or "")[:120]
            journal_lines.append(f"T{turn}: Phase1 -> tool={tool}, intent={intent}")
        elif event == "tool_result":
            tool = entry.get("tool") or "?"
            success = entry.get("success", False)
            output = (entry.get("output_preview") or "")[:200]
            journal_lines.append(f"T{turn}: {tool} {'OK' if success else 'FAIL'}: {output}")
        elif event == "early_exit":
            resp = (entry.get("response_text") or "")[:200]
            journal_lines.append(f"T{turn}: Done: {resp}")
        elif event == "loop_exit":
            status = entry.get("status", "")
            journal_lines.append(f"Loop exit: {status}")

    if not journal_lines:
        return []

    journal_text = "\n".join(journal_lines)
    skills_str = ", ".join(skills_triggered) if skills_triggered else "none"

    prompt = (
        "Condense this agent heartbeat journal into exactly one summary line per turn.\n"
        "Each line should be <120 chars and capture: what tool was called, what it found or did, and whether it succeeded.\n"
        "Do NOT add commentary, headers, or blank lines. Output ONLY the summary lines, one per line.\n\n"
        f"Skills triggered: {skills_str}\n\n"
        f"Journal:\n{journal_text}"
    )

    try:
        from llm_client import get_client_for_model
        haiku_client = get_client_for_model("claude-3-haiku-20240307")
        _llm_start = time.perf_counter()
        response = haiku_client.complete(
            prompt=prompt,
            system="You are a concise summarizer. Output only summary lines.",
            caller="heartbeat_summary",
            max_tokens=1024,
        )
        _llm_elapsed = (time.perf_counter() - _llm_start) * 1000

        # HiveLoop: Heartbeat summary LLM call tracking
        _task = get_current_task()
        if _task:
            try:
                _hb_in = getattr(haiku_client, '_last_input_tokens', 0)
                _hb_out = getattr(haiku_client, '_last_output_tokens', 0)
                _task.llm_call(
                    "heartbeat_summary",
                    model=haiku_client.model,
                    tokens_in=_hb_in,
                    tokens_out=_hb_out,
                    cost=estimate_cost(haiku_client.model, _hb_in, _hb_out),
                    duration_ms=round(_llm_elapsed),
                )
            except Exception:
                pass

        if response:
            # Filter out empty lines and lines that are clearly not summaries
            lines = [
                line.strip() for line in response.strip().split("\n")
                if line.strip() and len(line.strip()) > 5
            ]
            return lines
    except Exception as e:
        logger.warning("Haiku summarization failed, using fallback: %s", e)

    # Fallback: mechanical extraction from phase1_decision entries
    fallback_lines = []
    for entry in journal:
        if entry.get("event") == "phase1_decision":
            turn = entry.get("turn", "?")
            tool = entry.get("tool") or "none"
            step = (entry.get("step_summary") or "")[:80]
            if tool and tool != "none":
                fallback_lines.append(f"T{turn}: {tool} -- {step}")
            elif entry.get("done"):
                resp = (entry.get("response_text") or "")[:80]
                fallback_lines.append(f"T{turn}: Done -- {resp}")
    return fallback_lines


def _build_heartbeat_context_string(
    history: List[HeartbeatSummary], count: int
) -> Optional[str]:
    """Format last N heartbeat summaries for prompt injection."""
    if not history or count <= 0:
        return None

    recent = history[-count:]
    parts = []
    for i, summary in enumerate(recent):
        skills = ", ".join(summary.skills_triggered) if summary.skills_triggered else "none"
        header = (
            f"[{summary.timestamp}] skills={skills} "
            f"turns={summary.turn_count} status={summary.status}"
        )
        lines = "\n".join(f"  {line}" for line in summary.summary_lines)
        parts.append(f"{header}\n{lines}")

    return "\n".join(parts)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class AgentResult:
    """Result of an agent run."""
    agent_id: str
    session_id: str
    status: str  # completed, timeout, max_turns, error
    final_response: Optional[str]
    turns: int
    tools_called: List[str]
    total_tokens: int
    total_duration_ms: int
    error: Optional[str] = None
    loop_result: Optional[LoopResult] = None
    pending_events: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "status": self.status,
            "final_response": self.final_response,
            "turns": self.turns,
            "tools_called": self.tools_called,
            "total_tokens": self.total_tokens,
            "total_duration_ms": self.total_duration_ms,
            "error": self.error
        }
        if self.pending_events:
            result["pending_events"] = self.pending_events
        return result


# ============================================================================
# AGENT CLASS
# ============================================================================

class Agent:
    """
    An autonomous agent with tools, skills, and memory.

    The Agent class brings together all framework components:
    - AgentConfig: Agent-specific settings
    - ToolRegistry: Available tools
    - SkillLoader: Available skills
    - MemoryManager: Session and long-term memory
    - AgenticLoop: Core execution engine
    """

    def __init__(
        self,
        agent_id: str,
        config: AgentConfig,
        llm_client: Any,
        tool_registry: Optional[ToolRegistry] = None,
        skill_loader: Optional[SkillLoader] = None,
        skill_registry: Optional["AgentSkillRegistry"] = None,
        memory_manager: Optional[MemoryManager] = None,
        output_manager: Any = None,
        reflection_config: Optional[ReflectionConfig] = None,
        planning_config: Optional[PlanningConfig] = None,
        learning_config: Optional[LearningConfig] = None,
        agent_dir: Optional[str] = None,
        trait_compiler: Any = None
    ):
        """
        Initialize an agent.

        Args:
            agent_id: Unique identifier for this agent
            config: Agent configuration
            llm_client: LLM client for API calls
            tool_registry: Pre-configured tool registry (optional)
            skill_loader: Pre-configured skill loader (optional)
            memory_manager: Pre-configured memory manager (optional)
            output_manager: Pre-configured output manager (optional)
            reflection_config: Reflection configuration (optional, uses agent config or global)
            planning_config: Planning configuration (optional, uses agent config or global)
            learning_config: Learning configuration (optional, uses agent config or global)
        """
        self.agent_id = agent_id
        self.config = config
        self.llm_client = llm_client
        self.output_manager = output_manager
        self.agent_dir = agent_dir
        self.trait_compiler = trait_compiler

        # Initialize or use provided components
        self.tool_registry = tool_registry or ToolRegistry()
        self.skill_loader = skill_loader
        self.skill_registry = skill_registry
        self.memory_manager = memory_manager

        # Determine reflection config: explicit > agent config > default
        self.reflection_config = reflection_config or config.reflection or ReflectionConfig()

        # Determine planning config: explicit > agent config > default
        self.planning_config = planning_config or config.planning or PlanningConfig()

        # Determine learning config: explicit > agent config > global default
        self.learning_config = learning_config or getattr(config, 'learning', None) or LearningConfig()

        # Get memory path from memory manager if available
        memory_path = None
        if self.memory_manager:
            memory_path = str(self.memory_manager.base_path) if hasattr(self.memory_manager, 'base_path') else None

        # Create the agentic loop with reflection, planning, and learning support
        self.loop = AgenticLoop(
            llm_client=llm_client,
            tool_registry=self.tool_registry,
            max_turns=config.max_turns,
            timeout_seconds=config.timeout_seconds,
            reflection_config=self.reflection_config,
            planning_config=self.planning_config,
            learning_config=self.learning_config,
            memory_path=memory_path,
            agent_id=agent_id
        )

        # Set agent context on LLM client for usage attribution
        if hasattr(self.llm_client, 'set_context'):
            self.llm_client.set_context(agent_id=agent_id, agent_name=self.name)

        # Memory decision handlers
        self.directive_handler: Optional[UserDirectiveHandler] = None
        self.session_reviewer: Optional[SessionEndReviewer] = None
        self.memory_decay: Optional[MemoryDecay] = None
        self.turn_scanner: Optional[TurnScanner] = None

        if self.memory_manager:
            self.directive_handler = UserDirectiveHandler(self.memory_manager)
            self.memory_decay = MemoryDecay(self.memory_manager)
            self.turn_scanner = TurnScanner(self.memory_manager)
            if self.llm_client:
                self.session_reviewer = SessionEndReviewer(
                    self.llm_client,
                    self.memory_manager
                )

        # Statistics
        self.total_runs = 0
        self.active_sessions: Dict[str, str] = {}  # session_id -> status

    @property
    def name(self) -> str:
        """Get agent name."""
        return self.config.name or self.agent_id

    @property
    def description(self) -> str:
        """Get agent description."""
        return self.config.description

    @property
    def role(self) -> str:
        """Get agent role."""
        return self.config.role

    @property
    def system_prompt(self) -> str:
        """Get agent system prompt."""
        return self.config.system_prompt

    @property
    def enabled_tools(self) -> List[str]:
        """Get list of enabled tool names."""
        return self.config.enabled_tools

    # ========================================================================
    # RUN METHODS
    # ========================================================================

    def run(
        self,
        message: str,
        session_id: Optional[str] = None,
        conversation_history: Optional[List[Dict]] = None,
        additional_context: Optional[str] = None,
        event_context: Optional[Dict] = None,
        turn_callback: Callable[[int, List[str], int], None] = None,
        cancel_check: Callable[[], bool] = None,
        phase2_model: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> AgentResult:
        """
        Run the agent with a message.

        Args:
            message: User message to process
            session_id: Optional session ID for persistence
            conversation_history: Optional conversation history
            additional_context: Optional additional context to include
            event_context: Optional event metadata (source, priority, triggered_skills, etc.)
            turn_callback: Optional callback called after each turn
                          Args: (turn_number, tools_called_this_turn, total_tokens)
            cancel_check: Optional callable returning True when execution should stop
            phase2_model: Model for Phase 2 (None = same as Phase 1)
            skill_id: Optional skill ID to load into system prompt (single skill, inline)

        Returns:
            AgentResult with response and metadata
        """
        self.total_runs += 1

        # Set agent context on shared LLM client at run start (not just init)
        if hasattr(self.llm_client, 'set_context'):
            self.llm_client.set_context(agent_id=self.agent_id, agent_name=self.name)

        # Set skill context for usage attribution
        if skill_id and hasattr(self.llm_client, 'set_context'):
            self.llm_client.set_context(skill_id=skill_id)
        elif event_context and hasattr(self.llm_client, 'set_context'):
            triggered = event_context.get("triggered_skills", [])
            if triggered:
                self.llm_client.set_context(skill_id=",".join(triggered))
            elif not self.llm_client.skill_id:
                # No skill for this run — clear any previous
                self.llm_client.skill_id = None

        # ====================================================================
        # CHECK FOR USER DIRECTIVES (before processing)
        # ====================================================================
        directive_acknowledgment = None
        if self.directive_handler:
            directive_result = self.directive_handler.check_for_directive(message)
            if directive_result.found:
                directive_acknowledgment = directive_result.message

        # ====================================================================
        # CHECK FOR SESSION END COMMAND
        # ====================================================================
        is_ending_session = is_session_end_command(message)

        # ====================================================================
        # CHECK FOR MEMORY QUERY COMMANDS
        # ====================================================================
        memory_response = None
        memory_cmd = parse_memory_command(message)
        if memory_cmd and self.memory_manager:
            cmd, arg = memory_cmd
            if cmd == "list_all":
                # List all stored memories
                topics = self.memory_manager.list_topics()
                if topics:
                    lines = ["Here's what I remember:"]
                    for topic_data in topics:
                        topic_id = topic_data.get("id") if isinstance(topic_data, dict) else topic_data
                        index = self.memory_manager.get_topic_index(topic_id)
                        if index and index.entries:
                            lines.append(f"\n**{topic_id}:**")
                            for entry in index.entries[:5]:  # Limit to 5 per topic
                                lines.append(f"  - {entry.get('summary', 'Untitled')}")
                            if len(index.entries) > 5:
                                lines.append(f"  - ...and {len(index.entries) - 5} more")
                    memory_response = "\n".join(lines)
                else:
                    memory_response = "I don't have any stored memories yet."
            elif cmd == "forget" and arg:
                memory_response = f"To forget specific memories, please use the CLI command: memory-consolidate"
            elif cmd == "clear_all":
                memory_response = "Clearing all memories requires confirmation. Please use the CLI for this operation."

        # If pure memory query, return early
        if memory_response:
            return AgentResult(
                agent_id=self.agent_id,
                session_id=session_id or "",
                status="completed",
                final_response=memory_response,
                turns=0,
                tools_called=[],
                total_tokens=0,
                total_duration_ms=0,
                error=None,
                loop_result=None
            )

        # Load or create session
        session = None
        if session_id and self.memory_manager:
            session = self.memory_manager.load_session(session_id)
            if session is None:
                session = self.memory_manager.create_session(
                    agent_id=self.agent_id,
                    session_id=session_id
                )
            self.active_sessions[session_id] = "active"

            # Gap #10: Report session load to HiveLoop
            from .observability import get_current_task
            _task = get_current_task()
            if _task and session:
                try:
                    _task.event("session_loaded", payload={
                        "session_id": session_id,
                        "messages": len(session.conversation) if session.conversation else 0,
                    })
                except Exception:
                    pass

            # Use session conversation if no history provided
            if conversation_history is None:
                conversation_history = session.conversation

        # Determine run source early (needed for skills prompt + planning decisions)
        # Sources may be colon-separated (e.g. "webhook:wake"), extract the base.
        source = event_context.get("source", "human") if event_context else "human"
        source_base = source.split(":")[0]

        # Build skills prompt: single skill (inline) or no skills
        # When skill_id is provided, load that skill's content directly into
        # the system prompt. When absent, no skills are loaded — tools are
        # always available regardless of skills.
        skills_prompt = ""
        if skill_id:
            if self.skill_registry:
                skills_prompt = self.skill_registry.build_single_skill_prompt(skill_id)
            if not skills_prompt and self.skill_loader:
                skills_prompt = self.skill_loader.build_skill_content_prompt(skill_id)

        # Build memory prompt (with boost on access)
        memory_prompt = ""
        if self.memory_manager:
            memory_prompt = self._build_memory_prompt_with_boost(message)

        # Add additional context if provided
        if additional_context:
            if memory_prompt:
                memory_prompt = f"{memory_prompt}\n\n{additional_context}"
            else:
                memory_prompt = additional_context

        # Inject pending TO_DO items so the agent is aware of outstanding work
        if self.agent_dir:
            todo_prompt = get_pending_todos_prompt(self.agent_dir)
            if todo_prompt:
                if memory_prompt:
                    memory_prompt = f"{memory_prompt}\n\n{todo_prompt}"
                else:
                    memory_prompt = todo_prompt

        # Inject open issues so the agent knows about unresolved problems
        if self.agent_dir:
            issues_prompt = get_open_issues_prompt(self.agent_dir)
            if issues_prompt:
                if memory_prompt:
                    memory_prompt = f"{memory_prompt}\n\n{issues_prompt}"
                else:
                    memory_prompt = issues_prompt

        # Inject loopColony credentials so the agent always has exact values
        # (critical for atomic mode where Phase 2 needs precise tool params)
        if self.agent_dir:
            creds_path = Path(self.agent_dir) / "memory" / "loopcolony.json"
            if creds_path.exists():
                try:
                    import json
                    creds = json.loads(creds_path.read_text(encoding="utf-8"))

                    # Ensure last_sync_at is always present for heartbeat sync.
                    # If missing (first run ever), seed with now - 48h so the first
                    # sync catches up on recent workspace activity.
                    if "last_sync_at" not in creds:
                        default_since = (
                            datetime.now(timezone.utc) - timedelta(hours=48)
                        ).isoformat()
                        creds["last_sync_at"] = default_since

                    # Auto-populate mail_account_id from DB if missing.
                    # The claim-invitation response doesn't include it, so
                    # loopcolony.json often lacks it. Look it up once and persist.
                    if not creds.get("mail_account_id"):
                        try:
                            from loop_colony.db.json_db import get_db as _get_colony_db
                            _cdb = _get_colony_db()
                            _accts = _cdb.mail_accounts.find(
                                owner_id=creds.get("member_id", ""),
                                workspace_id=creds.get("workspace_id", ""),
                            )
                            if _accts:
                                _default = next(
                                    (a for a in _accts if a.get("is_default")),
                                    _accts[0],
                                )
                                creds["mail_account_id"] = _default["id"]
                                # Persist so we don't look it up every run
                                creds_path.write_text(
                                    json.dumps(creds, indent=2), encoding="utf-8"
                                )
                        except Exception:
                            pass  # loopColony DB may not be available

                    creds_prompt = (
                        "YOUR LOOPCOLONY CREDENTIALS (use these exact values for crm_search, crm_write, http_request, email_send):\n"
                        f"  base_url: {creds.get('base_url', '')}\n"
                        f"  auth_token: {creds.get('auth_token', '')}\n"
                        f"  workspace_id: {creds.get('workspace_id', '')}\n"
                        f"  member_id: {creds.get('member_id', '')}\n"
                        f"  mail_account_id: {creds.get('mail_account_id', '')}\n"
                        f"  last_sync_at: {creds.get('last_sync_at', '')}"
                    )
                    if memory_prompt:
                        memory_prompt = f"{memory_prompt}\n\n{creds_prompt}"
                    else:
                        memory_prompt = creds_prompt

                    # Pre-load credentials on colony/CRM/email tools so they
                    # don't depend on the LLM to provide correct values (Phase 2
                    # often hallucinates placeholders like "YOUR_AUTH_TOKEN").
                    _tool_creds = {
                        "base_url": creds.get("base_url", ""),
                        "auth_token": creds.get("auth_token", ""),
                        "workspace_id": creds.get("workspace_id", ""),
                        "mail_account_id": creds.get("mail_account_id", ""),
                    }
                    for _tool in self.loop.tool_registry._tools.values():
                        _tool.set_credentials(**_tool_creds)

                except Exception:
                    pass

        # Build identity block
        identity_prompt = self._build_identity_block(event_context, session_id)

        # Set execution context on queue_followup_event tool if present
        queue_followup_event_tool = self.loop.tool_registry.get("queue_followup_event")
        if queue_followup_event_tool:
            source = event_context.get("source", "unknown") if event_context else "human"
            queue_followup_event_tool.set_execution_context(source)

        p2_model = phase2_model or getattr(self.config, 'phase2_model', None)

        # Compile behavioral trait prompt
        traits_prompt = ""
        if self.trait_compiler and self.config.traits:
            traits_prompt = self.trait_compiler.compile(
                agent_id=self.agent_id,
                traits_block=self.config.traits.to_dict(),
            )

        # ====================================================================
        # PRE-LOOP: TODO REVIEW (heartbeat runs only)
        # ====================================================================
        # On heartbeat runs, if there are pending TODO items, run a short
        # mini-loop focused on clearing the backlog before the main task.
        pre_loop_result = None
        if self.agent_dir and source == "heartbeat":
            from .tools.todo_tools import _load_todos
            pre_todos = _load_todos(self.agent_dir)
            pending_before = [t for t in pre_todos if t.get("status") == "pending"]
            if pending_before:
                todo_review_msg = (
                    "PRIORITY: Review your pending TO-DO items before regular duties. "
                    "For each pending item, try to complete it now using the appropriate tools. "
                    "Verify each action succeeded by checking tool results. "
                    "If you cannot complete an item (missing permissions, missing account, "
                    "API error), escalate to the human operator via feed_post or send_dm_notification. "
                    "Use todo_complete to mark items done, todo_remove for irrelevant items."
                )
                original_max_turns = self.loop.max_turns
                self.loop.max_turns = min(10, original_max_turns)
                try:
                    pre_loop_result = self.loop.execute(
                        message=todo_review_msg,
                        system_prompt=self.system_prompt,
                        identity_prompt=identity_prompt,
                        skills_prompt=skills_prompt,
                        memory_prompt=memory_prompt,
                        traits_prompt=traits_prompt,
                        conversation_history=[],
                        enabled_tools=None,
                        cancel_check=cancel_check,
                        phase2_model=p2_model,
                        _skip_planning=True,
                    )
                    logger.info(
                        f"TODO pre-loop for '{self.agent_id}': "
                        f"{len(pre_loop_result.turns)} turns, "
                        f"{pre_loop_result.total_tokens.total} tokens, "
                        f"tools: {pre_loop_result.tools_called}"
                    )
                except Exception as e:
                    logger.warning(f"TODO pre-loop error for '{self.agent_id}' (non-fatal): {e}")
                finally:
                    self.loop.max_turns = original_max_turns

        # Build cross-heartbeat context for heartbeat runs
        heartbeat_context_str = None
        if source == "heartbeat" and self.agent_dir:
            hb_count = getattr(self.config, 'heartbeat_context_count', 3)
            if hb_count > 0:
                hb_history = _load_heartbeat_history(self.agent_dir)
                heartbeat_context_str = _build_heartbeat_context_string(hb_history, hb_count)

        # ====================================================================
        # DELEGATION: Depth-limit delegation tools for delegation runs
        # ====================================================================
        delegate_tool = self.loop.tool_registry.get("delegate_to_agent")
        delegate_human_tool = self.loop.tool_registry.get("delegate_to_human")
        if event_context and event_context.get("delegation_depth", 0) >= 1:
            if delegate_tool:
                delegate_tool._is_delegate = True
            if delegate_human_tool:
                delegate_human_tool._is_delegate = True

        # ====================================================================
        # DELEGATION: Check for delegation resume (result arriving back)
        # ====================================================================
        _restore_plan = None
        _restore_state = None
        if event_context and event_context.get("delegation_result"):
            from .delegation import load_suspended_delegation, delete_suspended_delegation
            if self.agent_dir:
                suspended = load_suspended_delegation(self.agent_dir)
                if suspended and suspended.get("delegation_id") == event_context.get("delegation_id"):
                    _restore_plan = suspended.get("plan")
                    _restore_state = suspended.get("atomic_state")
                    # Inject delegation result into restored state variables
                    if _restore_state:
                        _restore_state.setdefault("variables", {})["delegation_result"] = (
                            event_context.get("result", "")[:2000]
                        )
                    delete_suspended_delegation(self.agent_dir)
                    logger.info(
                        "[%s] Resuming from delegation '%s'",
                        self.agent_id, event_context.get("delegation_id"),
                    )

        # Execute the main loop (planning is gated by should_plan() heuristic)
        logger.debug("agent.run() calling loop.execute(), source=%s, agent=%s", source, self.agent_id)
        loop_result = self.loop.execute(
            message=message,
            system_prompt=self.system_prompt,
            identity_prompt=identity_prompt,
            skills_prompt=skills_prompt,
            memory_prompt=memory_prompt,
            traits_prompt=traits_prompt,
            conversation_history=conversation_history or [],
            enabled_tools=None,
            turn_callback=turn_callback,
            cancel_check=cancel_check,
            phase2_model=p2_model,
            heartbeat_context=heartbeat_context_str,
            _skip_planning=bool(_restore_plan),
            _restore_plan=_restore_plan,
            _restore_state=_restore_state,
        )

        # Reset delegate tool state for next run
        if delegate_tool:
            delegate_tool._is_delegate = False
        if delegate_human_tool:
            delegate_human_tool._is_delegate = False

        # ====================================================================
        # DELEGATION: Handle suspended status (agent or human delegation)
        # ====================================================================
        if loop_result.status == "suspended":
            # Find which delegation tool fired (agent or human)
            active_deleg_tool = None
            if delegate_tool and delegate_tool.get_suspension_data():
                active_deleg_tool = delegate_tool
            elif delegate_human_tool and delegate_human_tool.get_suspension_data():
                active_deleg_tool = delegate_human_tool

            if active_deleg_tool:
                suspension_data = active_deleg_tool.get_suspension_data()

                # Attach plan + atomic state to suspension data
                if loop_result.plan:
                    suspension_data["plan"] = loop_result.plan
                if loop_result.metadata and loop_result.metadata.get("atomic_state"):
                    suspension_data["atomic_state"] = loop_result.metadata["atomic_state"]

                if self.agent_dir:
                    from .delegation import save_suspended_delegation
                    save_suspended_delegation(self.agent_dir, suspension_data)

                # Human delegation "extra step": dispatch form + notification
                if suspension_data.get("type") == "human":
                    try:
                        from .human_delegation import dispatch_human_delegation
                        dispatch_human_delegation(
                            suspension_data,
                            suspension_data["delegation_id"],
                            self.agent_dir,
                        )
                    except Exception as e:
                        logger.error(
                            "[%s] Failed to dispatch human delegation: %s",
                            self.agent_id, e,
                        )

                # Collect delegation event for cross-agent routing
                pending_events = []
                delegation_event = active_deleg_tool.get_delegation_event()
                if delegation_event:
                    pending_events.append(delegation_event)

                # Also collect any queue_followup_event events created before suspension
                if queue_followup_event_tool:
                    pending_events.extend(queue_followup_event_tool.collect_events())

                active_deleg_tool.reset()

                # Return early -- skip post-loop processing
                return AgentResult(
                    agent_id=self.agent_id,
                    session_id=session_id or "",
                    status="suspended",
                    final_response=loop_result.final_response,
                    turns=len(loop_result.turns),
                    tools_called=list(loop_result.tools_called),
                    total_tokens=loop_result.total_tokens.total,
                    total_duration_ms=loop_result.total_duration_ms,
                    error=None,
                    loop_result=loop_result,
                    pending_events=pending_events,
                )

        # Run summary log
        logger.info(
            "[%s] Run complete: status=%s turns=%d tools=%s tokens=%d duration=%dms",
            self.agent_id, loop_result.status, len(loop_result.turns),
            loop_result.tools_called, loop_result.total_tokens.total,
            loop_result.total_duration_ms,
        )

        # Save cross-heartbeat summary for heartbeat runs
        if source == "heartbeat" and self.agent_dir and loop_result.journal:
            try:
                triggered_skills = [skill_id] if skill_id else (
                    event_context.get("triggered_skills", []) if event_context else []
                )
                summary_lines = _summarize_heartbeat_journal(
                    self.llm_client, loop_result.journal, triggered_skills,
                )
                new_summary = HeartbeatSummary(
                    timestamp=datetime.now().isoformat(),
                    skills_triggered=triggered_skills,
                    turn_count=len(loop_result.turns),
                    status=loop_result.status,
                    summary_lines=summary_lines,
                    total_tokens=loop_result.total_tokens.total,
                )
                hb_history = _load_heartbeat_history(self.agent_dir)
                hb_history.append(new_summary)
                _save_heartbeat_history(self.agent_dir, hb_history)
                logger.info(
                    "[%s] Heartbeat summary saved: %d lines",
                    self.agent_id, len(summary_lines),
                )
            except Exception as e:
                logger.warning(
                    "[%s] Failed to save heartbeat summary (non-fatal): %s",
                    self.agent_id, e,
                )

        # Auto-save last_sync_at after heartbeat so next sync starts here
        if source == "heartbeat" and self.agent_dir:
            try:
                _creds_path = Path(self.agent_dir) / "memory" / "loopcolony.json"
                if _creds_path.exists():
                    import json as _json
                    _creds = _json.loads(_creds_path.read_text(encoding="utf-8"))
                    _creds["last_sync_at"] = datetime.now(timezone.utc).isoformat()
                    _creds_path.write_text(
                        _json.dumps(_creds, indent=2), encoding="utf-8"
                    )
                    logger.debug("[%s] Saved last_sync_at for next heartbeat", self.agent_id)
            except Exception as e:
                logger.warning("[%s] Failed to save last_sync_at (non-fatal): %s", self.agent_id, e)

        # AUTO-TODO: Record failed tasks for retry on next heartbeat
        if self.agent_dir and loop_result.status not in ("completed",):
            try:
                from .tools.todo_tools import _load_todos, _save_todos, _next_id
                from datetime import datetime as _dt, timezone as _tz
                todos = _load_todos(self.agent_dir)

                # Dedup: don't add if the same task text already exists as pending
                task_text = f"RETRY: {message[:200]}"
                already_exists = any(
                    t.get("task") == task_text and t.get("status") == "pending"
                    for t in todos
                )
                if not already_exists:
                    context_parts = [f"status={loop_result.status}"]
                    if loop_result.error:
                        context_parts.append(f"error={loop_result.error[:200]}")
                    if loop_result.turns:
                        last_turn = loop_result.turns[-1]
                        tools_used = [tc.name for tc in last_turn.tool_calls] if last_turn.tool_calls else []
                        if tools_used:
                            context_parts.append(f"last_tools={','.join(tools_used)}")

                    # Capture plan state so the retry has full context
                    if loop_result.plan:
                        plan = loop_result.plan
                        steps = plan.get("steps", [])
                        completed = [s for s in steps if s.get("status") == "completed"]
                        remaining = [s for s in steps if s.get("status") != "completed"]
                        if completed:
                            completed_descs = "; ".join(
                                s.get("description", "")[:60] for s in completed
                            )
                            context_parts.append(f"completed_steps=[{completed_descs}]")
                        if remaining:
                            remaining_descs = "; ".join(
                                s.get("description", "")[:60] for s in remaining
                            )
                            context_parts.append(f"remaining_steps=[{remaining_descs}]")
                        current_idx = plan.get("current_step_index")
                        if current_idx is not None and current_idx < len(steps):
                            failed_desc = steps[current_idx].get("description", "")[:80]
                            context_parts.append(f"failed_at_step={failed_desc}")

                    item = {
                        "id": _next_id(todos),
                        "task": task_text,
                        "status": "pending",
                        "priority": "high",
                        "context": "; ".join(context_parts),
                        "created_at": _dt.now(_tz.utc).isoformat(),
                        "completed_at": None,
                    }
                    todos.append(item)
                    _save_todos(self.agent_dir, todos)
                    logger.info(f"Auto-todo [{item['id']}] for failed run: {loop_result.status}")

                    # HiveLoop: record retry TODO
                    _hl_task = get_current_task()
                    if _hl_task:
                        try:
                            _hl_task.retry(
                                f"Failed run ({loop_result.status}): {message[:100]}",
                                attempt=1,
                            )
                        except Exception:
                            pass
                    _hl_agent = get_hiveloop_agent()
                    if _hl_agent:
                        try:
                            _hl_agent.todo(
                                todo_id=item["id"],
                                action="created",
                                summary=task_text,
                                priority="high",
                                source="failed_run",
                                context=item.get("context", "")[:200],
                            )
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Failed to auto-create todo for failed run: {e}")

        # AUTO-TODO: Save remaining pending_actions as TODOs for next heartbeat
        if self.agent_dir and loop_result.pending_actions:
            try:
                from .tools.todo_tools import _load_todos, _save_todos, _next_id
                from datetime import datetime as _dt, timezone as _tz
                todos = _load_todos(self.agent_dir)
                existing_tasks = {t.get("task") for t in todos if t.get("status") == "pending"}
                added = 0
                for action in loop_result.pending_actions:
                    if action not in existing_tasks:
                        item = {
                            "id": _next_id(todos),
                            "task": action,
                            "status": "pending",
                            "priority": "normal",
                            "context": f"auto-created from pending_actions (run status={loop_result.status})",
                            "created_at": _dt.now(_tz.utc).isoformat(),
                            "completed_at": None,
                        }
                        todos.append(item)
                        existing_tasks.add(action)
                        added += 1
                if added:
                    _save_todos(self.agent_dir, todos)
                    logger.info(
                        "[%s] Auto-created %d TODO(s) from remaining pending_actions",
                        self.agent_id, added,
                    )

                    # HiveLoop: report auto-created pending_actions TODOs
                    _hl_agent = get_hiveloop_agent()
                    if _hl_agent:
                        for t in todos[-added:]:
                            try:
                                _hl_agent.todo(
                                    todo_id=t["id"],
                                    action="created",
                                    summary=t["task"],
                                    priority=t.get("priority", "normal"),
                                    source="pending_actions",
                                )
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"Failed to auto-create todos from pending_actions: {e}")

        # Collect events created by the agent during execution
        pending_events = []
        if queue_followup_event_tool:
            pending_events = queue_followup_event_tool.collect_events()

        # ====================================================================
        # POST-EXECUTION: TODO VERIFICATION & ESCALATION
        # ====================================================================
        # For heartbeat/task runs, check if high-priority TODO items are stuck.
        # If so, schedule a follow-up verification event so the agent reviews
        # failures and escalates to the human operator.
        if self.agent_dir:
            source = event_context.get("source", "human") if event_context else "human"
            # Only for background runs (heartbeat/task), not human chats or events
            if source in ("heartbeat", "task"):
                from .tools.todo_tools import _load_todos
                import uuid
                post_todos = _load_todos(self.agent_dir)
                stuck_high = [
                    t for t in post_todos
                    if t.get("status") == "pending" and t.get("priority") == "high"
                ]
                if stuck_high:
                    items_summary = "; ".join(
                        f"[{t['id']}] {t['task']}" for t in stuck_high[:5]
                    )
                    pending_events.append({
                        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
                        "title": "Review stuck high-priority TODO items",
                        "message": (
                            f"You have {len(stuck_high)} HIGH-priority TODO item(s) still pending: "
                            f"{items_summary}. "
                            "For each item: (1) Was the action attempted? Check recent tool results. "
                            "(2) Did it succeed? If not, why? "
                            "(3) If you cannot complete it (missing permissions, missing account, "
                            "API error, etc.), ESCALATE by posting to the team feed or sending "
                            "a notification to the human operator explaining what failed and "
                            "what is needed to fix it. "
                            "Mark items as complete if done, or remove if no longer relevant."
                        ),
                        "priority": "high",
                        "status": "active",
                        "created_by": "system",
                    })

        # ====================================================================
        # SCAN TURN FOR FACTS TO REMEMBER
        # ====================================================================
        if self.turn_scanner and loop_result.final_response:
            try:
                self.turn_scanner.scan_turn(
                    user_message=message,
                    assistant_response=loop_result.final_response
                )
            except Exception:
                # Don't let scanning failures affect the main flow
                pass

        # ====================================================================
        # PREPEND DIRECTIVE ACKNOWLEDGMENT TO RESPONSE
        # ====================================================================
        final_response = loop_result.final_response
        if directive_acknowledgment and final_response:
            final_response = f"{directive_acknowledgment}\n\n{final_response}"
        elif directive_acknowledgment:
            final_response = directive_acknowledgment

        # Update session with new messages
        # Only store role + content — LLM APIs reject extra fields like timestamp.
        # Session-level updated_at (set by MemoryManager.save_session) tracks timing.
        if session_id and self.memory_manager:
            new_messages = [
                {"role": "user", "content": message}
            ]
            if final_response:
                new_messages.append({
                    "role": "assistant",
                    "content": final_response,
                })
            self.memory_manager.append_to_session(session_id, new_messages)

            # Trim session if over threshold
            max_turns = getattr(self.config, 'session_max_turns', 50)
            if max_turns > 0:
                session = self.memory_manager.load_session(session_id)
                if session and len(session.conversation) > max_turns:
                    try:
                        compacted, result = self.loop.context_manager.compact(
                            session.conversation, self.config.system_prompt
                        )
                        if result.compacted:
                            session.conversation = compacted
                            self.memory_manager.save_session(session)
                            import logging
                            logging.getLogger(__name__).info(
                                f"Session '{session_id}' trimmed: "
                                f"{result.turns_summarized} turns compacted"
                            )
                    except Exception:
                        pass  # Don't let trimming failures affect the main flow

            # Update session status
            if loop_result.status == "completed":
                self.active_sessions[session_id] = "idle"
            elif loop_result.status in ("timeout", "max_turns", "error"):
                self.active_sessions[session_id] = "error"

        # ====================================================================
        # HANDLE SESSION END - Review for memories
        # ====================================================================
        session_review_notification = None
        if is_ending_session and session_id and self.session_reviewer:
            # Load updated session for review
            updated_session = self.memory_manager.load_session(session_id)
            if updated_session:
                review_result = self.session_reviewer.review_session(updated_session.to_dict())
                if review_result.notification:
                    session_review_notification = review_result.notification

            # Mark session as completed
            self.end_session(session_id)

        # Append session review notification
        if session_review_notification and final_response:
            final_response = f"{final_response}\n\n{session_review_notification}"
        elif session_review_notification:
            final_response = session_review_notification

        # Build result (aggregate pre-loop stats if applicable)
        total_turns = len(loop_result.turns)
        total_tokens = loop_result.total_tokens.total
        total_duration = loop_result.total_duration_ms
        all_tools = list(loop_result.tools_called)
        if pre_loop_result:
            total_turns += len(pre_loop_result.turns)
            total_tokens += pre_loop_result.total_tokens.total
            total_duration += pre_loop_result.total_duration_ms
            all_tools = list(set(all_tools + pre_loop_result.tools_called))

        return AgentResult(
            agent_id=self.agent_id,
            session_id=session_id or "",
            status=loop_result.status,
            final_response=final_response,
            turns=total_turns,
            tools_called=all_tools,
            total_tokens=total_tokens,
            total_duration_ms=total_duration,
            error=loop_result.error,
            loop_result=loop_result,
            pending_events=pending_events
        )

    def _build_memory_prompt_with_boost(self, query: str) -> str:
        """
        Build memory prompt and boost accessed memories.

        Args:
            query: Search query

        Returns:
            Memory prompt string
        """
        if not self.memory_manager:
            return ""

        # Search memory
        results = self.memory_manager.search_memory(query, limit=5)

        if not results:
            return ""

        # Boost accessed memories
        if self.memory_decay:
            for result in results:
                topic_id = result.get("topic_id", "")
                # Get content_id from entry or derive from content_file
                entry = result.get("entry", {})
                content_id = entry.get("content_id", "")
                if not content_id:
                    # Try to derive from content_file (e.g., "content_abc123.json" -> "content_abc123")
                    content_file = result.get("content_file", "")
                    if content_file:
                        content_id = content_file.replace(".json", "")
                if topic_id and content_id:
                    self.memory_decay.boost_on_access(topic_id, content_id)

        # Build prompt from results
        lines = ["## Relevant Memories\n"]
        for r in results:
            lines.append(f"- {r.get('summary', 'Untitled')}")

        return "\n".join(lines)

    def _build_identity_block(
        self,
        event_context: Optional[Dict] = None,
        session_id: Optional[str] = None
    ) -> str:
        """
        Build the identity block injected between system_prompt and skills_prompt.

        Tells the agent who it is, what credentials it has, its capabilities,
        team members, and current event context. Rebuilt per-execution because
        event metadata changes every time.
        """
        lines = ["# Your Identity"]
        lines.append(f"- **Agent ID**: {self.agent_id}")
        lines.append(f"- **Name**: {self.config.name or self.agent_id}")
        if self.config.role:
            lines.append(f"- **Role**: {self.config.role}")
        lines.append("- **Type**: agent")

        # Status and session from event context
        if event_context:
            status = event_context.get("agent_status", "started")
            lines.append(f"- **Status**: {status}")
        session_display = session_id or "main"
        lines.append(f"- **Current session**: {session_display}")

        # Credentials: scan agent memory files for credential-like data
        creds_block = self._scan_credentials()
        if creds_block:
            lines.append("")
            lines.append("## Your Credentials")
            lines.extend(creds_block)

        # Capabilities
        lines.append("")
        lines.append("## Your Capabilities")
        tool_names = self.loop.tool_registry.list_tools()
        if tool_names:
            lines.append(f"- **Tools**: {', '.join(tool_names)}")

        skill_names = []
        if self.skill_registry:
            skill_names = [e.name for e in self.skill_registry.entries]
        elif self.skill_loader:
            skill_names = self.skill_loader.list_skills()
        if skill_names:
            lines.append(f"- **Skills**: {', '.join(skill_names)}")

        lines.append(f"- **Limits**: {self.config.max_turns} turns, {self.config.timeout_seconds}s timeout")

        # Team: other agents (lightweight — just name + role)
        team_lines = self._get_team_info()
        if team_lines:
            lines.append("")
            lines.append("## Your Team")
            lines.extend(team_lines)

        # Delegation protocol (always present)
        delegation_lines = self._get_delegation_protocol()
        if delegation_lines:
            lines.append("")
            lines.extend(delegation_lines)

        # Workspace info
        workspace_lines = self._get_workspace_info()
        if workspace_lines:
            lines.append("")
            lines.append("## Your Workspace")
            lines.extend(workspace_lines)

        # Current event context
        if event_context:
            lines.append("")
            lines.append("## Current Event")
            lines.append(f"- **Source**: {event_context.get('source', 'unknown')}")
            lines.append(f"- **Priority**: {event_context.get('priority', 'NORMAL')}")
            triggered = event_context.get("triggered_skills", [])
            if triggered:
                lines.append(f"- **Skills triggered**: {', '.join(triggered)}")

        return "\n".join(lines)

    def _scan_credentials(self) -> List[str]:
        """Scan agent memory files for stored credentials."""
        cred_lines = []
        if not self.memory_manager:
            return cred_lines

        memory_dir = getattr(self.memory_manager, 'base_path', None)
        if not memory_dir:
            return cred_lines

        memory_path = Path(memory_dir)
        if not memory_path.exists():
            return cred_lines

        # Look for JSON files in memory dir that contain credential fields
        cred_keywords = {"auth_token", "api_key", "bearer", "access_token", "token"}
        try:
            for json_file in memory_path.glob("**/*.json"):
                if json_file.name.startswith("."):
                    continue
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    # Check top-level and one level deep for credential fields
                    self._extract_creds(data, json_file.stem, cred_keywords, cred_lines)
                except (json.JSONDecodeError, OSError):
                    continue
        except Exception:
            pass

        return cred_lines

    def _extract_creds(
        self, data: dict, source: str, keywords: set, cred_lines: List[str]
    ) -> None:
        """Extract credential-like fields from a dict into formatted lines."""
        # Check for nested dicts first (e.g., {"loopcolony": {"auth_token": "...", "base_url": "..."}})
        for key, value in data.items():
            if isinstance(value, dict):
                has_cred = any(k.lower() in keywords for k in value.keys())
                if has_cred:
                    parts = []
                    for k, v in value.items():
                        if isinstance(v, str):
                            display = v if len(v) <= 40 else v[:20] + "..." + v[-8:]
                            parts.append(f"{k}={display}")
                    if parts:
                        cred_lines.append(f"- **{key}**: {', '.join(parts)}")
                    return  # Found nested creds, done with this file

        # Flat file: if any top-level key is a credential, include ALL string fields
        has_top_cred = any(
            k.lower() in keywords for k, v in data.items() if isinstance(v, str)
        )
        if has_top_cred:
            parts = []
            for k, v in data.items():
                if isinstance(v, str):
                    display = v if len(v) <= 40 else v[:20] + "..." + v[-8:]
                    parts.append(f"{k}={display}")
            if parts:
                cred_lines.append(f"- **{source}**: {', '.join(parts)}")

    def _get_team_info(self) -> List[str]:
        """Get team info from team.json first, fallback to ConfigManager.

        Reads ``data/loopCore/CONFIG/team.json`` for rich human+agent info.
        For humans: includes capabilities, availability, and notes.
        For agents: includes capabilities.
        """
        lines = []

        # Try team.json first (check loopCore path, then generic)
        try:
            from .config.loader import _find_project_root
            root = _find_project_root()
            team_path = root / "data" / "loopCore" / "CONFIG" / "team.json"
            if not team_path.exists():
                team_path = root / "data" / "CONFIG" / "team.json"
            if team_path.exists():
                team_data = json.loads(team_path.read_text(encoding="utf-8"))
                members = team_data.get("members", [])
                for member in members:
                    mid = member.get("id", "")
                    if mid == self.agent_id:
                        continue
                    mtype = member.get("type", "agent")
                    name = member.get("name", mid)
                    role = member.get("role", "")
                    caps = member.get("capabilities", [])
                    caps_str = ", ".join(caps) if caps else ""

                    if mtype == "human":
                        line = f"- {name} ({mtype}, {role})"
                        if caps_str:
                            line += f" -- {caps_str}"
                        lines.append(line)
                        avail = member.get("availability", "")
                        if avail:
                            lines.append(f"  Available: {avail}")
                    else:
                        line = f"- {name} ({mtype}, {role})"
                        if caps_str:
                            line += f" -- {caps_str}"
                        lines.append(line)

                if lines:
                    lines.append(f'For full team details: file_read("data/CONFIG/team.json")')
                    return lines
        except Exception:
            pass

        # Fallback to ConfigManager
        try:
            config_manager = get_config_manager()
            all_agent_ids = config_manager.list_agents()
            for aid in all_agent_ids:
                if aid == self.agent_id:
                    continue
                try:
                    config = config_manager.load_agent(aid)
                    if getattr(config, 'is_deleted', False):
                        continue
                    desc = config.description or ""
                    role = config.role or ""
                    if role and desc:
                        lines.append(f"- {aid}: {desc} ({role})")
                    elif role:
                        lines.append(f"- {aid}: ({role})")
                    elif desc:
                        lines.append(f"- {aid}: {desc}")
                    else:
                        lines.append(f"- {aid}")
                except Exception:
                    continue
        except Exception:
            pass
        return lines

    def _get_delegation_protocol(self) -> List[str]:
        """Return compact delegation protocol rules for the identity block.

        Always present (~350 tokens). Tells the agent when/how to delegate
        to humans and agents, referencing delegate_to_human / delegate_to_agent.
        """
        return [
            "## Delegation Protocol",
            "",
            "You can delegate tasks to humans or other agents on your team.",
            "",
            "**When to delegate to a human:**",
            "- Physical action (inventory, shipping, meetings, inspections)",
            "- Legal authority (signing contracts, financial approvals over limits)",
            "- Subjective judgment (design taste, brand voice, hiring decisions)",
            "- External relationships (client dinners, vendor negotiations, interviews)",
            "",
            "**When NOT to delegate:**",
            "- You can look it up, calculate it, or automate it with your tools",
            "- You can draft it yourself (draft first, then delegate the review)",
            "- Another agent on your team has the right skills",
            "- Rule: exhaust your own capabilities before delegating",
            "",
            "**How to delegate:**",
            "- To a human: use `delegate_to_human` with target, task description, optional form fields, deadline",
            "- To an agent: use `delegate_to_agent` with target agent_id and task description",
            "- Both tools suspend your current plan until the delegate responds",
            "",
            "**After delegating:**",
            "- The system handles reminders and timeout automatically",
            "- Your plan resumes when the human submits their response or the agent completes",
            "- If the deadline expires, you receive a timeout notification and can retry or escalate",
        ]

    def _get_workspace_info(self) -> List[str]:
        """Get workspace directory info for the agent (only if it exists)."""
        lines = []
        try:
            from .config.loader import _find_project_root
            workspace_dir = _find_project_root() / "data" / "AGENTS" / self.agent_id / "workspace"
            if not workspace_dir.exists():
                return lines

            lines.append(f"- **Scratch directory**: `data/AGENTS/{self.agent_id}/workspace/`")

            # List project subdirectories and file counts
            for child in sorted(workspace_dir.iterdir()):
                if child.is_dir():
                    file_count = sum(1 for f in child.rglob("*") if f.is_file())
                    lines.append(f"  - `{child.name}/` ({file_count} files)")
                elif child.is_file():
                    lines.append(f"  - `{child.name}`")
        except Exception:
            pass
        return lines

    def run_with_skill(
        self,
        message: str,
        skill_id: str,
        session_id: Optional[str] = None,
        event_context: Optional[Dict] = None,
    ) -> AgentResult:
        """Run agent with a specific skill. Delegates to run().

        Kept for backward compatibility (CLI, API, runtime callers).
        """
        return self.run(
            message=message,
            skill_id=skill_id,
            session_id=session_id,
            event_context=event_context,
        )

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    def get_session(self, session_id: str) -> Optional[Any]:
        """Get a session by ID."""
        if self.memory_manager:
            return self.memory_manager.load_session(session_id)
        return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions for this agent."""
        if self.memory_manager:
            return self.memory_manager.list_sessions(agent_id=self.agent_id)
        return []

    def end_session(self, session_id: str) -> bool:
        """End a session (mark as completed)."""
        if self.memory_manager:
            result = self.memory_manager.update_session_status(session_id, "completed")
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
            return result
        return False

    # ========================================================================
    # TOOL MANAGEMENT
    # ========================================================================

    def register_tool(self, tool: Any) -> None:
        """Register a tool with the agent."""
        self.tool_registry.register(tool)

    def list_tools(self) -> List[str]:
        """List all registered tools."""
        return self.tool_registry.list_tools()

    def execute_tool(self, tool_name: str, parameters: Dict) -> ToolResult:
        """Execute a specific tool."""
        return self.tool_registry.execute(tool_name, parameters)

    # ========================================================================
    # INFO METHODS
    # ========================================================================

    def get_info(self) -> Dict[str, Any]:
        """Get agent information."""
        # Get loaded skill info
        loaded_skills = []
        if self.skill_loader:
            loaded_skills = self.skill_loader.list_skills()

        # Get per-agent registry skills
        registry_skills = []
        if self.skill_registry:
            registry_skills = [e.name for e in self.skill_registry.entries]

        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "model": self.config.llm.model,
            "max_turns": self.config.max_turns,
            "enabled_tools": self.enabled_tools,
            "loaded_skills": loaded_skills,  # All loaded skills (global + private)
            "registry_skills": registry_skills,  # Per-agent curated skills
            "heartbeat_context_count": getattr(self.config, 'heartbeat_context_count', 3),
            "heartbeat_enabled": getattr(self.config, 'heartbeat_enabled', True),
            "heartbeat_interval_minutes": getattr(self.config, 'heartbeat_interval_minutes', 15),
            "total_runs": self.total_runs,
            "active_sessions": len(self.active_sessions),
            "registered_tools": self.list_tools()
        }

    def __repr__(self) -> str:
        return f"Agent(id={self.agent_id}, name={self.name})"
