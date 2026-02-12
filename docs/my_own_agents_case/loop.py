"""
AGENTIC_LOOP
============

Core execution engine for the Agentic Loop Framework.

This is the innermost loop that drives agent behavior. Everything else
(Agent, AgentManager, Scheduler) ultimately calls into this.

Execution Cycle
---------------
::

    1. Build full system prompt:
       system_prompt + skills_prompt + memory_prompt + [plan context]
       (via ContextManager.build())

    2. Call LLM with tools (tool schemas from ToolRegistry)

    3. If LLM returns tool calls:
       → Execute each tool via ToolRegistry.execute()
       → Inject results as user messages (NOT system messages)
       → Continue to next turn

    4. If LLM returns text (no tool calls):
       → Return as final response

    5. Repeat until: final response, max_turns, timeout, or error

Turn Tracking
-------------
Each iteration is a "Turn" — one LLM call + tool executions.
Turns are tracked with token usage, tool calls, timing, and reflection results.

Optional Capabilities (per-turn)
---------------------------------
- **Reflection** (reflection.py): After each turn, the agent self-evaluates
  and decides to continue/adjust/pivot/escalate. Uses extra tokens.
- **Planning** (planning.py): Tracks progress against plan steps, injects
  ``[PLAN CONTEXT]`` into prompt, triggers replanning if stuck.
- **Learning** (learning.py): Captures successful patterns for future use.

Context Management
------------------
The ContextManager handles conversation history sizing. If the conversation
exceeds the token budget, older messages are summarized or trimmed. Skills
prompt and memory prompt are injected into the system message, not as separate
conversation turns.

Key Design Decision: Tool Results as User Messages
----------------------------------------------------
Tool results are injected as user messages, not system messages. This means
the LLM sees them as part of the conversation flow, which produces better
reasoning about tool outputs. (See CLAUDE.md gotcha about this.)

Usage::

    loop = AgenticLoop(
        llm_client=client,
        tool_registry=registry,
        max_turns=20,
        timeout_seconds=600
    )
    result = loop.execute(
        message="Check my loopColony notifications",
        system_prompt="You are a helpful assistant."
    )
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Literal, Any, Callable, TYPE_CHECKING

from .tools.base import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)
from .context import ContextManager, count_conversation_tokens
from .reflection import ReflectionManager, ReflectionConfig, ReflectionResult
from .planning import PlanningManager, PlanningConfig, ExecutionPlan
from .learning import LearningManager, LearningConfig


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class TokenUsage:
    """Token usage tracking."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens
        )

    def to_dict(self) -> Dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total": self.total
        }


@dataclass
class ToolCallRecord:
    """Record of a tool call."""
    id: str
    name: str
    parameters: Dict
    result: ToolResult

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "parameters": self.parameters,
            "result": self.result.to_dict()
        }


@dataclass
class Turn:
    """Represents a single turn in the agentic loop."""
    number: int
    timestamp: str
    llm_text: str
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    tokens_used: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    plan_step_index: Optional[int] = None
    plan_step_description: Optional[str] = None

    def to_dict(self) -> Dict:
        d = {
            "number": self.number,
            "timestamp": self.timestamp,
            "llm_text": self.llm_text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "tokens_used": self.tokens_used.to_dict(),
            "duration_ms": self.duration_ms,
        }
        if self.plan_step_index is not None:
            d["plan_step_index"] = self.plan_step_index
            d["plan_step_description"] = self.plan_step_description
        return d


@dataclass
class LoopResult:
    """Result of an agentic loop execution."""
    status: Literal["completed", "timeout", "max_turns", "error", "loop_detected", "escalation_needed", "cancelled"]
    turns: List[Turn]
    final_response: Optional[str]
    error: Optional[str] = None
    total_duration_ms: int = 0
    total_tokens: TokenUsage = field(default_factory=TokenUsage)
    tools_called: List[str] = field(default_factory=list)
    skill_files_read: List[str] = field(default_factory=list)
    reflections: List[ReflectionResult] = field(default_factory=list)
    plan: Optional[Dict] = None  # Plan data if planning was used
    learning_stats: Optional[Dict] = None  # Learning statistics if learning was used
    execution_trace: List[Dict] = field(default_factory=list)
    journal: List[Dict] = field(default_factory=list)
    pending_actions: List[str] = field(default_factory=list)  # Remaining actions the agent couldn't finish

    def to_dict(self) -> Dict:
        d = {
            "status": self.status,
            "turns": [t.to_dict() for t in self.turns],
            "final_response": self.final_response,
            "error": self.error,
            "total_duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens.to_dict(),
            "tools_called": self.tools_called,
            "skill_files_read": self.skill_files_read,
            "reflections": [r.to_dict() for r in self.reflections],
            "plan": self.plan,
            "learning_stats": self.learning_stats,
        }
        if self.execution_trace:
            d["execution_trace"] = self.execution_trace
        if self.pending_actions:
            d["pending_actions"] = self.pending_actions
        return d

    def get_step_stats(self) -> List[Dict]:
        """Group turns by plan_step_index and aggregate tokens/timing.

        Returns a list of per-step dicts with turns count, token totals,
        and start/complete timestamps from the execution trace.
        Zero cost if not called (lazy computation).
        """
        # Group turns by step index
        steps: Dict[Optional[int], List[Turn]] = {}
        for t in self.turns:
            steps.setdefault(t.plan_step_index, []).append(t)

        # Build trace event lookup: (event, step_index) -> timestamp
        trace_times: Dict[tuple, str] = {}
        for evt in self.execution_trace:
            key = (evt.get("event"), evt.get("step_index"))
            if key not in trace_times:
                trace_times[key] = evt.get("timestamp", "")

        result = []
        for step_idx in sorted(steps.keys(), key=lambda x: (x is None, x)):
            turns = steps[step_idx]
            total_input = sum(t.tokens_used.input_tokens for t in turns)
            total_output = sum(t.tokens_used.output_tokens for t in turns)
            stat = {
                "step_index": step_idx,
                "turns": len(turns),
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_input + total_output,
                "started_at": trace_times.get(("step_started", step_idx), ""),
                "completed_at": trace_times.get(("step_completed", step_idx), ""),
            }
            # Add description from the first turn in this step
            if turns and turns[0].plan_step_description:
                stat["description"] = turns[0].plan_step_description
            result.append(stat)
        return result


# ============================================================================
# TURN EXCHANGE (intra-heartbeat context)
# ============================================================================

@dataclass
class TurnExchange:
    """Record of a single turn's tool execution for intra-heartbeat context."""
    turn: int
    tool: str
    intent: str
    result_preview: str  # Truncated to ~1200 chars
    success: bool

    def format_line(self) -> str:
        status = "OK" if self.success else "FAIL"
        return f"Turn {self.turn}: {self.tool} [{status}] -- {self.intent}\n  Result: {self.result_preview}"


# ============================================================================
# COMPACT TOOL CATALOG (for Phase 1 prompt — names + short hints)
# ============================================================================

