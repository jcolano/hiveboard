"""
MEMORY MODULE
=============

Memory management for the Agentic Loop Framework.

Handles:
- Session persistence (conversation history)
- Long-term memory (topics, indexes, content)
- Memory search and retrieval
- Memory prompt injection
- Memory decision logic (when to store)
"""

from .manager import (
    MemoryManager,
    Session,
    MemoryEntry,
    TopicIndex
)

from .decision import (
    # Data structures
    DirectiveResult,
    SessionReviewResult,
    ConsolidationResult,
    DecayResult,

    # Handlers
    UserDirectiveHandler,
    SessionEndReviewer,
    MemoryConsolidator,
    MemoryDecay,

    # Utilities
    contains_sensitive_data,
    contains_health_data,
    is_session_end_command,
    parse_memory_command,
)

__all__ = [
    # Manager
    'MemoryManager',
    'Session',
    'MemoryEntry',
    'TopicIndex',

    # Decision data structures
    'DirectiveResult',
    'SessionReviewResult',
    'ConsolidationResult',
    'DecayResult',

    # Decision handlers
    'UserDirectiveHandler',
    'SessionEndReviewer',
    'MemoryConsolidator',
    'MemoryDecay',

    # Decision utilities
    'contains_sensitive_data',
    'contains_health_data',
    'is_session_end_command',
    'parse_memory_command',
]
