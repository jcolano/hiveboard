"""
CONTEXT MANAGEMENT MODULE
=========================

Manages context window size and conversation compaction.

Features:
- Token counting (approximate)
- Conversation compaction when approaching limits
- Preserves recent turns and system prompt
- Summarizes middle portions
"""

from .compaction import (
    ContextManager,
    CompactionResult,
    count_tokens,
    count_message_tokens,
    count_conversation_tokens,
    compact_conversation,
)

__all__ = [
    'ContextManager',
    'CompactionResult',
    'count_tokens',
    'count_message_tokens',
    'count_conversation_tokens',
    'compact_conversation',
]
