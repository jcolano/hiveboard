"""
AGENTIC_LOOP
============

Python framework for building LLM-powered agents.

Features:
- Multi-step task execution with tool calling
- Skill system (markdown-based behaviors)
- Memory system (sessions and long-term)
- Configurable safety limits

Usage:
    from loop_core import AgenticLoop
    from loop_core.tools import ToolRegistry
    from loop_core.skills import SkillLoader

    # Set up tools
    registry = ToolRegistry()

    # Set up skills
    skill_loader = SkillLoader("./data/SKILLS")
    skill_loader.load_all()

    # Create loop
    loop = AgenticLoop(llm_client, registry)
    result = loop.execute(message, skills_prompt=skill_loader.build_skill_content_prompt("my_skill"))
"""

__version__ = "1.0.0"

# Core loop
from .loop import AgenticLoop, LoopResult, Turn, ToolCallRecord, TokenUsage

# Reflection
from .reflection import ReflectionManager, ReflectionConfig, ReflectionResult

# Configuration
from .config import (
    ConfigManager,
    GlobalConfig,
    AgentConfig,
    TraitsConfig,
    ReflectionConfig,
    get_config_manager,
    load_global_config,
    load_agent_config
)

# Traits
from .traits import TraitCompiler

# Tools
from .tools import (
    BaseTool,
    ToolRegistry,
    ToolDefinition,
    ToolParameter,
    ToolResult
)

# Skills
from .skills import (
    Skill,
    SkillLoader,
    SkillLoadError
)

# Memory
from .memory import (
    MemoryManager,
    Session,
    MemoryEntry,
    TopicIndex
)

# Agent
from .agent import Agent, AgentResult

# Agent Manager
from .agent_manager import (
    AgentManager,
    get_agent_manager,
    run_agent
)

# Output
from .output import OutputManager, RunOutput

__all__ = [
    # Version
    '__version__',

    # Core
    'AgenticLoop',
    'LoopResult',
    'Turn',
    'ToolCallRecord',
    'TokenUsage',

    # Reflection
    'ReflectionManager',
    'ReflectionConfig',
    'ReflectionResult',

    # Configuration
    'ConfigManager',
    'GlobalConfig',
    'AgentConfig',
    'TraitsConfig',
    'ReflectionConfig',
    'get_config_manager',
    'load_global_config',
    'load_agent_config',

    # Traits
    'TraitCompiler',

    # Tools
    'BaseTool',
    'ToolRegistry',
    'ToolDefinition',
    'ToolParameter',
    'ToolResult',

    # Skills
    'Skill',
    'SkillLoader',
    'SkillLoadError',

    # Memory
    'MemoryManager',
    'Session',
    'MemoryEntry',
    'TopicIndex',

    # Agent
    'Agent',
    'AgentResult',

    # Agent Manager
    'AgentManager',
    'get_agent_manager',
    'run_agent',

    # Output
    'OutputManager',
    'RunOutput',
]
