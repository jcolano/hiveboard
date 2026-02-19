"""
PLANNING
========

Task planning capability for the Agentic Loop Framework.

Planning is **optional** and configured per-agent. It enables the agent to break
complex tasks into ordered steps, track progress, and dynamically replan when
circumstances change.

When Planning Triggers
----------------------
``should_plan(task)`` uses heuristics to decide:
- Word count > min_task_complexity (default: 10 words)
- Presence of sequence indicators ("first", "then", "finally", etc.)
- Multiple action verbs in the task description

Plan Lifecycle
--------------
1. **Create**: LLM generates plan with task_understanding, approach, steps,
   estimated_turns, and potential_blockers via ``complete_json()``.
2. **Execute**: Each step tracked via ``record_turn()`` and ``check_step_completion()``.
3. **Advance**: When step completes, ``advance_plan()`` moves to next step.
4. **Replan**: If a step gets stuck (too many turns, blocked), ``replan()``
   preserves completed steps and generates new pending steps.

Context Injection
-----------------
Plans are injected into the agent's prompt via ``get_current_step_context()``:
::

    [PLAN CONTEXT]
    Task: Build a REST API with authentication
    Progress: 33% (1/3 steps)
    Current Step (2/3): Implement JWT middleware
    Criteria: - JWT validation works - Protected routes reject invalid tokens
    Upcoming Steps:
      - Step 3: Add user registration endpoint
    [END PLAN CONTEXT]

Relationship to Skills & Tasks
-------------------------------
- **Planning** is internal to the agent loop (decomposes work into steps).
- **Skills** are external behavior guides (injected into prompt).
- **Tasks** are scheduled work items that may use both skills and planning.
A task with a skill reference can also trigger planning within the agent loop.

Usage::

    config = PlanningConfig(max_steps=10, max_turns_per_step=15)
    manager = PlanningManager(llm_client, config, available_tools=["file_read"])
    plan = manager.create_plan("Build a REST API with authentication")

    # In the loop
    manager.record_turn(tool_calls)
    is_complete, summary = manager.check_step_completion(actions, response)
    if is_complete:
        next_step = manager.advance_plan(summary)
"""

from __future__ import annotations

