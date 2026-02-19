"""
LEARNING
========

Learning capability for the Agentic Loop Framework.

Learning enables the agent to improve over time by capturing insights from
execution, storing what worked and what didn't, and applying past knowledge
to new situations. It transforms each task into an opportunity for
cumulative improvement.

Usage:
    from loop_core.learning import LearningManager, LearningConfig

    config = LearningConfig(enabled=True)
    manager = LearningManager(llm_client, memory_path, "agent_id", config)

    # Learn from errors
    pattern = manager.learn_from_error(tool_name, params, error_type, message)

    # Learn from success
    pattern = manager.learn_from_success(task, turns, tool_sequence)

    # Get relevant insights before execution
    insights = manager.get_relevant_insights(task)
    context = manager.format_context_injection(insights)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import Turn

# Import LearningConfig from config module (single source of truth)
from .config import LearningConfig
from .observability import get_current_task

logger = logging.getLogger("loop_core.learning")


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class ErrorPattern:
    """A learned error pattern and its resolution."""

    # Unique identifier
    pattern_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Error identification
    error_type: str = ""           # Exception type or error category
    error_signature: str = ""      # Unique pattern (e.g., "file_read:FileNotFoundError")
    error_message_pattern: str = ""  # Regex or substring pattern

    # Context
    tool_name: Optional[str] = None
    tool_parameters_pattern: Dict[str, Any] = field(default_factory=dict)

    # Resolution
    resolution_strategy: str = ""
    resolution_steps: List[str] = field(default_factory=list)
    preventive_action: str = ""

    # Metadata
    occurrences: int = 1
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success_rate: float = 0.0  # How often resolution works

    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "error_type": self.error_type,
            "error_signature": self.error_signature,
            "error_message_pattern": self.error_message_pattern,
            "tool_name": self.tool_name,
            "tool_parameters_pattern": self.tool_parameters_pattern,
            "resolution_strategy": self.resolution_strategy,
            "resolution_steps": self.resolution_steps,
            "preventive_action": self.preventive_action,
            "occurrences": self.occurrences,
            "last_seen": self.last_seen,
            "success_rate": self.success_rate
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ErrorPattern":
        return cls(
            pattern_id=data.get("pattern_id", str(uuid.uuid4())[:8]),
            error_type=data.get("error_type", ""),
            error_signature=data.get("error_signature", ""),
            error_message_pattern=data.get("error_message_pattern", ""),
            tool_name=data.get("tool_name"),
            tool_parameters_pattern=data.get("tool_parameters_pattern", {}),
            resolution_strategy=data.get("resolution_strategy", ""),
            resolution_steps=data.get("resolution_steps", []),
            preventive_action=data.get("preventive_action", ""),
            occurrences=data.get("occurrences", 1),
            last_seen=data.get("last_seen", datetime.now(timezone.utc).isoformat()),
            success_rate=data.get("success_rate", 0.0)
        )


@dataclass
class SuccessPattern:
    """A learned successful approach for a task type."""

    # Unique identifier
    pattern_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Task identification
    task_type: str = ""            # Category (e.g., "file_refactor", "api_call", "debug")
    task_keywords: List[str] = field(default_factory=list)

    # Approach
    approach_summary: str = ""
    key_steps: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    tool_sequence: List[str] = field(default_factory=list)  # Ordered tool calls

    # Outcomes
    typical_turns: int = 0
    success_indicators: List[str] = field(default_factory=list)

    # Metadata
    times_used: int = 1
    last_used: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "task_type": self.task_type,
            "task_keywords": self.task_keywords,
            "approach_summary": self.approach_summary,
            "key_steps": self.key_steps,
            "tools_used": self.tools_used,
            "tool_sequence": self.tool_sequence,
            "typical_turns": self.typical_turns,
            "success_indicators": self.success_indicators,
            "times_used": self.times_used,
            "last_used": self.last_used
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SuccessPattern":
        return cls(
            pattern_id=data.get("pattern_id", str(uuid.uuid4())[:8]),
            task_type=data.get("task_type", ""),
            task_keywords=data.get("task_keywords", []),
            approach_summary=data.get("approach_summary", ""),
            key_steps=data.get("key_steps", []),
            tools_used=data.get("tools_used", []),
            tool_sequence=data.get("tool_sequence", []),
            typical_turns=data.get("typical_turns", 0),
            success_indicators=data.get("success_indicators", []),
            times_used=data.get("times_used", 1),
            last_used=data.get("last_used", datetime.now(timezone.utc).isoformat())
        )


@dataclass
class ToolInsight:
    """Learned insight about a specific tool."""

    tool_name: str = ""

    # Usage patterns
    common_parameters: Dict[str, List[Any]] = field(default_factory=dict)
    common_errors: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)

    # Combinations
    often_followed_by: List[str] = field(default_factory=list)  # Tools called after
    often_preceded_by: List[str] = field(default_factory=list)  # Tools called before

    # Stats
    total_calls: int = 0
    success_rate: float = 0.0
    avg_execution_time_ms: int = 0

    def to_dict(self) -> Dict:
        return {
            "tool_name": self.tool_name,
            "common_parameters": self.common_parameters,
            "common_errors": self.common_errors,
            "best_practices": self.best_practices,
            "often_followed_by": self.often_followed_by,
            "often_preceded_by": self.often_preceded_by,
            "total_calls": self.total_calls,
            "success_rate": self.success_rate,
            "avg_execution_time_ms": self.avg_execution_time_ms
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ToolInsight":
        return cls(
            tool_name=data.get("tool_name", ""),
            common_parameters=data.get("common_parameters", {}),
            common_errors=data.get("common_errors", []),
            best_practices=data.get("best_practices", []),
            often_followed_by=data.get("often_followed_by", []),
            often_preceded_by=data.get("often_preceded_by", []),
            total_calls=data.get("total_calls", 0),
            success_rate=data.get("success_rate", 0.0),
            avg_execution_time_ms=data.get("avg_execution_time_ms", 0)
        )


@dataclass
class DomainFact:
    """A learned fact about the domain/codebase."""

    fact_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Content
    fact_type: Literal["location", "pattern", "convention", "constraint", "relationship"] = "pattern"
    description: str = ""
    keywords: List[str] = field(default_factory=list)

    # Evidence
    source: str = ""  # Where this fact was learned
    confidence: float = 0.5

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_validated: str = ""
    validation_count: int = 0

    def to_dict(self) -> Dict:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "description": self.description,
            "keywords": self.keywords,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_validated": self.last_validated,
            "validation_count": self.validation_count
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DomainFact":
        return cls(
            fact_id=data.get("fact_id", str(uuid.uuid4())[:8]),
            fact_type=data.get("fact_type", "pattern"),
            description=data.get("description", ""),
            keywords=data.get("keywords", []),
            source=data.get("source", ""),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_validated=data.get("last_validated", ""),
            validation_count=data.get("validation_count", 0)
        )


@dataclass
class LearningStore:
    """Container for all learned knowledge."""

    agent_id: str = ""

    error_patterns: List[ErrorPattern] = field(default_factory=list)
    success_patterns: List[SuccessPattern] = field(default_factory=list)
    tool_insights: Dict[str, ToolInsight] = field(default_factory=dict)
    domain_facts: List[DomainFact] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "error_patterns": [e.to_dict() for e in self.error_patterns],
            "success_patterns": [s.to_dict() for s in self.success_patterns],
            "tool_insights": {k: v.to_dict() for k, v in self.tool_insights.items()},
            "domain_facts": [f.to_dict() for f in self.domain_facts]
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LearningStore":
        store = cls(agent_id=data.get("agent_id", ""))

        for e in data.get("error_patterns", []):
            store.error_patterns.append(ErrorPattern.from_dict(e))

        for s in data.get("success_patterns", []):
            store.success_patterns.append(SuccessPattern.from_dict(s))

        for name, insight_data in data.get("tool_insights", {}).items():
            store.tool_insights[name] = ToolInsight.from_dict(insight_data)

        for f in data.get("domain_facts", []):
            store.domain_facts.append(DomainFact.from_dict(f))

        return store


# ============================================================================
# PROMPTS
# ============================================================================

ERROR_ANALYSIS_PROMPT = """Analyze this error and its resolution.