# Short hints for Phase 1 tool selection. Tools with self-evident names are
# omitted (None) — the LLM sees just the name. This saves ~1,500 tokens per
# Phase 1 call compared to full descriptions.
_TOOL_SHORT_HINTS = {
    "file_read": None,
    "file_write": None,
    "http_request": "Call any REST API (GET/POST/PUT/PATCH/DELETE)",
    "webpage_fetch": "Fetch web page as text/markdown",
    "schedule_create": "Create scheduled task (interval/cron/once)",
    "schedule_list": None,
    "schedule_get": None,
    "schedule_update": None,
    "schedule_delete": None,
    "schedule_trigger": "Manually trigger a scheduled task now",
    "schedule_run_list": "Get run history for a scheduled task",
    "schedule_state_set": "Persist key-value state between task runs",
    "schedule_state_get": "Read persisted task state",
    "feed_post": "Post a visible message to the human operator's feed panel",
    "queue_followup_event": "Queue a follow-up action for a later heartbeat",
    "web_search": "Search the web (DuckDuckGo)",
    "csv_export": None,
    "excel_workbook_create": "Create formatted .xlsx workbook",
    "send_dm_notification": "Send a DM to alert a team member (escalations, updates)",
    "image_generate": "Generate image via Gemini",
    "document_extract": "Extract structured data from text via LLM",
    "email_send": "Compose and send email (or draft) via loopColony mail",
    "support_ticket_create": "Create a support ticket (subject, priority, assignee, CRM links)",
    "support_ticket_update": "Update ticket: comment, resolve, close, reopen, assign, escalate",
    "crm_search": "Search CRM records: contacts, companies, deals, products, quotes, KB articles, emails, calendars, analytics",
    "crm_write": "Create/update/delete CRM records: contacts, companies, deals, products, quotes, KB articles, emails, calendars",
    "workspace_read": "Read workspace: conversations, DMs, notifications, feed, posts, tasks, topics, members, search, message, sync",
    "workspace_write": "Write workspace: send messages, create posts/comments/topics/tasks, vote, follow",
    "data_aggregate": "Numeric aggregation (count/sum/avg/min/max) over CRM entities",
    "math_eval": "Evaluate math expressions safely (+, -, *, /, round, min, max)",
    "todo_add": "Add item to your personal TO-DO list",
    "todo_list": None,
    "todo_complete": None,
    "todo_remove": None,
    "report_issue": "Report a problem you cannot resolve (visible to human operator)",
}


def _build_compact_tool_catalog(tool_registry) -> str:
    """Build a compact tool catalog for Phase 1 prompts.

    Uses short hints from _TOOL_SHORT_HINTS when available, falls back to
    name-only for self-evident tools, and uses the full description (truncated)
    for any tool not in the map.
    """
    lines = []
    for name in tool_registry.list_tools():
        if name in _TOOL_SHORT_HINTS:
            hint = _TOOL_SHORT_HINTS[name]
            if hint:
                lines.append(f"- {name}: {hint}")
            else:
                lines.append(f"- {name}")
        else:
            # Unknown tool — use first sentence of full description
            summaries = tool_registry.get_tool_summaries()
            for s in summaries:
                if s["name"] == name:
                    desc = s["description"].split(".")[0]
                    lines.append(f"- {name}: {desc}")
                    break
            else:
                lines.append(f"- {name}")
    return "\n".join(lines)


# ============================================================================
# ATOMIC STATE (for two-phase agentic loop)
# ============================================================================

@dataclass
class AtomicState:
    """State dict for the atomic two-phase agentic loop.

    Instead of accumulating full conversation history, atomic mode maintains
    a compact state dict that is rebuilt each turn. This keeps input token
    usage constant regardless of how many turns have been executed.
    """
    completed_steps: List[str] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    pending_actions: List[str] = field(default_factory=list)
    current_step: int = 0
    error_context: Optional[str] = None

    MAX_COMPLETED_STEPS = 20
    MAX_VARIABLES = 50
    MAX_PENDING_ACTIONS = 10

    def apply_update(self, update: Dict) -> None:
        """Merge LLM-produced state update into current state."""
        if not update:
            return

        # Merge completed_steps (deduplicate — only add genuinely new ones)
        new_steps = update.get("completed_steps", [])
        if new_steps:
            existing = set(self.completed_steps)
            for step in new_steps:
                if step not in existing:
                    self.completed_steps.append(step)
                    existing.add(step)
            # Cap to prevent unbounded growth
            if len(self.completed_steps) > self.MAX_COMPLETED_STEPS:
                self.completed_steps = self.completed_steps[-self.MAX_COMPLETED_STEPS:]

        # Merge variables
        new_vars = update.get("variables", {})
        if new_vars:
            self.variables.update(new_vars)
            # Cap to prevent unbounded growth
            if len(self.variables) > self.MAX_VARIABLES:
                # Keep the most recently added keys
                keys = list(self.variables.keys())
                for key in keys[:-self.MAX_VARIABLES]:
                    del self.variables[key]

        # Update pending_actions: replace the whole list each turn
        # (LLM returns the current remaining actions after working through them)
        if "pending_actions" in update:
            self.pending_actions = update["pending_actions"][:self.MAX_PENDING_ACTIONS]

        # Update step index if provided
        if "current_step" in update:
            self.current_step = update["current_step"]

        # Update error context
        if "error_context" in update:
            self.error_context = update["error_context"]

    def to_dict(self) -> Dict:
        """Serialize to compact dict for LLM prompt injection."""
        result = {
            "completed_steps": self.completed_steps,
            "variables": self.variables,
            "current_step": self.current_step,
        }
        if self.pending_actions:
            result["pending_actions"] = self.pending_actions
        if self.error_context:
            result["error_context"] = self.error_context
        return result


# ============================================================================
# LOOP DETECTOR
# ============================================================================

class LoopDetector:
    """
    Detects infinite loops in tool call patterns.

    Tracks sequences of tool calls and detects when:
    1. Same tool called repeatedly with identical parameters
    2. Same sequence of tools repeats multiple times
    3. No meaningful progress is being made
    """

    DEFAULT_REPEAT_THRESHOLD = 3  # Same pattern repeating 3+ times
    DEFAULT_SEQUENCE_LENGTH = 4   # Track last N tool calls for sequence detection

    def __init__(
        self,
        repeat_threshold: int = None,
        sequence_length: int = None
    ):
        """
        Initialize loop detector.

        Args:
            repeat_threshold: Number of repeats before flagging as loop
            sequence_length: Length of sequence to track for pattern detection
        """
        self.repeat_threshold = repeat_threshold or self.DEFAULT_REPEAT_THRESHOLD
        self.sequence_length = sequence_length or self.DEFAULT_SEQUENCE_LENGTH

        # Track tool call signatures (tool_name + hash of parameters)
        self._call_history: List[str] = []
        # Track consecutive identical calls
        self._consecutive_identical: int = 0
        self._last_signature: str = ""

    def reset(self) -> None:
        """Reset detector state."""
        self._call_history = []
        self._consecutive_identical = 0
        self._last_signature = ""

    def _make_signature(self, tool_name: str, parameters: Dict) -> str:
        """
        Create a signature for a tool call.

        Args:
            tool_name: Name of the tool
            parameters: Tool parameters

        Returns:
            String signature for the call
        """
        # Sort parameters for consistent hashing
        param_str = str(sorted(parameters.items())) if parameters else ""
        return f"{tool_name}:{hash(param_str)}"

    def record_call(self, tool_name: str, parameters: Dict) -> None:
        """
        Record a tool call for pattern tracking.

        Args:
            tool_name: Name of the tool called
            parameters: Parameters passed to the tool
        """
        signature = self._make_signature(tool_name, parameters)
        self._call_history.append(signature)

        # Track consecutive identical calls
        if signature == self._last_signature:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 1
            self._last_signature = signature

        # Keep history bounded
        if len(self._call_history) > self.sequence_length * 3:
            self._call_history = self._call_history[-self.sequence_length * 3:]

    def is_looping(self) -> tuple:
        """
        Check if a loop pattern is detected.

        Returns:
            Tuple of (is_looping: bool, reason: str or None)
        """
        # Check for consecutive identical calls
        if self._consecutive_identical >= self.repeat_threshold:
            return True, f"Same tool call repeated {self._consecutive_identical} times"

        # Check for repeating sequence pattern
        if len(self._call_history) >= self.sequence_length * 2:
            # Get last N calls as pattern
            pattern = self._call_history[-self.sequence_length:]

            # Look for this pattern earlier in history
            repeats = 0
            for i in range(len(self._call_history) - self.sequence_length):
                check = self._call_history[i:i + self.sequence_length]
                if check == pattern:
                    repeats += 1

            if repeats >= self.repeat_threshold - 1:  # -1 because current is also a repeat
                return True, f"Tool call sequence repeated {repeats + 1} times"

        return False, None

    def get_stats(self) -> Dict[str, Any]:
        """Get detector statistics."""
        return {
            "total_calls": len(self._call_history),
            "consecutive_identical": self._consecutive_identical,
            "last_signature": self._last_signature
        }


