"""
CONTEXT_COMPACTION
==================

Conversation compaction to manage context window limits.

When a conversation approaches the token limit, this module:
1. Keeps the system prompt intact
2. Keeps the most recent N turns verbatim
3. Summarizes the middle portion

This allows long conversations to continue without losing
critical context.
"""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from ..observability import get_current_task, estimate_cost


# ============================================================================
# TOKEN COUNTING
# ============================================================================

def count_tokens(text: str) -> int:
    """
    Approximate token count for text.

    Uses a simple heuristic: ~4 characters per token on average.
    This is a rough approximation that works reasonably well for
    both English text and code.

    Args:
        text: Text to count tokens for

    Returns:
        Approximate token count
    """
    if not text:
        return 0
    # Rough approximation: 1 token ≈ 4 characters
    # This tends to slightly overcount, which is safer
    return len(text) // 4 + 1


def count_message_tokens(message: Dict[str, Any]) -> int:
    """
    Count tokens in a single message.

    Args:
        message: Message dict with 'role' and 'content'

    Returns:
        Approximate token count
    """
    tokens = 0

    # Role overhead (~4 tokens for role markers)
    tokens += 4

    # Content
    content = message.get("content", "")
    if isinstance(content, str):
        tokens += count_tokens(content)
    elif isinstance(content, list):
        # Handle multimodal content
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    tokens += count_tokens(item.get("text", ""))
                elif item.get("type") == "tool_result":
                    tokens += count_tokens(str(item.get("content", "")))
                elif item.get("type") == "tool_use":
                    tokens += count_tokens(str(item.get("input", {})))
            elif isinstance(item, str):
                tokens += count_tokens(item)

    return tokens


def count_conversation_tokens(
    messages: List[Dict[str, Any]],
    system_prompt: str = ""
) -> int:
    """
    Count total tokens in a conversation.

    Args:
        messages: List of message dicts
        system_prompt: System prompt text

    Returns:
        Total approximate token count
    """
    total = count_tokens(system_prompt)
    for msg in messages:
        total += count_message_tokens(msg)
    return total


# ============================================================================
# COMPACTION RESULT
# ============================================================================

@dataclass
class CompactionResult:
    """Result of conversation compaction."""
    compacted: bool
    original_tokens: int
    final_tokens: int
    turns_summarized: int
    summary: str = ""
    preserved_recent: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict:
        return {
            "compacted": self.compacted,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "turns_summarized": self.turns_summarized,
            "summary_length": len(self.summary),
            "preserved_recent": self.preserved_recent,
            "timestamp": self.timestamp,
        }


# ============================================================================
# CONTEXT MANAGER
# ============================================================================

