"""
CLI MODULE
==========

Command-line interface for the Agentic Loop Framework.

Usage:
    python -m loop_core.cli run <agent_id> <message>
    python -m loop_core.cli list-agents
    python -m loop_core.cli sessions <agent_id>
    python -m loop_core.cli skills
"""

from .main import main, cli_run, cli_list_agents, cli_sessions, cli_skills

__all__ = [
    'main',
    'cli_run',
    'cli_list_agents',
    'cli_sessions',
    'cli_skills'
]