# ============================================================================
# CONTEXT BUILDER
# ============================================================================

class ContextBuilder:
    """Builds the full context for LLM calls."""

    def build(
        self,
        system_prompt: str,
        identity_prompt: str = "",
        skills_prompt: str = "",
        memory_prompt: str = "",
        conversation: List[Dict] = None
    ) -> tuple:
        """
        Build full system prompt and messages.

        Args:
            system_prompt: Base system prompt
            identity_prompt: Optional identity injection (who the agent is)
            skills_prompt: Optional skills injection
            memory_prompt: Optional memory injection
            conversation: Conversation history

        Returns:
            Tuple of (full_system, messages)
        """
        # Combine system components
        parts = [system_prompt]
        if identity_prompt:
            parts.append(identity_prompt)
        if skills_prompt:
            parts.append(skills_prompt)
        if memory_prompt:
            parts.append(memory_prompt)

        full_system = "\n\n".join(parts)

        # Build messages
        messages = conversation or []

        return full_system, messages


# ============================================================================
# AGENTIC LOOP
# ============================================================================

class AgenticLoop:
    """
    Core agentic loop engine.

    Executes the loop:
    1. Call LLM with context and tools
    2. If tool calls: execute tools, add results to conversation
    3. If no tool calls: return final response
    4. Check limits (turns, timeout, loop detection)
    5. Repeat until done
    """

    def __init__(
        self,
        llm_client,
        tool_registry: ToolRegistry,
        max_turns: int = 20,
        timeout_seconds: int = 600,
        max_context_tokens: int = 100000,
        enable_compaction: bool = True,
        enable_loop_detection: bool = True,
        loop_repeat_threshold: int = 3,
        reflection_config: ReflectionConfig = None,
        planning_config: PlanningConfig = None,
        learning_config: LearningConfig = None,
        memory_path: str = None,
        agent_id: str = "default"
    ):
        """
        Initialize the agentic loop.

        Args:
            llm_client: LLM client with complete_with_tools method
            tool_registry: Registry of available tools
            max_turns: Maximum number of loop iterations
            timeout_seconds: Maximum execution time
            max_context_tokens: Maximum context window size
            enable_compaction: Whether to enable automatic context compaction
            enable_loop_detection: Whether to detect infinite tool loops
            loop_repeat_threshold: Number of repeats before flagging as loop
            reflection_config: Configuration for reflection behavior
            planning_config: Configuration for planning behavior
            learning_config: Configuration for learning behavior
            memory_path: Path to memory directory for learning storage
            agent_id: Agent identifier for learning storage isolation
        """
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self._agent_id = agent_id
        self.context_builder = ContextBuilder()
        self.enable_compaction = enable_compaction
        self.context_manager = ContextManager(
            max_tokens=max_context_tokens,
            llm_client=llm_client if enable_compaction else None
        )
        self.compaction_count = 0

        # Loop detection
        self.enable_loop_detection = enable_loop_detection
        self.loop_detector = LoopDetector(repeat_threshold=loop_repeat_threshold)

        # Reflection - uses same llm_client for cost tracking
        self.reflection_config = reflection_config or ReflectionConfig()
        self.reflection_manager = ReflectionManager(
            llm_client=llm_client,
            config=self.reflection_config
        )

        # Planning - uses same llm_client for cost tracking
        self.planning_config = planning_config or PlanningConfig()
        self.planning_manager = PlanningManager(
            llm_client=llm_client,
            config=self.planning_config,
            available_tools=tool_registry.list_tools() if hasattr(tool_registry, 'list_tools') else [],
            tool_summaries=tool_registry.get_tool_summaries() if hasattr(tool_registry, 'get_tool_summaries') else []
        )

        # Learning - uses same llm_client for cost tracking
        self.learning_config = learning_config or LearningConfig()
        self.learning_manager: Optional[LearningManager] = None
        if self.learning_config.enabled and memory_path:
            from pathlib import Path
            self.learning_manager = LearningManager(
                llm_client=llm_client,
                memory_path=Path(memory_path),
                agent_id=agent_id,
                config=self.learning_config
            )

    # ====================================================================
    # SHARED HELPERS
    # ====================================================================

    def _build_loop_result(self, status: str, state: dict,
                           final_response: str = None, error: str = None) -> LoopResult:
        """Construct LoopResult from shared execution state.

        Every exit point (timeout, cancellation, error, loop_detected,
        completed, max_turns, escalation_needed) passes just status,
        final_response, and error — all other fields come from the
        shared tracking state dict.
        """
        return LoopResult(
            status=status,
            turns=state["turns"],
            final_response=final_response,
            error=error,
            total_duration_ms=int((time.time() - state["start_time"]) * 1000),
            total_tokens=state["total_tokens"],
            tools_called=list(set(state["tools_called"])),
            skill_files_read=state["skill_files_read"],
            reflections=state["reflections"],
            plan=state["plan"].to_dict() if state["plan"] else None,
            learning_stats=self.learning_manager.get_session_stats() if self.learning_manager else None,
            execution_trace=state.get("execution_trace", []),
            journal=state.get("journal", []),
            pending_actions=state.get("pending_actions", []),
        )

    def _init_execution(self, message: str, skip_planning: bool = False) -> dict:
        """Reset managers, get learning insights, create plan, return shared state.

        Called at the start of execute() to perform all pre-loop setup.
        Returns a dict with all shared tracking state.
        """
        # Reset all managers for new execution
        if self.enable_loop_detection:
            self.loop_detector.reset()
        self.reflection_manager.reset()
        self.planning_manager.reset()
        if self.learning_manager:
            self.learning_manager.reset()

        # Get relevant learning insights before execution
        insights_context = ""
        if self.learning_manager:
            insights = self.learning_manager.get_relevant_insights(message)
            insights_context = self.learning_manager.format_context_injection(insights)

        # Create plan if task is complex enough
        # Skip planning for event-triggered and heartbeat runs (reactive, not complex)
        plan: Optional[ExecutionPlan] = None
        if not skip_planning and self.planning_manager.should_plan(message):
            plan = self.planning_manager.create_plan(message)
            suggested_turns = self.planning_manager.suggest_turn_budget()
            if suggested_turns > self.max_turns:
                plan.potential_blockers.append(
                    f"Plan suggests {suggested_turns} turns but only {self.max_turns} available"
                )

        state = {
            "turns": [],
            "tools_called": [],
            "skill_files_read": [],
            "reflections": [],
            "total_tokens": TokenUsage(),
            "start_time": time.time(),
            "plan": plan,
            "insights_context": insights_context,
            "execution_trace": [],
            # Learning tracking
            "tool_sequence": [],
            "files_accessed": [],
            "observations": [],
            # Side-effect tracking: skip reflection/learning when run was read-only
            "had_side_effects": False,
            # Flight recorder journal (full-detail entries, persisted as journal.jsonl)
            "journal": [],
            # Intra-heartbeat context: all turn exchanges within this run
            "turn_exchanges": [],
        }

        # Emit initial trace events when a plan was created
        if plan:
            self._emit_trace_event(
                state, "plan_created", detail=f"{len(plan.steps)} steps",
            )
            if plan.steps:
                self._emit_trace_event(
                    state, "step_started", step_index=0,
                    detail=plan.steps[0].description if hasattr(plan.steps[0], 'description') else str(plan.steps[0]),
                )

        return state

    def _check_timeout(self, message: str, state: dict) -> Optional[LoopResult]:
        """Check if execution has timed out. Returns LoopResult if timed out, else None."""
        elapsed = time.time() - state["start_time"]
        if elapsed <= self.timeout_seconds:
            return None

        if self.learning_manager and self.learning_config.learn_from_errors:
            self.learning_manager.learn_from_error(
                error_message=f"Timeout after {elapsed:.1f}s",
                context=message, tool_name=None, resolution=None,
            )
        return self._build_loop_result(
            "timeout", state,
            final_response=state["turns"][-1].llm_text if state["turns"] else None,
            error=f"Timeout after {elapsed:.1f}s",
        )

    def _check_cancellation(self, cancel_check: Optional[Callable[[], bool]],
                            state: dict) -> Optional[LoopResult]:
        """Check if execution was cancelled. Returns LoopResult if cancelled, else None."""
        if not cancel_check or not cancel_check():
            return None
        return self._build_loop_result(
            "cancelled", state,
            final_response=state["turns"][-1].llm_text if state["turns"] else None,
            error="Execution cancelled (runtime shutting down)",
        )

    # Tool name patterns that indicate mutation / side effects.
    # Used to gate reflection and learning — read-only runs skip both.
    _WRITE_PATTERNS = ("_write", "_create", "_update", "_delete", "send_", "queue_")

    @staticmethod
    def _is_write_tool(tool_name: str) -> bool:
        """Return True if tool_name indicates a mutation (write/create/delete/send)."""
        return any(p in tool_name for p in AgenticLoop._WRITE_PATTERNS)

    def _process_tool_result(self, tool_name: str, parameters: dict,
                             tool_result: ToolResult, message: str, state: dict) -> None:
        """Track skill file reads and learn from tool execution.

        Called after each successful tool execution in both modes.
        Updates state dicts for skill_files_read, files_accessed,
        tool_sequence, and observations. Learns tool patterns and
        errors via learning_manager.
        """
        # Track side effects — any successful write tool sets the flag.
        # Exclude update_presence: it's housekeeping, not a meaningful mutation.
        if tool_result.success and self._is_write_tool(tool_name):
            is_presence = (
                tool_name == "workspace_write"
                and parameters.get("action") == "update_presence"
            )
            if not is_presence:
                state["had_side_effects"] = True
        # Track skill file reads
        if tool_name == "file_read":
            path = parameters.get("path", "")
            if "skill.md" in path or "/SKILLS/" in path or "\\SKILLS\\" in path:
                state["skill_files_read"].append(path)
            if path:
                state["files_accessed"].append(path)

        # Learning: track tool pattern and learn from results
        if self.learning_manager:
            self.learning_manager.learn_tool_pattern(
                tool_name=tool_name,
                parameters=parameters,
                result=tool_result,
                context=message,
            )
            state["tool_sequence"].append(tool_name)

            # Track observations from tool results
            if tool_result.output:
                state["observations"].append(f"Tool {tool_name}: {tool_result.output[:500]}")

            # Learn from tool errors
            if not tool_result.success and tool_result.error:
                self.learning_manager.learn_from_error(
                    error_message=tool_result.error,
                    context=message,
                    tool_name=tool_name,
                    resolution=None,
                )

    def _check_loop_detection(self, tool_name: str, parameters: dict,
                              turn: Turn, turn_start: float, last_text: str,
                              message: str, state: dict) -> Optional[LoopResult]:
        """Record tool call and check for infinite loops.

        Returns LoopResult if loop detected, else None. Also finalizes
        the turn duration and appends the turn if a loop is found.
        """
        if not self.enable_loop_detection:
            return None

        self.loop_detector.record_call(tool_name, parameters)
        is_looping, loop_reason = self.loop_detector.is_looping()
        if not is_looping:
            return None

        # Learn from loop detection
        if self.learning_manager and self.learning_config.learn_from_errors:
            self.learning_manager.learn_from_error(
                error_message=f"Infinite loop detected: {loop_reason}",
                context=message,
                tool_name=state["tool_sequence"][-1] if state["tool_sequence"] else None,
                resolution=None,
            )

        turn.duration_ms = int((time.time() - turn_start) * 1000)
        state["turns"].append(turn)
        return self._build_loop_result(
            "loop_detected", state,
            final_response=last_text,
            error=f"Infinite loop detected: {loop_reason}",
        )

    def _handle_reflection(self, turn_number: int, last_tool_failed: bool,
                           message: str, state: dict,
                           atomic_state=None) -> Optional[tuple]:
        """Check reflection trigger and handle the reflection decision.

        Returns:
            None - no reflection triggered, or decision was "continue"
            ("exit", LoopResult) - terminate or escalate
            ("guidance", text, plan) - adjust/pivot guidance + maybe replanned
        """
        # Skip reflection for read-only runs (no mutations = low-stakes, nothing to reflect on)
        if not state.get("had_side_effects"):
            return None

        elapsed = time.time() - state["start_time"]

        should_reflect, trigger = self.reflection_manager.should_reflect(
            turn_number=turn_number,
            max_turns=self.max_turns,
            elapsed_seconds=elapsed,
            timeout_seconds=self.timeout_seconds,
            last_tool_failed=last_tool_failed,
        )

        if not should_reflect:
            return None

        reflection = self.reflection_manager.reflect(
            original_task=message,
            turns=state["turns"],
            turn_number=turn_number,
            max_turns=self.max_turns,
            elapsed_seconds=elapsed,
            timeout_seconds=self.timeout_seconds,
            trigger=trigger,
            atomic_state=atomic_state,
        )
        state["reflections"].append(reflection)

        # Emit trace event for the reflection
        self._emit_trace_event(
            state, "reflection_triggered", turn=turn_number,
            detail=f"{reflection.decision}: {reflection.reasoning[:120] if reflection.reasoning else ''}",
        )

        if reflection.decision == "terminate":
            if self.learning_manager and self.learning_config.learn_from_reflection:
                self.learning_manager.learn_from_error(
                    error_message=f"Task terminated: {reflection.reasoning}",
                    context=message, tool_name=None,
                    resolution=f"Reflection decision: {reflection.next_action or 'terminate'}",
                )
            result = self._build_loop_result(
                "completed", state,
                final_response=f"Task terminated after reflection: {reflection.reasoning}",
            )
            return ("exit", result)

        elif reflection.decision == "escalate":
            if self.learning_manager and self.learning_config.learn_from_reflection:
                self.learning_manager.learn_from_error(
                    error_message=f"Escalation needed: {reflection.reasoning}",
                    context=message, tool_name=None,
                    resolution="Escalated to user",
                )
            result = self._build_loop_result(
                "escalation_needed", state,
                final_response=None,
                error=f"Escalation needed: {reflection.reasoning}",
            )
            return ("exit", result)

        elif reflection.decision in ("adjust", "pivot") and reflection.next_action:
            if self.learning_manager and self.learning_config.learn_from_reflection:
                self.learning_manager.learn_from_success(
                    task_description=message,
                    approach=f"Reflection-driven {reflection.decision}: {reflection.reasoning}",
                    result=f"Strategy adjusted: {reflection.next_action}",
                    key_steps=state["tool_sequence"].copy(),
                )

            # If pivoting and we have a plan, trigger replan
            plan = state["plan"]
            if reflection.decision == "pivot" and plan:
                try:
                    plan = self.planning_manager.replan(
                        reason=f"Reflection pivot: {reflection.reasoning}",
                        context=reflection.next_action,
                    )
                    state["plan"] = plan
                    self._emit_trace_event(
                        state, "replan", turn=turn_number,
                        detail=f"Reflection pivot: {reflection.reasoning[:120] if reflection.reasoning else ''}",
                    )
                except ValueError:
                    pass

            return ("guidance", reflection.next_action, state["plan"])

        return None  # "continue" decision

    def _check_step_completion_atomic(
        self, atomic_state: AtomicState, step_summary: str,
    ) -> tuple:
        """Check plan step completion using atomic state (structured data).

        Instead of searching for natural language signals like "step complete",
        this checks whether the agent's completed_steps list contains entries
        that semantically match the current plan step description.

        Returns:
            (is_complete, result_summary)
        """
        if not self.planning_manager.current_plan or not self.planning_manager.current_plan.current_step:
            return False, ""

        step = self.planning_manager.current_plan.current_step
        step_desc_lower = step.description.lower()

        # Extract key words from the plan step description (3+ char words)
        step_keywords = {
            w for w in step_desc_lower.split()
            if len(w) >= 3 and w not in {"the", "and", "for", "with", "from", "into", "that", "this"}
        }

        if not step_keywords:
            return False, ""

        # Check if any completed_step entry matches the plan step
        for completed in atomic_state.completed_steps:
            completed_lower = completed.lower()
            # Count how many plan step keywords appear in the completed step
            matches = sum(1 for kw in step_keywords if kw in completed_lower)
            # If more than half the keywords match, consider it complete
            if matches >= max(1, len(step_keywords) * 0.4):
                return True, completed

        # Also check the current step_summary for strong signals
        if step_summary:
            summary_lower = step_summary.lower()
            matches = sum(1 for kw in step_keywords if kw in summary_lower)
            if matches >= max(1, len(step_keywords) * 0.4):
                # Only count as complete if we have evidence of tool execution
                if len(self.planning_manager._step_actions) > 0:
                    return True, step_summary

        # Fallback: if exceeded turn limit for this step, auto-advance
        # instead of expensive LLM check -- keyword matching is good enough
        if self.planning_manager._step_turn_count >= self.planning_manager.config.max_turns_per_step:
            return True, f"Auto-advanced after {self.planning_manager._step_turn_count} turns"

        return False, ""

    def _handle_planning(self, tool_calls_list: list, step_text: str,
                         message: str, state: dict,
                         turn_number: int = 0,
                         atomic_state: Optional[AtomicState] = None) -> Optional[str]:
        """Record turn, check step completion, advance, replan if needed.

        Returns updated plan_context string if the plan context changed,
        or None if no change. Each mode injects this context its own way.

        In atomic mode, step completion uses structured state data
        (completed_steps, step_summary) rather than natural language heuristics.
        """
        plan = state["plan"]
        if not plan:
            return None

        self.planning_manager.record_turn(tool_calls_list)

        plan_context = None

        # Check if current step is complete
        if atomic_state is not None:
            # Atomic mode: use structured state for step completion
            is_step_complete, step_summary = self._check_step_completion_atomic(
                atomic_state, step_text
            )
        else:
            # Standard mode: use text heuristics
            is_step_complete, step_summary = self.planning_manager.check_step_completion(
                last_response=step_text
            )
        if is_step_complete:
            # Capture step index before advance changes it
            completed_idx = self.planning_manager.current_plan.current_step_index if self.planning_manager.current_plan else None
            self._emit_trace_event(
                state, "step_completed", turn=turn_number,
                step_index=completed_idx, detail=step_summary or "",
            )

            next_step = self.planning_manager.advance_plan(step_summary)
            if next_step and self.planning_manager.config.inject_plan_context:
                plan_context = self.planning_manager.get_current_step_context()

            # Emit step_started for the new step
            if next_step:
                new_idx = self.planning_manager.current_plan.current_step_index if self.planning_manager.current_plan else None
                new_desc = next_step.description if hasattr(next_step, 'description') else str(next_step)
                self._emit_trace_event(
                    state, "step_started", turn=turn_number,
                    step_index=new_idx, detail=new_desc,
                )

        # Check if replanning is needed
        should_replan, replan_reason = self.planning_manager.should_replan()
        if should_replan:
            state["plan"] = self.planning_manager.replan(replan_reason)
            self._emit_trace_event(
                state, "replan", turn=turn_number, detail=replan_reason or "",
            )
            if self.planning_manager.config.inject_plan_context:
                plan_context = self.planning_manager.get_current_step_context()

        return plan_context

    def _learn_on_completion(self, message: str, turn_number: int, state: dict,
                             final_response: str) -> None:
        """Learn from successful task completion and capture domain facts."""
        if not self.learning_manager:
            return

        # Skip learning for read-only runs (nothing meaningful to learn from)
        if not state.get("had_side_effects"):
            logger.debug("Skipping post-execution learning: no side effects detected")
            return

        if self.learning_config.learn_from_success:
            tools_str = ", ".join(set(state["tools_called"])) or "none"
            self.learning_manager.learn_from_success(
                task_description=message,
                approach=f"Completed in {turn_number} turns using tools: {tools_str}",
                result=(final_response or "Task completed")[:500],
                key_steps=state["tool_sequence"],
            )

        if self.learning_config.learn_domain_facts and state["observations"]:
            self.learning_manager.learn_domain_facts(
                observations=state["observations"],
                files_accessed=state["files_accessed"],
                context=message,
            )

    def _emit_trace_event(self, state: dict, event: str,
                          turn: Optional[int] = None,
                          step_index: Optional[int] = None,
                          detail: str = "") -> None:
        """Append a trace event to the execution trace."""
        state["execution_trace"].append({
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "step_index": step_index,
            "detail": detail,
        })

    # ====================================================================
    # EXECUTION (two-phase atomic loop)
    # ====================================================================

    def execute(
        self,
        message: str,
        system_prompt: str = "You are a helpful AI assistant.",
        identity_prompt: str = "",
        skills_prompt: str = "",
        memory_prompt: str = "",
        conversation_history: List[Dict] = None,
        enabled_tools: List[str] = None,
        turn_callback: Callable[[int, List[str], int], None] = None,
        cancel_check: Callable[[], bool] = None,
        phase1_model: str = None,
        phase2_model: str = None,
        heartbeat_context: Optional[str] = None,
        skip_planning: bool = False,
    ) -> LoopResult:
        """Execute the agentic loop using two-phase atomic execution.

        Uses a compact state dict and two-phase LLM calls per turn:

        Phase 1 (Reasoning): LLM sees system prompt + state dict + tool
            names (no schemas) + last tool result. Outputs JSON with
            state_update, tool choice, and intent string.

        Phase 2 (Parameters): LLM sees intent + ONE tool schema + state
            variables. Outputs native tool_use block with parameters.

        Args:
            message: User message to process
            system_prompt: System prompt for the agent
            identity_prompt: Optional identity context
            skills_prompt: Optional skills context
            memory_prompt: Optional memory context
            conversation_history: Ignored in atomic mode (state-based)
            enabled_tools: List of enabled tool names (None = all)
            turn_callback: Optional callback after each turn
            cancel_check: Optional callable returning True to cancel
            phase1_model: Model for reasoning phase (None = use main client)
            phase2_model: Model for parameter gen (None = use main client)

        Returns:
            LoopResult with status, turns, and final response
        """
        # Shared pre-loop setup
        exec_state = self._init_execution(message, skip_planning=skip_planning)

        # Build full system prompt
        full_system, _ = self.context_builder.build(
            system_prompt=system_prompt,
            identity_prompt=identity_prompt,
            skills_prompt=skills_prompt,
            memory_prompt=memory_prompt,
        )

        # Plan context is NOT added to system prompt in atomic mode --
        # it's injected into the Phase 1 user prompt where it stays current.

        # Add learning insights to system prompt
        if exec_state["insights_context"]:
            full_system = full_system + "\n\n" + exec_state["insights_context"]

        # Get compact tool catalog (name + short hint, ~350 tokens vs ~1850)
        tool_catalog = _build_compact_tool_catalog(self.tool_registry)

        # Initialize atomic state
        atomic_state = AtomicState()

        # Seed atomic state variables with credentials from memory prompt
        # so Phase 2 (which only sees variables, not system prompt) can use
        # exact values for crm_search, crm_write, http_request parameters.
        if memory_prompt and "base_url:" in memory_prompt:
            for line in memory_prompt.splitlines():
                line = line.strip()
                for key in ("base_url", "auth_token", "workspace_id", "member_id", "mail_account_id"):
                    if line.startswith(f"{key}:"):
                        val = line.split(":", 1)[1].strip()
                        if val:
                            atomic_state.variables[key] = val

        # Set up Phase 1/Phase 2 clients if different models requested
        phase1_client = self.llm_client
        if phase1_model and phase1_model != getattr(self.llm_client, 'model', None):
            from llm_client import get_client_for_model
            phase1_client = get_client_for_model(phase1_model)
            if hasattr(phase1_client, 'set_context') and hasattr(self.llm_client, 'agent_id'):
                phase1_client.set_context(
                    agent_id=self.llm_client.agent_id,
                    agent_name=self.llm_client.agent_name,
                    debug_prompts=self.llm_client.debug_prompts,
                )

        phase2_client = self.llm_client
        if phase2_model and phase2_model != getattr(self.llm_client, 'model', None):
            from llm_client import get_client_for_model
            phase2_client = get_client_for_model(phase2_model)
            if hasattr(phase2_client, 'set_context') and hasattr(self.llm_client, 'agent_id'):
                phase2_client.set_context(
                    agent_id=self.llm_client.agent_id,
                    agent_name=self.llm_client.agent_name,
                    debug_prompts=self.llm_client.debug_prompts,
                )

        # Track last tool result (shown once, then discarded)
        last_tool_result: Optional[str] = None

        # Build plan context string for Phase 1 prompt injection
        plan_context_str = ""
        if exec_state["plan"] and self.planning_manager.config.inject_plan_context:
            plan_context_str = self.planning_manager.get_current_step_context() or ""

        for turn_number in range(1, self.max_turns + 1):
            # Check timeout and cancellation
            result = self._check_timeout(message, exec_state)
            if result:
                return result
            result = self._check_cancellation(cancel_check, exec_state)
            if result:
                return result

            turn_start = time.time()
            timestamp = datetime.now(timezone.utc).isoformat()

            # ==============================================================
            # PHASE 1: Reasoning (tool selection + state update)
            # ==============================================================
            phase1_prompt = self._build_phase1_prompt(
                message=message,
                state=atomic_state,
                tool_catalog=tool_catalog,
                last_tool_result=last_tool_result,
                plan_context=plan_context_str,
                turn_exchanges=exec_state["turn_exchanges"],
                heartbeat_context=heartbeat_context,
            )

            phase1_response = phase1_client.complete_json(
                prompt=phase1_prompt,
                system=full_system,
                caller=f"atomic_phase1_turn_{turn_number}",
                max_tokens=2048,
            )

            if not phase1_response:
                if self.learning_manager and self.learning_config.learn_from_errors:
                    self.learning_manager.learn_from_error(
                        error_message="Phase 1 LLM call failed",
                        context=message, tool_name=None, resolution=None,
                    )
                return self._build_loop_result("error", exec_state, error="Phase 1 LLM call failed")

            # Track Phase 1 tokens
            p1_input = getattr(phase1_client, '_last_input_tokens', 0)
            p1_output = getattr(phase1_client, '_last_output_tokens', 0)
            exec_state["total_tokens"] = exec_state["total_tokens"].add(TokenUsage(p1_input, p1_output))

            # Apply state update from Phase 1
            atomic_state.apply_update(phase1_response.get("state_update", {}))

            # Extract Phase 1 outputs
            is_done = phase1_response.get("done", False)
            tool_name = phase1_response.get("tool")
            intent = phase1_response.get("intent", "")
            step_summary = phase1_response.get("step_summary", "")
            response_text = phase1_response.get("response_text")

            # --- Instrumentation: Phase 1 decision ---
            _aid = getattr(self, '_agent_id', 'agent')
            logger.info(
                "[%s] Turn %d Phase1: done=%s tool=%s step=%s",
                _aid, turn_number, is_done, tool_name, (step_summary or "")[:120],
            )
            exec_state["journal"].append({
                "event": "phase1_decision",
                "turn": turn_number,
                "timestamp": timestamp,
                "done": is_done,
                "tool": tool_name,
                "intent": intent,
                "step_summary": step_summary,
                "response_text": response_text,
                "state_update": phase1_response.get("state_update", {}),
                "tokens": {"input": p1_input, "output": p1_output},
            })

            # Track step_summary as observation for learning
            if self.learning_manager and step_summary:
                exec_state["observations"].append(f"Step: {step_summary}")

            # Capture active plan step before creating the turn
            _step_idx = None
            _step_desc = None
            if exec_state["plan"] and hasattr(self.planning_manager, 'plan') and self.planning_manager.current_plan:
                _step_idx = self.planning_manager.current_plan.current_step_index
                cs = self.planning_manager.current_plan.current_step
                _step_desc = cs.description if cs and hasattr(cs, 'description') else None

            # Create turn record
            turn = Turn(
                number=turn_number,
                timestamp=timestamp,
                llm_text=step_summary or response_text or "",
                tool_calls=[],
                tokens_used=TokenUsage(p1_input, p1_output),
                duration_ms=0,
                plan_step_index=_step_idx,
                plan_step_description=_step_desc,
            )

            if is_done or not tool_name:
                # Task complete
                turn.duration_ms = int((time.time() - turn_start) * 1000)
                exec_state["turns"].append(turn)

                # --- Instrumentation: Phase 1 early exit ---
                logger.info(
                    "[%s] Turn %d completed (done=%s, no_tool=%s)",
                    _aid, turn_number, is_done, not tool_name,
                )
                exec_state["journal"].append({
                    "event": "early_exit",
                    "turn": turn_number,
                    "timestamp": timestamp,
                    "done": is_done,
                    "tool_name": tool_name,
                    "response_text": (response_text or "")[:500],
                })

                if exec_state["plan"]:
                    exec_state["plan"].status = "completed"

                final_text = response_text or step_summary or "Task completed."
                self._learn_on_completion(message, turn_number, exec_state, final_text)

                if turn_callback:
                    try:
                        turn_callback(turn_number, [], exec_state["total_tokens"].total)
                    except Exception:
                        pass

                # --- Instrumentation: Loop exit (completed) ---
                elapsed_s = time.time() - exec_state["start_time"]
                logger.info(
                    "[%s] Loop finished: status=completed turns=%d tools=%s tokens=%d duration=%.1fs",
                    _aid, turn_number,
                    list(set(exec_state["tools_called"])),
                    exec_state["total_tokens"].total,
                    elapsed_s,
                )
                exec_state["journal"].append({
                    "event": "loop_exit",
                    "turn": turn_number,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "completed",
                    "total_tokens": exec_state["total_tokens"].total,
                    "duration_s": round(elapsed_s, 1),
                })
                exec_state["pending_actions"] = list(atomic_state.pending_actions)
                return self._build_loop_result("completed", exec_state, final_response=final_text)

            # ==============================================================
            # PHASE 2: Parameter generation (single tool schema)
            # ==============================================================
            tool_schema = self.tool_registry.get_single_schema(tool_name, format="anthropic")
            if not tool_schema:
                last_tool_result = json.dumps({"error": f"Unknown tool: {tool_name}"})
                atomic_state.error_context = f"Tool '{tool_name}' does not exist. Available: {', '.join(self.tool_registry.list_tools())}"
                turn.duration_ms = int((time.time() - turn_start) * 1000)
                exec_state["turns"].append(turn)
                continue

            phase2_messages = self._build_phase2_messages(
                intent=intent, variables=atomic_state.variables,
            )

            phase2_response = phase2_client.complete_with_tools(
                messages=phase2_messages,
                tools=[tool_schema],
                caller=f"atomic_phase2_turn_{turn_number}",
                max_tokens=1024,
            )

            if not phase2_response or not phase2_response.tool_calls:
                # --- Instrumentation: Phase 2 failure ---
                logger.warning(
                    "[%s] Turn %d Phase2 FAILED for tool=%s intent=%s",
                    _aid, turn_number, tool_name, (intent or "")[:100],
                )
                exec_state["journal"].append({
                    "event": "phase2_failure",
                    "turn": turn_number,
                    "timestamp": timestamp,
                    "tool": tool_name,
                    "intent": intent,
                    "had_response": phase2_response is not None,
                })
                last_tool_result = json.dumps({"error": "Phase 2 did not produce a tool call"})
                atomic_state.error_context = "Parameter generation failed. Retry with clearer intent."
                turn.duration_ms = int((time.time() - turn_start) * 1000)
                exec_state["turns"].append(turn)
                if phase2_response:
                    p2_input = phase2_response.usage.input_tokens
                    p2_output = phase2_response.usage.output_tokens
                    exec_state["total_tokens"] = exec_state["total_tokens"].add(TokenUsage(p2_input, p2_output))
                    turn.tokens_used = turn.tokens_used.add(TokenUsage(p2_input, p2_output))
                continue

            # Track Phase 2 tokens
            p2_input = phase2_response.usage.input_tokens
            p2_output = phase2_response.usage.output_tokens
            exec_state["total_tokens"] = exec_state["total_tokens"].add(TokenUsage(p2_input, p2_output))
            turn.tokens_used = turn.tokens_used.add(TokenUsage(p2_input, p2_output))

            # ==============================================================
            # TOOL EXECUTION
            # ==============================================================
            tool_call = phase2_response.tool_calls[0]
            parameters = tool_call.parameters

            # Loop detection
            loop_result = self._check_loop_detection(
                tool_name, parameters, turn, turn_start,
                step_summary, message, exec_state,
            )
            if loop_result:
                return loop_result

            # Execute the tool
            tool_result = self.tool_registry.execute(tool_name, parameters)

            # Record tool call on turn
            tool_record = ToolCallRecord(
                id=tool_call.id, name=tool_name,
                parameters=parameters, result=tool_result,
            )
            turn.tool_calls.append(tool_record)
            exec_state["tools_called"].append(tool_name)

            # Shared skill/learning tracking
            self._process_tool_result(tool_name, parameters, tool_result, message, exec_state)

            # Store result for next Phase 1 (seen once, then discarded)
            last_tool_result = str(tool_result)

            # --- Instrumentation: Tool result ---
            result_preview = last_tool_result[:200] if last_tool_result else ""
            logger.info(
                "[%s] Turn %d Tool %s: success=%s result=%s",
                _aid, turn_number, tool_name, tool_result.success,
                result_preview.replace("\n", " "),
            )
            exec_state["journal"].append({
                "event": "tool_result",
                "turn": turn_number,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": tool_name,
                "success": tool_result.success,
                "error": tool_result.error,
                "output_preview": (tool_result.output or "")[:500],
                "parameters": parameters,
                "tokens": {"input": p2_input, "output": p2_output},
            })

            # Record turn exchange for intra-heartbeat context
            exec_state["turn_exchanges"].append(TurnExchange(
                turn=turn_number,
                tool=tool_name,
                intent=(intent or "")[:200],
                result_preview=(tool_result.output or "")[:1200],
                success=tool_result.success,
            ))

            # Update atomic error context
            if tool_result.success:
                atomic_state.error_context = None
            else:
                atomic_state.error_context = f"Tool error: {tool_result.error}"

            turn.duration_ms = int((time.time() - turn_start) * 1000)
            exec_state["turns"].append(turn)

            # Track for reflection
            self.reflection_manager.record_tool_result(tool_result.success)

            # Planning: record turn, check step completion, replan
            plan_context = self._handle_planning(
                [tool_call], step_summary, message, exec_state,
                turn_number=turn_number,
                atomic_state=atomic_state,
            )
            if plan_context:
                plan_context_str = plan_context

            # Reflection
            ref_result = self._handle_reflection(
                turn_number, not tool_result.success, message, exec_state,
                atomic_state=atomic_state,
            )
            if ref_result:
                if ref_result[0] == "exit":
                    return ref_result[1]
                elif ref_result[0] == "guidance":
                    # In atomic mode, inject via state (not conversation)
                    atomic_state.error_context = f"Reflection guidance: {ref_result[1]}"
                    # Update plan_context_str if plan was replanned
                    if ref_result[2] and self.planning_manager.config.inject_plan_context:
                        plan_context_str = self.planning_manager.get_current_step_context() or plan_context_str

            # Turn callback
            if turn_callback:
                try:
                    turn_tools = [tc.name for tc in turn.tool_calls]
                    turn_callback(turn_number, turn_tools, exec_state["total_tokens"].total)
                except Exception:
                    pass

        # Max turns reached
        # --- Instrumentation: Loop exit (max_turns) ---
        elapsed_s = time.time() - exec_state["start_time"]
        logger.warning(
            "[%s] Loop finished: status=max_turns turns=%d tools=%s tokens=%d duration=%.1fs",
            self._agent_id, self.max_turns,
            list(set(exec_state["tools_called"])),
            exec_state["total_tokens"].total,
            elapsed_s,
        )
        exec_state["journal"].append({
            "event": "loop_exit",
            "turn": self.max_turns,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "max_turns",
            "total_tokens": exec_state["total_tokens"].total,
            "duration_s": round(elapsed_s, 1),
        })

        if self.learning_manager and self.learning_config.learn_from_errors:
            self.learning_manager.learn_from_error(
                error_message=f"Reached maximum turns ({self.max_turns})",
                context=message, tool_name=None, resolution=None,
            )
        exec_state["pending_actions"] = list(atomic_state.pending_actions)
        return self._build_loop_result(
            "max_turns", exec_state,
            final_response=exec_state["turns"][-1].llm_text if exec_state["turns"] else None,
            error=f"Reached maximum turns ({self.max_turns})",
        )

    def _build_phase1_prompt(
        self,
        message: str,
        state: AtomicState,
        tool_catalog: str,
        last_tool_result: Optional[str],
        plan_context: str = "",
        turn_exchanges: Optional[List["TurnExchange"]] = None,
        heartbeat_context: Optional[str] = None,
    ) -> str:
        """Build the prompt for Phase 1 (reasoning + tool selection).

        Args:
            message: The original user task/message
            state: Current atomic state (variables, completed steps, errors)
            tool_catalog: Formatted list of tool names + descriptions
            last_tool_result: Result from the previous turn's tool execution
            plan_context: Current plan step context from PlanningManager
            turn_exchanges: Intra-heartbeat turn history
            heartbeat_context: Cross-heartbeat summary string
        """
        parts = []

        parts.append(f"## Task\n{message}")

        if plan_context:
            parts.append(f"## Plan Context\n{plan_context}")

        parts.append(f"## Current State\n```json\n{json.dumps(state.to_dict(), indent=2)}\n```")

        parts.append(f"## Available Tools\n{tool_catalog}")

        # Recent History: cross-heartbeat + intra-heartbeat + last tool result
        history_parts = []

        if heartbeat_context:
            history_parts.append(f"### Prior Heartbeats\n{heartbeat_context}")

        if turn_exchanges:
            lines = [te.format_line() for te in turn_exchanges]
            history_parts.append(f"### This Run\n" + "\n".join(lines))

        if last_tool_result:
            result_str = last_tool_result
            if len(result_str) > 4000:
                result_str = result_str[:4000] + "\n... [truncated]"
            history_parts.append(f"### Last Tool Result\n```\n{result_str}\n```")

        if history_parts:
            parts.append("## Recent History\n" + "\n\n".join(history_parts))

        parts.append("""## Instructions
Follow this process every turn:

1. ANALYZE the last tool result (if any). What happened? Did it succeed? Does it contain new information, requests, or tasks to do?
2. UPDATE state: save important IDs/values to variables, record what you completed in completed_steps (only NEW steps, not previously listed ones).
3. UPDATE pending_actions: If the tool result revealed new work items (e.g., a DM asks you to do 3 things, or API data shows issues to address), add them. Remove any you just completed. These persist across turns so nothing gets lost.
4. DECIDE next action: Pick the next pending_action to work on, or if none remain and the original task is done, set done=true.

Respond with JSON only:
{
  "analysis": "What the last tool result revealed (new data, requests, errors). Write 'First turn' if no prior result.",
  "state_update": {
    "variables": {"key": "value"},
    "completed_steps": ["only NEW step descriptions, not previously listed ones"],
    "pending_actions": ["remaining actions still to do — FULL list, not just new ones"]
  },
  "step_summary": "What you will do next (or what you just finished)",
  "tool": "tool_name or null if done",
  "intent": "Describe exactly what the tool should do, include specific values/IDs from state.variables",
  "done": false,
  "response_text": null
}

CRITICAL RULES:
- pending_actions is the FULL remaining list each turn (not incremental). Remove items you completed, add items you discovered.
- Do NOT set done=true while pending_actions still has items. Work through them first.
- When done=true, set tool=null and put the final answer in response_text.
- Include specific values and IDs from state.variables in the intent string.
- If a tool result contains a human message asking you to do things, extract EACH request as a separate pending_action.""")

        return "\n\n".join(parts)

    def _build_phase2_messages(
        self,
        intent: str,
        variables: Dict[str, Any],
    ) -> List[Dict]:
        """Build messages for Phase 2 (parameter generation)."""
        content = f"Execute this action: {intent}"
        if variables:
            # Include variables for ID resolution
            vars_str = json.dumps(variables, indent=2)
            if len(vars_str) > 2000:
                vars_str = vars_str[:2000] + "\n..."
            content += f"\n\nAvailable variables for reference:\n```json\n{vars_str}\n```"
        content += (
            "\n\nCall the tool with ALL required parameters. "
            "IMPORTANT: If the tool schema has an object-typed parameter (like 'data', "
            "'filters', 'body'), you MUST include it as a JSON object with the relevant "
            "fields described in the intent. Do NOT flatten object fields into top-level "
            "parameters -- nest them inside the object parameter."
        )

        return [{"role": "user", "content": content}]


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Engine")
    print("=" * 60)
    print("\nThis module provides the core agentic loop.")
    print("For a full test, run with an LLM client and tool registry.")
    print("\nExample usage:")
    print("""
    from llm_client import get_anthropic_client
    from loop_core.tools import ToolRegistry
    from loop_core.tools.file_tools import FileReadTool
    from loop_core.loop import AgenticLoop

    # Setup
    client = get_anthropic_client()
    registry = ToolRegistry()
    registry.register(FileReadTool(allowed_paths=["./data"]))

    # Create loop
    loop = AgenticLoop(
        llm_client=client,
        tool_registry=registry,
        max_turns=10
    )

    # Execute
    result = loop.execute(
        message="What skills are available?",
        system_prompt="You are a helpful assistant."
    )

    print(f"Status: {result.status}")
    print(f"Response: {result.final_response}")
    print(f"Turns: {len(result.turns)}")
    print(f"Tools used: {result.tools_called}")
    """)