class ContextManager:
    """
    Manages context window and compaction.

    Monitors token usage and compacts conversation when
    approaching the configured limit.
    """

    # Default settings
    DEFAULT_MAX_TOKENS = 100000  # Conservative limit for Claude
    DEFAULT_THRESHOLD = 0.85     # Compact at 85% of limit
    DEFAULT_PRESERVE_RECENT = 6  # Keep last 6 turns verbatim

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        threshold: float = DEFAULT_THRESHOLD,
        preserve_recent: int = DEFAULT_PRESERVE_RECENT,
        llm_client: Optional[Any] = None
    ):
        """
        Initialize context manager.

        Args:
            max_tokens: Maximum context window size
            threshold: Fraction of max at which to trigger compaction
            preserve_recent: Number of recent turns to keep verbatim
            llm_client: Optional LLM client for generating summaries
        """
        self.max_tokens = max_tokens
        self.threshold = threshold
        self.preserve_recent = preserve_recent
        self.llm_client = llm_client
        self.compaction_count = 0

    def should_compact(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = ""
    ) -> bool:
        """
        Check if conversation should be compacted.

        Args:
            messages: Current conversation messages
            system_prompt: System prompt

        Returns:
            True if compaction needed
        """
        current_tokens = count_conversation_tokens(messages, system_prompt)
        threshold_tokens = int(self.max_tokens * self.threshold)
        return current_tokens >= threshold_tokens

    def compact(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str = ""
    ) -> tuple[List[Dict[str, Any]], CompactionResult]:
        """
        Compact conversation by summarizing middle portions.

        Strategy:
        1. Keep system prompt (always)
        2. Keep first turn if it contains important context
        3. Summarize middle turns
        4. Keep last N turns verbatim

        Args:
            messages: Conversation messages
            system_prompt: System prompt

        Returns:
            Tuple of (compacted messages, compaction result)
        """
        original_tokens = count_conversation_tokens(messages, system_prompt)

        # If under threshold, no compaction needed
        if not self.should_compact(messages, system_prompt):
            return messages, CompactionResult(
                compacted=False,
                original_tokens=original_tokens,
                final_tokens=original_tokens,
                turns_summarized=0,
                preserved_recent=len(messages)
            )

        # Not enough messages to compact
        if len(messages) <= self.preserve_recent + 2:
            return messages, CompactionResult(
                compacted=False,
                original_tokens=original_tokens,
                final_tokens=original_tokens,
                turns_summarized=0,
                preserved_recent=len(messages)
            )

        # Split messages
        # Keep first message if it's from user (often contains important context)
        first_turn = messages[:2] if len(messages) > 2 else []
        recent_turns = messages[-self.preserve_recent:]
        middle_turns = messages[2:-self.preserve_recent] if len(messages) > self.preserve_recent + 2 else []

        if not middle_turns:
            return messages, CompactionResult(
                compacted=False,
                original_tokens=original_tokens,
                final_tokens=original_tokens,
                turns_summarized=0,
                preserved_recent=len(messages)
            )

        # Generate summary of middle portion
        summary = self._summarize_turns(middle_turns)

        # Create summary message
        summary_message = {
            "role": "user",
            "content": f"[CONVERSATION SUMMARY - {len(middle_turns)} turns compacted]\n\n{summary}\n\n[END SUMMARY - Recent conversation continues below]"
        }

        # Build compacted conversation
        compacted = first_turn + [summary_message] + recent_turns

        final_tokens = count_conversation_tokens(compacted, system_prompt)
        self.compaction_count += 1

        # Gap #7: Report compaction event to HiveLoop
        _task = get_current_task()
        if _task:
            try:
                _ratio = round(1 - (final_tokens / original_tokens), 3) if original_tokens > 0 else 0
                _task.event("context_compacted", payload={
                    "tokens_before": original_tokens,
                    "tokens_after": final_tokens,
                    "compression_ratio": _ratio,
                    "turns_summarized": len(middle_turns),
                    "preserved_recent": len(recent_turns),
                    "compaction_count": self.compaction_count,
                })
            except Exception:
                pass

        return compacted, CompactionResult(
            compacted=True,
            original_tokens=original_tokens,
            final_tokens=final_tokens,
            turns_summarized=len(middle_turns),
            summary=summary,
            preserved_recent=len(recent_turns)
        )

    def _summarize_turns(self, turns: List[Dict[str, Any]]) -> str:
        """
        Generate a summary of conversation turns.

        If LLM client is available, uses it for high-quality summary.
        Otherwise, falls back to extractive summary.

        Args:
            turns: Turns to summarize

        Returns:
            Summary text
        """
        if self.llm_client:
            return self._llm_summarize(turns)
        else:
            return self._extractive_summarize(turns)

    def _llm_summarize(self, turns: List[Dict[str, Any]]) -> str:
        """Use LLM to generate summary."""
        try:
            # Format turns for summary
            formatted = []
            for turn in turns:
                role = turn.get("role", "unknown")
                content = turn.get("content", "")
                if isinstance(content, str):
                    formatted.append(f"{role.upper()}: {content[:500]}")
                elif isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", "")[:200])
                    if text_parts:
                        formatted.append(f"{role.upper()}: {' '.join(text_parts)}")

            conversation_text = "\n\n".join(formatted)

            prompt = f"""Summarize this conversation excerpt concisely. Focus on:
1. Key decisions made
2. Important information shared
3. Tasks completed or in progress
4. Any commitments or action items

Keep the summary under 500 words.

CONVERSATION:
{conversation_text}

SUMMARY:"""

            _llm_start = time.perf_counter()
            response = self.llm_client.complete(
                prompt=prompt,
                system="You are a precise summarizer. Extract key facts only.",
                max_tokens=600,
                caller="context_compaction"
            )
            _llm_elapsed = (time.perf_counter() - _llm_start) * 1000

            # HiveLoop: Context compaction LLM call tracking
            _task = get_current_task()
            if _task:
                try:
                    _cc_in = getattr(self.llm_client, '_last_input_tokens', 0)
                    _cc_out = getattr(self.llm_client, '_last_output_tokens', 0)
                    _task.llm_call(
                        "context_compaction",
                        model=self.llm_client.model,
                        tokens_in=_cc_in,
                        tokens_out=_cc_out,
                        cost=estimate_cost(self.llm_client.model, _cc_in, _cc_out),
                        duration_ms=round(_llm_elapsed),
                        metadata={
                            "cache_read_tokens": getattr(self.llm_client, '_last_cache_read_tokens', None),
                            "cache_write_tokens": getattr(self.llm_client, '_last_cache_creation_tokens', None),
                        },
                    )
                except Exception:
                    pass

            return response.content if response.content else self._extractive_summarize(turns)

        except Exception as e:
            # Fall back to extractive if LLM fails
            return self._extractive_summarize(turns)

    def _extractive_summarize(self, turns: List[Dict[str, Any]]) -> str:
        """
        Create extractive summary without LLM.

        Extracts key sentences and information from turns.
        """
        summaries = []

        for i, turn in enumerate(turns):
            role = turn.get("role", "unknown")
            content = turn.get("content", "")

            if isinstance(content, str):
                # Extract first meaningful sentence
                sentences = content.split('. ')
                if sentences:
                    first = sentences[0].strip()
                    if len(first) > 10:  # Skip very short
                        if len(first) > 150:
                            first = first[:150] + "..."
                        summaries.append(f"- [{role}] {first}")
            elif isinstance(content, list):
                # Handle tool calls
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "tool_use":
                            tool_name = item.get("name", "unknown")
                            summaries.append(f"- [tool] Called {tool_name}")
                        elif item.get("type") == "tool_result":
                            summaries.append(f"- [result] Tool returned result")

        # Limit summary length
        if len(summaries) > 20:
            summaries = summaries[:10] + ["- ... (additional turns omitted) ..."] + summaries[-5:]

        return "\n".join(summaries) if summaries else "No significant content to summarize."

    def get_stats(self) -> Dict:
        """Get compaction statistics."""
        return {
            "max_tokens": self.max_tokens,
            "threshold": self.threshold,
            "preserve_recent": self.preserve_recent,
            "compaction_count": self.compaction_count,
        }


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def compact_conversation(
    messages: List[Dict[str, Any]],
    system_prompt: str = "",
    max_tokens: int = 100000,
    preserve_recent: int = 6,
    llm_client: Optional[Any] = None
) -> tuple[List[Dict[str, Any]], CompactionResult]:
    """
    Compact a conversation if needed.

    Convenience function that creates a ContextManager and compacts.

    Args:
        messages: Conversation messages
        system_prompt: System prompt
        max_tokens: Maximum context window
        preserve_recent: Turns to keep verbatim
        llm_client: Optional LLM for summarization

    Returns:
        Tuple of (compacted messages, result)
    """
    manager = ContextManager(
        max_tokens=max_tokens,
        preserve_recent=preserve_recent,
        llm_client=llm_client
    )
    return manager.compact(messages, system_prompt)


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Context Compaction Module")
    print("=" * 60)

    # Test token counting
    print("\n--- Token Counting ---")
    test_text = "Hello, this is a test message for token counting."
    print(f"Text: {test_text}")
    print(f"Tokens: {count_tokens(test_text)}")

    # Test conversation token counting
    print("\n--- Conversation Token Counting ---")
    test_messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you for asking!"},
        {"role": "user", "content": "Can you help me with a task?"},
    ]
    print(f"Messages: {len(test_messages)}")
    print(f"Total tokens: {count_conversation_tokens(test_messages)}")

    # Test compaction (with small limit to trigger it)
    print("\n--- Compaction Test ---")
    long_messages = [
        {"role": "user", "content": f"Message {i}: " + "x" * 100}
        for i in range(20)
    ]
    print(f"Original messages: {len(long_messages)}")
    print(f"Original tokens: {count_conversation_tokens(long_messages)}")

    manager = ContextManager(max_tokens=500, threshold=0.5, preserve_recent=4)
    compacted, result = manager.compact(long_messages)

    print(f"Compacted: {result.compacted}")
    print(f"Final messages: {len(compacted)}")
    print(f"Final tokens: {result.final_tokens}")
    print(f"Turns summarized: {result.turns_summarized}")
    print(f"Summary preview: {result.summary[:200]}...")
