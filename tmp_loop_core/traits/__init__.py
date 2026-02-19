"""
TRAIT SYSTEM
============

Behavioral trait system for loopCore agents.

Traits are measurable personality dimensions that influence how agents
plan, verify, communicate, and make decisions. The trait compiler translates
agent trait values into system prompt modifications.

Usage:
    from loop_core.traits import TraitCompiler

    compiler = TraitCompiler(traits_dir)
    prompt = compiler.compile(agent_id, traits_block)
"""

from .compiler import TraitCompiler

__all__ = ["TraitCompiler"]
