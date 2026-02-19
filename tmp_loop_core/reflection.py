"""
REFLECTION
==========

Self-evaluation capability for the Agentic Loop Framework.

Reflection enables the agent to pause execution, evaluate progress,
identify problems, and adjust approach. It transforms blind execution
into self-aware problem-solving.

Usage:
    from loop_core.reflection import ReflectionManager, ReflectionConfig

    config = ReflectionConfig(interval_turns=5)
    manager = ReflectionManager(llm_client, config)

    # In the loop
    if manager.should_reflect(turn_number, max_turns, elapsed, timeout):
        result = manager.reflect(original_task, turns, ...)
        if result.decision == "escalate":
            # Handle escalation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Literal, Any, TYPE_CHECKING

import time

from .observability import get_current_task, estimate_cost

if TYPE_CHECKING:
    from .loop import Turn


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ReflectionConfig:
    """Configuration for reflection behavior.

    Tuned for atomic agentic loop (2026-02-11):
    - interval_turns=0: disabled; atomic state already tracks progress
    - reflect_on_tool_failure=False: atomic error_context handles this
    - no_progress_turns=3: kept; consecutive failures need course correction
    - max_reflections=2: cap overhead; most value is in first 1-2 reflections
    """

    # Enable/disable reflection
    enabled: bool = True

    # Interval trigger: reflect every N turns (0 = disabled)
    interval_turns: int = 0

    # Progress trigger: reflect after N turns without tool success
    no_progress_turns: int = 3

    # Reflect after tool failure (disabled: atomic error_context suffices)
    reflect_on_tool_failure: bool = False

    # Reflect when approaching limits (fraction of max_turns/timeout)
    resource_warning_threshold: float = 0.8

    # Maximum reflections per execution
    max_reflections: int = 2

    # Token budget per reflection
    max_reflection_tokens: int = 500

    # Temperature for reflection (slightly higher for self-critique)
    reflection_temperature: float = 0.3

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "interval_turns": self.interval_turns,
            "no_progress_turns": self.no_progress_turns,
            "reflect_on_tool_failure": self.reflect_on_tool_failure,
            "resource_warning_threshold": self.resource_warning_threshold,
            "max_reflections": self.max_reflections,
            "max_reflection_tokens": self.max_reflection_tokens,
            "reflection_temperature": self.reflection_temperature,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ReflectionConfig":
        return cls(
            enabled=data.get("enabled", True),
            interval_turns=data.get("interval_turns", 5),
            no_progress_turns=data.get("no_progress_turns", 3),
            reflect_on_tool_failure=data.get("reflect_on_tool_failure", True),
            resource_warning_threshold=data.get("resource_warning_threshold", 0.8),
            max_reflections=data.get("max_reflections", 5),
            max_reflection_tokens=data.get("max_reflection_tokens", 500),
            reflection_temperature=data.get("reflection_temperature", 0.3),
        )


# ============================================================================
# RESULT DATA STRUCTURE
# ============================================================================

@dataclass
class ReflectionResult:
    """Result of a reflection evaluation."""

    # When reflection occurred
    turn_number: int
    timestamp: str

    # Trigger that caused reflection
    trigger: Literal[
        "interval",           # Every N turns
        "no_progress",        # Detected stagnation
        "tool_failure",       # Tool returned error
        "loop_warning",       # Near loop detection threshold
        "resource_warning",   # Near max_turns or timeout
        "explicit"            # Skill or user requested
    ]

    # Assessment
    progress_assessment: Literal["good", "slow", "stuck", "regressing"]
    confidence_in_approach: float  # 0.0 - 1.0

    # Analysis
    what_worked: List[str] = field(default_factory=list)
    what_failed: List[str] = field(default_factory=list)
    blockers_identified: List[str] = field(default_factory=list)

    # Decision
    decision: Literal[
        "continue",           # Keep current approach
        "adjust",             # Modify current approach
        "pivot",              # Try completely different approach
        "escalate",           # Need human help
        "terminate"           # Give up with partial result
    ] = "continue"

    # Action plan (if adjust or pivot)
    next_action: Optional[str] = None
    reasoning: str = ""

    # Tokens used for reflection (tracked via llm_client)
    tokens_used: int = 0

    def to_dict(self) -> Dict:
        return {
            "turn_number": self.turn_number,
            "timestamp": self.timestamp,
            "trigger": self.trigger,
            "progress_assessment": self.progress_assessment,
            "confidence_in_approach": self.confidence_in_approach,
            "what_worked": self.what_worked,
            "what_failed": self.what_failed,
            "blockers_identified": self.blockers_identified,
            "decision": self.decision,
            "next_action": self.next_action,
            "reasoning": self.reasoning,
            "tokens_used": self.tokens_used,
        }


# ============================================================================
# PROMPTS
# ============================================================================

REFLECTION_SYSTEM_PROMPT = """You are performing a reflection checkpoint during task execution.

