"""
CONFIG_LOADER
=============

Configuration management for the Agentic Loop Framework.

Handles:
- Global configuration (paths, defaults, tool settings)
- Agent configurations (per-agent settings)
- Runtime configuration access

Usage:
    from loop_core.config import get_config_manager, load_agent_config

    # Get global config
    config = get_config_manager()
    print(config.global_config.paths.skills_dir)

    # Load agent config
    agent = load_agent_config("main_agent")
    print(agent.system_prompt)
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any


# ============================================================================
# PATH RESOLUTION
# ============================================================================

def _find_project_root() -> Path:
    """
    Find the project root directory.

    Looks for data/loopCore/CONFIG/config.json as the definitive marker,
    since this only exists at the true project root.
    """
    current = Path(__file__).resolve().parent

    # Walk up looking for the config file (definitive marker)
    for _ in range(5):
        config_file = current / "data" / "loopCore" / "CONFIG" / "config.json"
        if config_file.exists():
            return current
        current = current.parent

    # Fallback: walk up from this file (loop_core/config/loader.py)
    # to find a directory that has data/loopCore/CONFIG/ structure
    loader_path = Path(__file__).resolve()
    # loader.py is at src/loop_core/config/loader.py
    # so project root is 4 levels up: config -> loop_core -> src -> project_root
    candidate = loader_path.parent.parent.parent.parent
    if (candidate / "data" / "loopCore" / "CONFIG").exists():
        return candidate

    # Last fallback
    return loader_path.parent.parent.parent.parent


def _get_data_dir() -> Path:
    """Get the loopCore data directory path."""
    root = _find_project_root()
    return root / "data" / "loopCore"


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class PathsConfig:
    """Directory paths configuration.

    Per-agent directories:
    - Memory: data/loopCore/AGENTS/{agent_id}/memory/
    - Output/Runs: data/loopCore/AGENTS/{agent_id}/runs/
    - Tasks: data/loopCore/AGENTS/{agent_id}/tasks/
    - Sessions: data/loopCore/AGENTS/{agent_id}/sessions/
    - Skills: data/loopCore/AGENTS/{agent_id}/skills/ (agent-private)
    """
    skills_dir: str = "./data/loopCore/SKILLS"  # Global skills directory
    agents_dir: str = "./data/loopCore/AGENTS"  # Per-agent directories
    config_dir: str = "./data/loopCore/CONFIG"
    sandbox_dir: str = "./data/loopCore/SANDBOX"
    extensions_dir: str = "./data/loopCore/EXTENSIONS"  # Drop-in extension tools
    apikeys_dir: str = "./apikeys"

    def to_dict(self) -> Dict:
        return {
            "skills_dir": self.skills_dir,
            "agents_dir": self.agents_dir,
            "config_dir": self.config_dir,
            "sandbox_dir": self.sandbox_dir,
            "extensions_dir": self.extensions_dir,
            "apikeys_dir": self.apikeys_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PathsConfig":
        # Note: Ignores legacy fields (memory_dir, output_dir, tasks_dir) if present
        return cls(
            skills_dir=data.get("skills_dir", "./data/loopCore/SKILLS"),
            agents_dir=data.get("agents_dir", "./data/loopCore/AGENTS"),
            config_dir=data.get("config_dir", "./data/loopCore/CONFIG"),
            sandbox_dir=data.get("sandbox_dir", "./data/loopCore/SANDBOX"),
            extensions_dir=data.get("extensions_dir", "./data/loopCore/EXTENSIONS"),
            apikeys_dir=data.get("apikeys_dir", "./apikeys"),
        )

    def resolve(self, base_path: Path) -> "PathsConfig":
        """Resolve relative paths against base path."""
        return PathsConfig(
            skills_dir=str((base_path / self.skills_dir).resolve()),
            agents_dir=str((base_path / self.agents_dir).resolve()),
            config_dir=str((base_path / self.config_dir).resolve()),
            sandbox_dir=str((base_path / self.sandbox_dir).resolve()),
            extensions_dir=str((base_path / self.extensions_dir).resolve()),
            apikeys_dir=str((base_path / self.apikeys_dir).resolve()),
        )

    def get_agent_dir(self, agent_id: str) -> Path:
        """Get the root directory for a specific agent."""
        return Path(self.agents_dir) / agent_id

    def get_agent_config_path(self, agent_id: str) -> Path:
        """Get the config.json path for an agent."""
        return self.get_agent_dir(agent_id) / "config.json"

    def get_agent_skills_dir(self, agent_id: str) -> Path:
        """Get the private skills directory for an agent."""
        return self.get_agent_dir(agent_id) / "skills"

    def get_agent_tasks_dir(self, agent_id: str) -> Path:
        """Get the tasks directory for an agent."""
        return self.get_agent_dir(agent_id) / "tasks"

    def get_agent_memory_dir(self, agent_id: str) -> Path:
        """Get the memory directory for an agent."""
        return self.get_agent_dir(agent_id) / "memory"

    def get_agent_sessions_dir(self, agent_id: str) -> Path:
        """Get the sessions directory for an agent."""
        return self.get_agent_dir(agent_id) / "sessions"

    def get_agent_runs_dir(self, agent_id: str) -> Path:
        """Get the runs directory for an agent."""
        return self.get_agent_dir(agent_id) / "runs"

    def get_global_skills_dir(self) -> Path:
        """Get the global skills directory (inherited by all agents)."""
        return Path(self.skills_dir)


@dataclass
class DefaultsConfig:
    """Default settings for agents."""
    max_turns: int = 20
    timeout_seconds: int = 600
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-5-20250929"

    def to_dict(self) -> Dict:
        return {
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DefaultsConfig":
        return cls(
            max_turns=data.get("max_turns", 20),
            timeout_seconds=data.get("timeout_seconds", 600),
            llm_provider=data.get("llm_provider", "anthropic"),
            llm_model=data.get("llm_model", "claude-sonnet-4-5-20250929"),
        )


@dataclass
class ToolConfig:
    """Configuration for a single tool."""
    enabled: bool = True
    allowed_paths: List[str] = field(default_factory=list)
    timeout_seconds: int = 30
    max_length: int = 15000

    def to_dict(self) -> Dict:
        result = {"enabled": self.enabled}
        if self.allowed_paths:
            result["allowed_paths"] = self.allowed_paths
        if self.timeout_seconds != 30:
            result["timeout_seconds"] = self.timeout_seconds
        if self.max_length != 15000:
            result["max_length"] = self.max_length
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> "ToolConfig":
        return cls(
            enabled=data.get("enabled", True),
            allowed_paths=data.get("allowed_paths", []),
            timeout_seconds=data.get("timeout_seconds", 30),
            max_length=data.get("max_length", 15000),
        )


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_second: float = 10.0
    requests_per_minute: float = 100.0
    max_concurrent: int = 20
    token_budget_per_minute: int = 500000

    def to_dict(self) -> Dict:
        return {
            "requests_per_second": self.requests_per_second,
            "requests_per_minute": self.requests_per_minute,
            "max_concurrent": self.max_concurrent,
            "token_budget_per_minute": self.token_budget_per_minute,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "RateLimitConfig":
        return cls(
            requests_per_second=data.get("requests_per_second", 10.0),
            requests_per_minute=data.get("requests_per_minute", 100.0),
            max_concurrent=data.get("max_concurrent", 20),
            token_budget_per_minute=data.get("token_budget_per_minute", 500000),
        )


@dataclass
class ReflectionConfig:
    """Configuration for reflection behavior.

    Tuned for atomic agentic loop (2026-02-11):
    - interval_turns=0: disabled; atomic state already tracks progress
    - reflect_on_tool_failure=False: atomic error_context handles this
    - no_progress_turns=3: kept; consecutive failures need course correction
    - max_reflections=2: cap overhead; most value is in first 1-2 reflections
    """
    enabled: bool = True
    interval_turns: int = 0  # Disabled: atomic state makes periodic check-ins redundant
    no_progress_turns: int = 3
    reflect_on_tool_failure: bool = False  # Atomic error_context already informs Phase 1
    resource_warning_threshold: float = 0.8
    max_reflections: int = 2
    max_reflection_tokens: int = 4096
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
            interval_turns=data.get("interval_turns", 0),
            no_progress_turns=data.get("no_progress_turns", 3),
            reflect_on_tool_failure=data.get("reflect_on_tool_failure", False),
            resource_warning_threshold=data.get("resource_warning_threshold", 0.8),
            max_reflections=data.get("max_reflections", 2),
            max_reflection_tokens=data.get("max_reflection_tokens", 4096),
            reflection_temperature=data.get("reflection_temperature", 0.3),
        )


@dataclass
class PlanningConfig:
    """Configuration for planning behavior."""
    enabled: bool = True
    min_task_complexity: int = 10
    max_steps: int = 10
    max_turns_per_step: int = 5
    auto_replan_on_block: bool = True
    max_planning_tokens: int = 4096  # Increased from 800 to avoid truncation
    inject_plan_context: bool = True
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


@dataclass
class LearningConfig:
    """Configuration for learning behavior.

    Tuned for atomic agentic loop (2026-02-11):
    - learn_domain_facts=False: LLM call per completion for vague facts; low ROI
    - learn_from_reflection=False: reflection already produces guidance; double-dipping
    - min_turns_for_insight=5: skip trivial heartbeats (3-4 turns)
    """
    enabled: bool = True
    learn_from_errors: bool = True
    learn_from_success: bool = True
    learn_tool_patterns: bool = True  # No LLM call — pure local stats
    learn_domain_facts: bool = False  # Was True; LLM call each completion for low-value facts
    learn_from_reflection: bool = False  # Was True; redundant with reflection guidance
    learn_after_every_execution: bool = False
    min_turns_for_insight: int = 5  # Was 3; skip trivial heartbeat runs
    max_error_patterns: int = 100
    max_success_patterns: int = 100
    max_domain_facts: int = 200
    max_insights_per_request: int = 5
    relevance_threshold: float = 0.3
    max_extraction_tokens: int = 4096  # Increased from 500 to avoid truncation
    max_context_injection_tokens: int = 300
    decay_unused_after_days: int = 30
    min_occurrences_to_keep: int = 2

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "learn_from_errors": self.learn_from_errors,
            "learn_from_success": self.learn_from_success,
            "learn_tool_patterns": self.learn_tool_patterns,
            "learn_domain_facts": self.learn_domain_facts,
            "learn_from_reflection": self.learn_from_reflection,
            "learn_after_every_execution": self.learn_after_every_execution,
            "min_turns_for_insight": self.min_turns_for_insight,
            "max_error_patterns": self.max_error_patterns,
            "max_success_patterns": self.max_success_patterns,
            "max_domain_facts": self.max_domain_facts,
            "max_insights_per_request": self.max_insights_per_request,
            "relevance_threshold": self.relevance_threshold,
            "max_extraction_tokens": self.max_extraction_tokens,
            "max_context_injection_tokens": self.max_context_injection_tokens,
            "decay_unused_after_days": self.decay_unused_after_days,
            "min_occurrences_to_keep": self.min_occurrences_to_keep,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LearningConfig":
        return cls(
            enabled=data.get("enabled", True),
            learn_from_errors=data.get("learn_from_errors", True),
            learn_from_success=data.get("learn_from_success", True),
            learn_tool_patterns=data.get("learn_tool_patterns", True),
            learn_domain_facts=data.get("learn_domain_facts", False),
            learn_from_reflection=data.get("learn_from_reflection", False),
            learn_after_every_execution=data.get("learn_after_every_execution", False),
            min_turns_for_insight=data.get("min_turns_for_insight", 5),
            max_error_patterns=data.get("max_error_patterns", 100),
            max_success_patterns=data.get("max_success_patterns", 100),
            max_domain_facts=data.get("max_domain_facts", 200),
            max_insights_per_request=data.get("max_insights_per_request", 5),
            relevance_threshold=data.get("relevance_threshold", 0.3),
            max_extraction_tokens=data.get("max_extraction_tokens", 4096),
            max_context_injection_tokens=data.get("max_context_injection_tokens", 300),
            decay_unused_after_days=data.get("decay_unused_after_days", 30),
            min_occurrences_to_keep=data.get("min_occurrences_to_keep", 2),
        )


@dataclass
class TraitsConfig:
    """Per-agent trait configuration.

    Stores the agent's behavioral trait values and an enable/disable switch.
    When traits is None on AgentConfig, the agent is unaffected by the trait system.
    """
    enabled: bool = True
    values: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "values": dict(self.values),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TraitsConfig":
        return cls(
            enabled=data.get("enabled", True),
            values=data.get("values", {}),
        )


@dataclass
class GlobalConfig:
    """Global configuration for the framework."""
    version: str = "1.0.0"
    paths: PathsConfig = field(default_factory=PathsConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    tools: Dict[str, ToolConfig] = field(default_factory=dict)
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    logging_level: str = "INFO"
    logging_file: Optional[str] = None
    hiveloop_log_prompts: bool = False

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "paths": self.paths.to_dict(),
            "defaults": self.defaults.to_dict(),
            "tools": {name: cfg.to_dict() for name, cfg in self.tools.items()},
            "rate_limits": self.rate_limits.to_dict(),
            "reflection": self.reflection.to_dict(),
            "planning": self.planning.to_dict(),
            "learning": self.learning.to_dict(),
            "logging": {
                "level": self.logging_level,
                "file": self.logging_file,
            },
            "hiveloop_log_prompts": self.hiveloop_log_prompts,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GlobalConfig":
        tools = {}
        for name, cfg in data.get("tools", {}).items():
            tools[name] = ToolConfig.from_dict(cfg)

        logging = data.get("logging", {})

        return cls(
            version=data.get("version", "1.0.0"),
            paths=PathsConfig.from_dict(data.get("paths", {})),
            defaults=DefaultsConfig.from_dict(data.get("defaults", {})),
            tools=tools,
            rate_limits=RateLimitConfig.from_dict(data.get("rate_limits", {})),
            reflection=ReflectionConfig.from_dict(data.get("reflection", {})),
            planning=PlanningConfig.from_dict(data.get("planning", {})),
            learning=LearningConfig.from_dict(data.get("learning", {})),
            logging_level=logging.get("level", "INFO"),
            logging_file=logging.get("file"),
            hiveloop_log_prompts=data.get("hiveloop_log_prompts", False),
        )

    @classmethod
    def create_default(cls) -> "GlobalConfig":
        """Create default configuration with standard tool settings."""
        # Note: allowed_paths=None means agent_manager will use per-agent paths dynamically
        return cls(
            tools={
                "file_read": ToolConfig(
                    enabled=True,
                    allowed_paths=None  # Uses per-agent paths: skills_dir, agent/memory, agent/runs, sandbox
                ),
                "file_write": ToolConfig(
                    enabled=True,
                    allowed_paths=None  # Uses per-agent paths: agent/runs, sandbox, agent/memory
                ),
                "http_request": ToolConfig(
                    enabled=True,
                    timeout_seconds=30
                ),
                "webpage_fetch": ToolConfig(
                    enabled=True,
                    max_length=15000
                ),
            }
        )


@dataclass
class LLMConfig:
    """LLM configuration for an agent."""
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5-20250929"
    temperature: float = 0.0
    max_tokens: int = 4096

    def to_dict(self) -> Dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "LLMConfig":
        return cls(
            provider=data.get("provider", "anthropic"),
            model=data.get("model", "claude-sonnet-4-5-20250929"),
            temperature=data.get("temperature", 0.0),
            max_tokens=data.get("max_tokens", 4096),
        )


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    agent_id: str
    name: str = ""
    description: str = ""
    role: str = ""
    company_id: str = "default"  # Multi-tenant: owner company
    llm: LLMConfig = field(default_factory=LLMConfig)
    system_prompt: str = "You are a helpful AI assistant."
    max_turns: int = 20
    timeout_seconds: int = 600
    enabled_tools: List[str] = field(default_factory=list)
    sandbox_paths: List[str] = field(default_factory=list)
    reflection: Optional[ReflectionConfig] = None  # Per-agent override (None = use global)
    planning: Optional[PlanningConfig] = None  # Per-agent override (None = use global)
    learning: Optional[LearningConfig] = None  # Per-agent override (None = use global)
    traits: Optional[TraitsConfig] = None  # Per-agent traits (None = not participating)
    session_max_turns: int = 50  # Compact session after this many turns (0 = disabled)
    phase2_model: Optional[str] = None  # Model for Phase 2 (None = same as Phase 1)
    persist_queue_on_stop: bool = False  # Save pending events to disk on stop
    webhook_secret: Optional[str] = None  # Secret for X-Hook-Secret webhook auth
    heartbeat_context_count: int = 3  # Number of prior heartbeat summaries to inject
    heartbeat_enabled: bool = True  # Opt-out by setting False
    heartbeat_interval_minutes: int = 15  # Agent-wide heartbeat cadence
    is_deleted: bool = False  # Soft delete flag

    def to_dict(self) -> Dict:
        result = {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "company_id": self.company_id,
            "llm": self.llm.to_dict(),
            "system_prompt": self.system_prompt,
            "limits": {
                "max_turns": self.max_turns,
                "timeout_seconds": self.timeout_seconds,
                "session_max_turns": self.session_max_turns,
                "phase2_model": self.phase2_model,
                "heartbeat_context_count": self.heartbeat_context_count,
            },
            "tools": {
                "enabled": self.enabled_tools,
            },
            "sandbox": {
                "root_paths": self.sandbox_paths,
            },
            "heartbeat": {
                "enabled": self.heartbeat_enabled,
                "interval_minutes": self.heartbeat_interval_minutes,
            },
            "is_deleted": self.is_deleted
        }
        webhooks = {}
        if self.webhook_secret:
            webhooks["secret"] = self.webhook_secret
        if self.persist_queue_on_stop:
            webhooks["persist_queue"] = self.persist_queue_on_stop
        if webhooks:
            result["webhooks"] = webhooks
        if self.reflection:
            result["reflection"] = self.reflection.to_dict()
        if self.planning:
            result["planning"] = self.planning.to_dict()
        if self.learning:
            result["learning"] = self.learning.to_dict()
        if self.traits:
            result["traits"] = self.traits.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> "AgentConfig":
        llm_data = data.get("llm", {})
        limits = data.get("limits", {})
        tools = data.get("tools", {})
        sandbox = data.get("sandbox", {})
        reflection_data = data.get("reflection")
        planning_data = data.get("planning")
        learning_data = data.get("learning")
        traits_data = data.get("traits")
        webhooks = data.get("webhooks", {})
        heartbeat_cfg = data.get("heartbeat", {})

        return cls(
            agent_id=data.get("agent_id", "default"),
            name=data.get("name", data.get("agent_id", "default")),
            description=data.get("description", ""),
            role=data.get("role", ""),
            company_id=data.get("company_id", "default"),
            llm=LLMConfig.from_dict(llm_data),
            system_prompt=data.get("system_prompt", "You are a helpful AI assistant."),
            max_turns=limits.get("max_turns", 20),
            timeout_seconds=limits.get("timeout_seconds", 600),
            session_max_turns=limits.get("session_max_turns", 50),
            phase2_model=limits.get("phase2_model"),
            heartbeat_context_count=limits.get("heartbeat_context_count", 3),
            enabled_tools=tools.get("enabled", []),
            sandbox_paths=sandbox.get("root_paths", []),
            reflection=ReflectionConfig.from_dict(reflection_data) if reflection_data else None,
            planning=PlanningConfig.from_dict(planning_data) if planning_data else None,
            learning=LearningConfig.from_dict(learning_data) if learning_data else None,
            traits=TraitsConfig.from_dict(traits_data) if traits_data else None,
            persist_queue_on_stop=webhooks.get("persist_queue", False),
            webhook_secret=webhooks.get("secret"),
            heartbeat_enabled=heartbeat_cfg.get("enabled", True),
            heartbeat_interval_minutes=heartbeat_cfg.get("interval_minutes", 15),
            is_deleted=data.get("is_deleted", False),
        )

    @classmethod
    def from_file(cls, config_path: str) -> "AgentConfig":
        """Load agent config from JSON file."""
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def create_default(cls, agent_id: str = "main") -> "AgentConfig":
        """Create a default agent configuration."""
        return cls(
            agent_id=agent_id,
            name="Main Agent",
            description="General-purpose AI assistant",
            enabled_tools=[],
        )


# ============================================================================
# CONFIG MANAGER
# ============================================================================

class ConfigManager:
    """Manage global and agent configurations."""

    # Agent subdirectories to create
    AGENT_SUBDIRS = ["skills", "tasks", "memory", "sessions", "runs"]

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = _get_data_dir() / "CONFIG"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.global_config: GlobalConfig = GlobalConfig.create_default()
        self.agent_configs: Dict[str, AgentConfig] = {}
        self._project_root = _find_project_root()

    def load_global(self) -> GlobalConfig:
        """Load global configuration from file."""
        config_path = self.config_dir / "config.json"

        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.global_config = GlobalConfig.from_dict(data)
            except Exception as e:
                print(f"[WARN] Could not load global config: {e}, using defaults")
                self.global_config = GlobalConfig.create_default()
        else:
            self.global_config = GlobalConfig.create_default()
            self.save_global()

        # Resolve paths relative to project root
        self.global_config.paths = self.global_config.paths.resolve(self._project_root)

        return self.global_config

    def save_global(self) -> None:
        """Save global configuration to file."""
        config_path = self.config_dir / "config.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(self.global_config.to_dict(), f, indent=2)

    def _ensure_agent_structure(self, agent_id: str) -> Path:
        """
        Ensure the full directory structure exists for an agent.

        Creates:
            AGENTS/{agent_id}/
            ├── config.json (if not exists)
            ├── skills/
            ├── tasks/
            ├── memory/
            ├── sessions/
            └── runs/

        Returns:
            Path to the agent's directory
        """
        agents_dir = Path(self.global_config.paths.agents_dir)
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Create all subdirectories
        for subdir in self.AGENT_SUBDIRS:
            (agent_dir / subdir).mkdir(exist_ok=True)

        return agent_dir

    def load_agent(self, agent_id: str) -> AgentConfig:
        """
        Load agent configuration from the per-agent directory structure.

        Looks for: data/AGENTS/{agent_id}/config.json
        Falls back to legacy: data/CONFIG/agents/{agent_id}.json
        """
        # Ensure global config is loaded (for paths)
        if not hasattr(self.global_config.paths, 'agents_dir') or not self.global_config.paths.agents_dir:
            self.load_global()

        # New location: AGENTS/{agent_id}/config.json
        agent_dir = self._ensure_agent_structure(agent_id)
        agent_config_path = agent_dir / "config.json"

        # Legacy location: CONFIG/agents/{agent_id}.json
        legacy_path = self.config_dir / "agents" / f"{agent_id}.json"

        # Try new location first
        if agent_config_path.exists():
            try:
                config = AgentConfig.from_file(str(agent_config_path))
                self.agent_configs[agent_id] = config
                return config
            except Exception as e:
                print(f"[WARN] Could not load agent config for {agent_id}: {e}")

        # Try legacy location
        if legacy_path.exists():
            try:
                config = AgentConfig.from_file(str(legacy_path))
                self.agent_configs[agent_id] = config
                # Migrate to new location
                self.save_agent(config)
                return config
            except Exception as e:
                print(f"[WARN] Could not load legacy agent config for {agent_id}: {e}")

        # Create default config
        config = AgentConfig.create_default(agent_id)
        self.save_agent(config)
        self.agent_configs[agent_id] = config
        return config

    def save_agent(self, config: AgentConfig) -> None:
        """
        Save agent configuration to the per-agent directory.

        Saves to: data/AGENTS/{agent_id}/config.json
        """
        agent_dir = self._ensure_agent_structure(config.agent_id)
        agent_config_path = agent_dir / "config.json"

        with open(agent_config_path, 'w', encoding='utf-8') as f:
            json.dump(config.to_dict(), f, indent=2)

        self.agent_configs[config.agent_id] = config

    def delete_agent(self, agent_id: str, hard_delete: bool = False) -> bool:
        """
        Delete an agent.

        Args:
            agent_id: Agent ID to delete
            hard_delete: If True, remove the directory entirely

        Returns:
            True if deleted successfully
        """
        if agent_id == "main":
            return False  # Never delete main agent

        if hard_delete:
            import shutil
            agent_dir = Path(self.global_config.paths.agents_dir) / agent_id
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
        else:
            # Soft delete - set is_deleted flag
            config = self.load_agent(agent_id)
            config.is_deleted = True
            self.save_agent(config)

        # Remove from cache
        if agent_id in self.agent_configs:
            del self.agent_configs[agent_id]

        return True

    def list_agents(self) -> List[str]:
        """
        List all configured agent IDs.

        Scans data/AGENTS/ directory for agent subdirectories.
        Falls back to legacy CONFIG/agents/*.json if AGENTS dir doesn't exist.
        """
        # Ensure global config is loaded
        if not hasattr(self.global_config.paths, 'agents_dir') or not self.global_config.paths.agents_dir:
            self.load_global()

        agents = set()

        # New location: AGENTS/*/config.json
        agents_dir = Path(self.global_config.paths.agents_dir)
        if agents_dir.exists():
            for agent_dir in agents_dir.iterdir():
                if agent_dir.is_dir() and (agent_dir / "config.json").exists():
                    agents.add(agent_dir.name)

        # Legacy location: CONFIG/agents/*.json
        legacy_dir = self.config_dir / "agents"
        if legacy_dir.exists():
            for config_file in legacy_dir.glob("*.json"):
                agents.add(config_file.stem)

        return sorted(list(agents))

    def get_agent_dir(self, agent_id: str) -> Path:
        """Get the directory path for an agent."""
        return self._ensure_agent_structure(agent_id)

    def get_tool_config(self, tool_name: str) -> ToolConfig:
        """Get configuration for a specific tool."""
        return self.global_config.tools.get(tool_name, ToolConfig())

    def get_resolved_path(self, path_name: str) -> Path:
        """Get a resolved path from configuration."""
        paths = self.global_config.paths
        path_str = getattr(paths, path_name, None)
        if path_str:
            return Path(path_str)
        return self._project_root / "data" / path_name.upper().replace("_dir", "")


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_config_manager: Optional[ConfigManager] = None