ERROR:
Tool: {tool_name}
Parameters: {parameters}
Error Type: {error_type}
Error Message: {error_message}

RESOLUTION (if any):
{resolution_context}

Extract a reusable pattern. Respond with JSON:
{{
    "error_signature": "unique_pattern:ErrorType",
    "error_message_pattern": "substring or regex to match similar errors",
    "resolution_strategy": "How to fix this type of error",
    "resolution_steps": ["Step 1", "Step 2"],
    "preventive_action": "How to avoid this error in future"
}}

Be specific but general enough to apply to similar situations."""


SUCCESS_ANALYSIS_PROMPT = """Analyze this successful task execution.

TASK: {task_description}

EXECUTION SUMMARY:
Turns: {turn_count}
Tools Used: {tools_used}
Tool Sequence: {tool_sequence}

KEY ACTIONS:
{key_actions}

Extract a reusable success pattern. Respond with JSON:
{{
    "task_type": "category of task (e.g., 'file_refactor', 'debugging', 'api_integration')",
    "task_keywords": ["keywords", "that", "identify", "this", "type"],
    "approach_summary": "Brief description of the approach that worked",
    "key_steps": ["Important step 1", "Important step 2"],
    "success_indicators": ["How to know this approach is working"]
}}

Focus on what made this successful and transferable to similar tasks."""


DOMAIN_FACT_PROMPT = """Extract factual knowledge from this execution.