Your job is to honestly evaluate progress and decide the best path forward.

Be CRITICAL and HONEST. Do not rationalize poor progress. If something isn't working, say so.

You must respond with valid JSON matching this schema:
{
    "progress_assessment": "good" | "slow" | "stuck" | "regressing",
    "confidence_in_approach": 0.0-1.0,
    "what_worked": ["list of successful actions"],
    "what_failed": ["list of failed actions"],
    "blockers_identified": ["list of obstacles"],
    "decision": "continue" | "adjust" | "pivot" | "escalate" | "terminate",
    "next_action": "specific next step if adjusting/pivoting, null otherwise",
    "reasoning": "brief explanation of your assessment and decision"
}

Decision guidelines:
- "continue": Clear progress, current approach working
- "adjust": Approach is right but needs minor modification
- "pivot": Current approach fundamentally flawed, try different strategy
- "escalate": Need information or capability you don't have
- "terminate": Task cannot be completed, return best partial result"""


REFLECTION_PROMPT_TEMPLATE = """## Reflection Checkpoint

### Original Task
{original_task}

### Progress So Far
Turns completed: {turns_completed} / {max_turns}
Time elapsed: {elapsed_seconds}s / {timeout_seconds}s
Tools called: {tools_called}
Tool successes: {tool_successes}
Tool failures: {tool_failures}

### Recent Actions (last {recent_turns} turns)
{recent_actions}

### Trigger for This Reflection
{trigger_reason}