import uuid
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
class PlanningConfig:
    """Configuration for planning behavior."""

    # Enable/disable planning
    enabled: bool = True

    # Minimum task complexity to trigger planning (word count)
    min_task_complexity: int = 10

    # Maximum steps in a plan
    max_steps: int = 10

    # Maximum turns per step before considering stuck
    max_turns_per_step: int = 5

    # Auto-replan when step is blocked
    auto_replan_on_block: bool = True

    # Token budget for planning
    max_planning_tokens: int = 4096

    # Include plan in conversation context
    inject_plan_context: bool = True

    # Maximum replans before giving up
    max_replans: int = 3

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "min_task_complexity": self.min_task_complexity,
            "max_steps": self.max_steps,
            "max_turns_per_step": self.max_turns_per_step,
            "auto_replan_on_block": self.auto_replan_on_block,
            "max_planning_tokens": self.max_planning_tokens,
            "inject_plan_context": self.inject_plan_context,
            "max_replans": self.max_replans,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PlanningConfig":
        return cls(
            enabled=data.get("enabled", True),
            min_task_complexity=data.get("min_task_complexity", 10),
            max_steps=data.get("max_steps", 10),
            max_turns_per_step=data.get("max_turns_per_step", 5),
            auto_replan_on_block=data.get("auto_replan_on_block", True),
            max_planning_tokens=data.get("max_planning_tokens", 4096),
            inject_plan_context=data.get("inject_plan_context", True),
            max_replans=data.get("max_replans", 3),
        )


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class PlanStep:
    """A single step in an execution plan."""

    # Unique identifier
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Step description
    description: str = ""

    # Status
    status: Literal["pending", "in_progress", "completed", "skipped", "blocked"] = "pending"

    # Ordering
    order: int = 0

    # Dependencies (step_ids that must complete first)
    depends_on: List[str] = field(default_factory=list)

    # Expected tools to use
    expected_tools: List[str] = field(default_factory=list)

    # Acceptance criteria (how to know step is done)
    acceptance_criteria: str = ""

    # Pre-conditions (what must be true before starting)
    pre_conditions: str = ""

    # Failure strategy (what to do if this step fails)
    on_failure: str = ""

    # Execution tracking
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    turns_taken: int = 0

    # Result summary
    result_summary: str = ""
    blockers: List[str] = field(default_factory=list)

    def mark_started(self) -> None:
        """Mark step as in progress."""
        self.status = "in_progress"
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, summary: str = "") -> None:
        """Mark step as completed."""
        self.status = "completed"
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.result_summary = summary

    def mark_blocked(self, blockers: List[str]) -> None:
        """Mark step as blocked."""
        self.status = "blocked"
        self.blockers = blockers

    def mark_skipped(self, reason: str = "") -> None:
        """Mark step as skipped."""
        self.status = "skipped"
        self.result_summary = f"Skipped: {reason}"

    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "status": self.status,
            "order": self.order,
            "depends_on": self.depends_on,
            "expected_tools": self.expected_tools,
            "acceptance_criteria": self.acceptance_criteria,
            "pre_conditions": self.pre_conditions,
            "on_failure": self.on_failure,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "turns_taken": self.turns_taken,
            "result_summary": self.result_summary,
            "blockers": self.blockers
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PlanStep":
        return cls(
            step_id=data.get("step_id", str(uuid.uuid4())[:8]),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            order=data.get("order", 0),
            depends_on=data.get("depends_on", []),
            expected_tools=data.get("expected_tools", []),
            acceptance_criteria=data.get("acceptance_criteria", ""),
            pre_conditions=data.get("pre_conditions", ""),
            on_failure=data.get("on_failure", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            turns_taken=data.get("turns_taken", 0),
            result_summary=data.get("result_summary", ""),
            blockers=data.get("blockers", [])
        )


@dataclass
class ExecutionPlan:
    """Complete execution plan for a task."""

    # Plan metadata
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Original task
    task_description: str = ""

    # Steps
    steps: List[PlanStep] = field(default_factory=list)

    # Current execution state
    current_step_index: int = 0

    # Plan history (for replanning)
    revision_count: int = 0
    revision_history: List[str] = field(default_factory=list)

    # Overall status
    status: Literal["planning", "executing", "completed", "failed", "replanning"] = "planning"

    # Task understanding from LLM
    task_understanding: str = ""
    approach: str = ""
    estimated_turns: int = 0
    potential_blockers: List[str] = field(default_factory=list)

    @property
    def current_step(self) -> Optional[PlanStep]:
        """Get current step being executed."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def completed_steps(self) -> List[PlanStep]:
        """Get all completed steps."""
        return [s for s in self.steps if s.status == "completed"]

    @property
    def pending_steps(self) -> List[PlanStep]:
        """Get all pending steps."""
        return [s for s in self.steps if s.status == "pending"]

    @property
    def progress_percentage(self) -> float:
        """Calculate completion percentage."""
        if not self.steps:
            return 0.0
        completed = len([s for s in self.steps if s.status in ("completed", "skipped")])
        return (completed / len(self.steps)) * 100

    def add_step(self, step: PlanStep) -> None:
        """Add a step to the plan."""
        step.order = len(self.steps)
        self.steps.append(step)

    def insert_step(self, index: int, step: PlanStep) -> None:
        """Insert a step at specific position."""
        step.order = index
        self.steps.insert(index, step)
        # Reorder subsequent steps
        for i, s in enumerate(self.steps[index + 1:], start=index + 1):
            s.order = i

    def advance_to_next_step(self) -> Optional[PlanStep]:
        """Move to the next pending step."""
        for i, step in enumerate(self.steps[self.current_step_index:], start=self.current_step_index):
            if step.status == "pending":
                self.current_step_index = i
                return step
        return None

    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "task_description": self.task_description,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "revision_count": self.revision_count,
            "revision_history": self.revision_history,
            "status": self.status,
            "progress_percentage": self.progress_percentage,
            "task_understanding": self.task_understanding,
            "approach": self.approach,
            "estimated_turns": self.estimated_turns,
            "potential_blockers": self.potential_blockers
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ExecutionPlan":
        steps = [PlanStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())[:12]),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            task_description=data.get("task_description", ""),
            steps=steps,
            current_step_index=data.get("current_step_index", 0),
            revision_count=data.get("revision_count", 0),
            revision_history=data.get("revision_history", []),
            status=data.get("status", "planning"),
            task_understanding=data.get("task_understanding", ""),
            approach=data.get("approach", ""),
            estimated_turns=int(data.get("estimated_turns", 0) or 0),
            potential_blockers=data.get("potential_blockers", [])
        )

    def to_progress_string(self) -> str:
        """Format plan as readable progress string."""
        lines = [f"Plan: {self.task_description}", ""]
        for step in self.steps:
            if step.status == "completed":
                marker = "[x]"
            elif step.status == "in_progress":
                marker = "[>]"
            elif step.status == "blocked":
                marker = "[!]"
            elif step.status == "skipped":
                marker = "[-]"
            else:
                marker = "[ ]"
            lines.append(f"  {step.order + 1}. {marker} {step.description}")
        lines.append(f"\nProgress: {self.progress_percentage:.0f}%")
        return "\n".join(lines)


# ============================================================================
# PROMPTS
# ============================================================================

PLANNING_SYSTEM_PROMPT = """You are creating an execution plan for an AI agent that uses tools to complete tasks.

The agent executes ONE tool per turn, sees the result, then decides the next action. Your plan must account for this sequential tool-by-tool execution.

## Plan Requirements

Each step must be:
- **Atomic**: Achievable in 1-5 tool calls
- **Verifiable**: Has clear criteria to confirm success (not just "do X" but "do X and confirm Y")
- **Self-contained**: States what data/IDs it needs and where they come from (previous step output or known values)

## Step Structure

For each step, specify:
- `description`: What this step accomplishes and HOW (mention specific tools)
- `expected_tools`: Which tools will be used
- `acceptance_criteria`: How to VERIFY this step succeeded (e.g., "API returns 200", "file contains X", "variable Y is set")
- `pre_conditions`: What must be true before starting (e.g., "company_id from step 1 is available")
- `on_failure`: What to do if this step fails (e.g., "retry with different parameters", "skip and note in response", "abort plan")

## Guidelines

1. **Front-load discovery**: Start with information gathering (read, search, list) before taking action (write, create, update)
2. **Verify before proceeding**: After creating/updating something, verify it worked before using the result in the next step
3. **Capture IDs and values**: When a tool returns an ID or value needed later, explicitly note it (e.g., "Save the returned company_id for step 3")
4. **Handle the happy path AND failures**: Note what could go wrong and what to do about it
5. **Keep plans to 3-8 steps**: If the task needs more, break it into phases
6. **Don't assume**: If you need information you don't have, make "gather information" the first step

Respond with valid JSON:
{
    "task_understanding": "Your interpretation of what needs to be done",
    "approach": "High-level strategy including key decision points",
    "steps": [
        {
            "description": "What this step does and how",
            "expected_tools": ["tool1", "tool2"],
            "acceptance_criteria": "How to verify success",
            "pre_conditions": "What must be true before starting (or 'none' for first step)",
            "on_failure": "What to do if this step fails"
        }
    ],
    "estimated_turns": 10,
    "potential_blockers": ["Specific things that might prevent completion"]
}"""


REPLAN_PROMPT_TEMPLATE = """## Replanning Required

### Original Task
{original_task}

### Original Plan
{original_plan}

### Progress So Far
{progress_summary}

### Reason for Replanning
{replan_reason}

### Current Blockers
{blockers}

### Instructions
Create an updated plan that:
1. Keeps completed steps as-is (don't repeat them)
2. Addresses the blockers or new information
3. Provides a path to task completion

Respond with the same JSON schema as the original plan.
Only include NEW steps that still need to be done."""


STEP_COMPLETION_PROMPT = """## Step Completion Check

### Current Step
{step_description}

### Acceptance Criteria
{acceptance_criteria}

### Actions Taken This Step
{actions_taken}

### Question
Is this step complete? Respond with JSON:
{{
    "complete": true/false,
    "reason": "Why complete or what's still needed",
    "result_summary": "Brief summary of what was accomplished"
}}"""


# ============================================================================
# PLANNING MANAGER
# ============================================================================

class PlanningManager:
    """
    Manages execution planning for agentic loops.

    Uses the provided llm_client for all LLM calls, ensuring proper
    cost tracking and usage monitoring through the existing infrastructure.
    """

    def __init__(
        self,
        llm_client,
        config: PlanningConfig = None,
        available_tools: List[str] = None,
        tool_summaries: List[Dict[str, str]] = None
    ):
        """
        Initialize the planning manager.

        Args:
            llm_client: LLM client instance (must have complete_json method).
                       All calls go through this client for cost tracking.
            config: Planning configuration
            available_tools: List of available tool names
            tool_summaries: List of {name, description} dicts for richer planning context
        """
        self.llm_client = llm_client
        self.config = config or PlanningConfig()
        self.available_tools = available_tools or []
        self.tool_summaries = tool_summaries or []
        self.current_plan: Optional[ExecutionPlan] = None
        self._step_turn_count: int = 0
        self._step_actions: List[str] = []
        self._replan_count: int = 0

    def reset(self) -> None:
        """Reset state for new execution."""
        self.current_plan = None
        self._step_turn_count = 0
        self._step_actions = []
        self._replan_count = 0

    def should_plan(self, task: str) -> bool:
        """
        Determine if task needs planning.

        Args:
            task: The task description

        Returns:
            True if planning should be performed
        """
        if not self.config.enabled:
            return False

        # Check complexity (word count as proxy)
        word_count = len(task.split())
        task_lower = task.lower()

        # Additional heuristics: look for sequence indicators
        sequence_words = ["then", "after", "first", "finally", "next", "step", "followed by"]
        has_sequence = any(word in task_lower for word in sequence_words)

        # Look for multiple action verbs
        action_verbs = ["create", "build", "add", "remove", "update", "implement",
                       "write", "read", "modify", "configure", "setup", "test"]
        action_count = sum(1 for verb in action_verbs if verb in task_lower)

        # Plan if:
        # - Complex enough by word count, OR
        # - Has sequence indicators (suggesting multi-step), OR
        # - Has multiple action verbs (suggesting multiple operations)
        return word_count >= self.config.min_task_complexity or has_sequence or action_count >= 2

    def create_plan(self, task: str) -> ExecutionPlan:
        """
        Generate an execution plan for a task.

        All LLM calls go through self.llm_client for cost tracking.

        Args:
            task: The task to plan for

        Returns:
            ExecutionPlan with steps
        """
        if self.tool_summaries:
            tools_str = ", ".join(t['name'] for t in self.tool_summaries)
        elif self.available_tools:
            tools_str = ", ".join(self.available_tools)
        else:
            tools_str = "file_read, file_write, http_request, webpage_fetch"

        prompt = f"""Create an execution plan for this task:

TASK: {task}

AVAILABLE TOOLS:
{tools_str}"""

        _llm_start = time.perf_counter()
        response = self.llm_client.complete_json(
            prompt=prompt,
            system=PLANNING_SYSTEM_PROMPT,
            caller="planning",
            max_tokens=self.config.max_planning_tokens
        )
        _llm_elapsed = (time.perf_counter() - _llm_start) * 1000

        # HiveLoop: Planning LLM call tracking
        _task = get_current_task()
        if _task:
            try:
                _p_in = getattr(self.llm_client, '_last_input_tokens', 0)
                _p_out = getattr(self.llm_client, '_last_output_tokens', 0)
                _task.llm_call(
                    "create_plan",
                    model=self.llm_client.model,
                    tokens_in=_p_in,
                    tokens_out=_p_out,
                    cost=estimate_cost(self.llm_client.model, _p_in, _p_out),
                    duration_ms=round(_llm_elapsed),
                    metadata={
                        "cache_read_tokens": getattr(self.llm_client, '_last_cache_read_tokens', None),
                        "cache_write_tokens": getattr(self.llm_client, '_last_cache_creation_tokens', None),
                        "stop_reason": getattr(self.llm_client, '_last_stop_reason', None),
                    },
                )
            except Exception:
                pass

        plan = self._parse_plan_response(response, task)
        plan.status = "executing"

        # Start first step
        if plan.steps:
            plan.steps[0].mark_started()

        self.current_plan = plan
        self._step_turn_count = 0
        self._step_actions = []

        # Report plan to HiveLoop
        _task = get_current_task()
        if _task:
            try:
                step_descriptions = [s.description for s in plan.steps]
                _task.plan(task, step_descriptions)
            except Exception:
                pass

            # Report first step started
            if plan.steps:
                try:
                    _task.plan_step(
                        step_index=0,
                        action="started",
                        summary=plan.steps[0].description,
                    )
                except Exception:
                    pass

        return plan

    def _parse_plan_response(self, response: Optional[Dict], task: str) -> ExecutionPlan:
        """Parse LLM response into ExecutionPlan."""
        plan = ExecutionPlan(task_description=task)

        if not response:
            # Fallback: single step plan
            plan.add_step(PlanStep(
                description="Complete the task",
                acceptance_criteria="Task requirements are met"
            ))
            return plan

        # Extract metadata
        plan.task_understanding = response.get("task_understanding", "")
        plan.approach = response.get("approach", "")
        raw_turns = response.get("estimated_turns", 0)
        try:
            plan.estimated_turns = int(raw_turns) if not isinstance(raw_turns, int) else raw_turns
        except (ValueError, TypeError):
            plan.estimated_turns = 0
        plan.potential_blockers = response.get("potential_blockers", [])

        steps_data = response.get("steps", [])

        if not steps_data:
            # Fallback: single step plan
            plan.add_step(PlanStep(
                description="Complete the task",
                acceptance_criteria="Task requirements are met"
            ))
            return plan

        for i, step_data in enumerate(steps_data[:self.config.max_steps]):
            step = PlanStep(
                description=step_data.get("description", f"Step {i+1}"),
                expected_tools=step_data.get("expected_tools", []),
                acceptance_criteria=step_data.get("acceptance_criteria", ""),
                pre_conditions=step_data.get("pre_conditions", ""),
                on_failure=step_data.get("on_failure", ""),
                depends_on=step_data.get("depends_on", [])
            )
            plan.add_step(step)

        return plan

    def get_current_step_context(self) -> str:
        """
        Get context about current plan and step for injection.

        Returns:
            Formatted string describing current plan state
        """
        if not self.current_plan:
            return ""

        plan = self.current_plan
        step = plan.current_step

        if not step:
            return ""

        context = f"""[PLAN CONTEXT]
Task: {plan.task_description}
Progress: {plan.progress_percentage:.0f}% ({len(plan.completed_steps)}/{len(plan.steps)} steps)

Current Step ({step.order + 1}/{len(plan.steps)}): {step.description}
"""
        if step.pre_conditions:
            context += f"Pre-conditions: {step.pre_conditions}\n"
        context += f"Criteria: {step.acceptance_criteria or 'Complete the step requirements'}\n"
        if step.on_failure:
            context += f"If this step fails: {step.on_failure}\n"

        # Add remaining steps
        remaining = [s for s in plan.steps if s.status == "pending" and s != step]
        if remaining:
            context += "\nUpcoming Steps:\n"
            for s in remaining[:3]:  # Show next 3 steps max
                context += f"  - {s.description}\n"

        context += "[END PLAN CONTEXT]"
        return context

    def record_turn(self, tool_calls: List[Any]) -> None:
        """
        Record a turn was taken for the current step.

        Args:
            tool_calls: List of tool calls made this turn
        """
        self._step_turn_count += 1
        if self.current_plan and self.current_plan.current_step:
            self.current_plan.current_step.turns_taken += 1

        # Track actions
        for tc in (tool_calls or []):
            if hasattr(tc, 'name'):
                action = f"{tc.name}"
                if hasattr(tc, 'result') and tc.result:
                    status = "success" if tc.result.success else "failed"
                    action += f" ({status})"
                self._step_actions.append(action)

    def check_step_completion(
        self,
        last_response: str
    ) -> tuple:
        """
        Check if current step is complete.

        Args:
            last_response: LLM's last response text

        Returns:
            (is_complete, result_summary)
        """
        if not self.current_plan or not self.current_plan.current_step:
            return False, ""

        step = self.current_plan.current_step

        # Quick heuristics first - check if LLM signals completion
        completion_signals = [
            "step complete", "step is complete", "completed this step",
            "moving on to", "proceeding to", "next step",
            "finished with this step", "step done"
        ]
        response_lower = last_response.lower() if last_response else ""
        if any(signal in response_lower for signal in completion_signals):
            return True, "Completion signaled in response"

        # If exceeded turn limit for this step, use LLM to check
        if self._step_turn_count >= self.config.max_turns_per_step:
            return self._llm_check_completion(step)

        return False, ""

    def _llm_check_completion(self, step: PlanStep) -> tuple:
        """
        Use LLM to determine if step is complete.

        Args:
            step: The step to check

        Returns:
            (is_complete, result_summary)
        """
        actions_str = "\n".join(self._step_actions[-10:]) if self._step_actions else "No actions recorded"

        prompt = STEP_COMPLETION_PROMPT.format(
            step_description=step.description,
            acceptance_criteria=step.acceptance_criteria or "Task requirements met",
            actions_taken=actions_str
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You evaluate task step completion. Be honest about what's done.",
            caller="planning_check",
            max_tokens=500
        )

        if response:
            return response.get("complete", False), response.get("result_summary", "")
        return False, ""

    def advance_plan(self, result_summary: str = "") -> Optional[PlanStep]:
        """
        Mark current step complete and advance to next.

        Args:
            result_summary: Summary of what was accomplished

        Returns:
            Next step, or None if plan complete
        """
        if not self.current_plan:
            return None

        current = self.current_plan.current_step
        if current:
            current.mark_completed(result_summary)

            # HiveLoop: report step completed
            _task = get_current_task()
            if _task:
                try:
                    _task.plan_step(
                        step_index=current.order,
                        action="completed",
                        summary=result_summary or current.description,
                        turns=current.turns_taken,
                    )
                except Exception:
                    pass

        self._step_turn_count = 0
        self._step_actions = []
        next_step = self.current_plan.advance_to_next_step()

        if next_step:
            next_step.mark_started()

            # HiveLoop: report next step started
            _task = get_current_task()
            if _task:
                try:
                    _task.plan_step(
                        step_index=next_step.order,
                        action="started",
                        summary=next_step.description,
                    )
                except Exception:
                    pass

            return next_step
        else:
            self.current_plan.status = "completed"
            return None

    def mark_step_blocked(self, blockers: List[str]) -> None:
        """Mark current step as blocked."""
        if self.current_plan and self.current_plan.current_step:
            step = self.current_plan.current_step
            step.mark_blocked(blockers)

            # HiveLoop: report step failed/blocked
            _task = get_current_task()
            if _task:
                try:
                    _task.plan_step(
                        step_index=step.order,
                        action="failed",
                        summary=f"Blocked: {', '.join(blockers)}" if blockers else "Blocked",
                    )
                except Exception:
                    pass

    def should_replan(self) -> tuple:
        """
        Check if replanning is needed.

        Returns:
            (should_replan, reason)
        """
        if not self.current_plan:
            return False, ""

        # Check max replans
        if self._replan_count >= self.config.max_replans:
            return False, ""

        step = self.current_plan.current_step

        # Check if current step is blocked
        if step and step.status == "blocked":
            if self.config.auto_replan_on_block:
                return True, f"Step blocked: {', '.join(step.blockers)}"

        # Check if stuck on step too long
        if self._step_turn_count > self.config.max_turns_per_step * 2:
            return True, f"Step taking too long ({self._step_turn_count} turns)"

        return False, ""

    def replan(self, reason: str, context: str = "") -> ExecutionPlan:
        """
        Create a revised plan based on current progress.

        All LLM calls go through self.llm_client for cost tracking.

        Args:
            reason: Why replanning is needed
            context: Additional context (e.g., error messages)

        Returns:
            Updated ExecutionPlan
        """
        if not self.current_plan:
            raise ValueError("No current plan to revise")

        plan = self.current_plan
        self._replan_count += 1

        # Build progress summary
        progress = []
        for step in plan.completed_steps:
            progress.append(f"✓ {step.description}: {step.result_summary or 'completed'}")

        prompt = REPLAN_PROMPT_TEMPLATE.format(
            original_task=plan.task_description,
            original_plan=plan.to_progress_string(),
            progress_summary="\n".join(progress) if progress else "No steps completed yet",
            replan_reason=reason,
            blockers=context or "None specified"
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system=PLANNING_SYSTEM_PROMPT,
            caller="replanning",
            max_tokens=self.config.max_planning_tokens
        )

        # Create new plan with new steps
        new_steps_plan = self._parse_plan_response(response, plan.task_description)

        # Preserve completed steps at beginning
        completed = [s for s in plan.steps if s.status == "completed"]

        # Create new plan preserving metadata
        new_plan = ExecutionPlan(
            plan_id=plan.plan_id,  # Keep same ID
            task_description=plan.task_description,
            task_understanding=new_steps_plan.task_understanding or plan.task_understanding,
            approach=new_steps_plan.approach or plan.approach,
            revision_count=plan.revision_count + 1,
            revision_history=plan.revision_history + [f"Revision {plan.revision_count + 1}: {reason}"]
        )

        # Add completed steps first
        for step in completed:
            new_plan.steps.append(step)

        # Add new steps
        for step in new_steps_plan.steps:
            step.order = len(new_plan.steps)
            new_plan.steps.append(step)

        # Set current step to first pending
        new_plan.current_step_index = len(completed)
        if new_plan.current_step_index < len(new_plan.steps):
            new_plan.steps[new_plan.current_step_index].mark_started()

        new_plan.status = "executing"
        self.current_plan = new_plan
        self._step_turn_count = 0
        self._step_actions = []

        # HiveLoop: report revised plan
        _task = get_current_task()
        if _task:
            try:
                step_descriptions = [s.description for s in new_plan.steps]
                _task.plan(
                    new_plan.task_description,
                    step_descriptions,
                    revision=new_plan.revision_count,
                )
            except Exception:
                pass

            # Report first pending step started
            if new_plan.current_step_index < len(new_plan.steps):
                try:
                    step = new_plan.steps[new_plan.current_step_index]
                    _task.plan_step(
                        step_index=step.order,
                        action="started",
                        summary=step.description,
                    )
                except Exception:
                    pass

        return new_plan

    def get_plan_summary(self) -> Dict:
        """Get summary of current plan for logging/metrics."""
        if not self.current_plan:
            return {"active": False}

        plan = self.current_plan
        return {
            "active": True,
            "plan_id": plan.plan_id,
            "total_steps": len(plan.steps),
            "completed_steps": len(plan.completed_steps),
            "progress_percentage": plan.progress_percentage,
            "revision_count": plan.revision_count,
            "current_step": plan.current_step.description if plan.current_step else None,
            "status": plan.status
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get planning statistics."""
        return {
            "has_plan": self.current_plan is not None,
            "replan_count": self._replan_count,
            "step_turn_count": self._step_turn_count,
            "step_actions": self._step_actions[-5:],
            "config": self.config.to_dict(),
            "plan_summary": self.get_plan_summary()
        }

    def suggest_turn_budget(self) -> int:
        """
        Suggest a turn budget based on the current plan.

        Returns:
            Suggested number of turns needed to complete the plan
        """
        if not self.current_plan:
            return 0

        # Calculate based on:
        # - Number of steps * expected turns per step
        # - Add buffer for planning and verification
        # - Add buffer for potential replanning
        num_steps = len(self.current_plan.steps)
        turns_per_step = self.config.max_turns_per_step

        # Base calculation: steps * turns_per_step
        base_turns = num_steps * turns_per_step

        # Add planning overhead (initial plan creation)
        planning_overhead = 2

        # Add buffer for reflections and replanning (20%)
        buffer = int(base_turns * 0.2)

        suggested = base_turns + planning_overhead + buffer

        # Use estimated_turns from LLM if available and reasonable
        if self.current_plan.estimated_turns > 0:
            llm_estimate = self.current_plan.estimated_turns
            # Average between our calculation and LLM estimate
            suggested = (suggested + llm_estimate) // 2

        return max(suggested, 10)  # Minimum 10 turns


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Planning Module")
    print("=" * 60)
    print("\nThis module provides task planning capabilities.")
    print("\nExample usage:")
    print("""
    from llm_client import get_anthropic_client
    from loop_core.planning import PlanningManager, PlanningConfig

    # Setup with existing LLM client (maintains cost tracking)
    client = get_anthropic_client()
    config = PlanningConfig(max_steps=10)
    manager = PlanningManager(client, config, available_tools=["file_read"])

    # Check if task needs planning
    task = "Build a REST API with authentication and testing"
    if manager.should_plan(task):
        plan = manager.create_plan(task)
        print(f"Plan created with {len(plan.steps)} steps")
        print(plan.to_progress_string())

    # During execution
    manager.record_turn(tool_calls)
    is_complete, summary = manager.check_step_completion(response_text)
    if is_complete:
        next_step = manager.advance_plan(summary)
        if next_step:
            print(f"Now working on: {next_step.description}")
        else:
            print("Plan completed!")
    """)