TASK: {task_description}

OBSERVATIONS:
{observations}

FILES ACCESSED:
{files}

Extract domain facts. Respond with JSON array:
[
    {{
        "fact_type": "location|pattern|convention|constraint|relationship",
        "description": "The specific fact",
        "keywords": ["relevant", "keywords"],
        "confidence": 0.0-1.0
    }}
]

Examples:
- "Tests are located in tests/ directory" (location)
- "This codebase uses snake_case for variables" (convention)
- "User model requires email validation" (constraint)

Only include non-obvious facts that would help with future tasks."""


# ============================================================================
# LEARNING MANAGER
# ============================================================================

class LearningManager:
    """
    Manages learning from execution and applying past knowledge.

    Uses the provided llm_client for all LLM calls, ensuring proper
    cost tracking and usage monitoring through the existing infrastructure.
    """

    LEARNING_FILE = "learning_store.json"

    def __init__(
        self,
        llm_client,
        memory_path: Path,
        agent_id: str,
        config: LearningConfig = None
    ):
        """
        Initialize the learning manager.

        Args:
            llm_client: LLM client instance (must have complete_json method).
                       All calls go through this client for cost tracking.
            memory_path: Path to memory directory
            agent_id: Agent identifier for storage isolation
            config: Learning configuration
        """
        self.llm_client = llm_client
        if not memory_path:
            raise ValueError("memory_path is required for LearningManager")
        self.memory_path = Path(memory_path)
        self.agent_id = agent_id
        self.config = config or LearningConfig()
        self.store = self._load_store()

        # Track tool sequence for retroactive updates
        self._last_tool_name: Optional[str] = None

    def reset(self) -> None:
        """Reset state for new execution (but keep learned knowledge)."""
        self._last_tool_name = None

    def _load_store(self) -> LearningStore:
        """Load learning store from disk."""
        # memory_path already points to per-agent memory directory (e.g., data/AGENTS/{agent_id}/memory)
        store_path = self.memory_path / self.LEARNING_FILE

        if store_path.exists():
            try:
                with open(store_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.debug(f"Loaded learning store from {store_path}")
                return LearningStore.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load learning store: {e}")

        return LearningStore(agent_id=self.agent_id)

    def _save_store(self) -> None:
        """Persist learning store to disk."""
        # memory_path already points to per-agent memory directory (e.g., data/AGENTS/{agent_id}/memory)
        store_path = self.memory_path / self.LEARNING_FILE
        store_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(store_path, 'w', encoding='utf-8') as f:
                json.dump(self.store.to_dict(), f, indent=2)
            logger.debug(f"Saved learning store to {store_path}")
        except Exception as e:
            logger.warning(f"Failed to save learning store: {e}")

    # =========================================================================
    # LEARNING FROM EXECUTION
    # =========================================================================

    def learn_from_error(
        self,
        error_message: str,
        context: str,
        tool_name: Optional[str] = None,
        resolution: Optional[str] = None,
        parameters: Dict = None
    ) -> Optional[ErrorPattern]:
        """
        Learn from an error occurrence.

        Args:
            error_message: Error message text
            context: Task or execution context
            tool_name: Tool that produced the error (optional)
            resolution: What fixed it (if known)
            parameters: Parameters passed to tool (optional)

        Returns:
            ErrorPattern if new insight extracted
        """
        if not self.config.enabled or not self.config.learn_from_errors:
            return None

        # Extract error type from message
        error_type = self._extract_error_type(error_message)
        tool_name = tool_name or "execution"
        parameters = parameters or {}

        # Check if we already have this pattern
        signature = f"{tool_name}:{error_type}"
        existing = self._find_error_pattern(signature, error_message)

        if existing:
            # Update existing pattern
            existing.occurrences += 1
            existing.last_seen = datetime.now(timezone.utc).isoformat()
            if resolution and not existing.resolution_strategy:
                existing.resolution_strategy = resolution
            self._save_store()
            logger.info(f"Updated error pattern: {signature} (occurrences: {existing.occurrences})")
            return existing

        # Extract new pattern using LLM
        prompt = ERROR_ANALYSIS_PROMPT.format(
            tool_name=tool_name,
            parameters=json.dumps(parameters, default=str)[:500],
            error_type=error_type,
            error_message=error_message[:500],
            resolution_context=resolution or "Unknown"
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You extract error patterns for future reference.",
            caller="learning_error",
            max_tokens=self.config.max_extraction_tokens
        )

        if not response:
            logger.warning(f"Failed to extract error pattern for {signature}")
            return None

        pattern = ErrorPattern(
            error_type=error_type,
            error_signature=response.get("error_signature", signature),
            error_message_pattern=response.get("error_message_pattern", ""),
            tool_name=tool_name,
            resolution_strategy=response.get("resolution_strategy", ""),
            resolution_steps=response.get("resolution_steps", []),
            preventive_action=response.get("preventive_action", "")
        )

        self._add_error_pattern(pattern)
        logger.info(f"Learned new error pattern: {pattern.error_signature}")

        # Gap #6: Report learning event to HiveLoop
        _task = get_current_task()
        if _task:
            try:
                _task.event("learning_captured", payload={
                    "type": "error_pattern",
                    "trigger": "error",
                    "category": tool_name,
                    "summary": f"Error pattern: {pattern.error_signature}",
                    "resolution": (pattern.resolution_strategy or "")[:200],
                })
            except Exception:
                pass

        return pattern

    def _extract_error_type(self, error_message: str) -> str:
        """Extract error type from error message."""
        # Common error type patterns
        patterns = [
            r"(\w+Error)",
            r"(\w+Exception)",
            r"(\w+Failure)",
            r"^(Timeout)",
            r"^(Connection)",
        ]
        for pattern in patterns:
            match = re.search(pattern, error_message)
            if match:
                return match.group(1)
        return "Error"

    def learn_from_success(
        self,
        task_description: str,
        approach: str,
        result: str,
        key_steps: List[str] = None
    ) -> Optional[SuccessPattern]:
        """
        Learn from a successful execution.

        Args:
            task_description: Original task
            approach: How the task was approached
            result: What the outcome was
            key_steps: Ordered list of tools/steps used

        Returns:
            SuccessPattern if new insight extracted
        """
        if not self.config.enabled or not self.config.learn_from_success:
            return None

        key_steps = key_steps or []

        # Check if this is a significant enough execution
        if len(key_steps) < self.config.min_turns_for_insight:
            logger.debug(f"Skipping success learning: only {len(key_steps)} steps (min: {self.config.min_turns_for_insight})")
            return None

        # Build key actions summary
        key_actions = [approach, result]

        prompt = SUCCESS_ANALYSIS_PROMPT.format(
            task_description=task_description,
            turn_count=len(key_steps),
            tools_used=list(set(key_steps)),
            tool_sequence=key_steps[:20],  # First 20 calls
            key_actions="\n".join(key_actions)
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You extract success patterns for future reference.",
            caller="learning_success",
            max_tokens=self.config.max_extraction_tokens
        )

        if not response:
            logger.warning("Failed to extract success pattern")
            return None

        pattern = SuccessPattern(
            task_type=response.get("task_type", "general"),
            task_keywords=response.get("task_keywords", []),
            approach_summary=response.get("approach_summary", approach),
            key_steps=response.get("key_steps", []),
            tools_used=list(set(key_steps)),
            tool_sequence=key_steps[:20],
            typical_turns=len(key_steps),
            success_indicators=response.get("success_indicators", [])
        )

        self._add_success_pattern(pattern)
        logger.info(f"Learned success pattern: {pattern.task_type}")

        # Gap #6: Report learning event to HiveLoop
        _task = get_current_task()
        if _task:
            try:
                _task.event("learning_captured", payload={
                    "type": "success_pattern",
                    "trigger": "successful_execution",
                    "category": pattern.task_type,
                    "summary": f"Success pattern: {(pattern.approach_summary or '')[:200]}",
                    "tools_used": pattern.tools_used[:10],
                    "typical_turns": pattern.typical_turns,
                })
            except Exception:
                pass

        return pattern

    def learn_tool_pattern(
        self,
        tool_name: str,
        parameters: Dict,
        result: Any,
        context: str = ""
    ) -> None:
        """
        Learn tool usage pattern.

        Args:
            tool_name: Tool that was called
            parameters: Parameters used
            result: ToolResult object with success, output, error, duration
            context: Task context for additional info
        """
        if not self.config.enabled or not self.config.learn_tool_patterns:
            return

        if tool_name not in self.store.tool_insights:
            self.store.tool_insights[tool_name] = ToolInsight(tool_name=tool_name)

        insight = self.store.tool_insights[tool_name]

        # Extract from result
        success = getattr(result, 'success', True)
        error_message = getattr(result, 'error', None)
        execution_time_ms = getattr(result, 'duration_ms', 0) or 0

        # Update stats
        insight.total_calls += 1
        old_rate = insight.success_rate
        insight.success_rate = (old_rate * (insight.total_calls - 1) + (1 if success else 0)) / insight.total_calls
        if execution_time_ms > 0:
            insight.avg_execution_time_ms = int(
                (insight.avg_execution_time_ms * (insight.total_calls - 1) + execution_time_ms) / insight.total_calls
            )

        # Track sequence - update previous tool's "followed_by"
        if self._last_tool_name and self._last_tool_name in self.store.tool_insights:
            prev_insight = self.store.tool_insights[self._last_tool_name]
            if tool_name not in prev_insight.often_followed_by:
                prev_insight.often_followed_by.append(tool_name)
                prev_insight.often_followed_by = prev_insight.often_followed_by[-10:]

        # Track current tool's "preceded_by"
        if self._last_tool_name and self._last_tool_name not in insight.often_preceded_by:
            insight.often_preceded_by.append(self._last_tool_name)
            insight.often_preceded_by = insight.often_preceded_by[-10:]

        # Track common parameters (only string/simple values)
        for key, value in (parameters or {}).items():
            if isinstance(value, (str, int, float, bool)):
                if key not in insight.common_parameters:
                    insight.common_parameters[key] = []
                str_value = str(value)[:100]  # Limit value length
                if str_value not in insight.common_parameters[key]:
                    insight.common_parameters[key].append(str_value)
                    insight.common_parameters[key] = insight.common_parameters[key][-5:]

        # Track common errors
        if not success and error_message:
            error_preview = str(error_message)[:100]
            if error_preview not in insight.common_errors:
                insight.common_errors.append(error_preview)
                insight.common_errors = insight.common_errors[-10:]

        # Update last tool for next call
        self._last_tool_name = tool_name

        self._save_store()

    def learn_domain_facts(
        self,
        observations: List[str],
        files_accessed: List[str],
        context: str = ""
    ) -> List[DomainFact]:
        """
        Extract domain facts from execution.

        Args:
            observations: Notable observations during execution (LLM responses + tool outputs)
            files_accessed: Files that were read/written
            context: Task description / what was being done

        Returns:
            List of extracted facts
        """
        if not self.config.enabled or not self.config.learn_domain_facts:
            return []

        if not observations and not files_accessed:
            return []

        prompt = DOMAIN_FACT_PROMPT.format(
            task_description=context or "General execution",
            observations="\n".join(observations[:20]),
            files="\n".join(files_accessed[:20])
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You extract domain knowledge from observations.",
            caller="learning_domain",
            max_tokens=self.config.max_extraction_tokens
        )

        if not response:
            logger.warning("Failed to extract domain facts")
            return []

        if not isinstance(response, list):
            logger.warning(f"Domain fact response not a list: {type(response)}")
            return []

        facts = []
        for fact_data in response[:5]:  # Max 5 facts per extraction
            if not isinstance(fact_data, dict):
                continue

            fact_type = fact_data.get("fact_type", "pattern")
            if fact_type not in ("location", "pattern", "convention", "constraint", "relationship"):
                fact_type = "pattern"

            fact = DomainFact(
                fact_type=fact_type,
                description=fact_data.get("description", ""),
                keywords=fact_data.get("keywords", []),
                confidence=min(1.0, max(0.0, fact_data.get("confidence", 0.5))),
                source=(context or "General execution")[:100]
            )

            # Deduplicate
            if fact.description and not self._has_similar_fact(fact):
                self.store.domain_facts.append(fact)
                facts.append(fact)
                logger.info(f"Learned domain fact: {fact.description[:50]}...")

        # Enforce limit
        while len(self.store.domain_facts) > self.config.max_domain_facts:
            self.store.domain_facts.pop(0)

        if facts:
            self._save_store()

        return facts

    def learn_from_reflection(
        self,
        decision: str,
        reasoning: str,
        next_action: str,
        task: str
    ) -> Optional[ErrorPattern]:
        """
        Learn from reflection pivot/terminate decisions.

        Args:
            decision: Reflection decision (pivot, terminate, etc.)
            reasoning: Why this decision was made
            next_action: What to do next (for pivots)
            task: Original task description

        Returns:
            ErrorPattern if learned
        """
        if not self.config.enabled or not self.config.learn_from_reflection:
            return None

        if decision not in ("pivot", "terminate"):
            return None

        return self.learn_from_error(
            tool_name="execution",
            parameters={"task": task[:200]},
            error_type="StrategyFailure",
            error_message=reasoning,
            resolution_context=next_action if decision == "pivot" else ""
        )

    # =========================================================================
    # RETRIEVING KNOWLEDGE
    # =========================================================================

    def get_relevant_insights(self, task: str) -> Dict[str, Any]:
        """
        Get insights relevant to a task.

        Args:
            task: Task description

        Returns:
            Dict with relevant error patterns, success patterns, and facts
        """
        keywords = self._extract_keywords(task)

        return {
            "error_patterns": self._find_relevant_errors(keywords),
            "success_patterns": self._find_relevant_successes(keywords),
            "domain_facts": self._find_relevant_facts(keywords),
            "tool_insights": self._find_relevant_tools(keywords)
        }

    def get_error_resolution(
        self,
        tool_name: str,
        error_type: str,
        error_message: str
    ) -> Optional[ErrorPattern]:
        """
        Find resolution for an error.

        Args:
            tool_name: Tool that errored
            error_type: Type of error
            error_message: Error message

        Returns:
            ErrorPattern with resolution if found
        """
        signature = f"{tool_name}:{error_type}"
        return self._find_error_pattern(signature, error_message)

    def format_context_injection(self, insights: Dict[str, Any]) -> str:
        """
        Format insights for injection into LLM context.

        Args:
            insights: Output from get_relevant_insights

        Returns:
            Formatted string for context injection
        """
        parts = ["[LEARNED INSIGHTS]"]

        # Error patterns
        if insights.get("error_patterns"):
            parts.append("\n### Known Error Patterns:")
            for pattern in insights["error_patterns"][:3]:
                parts.append(f"- {pattern.error_signature}: {pattern.resolution_strategy}")

        # Success patterns
        if insights.get("success_patterns"):
            parts.append("\n### Successful Approaches:")
            for pattern in insights["success_patterns"][:2]:
                parts.append(f"- {pattern.task_type}: {pattern.approach_summary}")

        # Domain facts
        if insights.get("domain_facts"):
            parts.append("\n### Domain Knowledge:")
            for fact in insights["domain_facts"][:3]:
                parts.append(f"- {fact.description}")

        parts.append("[END INSIGHTS]")

        result = "\n".join(parts)

        # Respect token limit (rough estimate: ~4 chars per token)
        max_chars = self.config.max_context_injection_tokens * 4
        if len(result) > max_chars:
            result = result[:max_chars] + "\n[TRUNCATED]"

        return result

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _find_error_pattern(
        self,
        signature: str,
        message: str
    ) -> Optional[ErrorPattern]:
        """Find matching error pattern."""
        for pattern in self.store.error_patterns:
            if pattern.error_signature == signature:
                return pattern
            if pattern.error_message_pattern:
                try:
                    if re.search(pattern.error_message_pattern, message):
                        return pattern
                except re.error:
                    if pattern.error_message_pattern in message:
                        return pattern
        return None

    def _add_error_pattern(self, pattern: ErrorPattern) -> None:
        """Add error pattern respecting limits."""
        self.store.error_patterns.append(pattern)
        while len(self.store.error_patterns) > self.config.max_error_patterns:
            # Remove oldest with lowest occurrences
            sorted_patterns = sorted(
                self.store.error_patterns,
                key=lambda p: (p.occurrences, p.last_seen)
            )
            self.store.error_patterns.remove(sorted_patterns[0])
        self._save_store()

    def _add_success_pattern(self, pattern: SuccessPattern) -> None:
        """Add success pattern respecting limits."""
        self.store.success_patterns.append(pattern)
        while len(self.store.success_patterns) > self.config.max_success_patterns:
            sorted_patterns = sorted(
                self.store.success_patterns,
                key=lambda p: (p.times_used, p.last_used)
            )
            self.store.success_patterns.remove(sorted_patterns[0])
        self._save_store()

    def _has_similar_fact(self, new_fact: DomainFact) -> bool:
        """Check if similar fact already exists."""
        for fact in self.store.domain_facts:
            if fact.description.lower() == new_fact.description.lower():
                return True
            # Keyword overlap check
            overlap = set(fact.keywords) & set(new_fact.keywords)
            if len(overlap) >= 2 and fact.fact_type == new_fact.fact_type:
                return True
        return False

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from text."""
        # Simple extraction - could be enhanced with NLP
        words = re.findall(r'\b\w{3,}\b', text.lower())
        # Remove common words
        stopwords = {'the', 'and', 'for', 'this', 'that', 'with', 'from', 'have', 'are',
                     'was', 'were', 'been', 'being', 'will', 'would', 'could', 'should'}
        return [w for w in words if w not in stopwords][:20]

    def _find_relevant_errors(self, keywords: List[str]) -> List[ErrorPattern]:
        """Find error patterns matching keywords."""
        results = []
        for pattern in self.store.error_patterns:
            score = sum(1 for kw in keywords if kw in pattern.error_signature.lower())
            score += sum(1 for kw in keywords if kw in (pattern.tool_name or "").lower())
            if score > 0:
                results.append((score, pattern))
        results.sort(key=lambda x: -x[0])
        return [p for _, p in results[:self.config.max_insights_per_request]]

    def _find_relevant_successes(self, keywords: List[str]) -> List[SuccessPattern]:
        """Find success patterns matching keywords."""
        results = []
        for pattern in self.store.success_patterns:
            score = sum(1 for kw in keywords if kw in pattern.task_keywords)
            score += sum(1 for kw in keywords if kw in pattern.task_type.lower())
            if score > 0:
                results.append((score, pattern))
        results.sort(key=lambda x: -x[0])
        return [p for _, p in results[:self.config.max_insights_per_request]]

    def _find_relevant_facts(self, keywords: List[str]) -> List[DomainFact]:
        """Find domain facts matching keywords."""
        results = []
        for fact in self.store.domain_facts:
            score = sum(1 for kw in keywords if kw in fact.keywords)
            score += sum(1 for kw in keywords if kw in fact.description.lower())
            if score > 0:
                results.append((score, fact))
        results.sort(key=lambda x: -x[0])
        return [f for _, f in results[:self.config.max_insights_per_request]]

    def _find_relevant_tools(self, keywords: List[str]) -> List[ToolInsight]:
        """Find tool insights for mentioned tools."""
        results = []
        keyword_str = ' '.join(keywords)
        for tool_name, insight in self.store.tool_insights.items():
            if tool_name in keyword_str:
                results.append(insight)
        return results[:self.config.max_insights_per_request]

    def get_stats(self) -> Dict:
        """Get learning statistics."""
        return {
            "error_patterns_count": len(self.store.error_patterns),
            "success_patterns_count": len(self.store.success_patterns),
            "tool_insights_count": len(self.store.tool_insights),
            "domain_facts_count": len(self.store.domain_facts)
        }

    def get_session_stats(self) -> Dict:
        """Get session-specific learning statistics.

        Returns statistics about what was learned during the current session,
        useful for including in LoopResult.
        """
        return {
            "enabled": self.config.enabled,
            "store_stats": self.get_stats(),
            "last_tool_tracked": self._last_tool_name
        }


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Learning Module")
    print("=" * 60)
    print("\nThis module provides learning capabilities.")
    print("\nExample usage:")
    print("""
    from llm_client import get_anthropic_client
    from loop_core.learning import LearningManager, LearningConfig

    # Setup with existing LLM client (maintains cost tracking)
    # memory_path should point to per-agent memory directory
    client = get_anthropic_client()
    config = LearningConfig(enabled=True)
    manager = LearningManager(client, Path("./data/AGENTS/main/memory"), "main", config)

    # Learn from errors
    pattern = manager.learn_from_error(
        tool_name="file_read",
        parameters={"path": "/missing.txt"},
        error_type="FileNotFoundError",
        error_message="File not found"
    )

    # Get relevant insights before execution
    insights = manager.get_relevant_insights("Read and modify config file")
    context = manager.format_context_injection(insights)

    # Check stats
    print(manager.get_stats())
    """)