### Reflect Now
Evaluate your progress and decide how to proceed. Be honest about what's working and what isn't."""


# ============================================================================
# REFLECTION MANAGER
# ============================================================================

class ReflectionManager:
    """
    Manages reflection during agentic loop execution.

    Uses the provided llm_client for all LLM calls, ensuring proper
    cost tracking and usage monitoring through the existing infrastructure.
    """

    def __init__(
        self,
        llm_client,
        config: ReflectionConfig = None
    ):
        """
        Initialize the reflection manager.

        Args:
            llm_client: LLM client instance (must have complete_json method).
                       All calls go through this client for cost tracking.
            config: Reflection configuration
        """
        self.llm_client = llm_client
        self.config = config or ReflectionConfig()
        self.reflection_count = 0
        self.last_reflection_turn = 0
        self._recent_tool_results: List[bool] = []  # Success/failure tracking

    def reset(self) -> None:
        """Reset state for new execution."""
        self.reflection_count = 0
        self.last_reflection_turn = 0
        self._recent_tool_results = []

    def record_tool_result(self, success: bool) -> None:
        """
        Record a tool execution result for progress tracking.

        Args:
            success: Whether the tool call succeeded
        """
        self._recent_tool_results.append(success)
        # Keep last N results for no_progress detection
        if len(self._recent_tool_results) > 10:
            self._recent_tool_results.pop(0)

    def should_reflect(
        self,
        turn_number: int,
        max_turns: int,
        elapsed_seconds: float,
        timeout_seconds: float,
        last_tool_failed: bool = False
    ) -> tuple:
        """
        Determine if reflection should occur.

        Args:
            turn_number: Current turn number
            max_turns: Maximum turns allowed
            elapsed_seconds: Time elapsed so far
            timeout_seconds: Timeout limit
            last_tool_failed: Whether the last tool call failed

        Returns:
            Tuple of (should_reflect: bool, trigger_reason: str)
        """
        if not self.config.enabled:
            return False, ""

        if self.reflection_count >= self.config.max_reflections:
            return False, ""

        # Check interval trigger
        if self.config.interval_turns > 0:
            turns_since_reflection = turn_number - self.last_reflection_turn
            if turns_since_reflection >= self.config.interval_turns:
                return True, "interval"

        # Check tool failure trigger
        if last_tool_failed and self.config.reflect_on_tool_failure:
            return True, "tool_failure"

        # Check no progress trigger
        if self.config.no_progress_turns > 0:
            recent = self._recent_tool_results[-self.config.no_progress_turns:]
            if len(recent) >= self.config.no_progress_turns and not any(recent):
                return True, "no_progress"

        # Check resource warning trigger
        turn_ratio = turn_number / max_turns if max_turns > 0 else 0
        time_ratio = elapsed_seconds / timeout_seconds if timeout_seconds > 0 else 0
        if max(turn_ratio, time_ratio) >= self.config.resource_warning_threshold:
            # Only trigger once for resource warning
            if not hasattr(self, '_resource_warning_triggered'):
                self._resource_warning_triggered = True
                return True, "resource_warning"

        return False, ""

    def reflect(
        self,
        original_task: str,
        turns: List["Turn"],
        turn_number: int,
        max_turns: int,
        elapsed_seconds: float,
        timeout_seconds: float,
        trigger: str,
        atomic_state=None,
    ) -> ReflectionResult:
        """
        Perform reflection and return result.

        All LLM calls go through self.llm_client to maintain proper
        cost tracking and usage monitoring.

        Args:
            original_task: The original user message/task
            turns: List of Turn objects so far
            turn_number: Current turn number
            max_turns: Maximum turns allowed
            elapsed_seconds: Time elapsed
            timeout_seconds: Timeout limit
            trigger: What triggered this reflection
            atomic_state: Optional AtomicState with completed_steps and pending_actions

        Returns:
            ReflectionResult with assessment and decision
        """
        # Track reflection in HiveLoop
        _task = get_current_task()
        if _task:
            try:
                _task.event("reflection_started", trigger=trigger, turn=turn_number)
            except Exception:
                pass

        # Build reflection prompt
        prompt = self._build_reflection_prompt(
            original_task=original_task,
            turns=turns,
            turn_number=turn_number,
            max_turns=max_turns,
            elapsed_seconds=elapsed_seconds,
            timeout_seconds=timeout_seconds,
            trigger=trigger,
            atomic_state=atomic_state,
        )

        # Get token count before call for tracking
        tokens_before = self.llm_client.total_output_tokens if hasattr(self.llm_client, 'total_output_tokens') else 0

        # Call LLM for reflection using existing client (maintains cost tracking)
        _llm_start = time.perf_counter()
        response = self.llm_client.complete_json(
            prompt=prompt,
            system=REFLECTION_SYSTEM_PROMPT,
            caller="reflection",
            max_tokens=self.config.max_reflection_tokens
        )
        _llm_elapsed = (time.perf_counter() - _llm_start) * 1000

        # Calculate tokens used for this reflection
        tokens_after = self.llm_client.total_output_tokens if hasattr(self.llm_client, 'total_output_tokens') else 0
        tokens_used = tokens_after - tokens_before

        # HiveLoop: Reflection LLM call tracking
        _r_input = getattr(self.llm_client, '_last_input_tokens', 0)
        _r_output = getattr(self.llm_client, '_last_output_tokens', 0)
        if _task:
            try:
                _task.llm_call(
                    "reflection",
                    model=self.llm_client.model,
                    tokens_in=_r_input,
                    tokens_out=_r_output,
                    cost=estimate_cost(self.llm_client.model, _r_input, _r_output),
                    duration_ms=round(_llm_elapsed),
                    metadata={
                        "turn_number": turn_number,
                        "trigger": trigger,
                        "cache_read_tokens": getattr(self.llm_client, '_last_cache_read_tokens', None),
                        "cache_write_tokens": getattr(self.llm_client, '_last_cache_creation_tokens', None),
                        "stop_reason": getattr(self.llm_client, '_last_stop_reason', None),
                    },
                )
            except Exception:
                pass

        # Parse response
        result = self._parse_reflection_response(
            response=response,
            turn_number=turn_number,
            trigger=trigger,
            tokens_used=tokens_used
        )

        # Update state
        self.reflection_count += 1
        self.last_reflection_turn = turn_number

        # Report reflection result to HiveLoop
        if _task:
            try:
                _task.event(
                    "reflection_completed",
                    decision=result.decision,
                    trigger=trigger,
                    turn=turn_number,
                    tokens=tokens_used,
                )
            except Exception:
                pass

        return result

    def _build_reflection_prompt(
        self,
        original_task: str,
        turns: List["Turn"],
        turn_number: int,
        max_turns: int,
        elapsed_seconds: float,
        timeout_seconds: float,
        trigger: str,
        atomic_state=None,
    ) -> str:
        """Build the reflection prompt from template."""
        # Count tool results
        tool_successes = 0
        tool_failures = 0
        tools_called = set()

        for turn in turns:
            for tc in turn.tool_calls:
                tools_called.add(tc.name)
                if tc.result.success:
                    tool_successes += 1
                else:
                    tool_failures += 1

        # Format recent actions
        recent_turns = turns[-5:] if len(turns) > 5 else turns
        recent_actions = []
        for turn in recent_turns:
            action = f"Turn {turn.number}: "
            if turn.tool_calls:
                tools = [f"{tc.name}({'success' if tc.result.success else 'FAILED'})"
                        for tc in turn.tool_calls]
                action += ", ".join(tools)
            else:
                action += "Response (no tools)"
            recent_actions.append(action)

        # Map trigger to human-readable reason
        trigger_reasons = {
            "interval": f"Regular checkpoint (every {self.config.interval_turns} turns)",
            "no_progress": f"No successful tool calls in last {self.config.no_progress_turns} turns",
            "tool_failure": "Tool execution failed",
            "resource_warning": f"Approaching resource limits ({turn_number}/{max_turns} turns)",
            "loop_warning": "Possible infinite loop detected",
            "explicit": "Explicit reflection requested"
        }

        prompt = REFLECTION_PROMPT_TEMPLATE.format(
            original_task=original_task,
            turns_completed=turn_number,
            max_turns=max_turns,
            elapsed_seconds=int(elapsed_seconds),
            timeout_seconds=int(timeout_seconds),
            tools_called=", ".join(tools_called) or "None",
            tool_successes=tool_successes,
            tool_failures=tool_failures,
            recent_turns=len(recent_actions),
            recent_actions="\n".join(recent_actions) if recent_actions else "No actions yet",
            trigger_reason=trigger_reasons.get(trigger, trigger)
        )

        # Inject atomic state context so reflection can see actual progress
        if atomic_state:
            completed = getattr(atomic_state, "completed_steps", []) or []
            pending = getattr(atomic_state, "pending_actions", []) or []
            if completed or pending:
                state_lines = ["\n\n### Task Progress (from execution state)"]
                if completed:
                    state_lines.append("Completed:")
                    for step in completed:
                        state_lines.append(f"  - {step}")
                if pending:
                    state_lines.append("Still pending:")
                    for act in pending:
                        state_lines.append(f"  - {act}")
                prompt += "\n".join(state_lines)

        return prompt

    def _parse_reflection_response(
        self,
        response: Optional[Dict],
        turn_number: int,
        trigger: str,
        tokens_used: int = 0
    ) -> ReflectionResult:
        """Parse LLM response into ReflectionResult."""
        timestamp = datetime.now(timezone.utc).isoformat()

        if not response:
            # Fallback if LLM fails
            return ReflectionResult(
                turn_number=turn_number,
                timestamp=timestamp,
                trigger=trigger,
                progress_assessment="stuck",
                confidence_in_approach=0.5,
                what_worked=[],
                what_failed=["Reflection failed"],
                blockers_identified=["Could not assess progress"],
                decision="continue",
                reasoning="Reflection LLM call failed, continuing with current approach",
                tokens_used=tokens_used
            )

        # Validate and extract fields with defaults
        progress = response.get("progress_assessment", "slow")
        if progress not in ("good", "slow", "stuck", "regressing"):
            progress = "slow"

        decision = response.get("decision", "continue")
        if decision not in ("continue", "adjust", "pivot", "escalate", "terminate"):
            decision = "continue"

        confidence = response.get("confidence_in_approach", 0.5)
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        return ReflectionResult(
            turn_number=turn_number,
            timestamp=timestamp,
            trigger=trigger,
            progress_assessment=progress,
            confidence_in_approach=confidence,
            what_worked=response.get("what_worked", []) or [],
            what_failed=response.get("what_failed", []) or [],
            blockers_identified=response.get("blockers_identified", []) or [],
            decision=decision,
            next_action=response.get("next_action"),
            reasoning=response.get("reasoning", ""),
            tokens_used=tokens_used
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get reflection statistics."""
        return {
            "reflection_count": self.reflection_count,
            "last_reflection_turn": self.last_reflection_turn,
            "recent_tool_results": self._recent_tool_results[-5:],
            "config": self.config.to_dict()
        }


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Reflection Module")
    print("=" * 60)
    print("\nThis module provides self-evaluation capabilities.")
    print("\nExample usage:")
    print("""
    from llm_client import get_anthropic_client
    from loop_core.reflection import ReflectionManager, ReflectionConfig

    # Setup with existing LLM client (maintains cost tracking)
    client = get_anthropic_client()
    config = ReflectionConfig(interval_turns=5)
    manager = ReflectionManager(client, config)

    # Check if reflection needed
    should, trigger = manager.should_reflect(
        turn_number=5,
        max_turns=20,
        elapsed_seconds=30,
        timeout_seconds=600
    )

    if should:
        result = manager.reflect(
            original_task="Complete the task",
            turns=turns_list,
            turn_number=5,
            max_turns=20,
            elapsed_seconds=30,
            timeout_seconds=600,
            trigger=trigger
        )
        print(f"Decision: {result.decision}")
        print(f"Reasoning: {result.reasoning}")
    """)