def get_config_manager(config_dir: Optional[str] = None) -> ConfigManager:
    """Get or create the global config manager."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_dir)
        _config_manager.load_global()
    return _config_manager


def load_global_config() -> GlobalConfig:
    """Load and return global configuration."""
    return get_config_manager().load_global()


def load_agent_config(agent_id: str) -> AgentConfig:
    """Load and return agent configuration."""
    return get_config_manager().load_agent(agent_id)


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Config Manager")
    print("=" * 60)

    # Show paths
    root = _find_project_root()
    data_dir = _get_data_dir()
    print(f"Project root: {root}")
    print(f"Data directory: {data_dir}")

    # Load config
    manager = get_config_manager()
    print(f"\nConfig directory: {manager.config_dir}")

    # Show global config
    global_cfg = manager.load_global()
    print(f"\n--- Global Config ---")
    print(f"Version: {global_cfg.version}")
    print(f"Default provider: {global_cfg.defaults.llm_provider}")
    print(f"Default model: {global_cfg.defaults.llm_model}")
    print(f"Skills dir: {global_cfg.paths.skills_dir}")

    # Show agent config
    agent_cfg = manager.load_agent("main")
    print(f"\n--- Agent Config (main) ---")
    print(f"Name: {agent_cfg.name}")
    print(f"Model: {agent_cfg.llm.model}")
    print(f"Max turns: {agent_cfg.max_turns}")
    print(f"Enabled tools: {agent_cfg.enabled_tools}")
