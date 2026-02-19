"""
MEMORY_DECISION
===============

Memory decision logic for the Agentic Loop Framework.

Implements when and how the agent decides what to store in long-term memory:
1. User Directives - Explicit "remember this" requests (highest priority)
2. Session End Review - LLM-driven extraction when session completes

Based on MEMORY_DECISION_SPEC.md.

Usage:
    from loop_core.memory.decision import (
        UserDirectiveHandler,
        SessionEndReviewer,
        MemoryConsolidator,
        MemoryDecay,
        contains_sensitive_data
    )
"""

import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path


# ============================================================================
# SENSITIVE DATA DETECTION
# ============================================================================

SENSITIVE_PATTERNS = {
    # Never store - security credentials
    "password": r"(?:password|passwd|pwd)\s*(?:[:=]|is)\s*\S+",
    "api_key": r"(?:api[_\s]?key|apikey)\s*(?:[:=]|is)\s*\S+",
    "secret_key": r"(?:secret[_\s]?key|secretkey)\s*(?:[:=]|is)\s*\S+",
    "access_token": r"(?:access[_\s]?token|bearer)\s*(?:[:=]|is)\s*\S+",
    "private_key": r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----",
    "ssh_key": r"ssh-(?:rsa|ed25519|dss)\s+\S+",

    # Never store - financial
    "credit_card": r"\b(?:\d{4}[- ]?){3}\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
}

HEALTH_PATTERNS = [
    "diagnosed", "diagnosis", "symptom", "medication", "prescription",
    "doctor", "hospital", "medical", "health condition",
    "treatment", "therapy", "disease", "illness"
]


def contains_sensitive_data(text: str) -> Tuple[bool, Optional[str]]:
    """
    Check if text contains sensitive data that should never be stored.

    Returns:
        (is_sensitive, reason) - reason is the category if sensitive
    """
    text_lower = text.lower()

    for category, pattern in SENSITIVE_PATTERNS.items():
        if re.search(pattern, text_lower, re.IGNORECASE):
            return (True, category)

    return (False, None)


def contains_health_data(text: str) -> bool:
    """Check if text contains health/medical information."""
    text_lower = text.lower()
    return any(indicator in text_lower for indicator in HEALTH_PATTERNS)


# ============================================================================
# USER DIRECTIVE PATTERNS
# ============================================================================

USER_DIRECTIVE_PATTERNS = [
    # Explicit remember requests
    r"remember (?:that |this[:\s])?(.+)",
    r"don'?t forget (?:that )?(.+)",
    r"note (?:that |this[:\s])?(.+)",
    r"keep in mind (?:that )?(.+)",
    r"make a note[:\s]?(.+)",
    r"store (?:this|that)[:\s]?(.+)",
    r"save (?:this|that) (?:for later|to memory)[:\s]?(.+)",

    # Implicit strong signals
    r"(?:my|i) (?:always|never) (.+)",
    r"(?:it'?s|that'?s) important (?:that|to) (.+)",
]

# Topic inference keywords
TOPIC_KEYWORDS = {
    "preferences": ["prefer", "like", "favorite", "always", "never", "hate", "love"],
    "user_info": ["name is", "work at", "live in", "age is", "birthday", "i am"],
    "contacts": ["email", "phone", "contact", "address", "colleague", "friend"],
    "projects": ["project", "working on", "building", "developing", "task", "repo"],
    "decisions": ["decided", "decision", "agreed", "commitment", "plan to"],
}


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class DirectiveResult:
    """Result of checking for user directive."""
    found: bool
    stored: bool
    content_id: Optional[str] = None
    message: Optional[str] = None
    topic: Optional[str] = None


@dataclass
class SessionReviewResult:
    """Result of session end review."""
    memories_created: int
    notification: Optional[str]
    items: List[Dict] = field(default_factory=list)


@dataclass
class ConsolidationResult:
    """Result of memory consolidation."""
    topics_processed: int
    memories_merged: int
    memories_removed: int


@dataclass
class DecayResult:
    """Result of decay application."""
    memories_decayed: int
    memories_archived: int


# ============================================================================
# USER DIRECTIVE HANDLER
# ============================================================================

class UserDirectiveHandler:
    """
    Handles explicit user memory requests.

    Checks user messages for memory directives like "remember that...",
    "don't forget...", etc. and stores them immediately with maximum importance.
    """

    def __init__(self, memory_manager):
        """
        Initialize handler.

        Args:
            memory_manager: MemoryManager instance for storage
        """
        self.memory_manager = memory_manager

    def check_for_directive(self, user_message: str) -> DirectiveResult:
        """
        Check if user message contains a memory directive.

        Called BEFORE the agentic loop processes the message.
        If directive found, store immediately and acknowledge.

        Args:
            user_message: The user's message

        Returns:
            DirectiveResult with found/stored status and acknowledgment
        """
        message_lower = user_message.lower().strip()

        for pattern in USER_DIRECTIVE_PATTERNS:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                content = match.group(1).strip()

                # Don't store very short content
                if len(content) < 5:
                    continue

                # Filter sensitive data even for explicit requests
                is_sensitive, reason = contains_sensitive_data(content)
                if is_sensitive:
                    return DirectiveResult(
                        found=True,
                        stored=False,
                        message=f"I can't store that because it appears to contain {reason}."
                    )

                # Determine topic from content
                topic = self._infer_topic(content)

                # Ensure topic exists
                self.memory_manager._ensure_topic(topic)

                # Store with highest importance
                content_id = self.memory_manager.add_memory(
                    topic_id=topic,
                    title=self._generate_title(content),
                    content=content,
                    keywords=self._extract_keywords(content),
                    metadata={
                        "source": "user_directive",
                        "importance": 1.0,
                        "user_requested": True,
                        "original_message": user_message[:200],
                        "stored_at": datetime.now(timezone.utc).isoformat()
                    }
                )

                return DirectiveResult(
                    found=True,
                    stored=True,
                    content_id=content_id,
                    topic=topic,
                    message="Got it, I'll remember that."
                )

        return DirectiveResult(found=False, stored=False, message=None)

    def _infer_topic(self, content: str) -> str:
        """Infer appropriate topic from content."""
        content_lower = content.lower()

        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(kw in content_lower for kw in keywords):
                return topic

        return "general"

    def _generate_title(self, content: str) -> str:
        """Generate a brief title for the memory."""
        # Take first 50 chars or first sentence
        if len(content) <= 50:
            return content

        # Try to find first sentence
        sentence_end = content.find('.')
        if 0 < sentence_end <= 60:
            return content[:sentence_end]

        return content[:47] + "..."

    def _extract_keywords(self, content: str) -> List[str]:
        """Extract keywords from content."""
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'can', 'to', 'of', 'in',
            'for', 'on', 'with', 'at', 'by', 'from', 'that', 'this',
            'i', 'my', 'me', 'we', 'our', 'you', 'your', 'it', 'its',
            'and', 'or', 'but', 'so', 'if', 'then', 'than', 'when'
        }

        words = re.findall(r'\b[a-zA-Z]{3,}\b', content.lower())
        word_counts = {}
        for word in words:
            if word not in stop_words:
                word_counts[word] = word_counts.get(word, 0) + 1

        sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        return [word for word, count in sorted_words[:8]]


# ============================================================================
# SESSION END REVIEWER
# ============================================================================

MEMORY_EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction assistant. Your job is to identify facts from conversations that are worth remembering long-term.

Focus on:
- User identity and preferences
- Important decisions and agreements
- Project names and details
- Contact information (except passwords)
- Explicit "remember this" requests

Be selective. Only extract facts that would genuinely help in future conversations. Ignore temporary details, pleasantries, and session-specific context.

Return valid JSON only."""


class SessionEndReviewer:
    """
    Reviews completed sessions for memory extraction.

    Uses the LLM to perform a deep review of the conversation and
    extract key facts that should be remembered long-term.
    """

    def __init__(self, llm_client, memory_manager):
        """
        Initialize reviewer.

        Args:
            llm_client: LLM client with complete_json method
            memory_manager: MemoryManager instance for storage
        """
        self.llm_client = llm_client
        self.memory_manager = memory_manager

    def review_session(self, session: Dict) -> SessionReviewResult:
        """
        Review a completed session and extract memories.

        Called when:
        - Session status changes to "completed"
        - Explicit session end request

        Args:
            session: Session data dict with conversation history

        Returns:
            SessionReviewResult with created memories and notification
        """
        conversation = session.get("conversation", [])

        if len(conversation) < 3:
            # Too short to extract meaningful memories
            return SessionReviewResult(memories_created=0, notification=None)

        # Build extraction prompt
        prompt = self._build_extraction_prompt(conversation)

        # Ask LLM to extract facts
        response = self.llm_client.complete_json(
            prompt=prompt,
            system=MEMORY_EXTRACTION_SYSTEM_PROMPT,
            caller="session_end_review",
            max_tokens=4096  # Increased from 2000 to avoid truncation
        )

        if not response:
            return SessionReviewResult(memories_created=0, notification=None)

        # Process extracted facts
        facts = response.get("facts", [])
        memories_created = 0
        stored_items = []

        for fact in facts:
            # Validate fact structure
            if not isinstance(fact, dict) or not fact.get("content"):
                continue

            content = fact.get("content", "")

            # Filter sensitive data
            is_sensitive, _ = contains_sensitive_data(content)
            if is_sensitive:
                continue

            # Filter health data unless explicitly requested
            if contains_health_data(content) and not fact.get("user_requested", False):
                continue

            topic = fact.get("topic", "general")

            # Ensure topic exists
            self.memory_manager._ensure_topic(topic)

            # Store the memory
            content_id = self.memory_manager.add_memory(
                topic_id=topic,
                title=fact.get("title", "Untitled"),
                content=content,
                keywords=fact.get("keywords", []),
                metadata={
                    "source": "session_review",
                    "session_id": session.get("session_id"),
                    "importance": fact.get("importance", 0.5),
                    "extracted_at": datetime.now(timezone.utc).isoformat()
                }
            )

            memories_created += 1
            stored_items.append({
                "title": fact.get("title", "Untitled"),
                "topic": topic
            })

        # Build user notification
        notification = None
        if stored_items:
            notification = self._build_notification(stored_items)

        return SessionReviewResult(
            memories_created=memories_created,
            notification=notification,
            items=stored_items
        )

    def _build_extraction_prompt(self, conversation: List[Dict]) -> str:
        """Build prompt for LLM memory extraction."""
        # Format conversation for review
        formatted = []
        for msg in conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            # Truncate long messages
            if len(content) > 500:
                content = content[:500] + "..."
            formatted.append(f"{role.upper()}: {content}")

        # Take last 20 messages
        conversation_text = "\n".join(formatted[-20:])

        return f"""Review this conversation and extract facts worth remembering long-term.

CONVERSATION:
{conversation_text}

Extract facts that are:
- Personal information about the user (name, preferences, work, etc.)
- Decisions made during this conversation
- Important context for future conversations
- Explicit requests to remember something

DO NOT extract:
- Passwords, API keys, or security credentials
- Temporary or session-specific details
- Health/medical information (unless user explicitly asked to remember it)
- Mundane conversation details

Return JSON:
{{
  "facts": [
    {{
      "title": "Brief title",
      "content": "The fact to remember",
      "topic": "user_info|preferences|decisions|projects|contacts|general",
      "keywords": ["keyword1", "keyword2"],
      "importance": 0.5,
      "user_requested": false
    }}
  ]
}}"""

    def _build_notification(self, items: List[Dict]) -> str:
        """Build user notification about stored memories."""
        if not items:
            return None

        lines = ["I've noted the following for future reference:"]
        for item in items[:5]:  # Limit to 5 items in notification
            lines.append(f"  - {item['title']} ({item['topic']})")

        if len(items) > 5:
            lines.append(f"  - ...and {len(items) - 5} more items")

        return "\n".join(lines)


# ============================================================================
# MEMORY CONSOLIDATION
# ============================================================================

class MemoryConsolidator:
    """
    Consolidates related memories to reduce redundancy.

    Groups memories by keyword similarity and uses the LLM to
    merge related entries into comprehensive summaries.
    """

    def __init__(self, llm_client, memory_manager):
        """
        Initialize consolidator.

        Args:
            llm_client: LLM client for merging
            memory_manager: MemoryManager instance
        """
        self.llm_client = llm_client
        self.memory_manager = memory_manager

    def run_consolidation(self, topic_id: str = None) -> ConsolidationResult:
        """
        Consolidate memories within topics.

        Process:
        1. Load all memories for topic(s)
        2. Group by keyword similarity
        3. For groups with 3+ items, ask LLM to merge
        4. Replace old entries with consolidated version

        Args:
            topic_id: Specific topic to consolidate, or None for all

        Returns:
            ConsolidationResult with statistics
        """
        topics = self.memory_manager.list_topics()

        # Extract topic IDs from topics list
        topic_ids = []
        for t in topics:
            if isinstance(t, dict):
                topic_ids.append(t.get("id", ""))
            else:
                topic_ids.append(t)

        target_topics = topic_ids if topic_id is None else [topic_id]

        total_merged = 0
        total_removed = 0
        topics_processed = 0

        for tid in target_topics:
            result = self._consolidate_topic(tid)
            total_merged += result.get("merged", 0)
            total_removed += result.get("removed", 0)
            topics_processed += 1

        return ConsolidationResult(
            topics_processed=topics_processed,
            memories_merged=total_merged,
            memories_removed=total_removed
        )

    def _consolidate_topic(self, topic_id: str) -> Dict:
        """Consolidate a single topic."""
        index = self.memory_manager.get_topic_index(topic_id)
        if not index:
            return {"merged": 0, "removed": 0}

        entries = index.entries
        if len(entries) < 3:
            return {"merged": 0, "removed": 0}

        # Group by keyword similarity
        groups = self._group_by_similarity(entries)

        merged = 0
        removed = 0

        for group in groups:
            if len(group) >= 3:
                # Load full content for each entry
                contents = []
                for entry in group:
                    content = self.memory_manager.get_memory(
                        topic_id,
                        entry.get("content_id", "")
                    )
                    if content:
                        contents.append({
                            "entry": entry,
                            "content": content
                        })

                if len(contents) >= 3:
                    # Ask LLM to consolidate
                    consolidated = self._llm_consolidate(contents)

                    if consolidated:
                        # Add consolidated memory
                        self.memory_manager.add_memory(
                            topic_id=topic_id,
                            title=consolidated.get("title", "Consolidated"),
                            content=consolidated.get("content", ""),
                            keywords=consolidated.get("keywords", []),
                            metadata={
                                "consolidated_from": [c["entry"].get("content_id") for c in contents],
                                "consolidated_at": datetime.now(timezone.utc).isoformat()
                            }
                        )
                        merged += 1

                        # Mark old entries for removal
                        for c in contents:
                            self.memory_manager.delete_memory(
                                topic_id,
                                c["entry"].get("content_id", "")
                            )
                            removed += 1

        return {"merged": merged, "removed": removed}

    def _group_by_similarity(self, entries: List[Dict]) -> List[List[Dict]]:
        """Group entries by keyword similarity."""
        groups = []
        used = set()

        for i, entry in enumerate(entries):
            if i in used:
                continue

            group = [entry]
            used.add(i)
            keywords_i = set(entry.get("keywords", []))

            for j, other in enumerate(entries):
                if j in used:
                    continue

                keywords_j = set(other.get("keywords", []))

                # Calculate Jaccard similarity
                if keywords_i and keywords_j:
                    intersection = len(keywords_i & keywords_j)
                    union = len(keywords_i | keywords_j)
                    similarity = intersection / union if union > 0 else 0

                    if similarity >= 0.5:  # 50% keyword overlap
                        group.append(other)
                        used.add(j)

            if len(group) >= 2:
                groups.append(group)

        return groups

    def _llm_consolidate(self, contents: List[Dict]) -> Optional[Dict]:
        """Use LLM to consolidate multiple memories into one."""
        memories_text = "\n\n".join(
            f"Memory {i+1}:\n{c['content'].get('content', '') if isinstance(c['content'], dict) else str(c['content'])}"
            for i, c in enumerate(contents)
        )

        prompt = f"""Consolidate these related memories into a single, comprehensive memory:

{memories_text}

Combine the information, remove redundancy, and keep all important details.

Return JSON:
{{
  "title": "Consolidated title",
  "content": "Consolidated content with all key information",
  "keywords": ["keyword1", "keyword2"]
}}"""

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You consolidate related memories into concise, comprehensive summaries. Return valid JSON only.",
            caller="memory_consolidation",
            max_tokens=4096  # Increased from 1000 to avoid truncation
        )

        return response


# ============================================================================
# MEMORY DECAY
# ============================================================================

class MemoryDecay:
    """
    Applies decay to memory relevance scores.

    Old, unused memories fade over time. This prevents memory bloat
    and keeps the system focused on relevant information.
    """

    # Decay configuration
    DECAY_RATE = 0.05           # 5% decay per period
    DECAY_PERIOD_DAYS = 7       # Apply decay weekly
    MIN_RELEVANCE = 0.1         # Below this, memory is archived
    BOOST_ON_ACCESS = 0.2       # Boost when memory is accessed

    def __init__(self, memory_manager):
        """
        Initialize decay manager.

        Args:
            memory_manager: MemoryManager instance
        """
        self.memory_manager = memory_manager

    def apply_decay(self) -> DecayResult:
        """
        Apply decay to all memory relevance scores.

        Decay formula:
        new_score = old_score * (1 - DECAY_RATE)

        Memories below MIN_RELEVANCE are archived (not deleted).

        Returns:
            DecayResult with statistics
        """
        topics = self.memory_manager.list_topics()

        decayed = 0
        archived = 0

        for topic_data in topics:
            # topics can be list of dicts with 'id' key or list of strings
            topic_id = topic_data.get("id") if isinstance(topic_data, dict) else topic_data
            index = self.memory_manager.get_topic_index(topic_id)

            if not index:
                continue

            entries = index.entries
            updated = False

            for entry in entries:
                # Skip already archived entries
                if entry.get("status") == "archived":
                    continue

                # Check if decay should be applied
                if self._should_decay(entry):
                    old_score = entry.get("relevance_score", 1.0)
                    new_score = old_score * (1 - self.DECAY_RATE)
                    entry["relevance_score"] = new_score
                    entry["decayed_at"] = datetime.now(timezone.utc).isoformat()
                    decayed += 1
                    updated = True

                    if new_score < self.MIN_RELEVANCE:
                        entry["status"] = "archived"
                        archived += 1

            # Save updated index
            if updated:
                self.memory_manager.save_topic_index(index)

        return DecayResult(
            memories_decayed=decayed,
            memories_archived=archived
        )

    def boost_on_access(self, topic_id: str, content_id: str) -> None:
        """
        Boost relevance score when a memory is accessed.

        Called when:
        - Memory appears in search results and is used
        - Memory is explicitly referenced in conversation

        Args:
            topic_id: The topic containing the memory
            content_id: The memory content ID
        """
        index = self.memory_manager.get_topic_index(topic_id)
        if not index:
            return

        updated = False
        for entry in index.entries:
            if entry.get("content_id") == content_id:
                old_score = entry.get("relevance_score", 0.5)
                new_score = min(1.0, old_score + self.BOOST_ON_ACCESS)
                entry["relevance_score"] = new_score
                entry["last_accessed"] = datetime.now(timezone.utc).isoformat()
                entry["access_count"] = entry.get("access_count", 0) + 1
                updated = True
                break

        if updated:
            self.memory_manager.save_topic_index(index)

    def _should_decay(self, entry: Dict) -> bool:
        """Check if entry should have decay applied."""
        # Don't decay archived entries
        if entry.get("status") == "archived":
            return False

        # Check last decay time
        last_decay = entry.get("decayed_at")
        if last_decay:
            try:
                last_decay_dt = datetime.fromisoformat(last_decay.replace('Z', '+00:00'))
                days_since = (datetime.now(timezone.utc) - last_decay_dt).days
                return days_since >= self.DECAY_PERIOD_DAYS
            except (ValueError, TypeError):
                pass

        # Check creation time for new entries
        created = entry.get("created_at")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                days_since = (datetime.now(timezone.utc) - created_dt).days
                return days_since >= self.DECAY_PERIOD_DAYS
            except (ValueError, TypeError):
                pass

        return True



# ============================================================================
# SESSION END COMMANDS
# ============================================================================

SESSION_END_PATTERNS = [
    r"^/end\s*session\s*$",
    r"^/end\s*$",
    r"^end\s+session\s*$",
    r"^goodbye\s*$",
    r"^bye\s*$",
    r"^exit\s*$",
    r"^quit\s*$",
]


def is_session_end_command(message: str) -> bool:
    """
    Check if message is a session end command.

    Args:
        message: User message to check

    Returns:
        True if message indicates session should end
    """
    message_lower = message.lower().strip()

    for pattern in SESSION_END_PATTERNS:
        if re.match(pattern, message_lower, re.IGNORECASE):
            return True

    return False


# ============================================================================
# MEMORY QUERY COMMANDS
# ============================================================================

MEMORY_QUERY_PATTERNS = [
    (r"what do you (?:remember|know) about me", "list_all"),
    (r"show (?:me )?(?:my )?(?:stored )?memories", "list_all"),
    (r"list (?:my )?memories", "list_all"),
    (r"forget (?:that )?(.+)", "forget"),
    (r"delete memory[:\s]?(.+)", "forget"),
    (r"clear all (?:my )?memories", "clear_all"),
]


def parse_memory_command(message: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Parse memory-related commands from user message.

    Args:
        message: User message to parse

    Returns:
        Tuple of (command, argument) or None if no command found
        Commands: "list_all", "forget", "clear_all"
    """
    message_lower = message.lower().strip()

    for pattern, command in MEMORY_QUERY_PATTERNS:
        match = re.search(pattern, message_lower, re.IGNORECASE)
        if match:
            arg = match.group(1) if match.lastindex else None
            return (command, arg)

    return None


# ============================================================================
# TURN SCANNER - Per-Turn Fact Extraction
# ============================================================================

@dataclass
class TurnScanResult:
    """Result of scanning a turn for facts."""
    facts_found: int = 0
    facts_stored: int = 0
    topics_updated: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "facts_found": self.facts_found,
            "facts_stored": self.facts_stored,
            "topics_updated": self.topics_updated,
        }


# Patterns for extracting explicit facts
FACT_PATTERNS = [
    # Identity statements
    (r"(?:my name is|i am|i'm called)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", "user_info", 0.9),
    (r"(?:i work (?:at|for)|i'm (?:at|with))\s+(.+?)(?:\.|,|$)", "work", 0.8),
    (r"(?:i live in|i'm from|i'm based in)\s+(.+?)(?:\.|,|$)", "user_info", 0.7),

    # Preferences
    (r"(?:i prefer|i like|i love|my favorite)\s+(.+?)(?:\.|,|$)", "preferences", 0.6),
    (r"(?:i don't like|i hate|i dislike)\s+(.+?)(?:\.|,|$)", "preferences", 0.6),

    # Technical context
    (r"(?:i'm using|i use|we use|our stack is)\s+(.+?)(?:\.|,|$)", "technical", 0.7),
    (r"(?:the project is called|project name is|working on)\s+(.+?)(?:\.|,|$)", "projects", 0.8),
]


class TurnScanner:
    """
    Scans individual turns for facts worth remembering.

    Runs after each turn to extract explicit factual statements
    without requiring explicit "remember" directives.
    """

    MIN_IMPORTANCE = 0.5  # Minimum importance to store
    MAX_FACTS_PER_TURN = 3  # Limit extraction per turn

    def __init__(self, memory_manager):
        """
        Initialize turn scanner.

        Args:
            memory_manager: MemoryManager instance
        """
        self.memory_manager = memory_manager
        self.seen_facts = set()  # Track to avoid duplicates within session

    def scan_turn(
        self,
        user_message: str,
        assistant_response: str = ""
    ) -> TurnScanResult:
        """
        Scan a turn for extractable facts.

        Args:
            user_message: The user's message
            assistant_response: The assistant's response (optional)

        Returns:
            TurnScanResult with extraction statistics
        """
        if not self.memory_manager:
            return TurnScanResult()

        facts = []

        # Extract from user message
        facts.extend(self._extract_facts(user_message))

        # Limit and filter
        facts = self._filter_facts(facts)
        facts = facts[:self.MAX_FACTS_PER_TURN]

        # Store facts
        stored = 0
        topics = set()

        for fact in facts:
            content = fact["content"]

            # Skip if we've seen this fact
            fact_hash = hash(content.lower().strip())
            if fact_hash in self.seen_facts:
                continue
            self.seen_facts.add(fact_hash)

            # Skip sensitive data
            is_sensitive, _ = contains_sensitive_data(content)
            if is_sensitive:
                continue

            # Store the fact
            topic = fact["topic"]
            self.memory_manager._ensure_topic(topic)

            self.memory_manager.add_memory(
                topic_id=topic,
                title=content[:50] + "..." if len(content) > 50 else content,
                content=content,
                keywords=self._extract_keywords(content),
                metadata={
                    "source": "turn_scanner",
                    "importance": fact["importance"],
                    "extracted_at": datetime.now(timezone.utc).isoformat()
                }
            )
            stored += 1
            topics.add(topic)

        return TurnScanResult(
            facts_found=len(facts),
            facts_stored=stored,
            topics_updated=list(topics)
        )

    def _extract_facts(self, text: str) -> List[Dict]:
        """Extract facts from text using patterns."""
        facts = []

        for pattern, topic, importance in FACT_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                content = match.group(1).strip() if match.lastindex else match.group(0).strip()
                if len(content) > 5:  # Skip very short matches
                    facts.append({
                        "content": content,
                        "topic": topic,
                        "importance": importance,
                        "pattern": pattern[:30]
                    })

        return facts

    def _filter_facts(self, facts: List[Dict]) -> List[Dict]:
        """Filter facts by importance and quality."""
        # Sort by importance
        facts = sorted(facts, key=lambda x: x["importance"], reverse=True)

        # Filter by minimum importance
        facts = [f for f in facts if f["importance"] >= self.MIN_IMPORTANCE]

        # Remove very short content
        facts = [f for f in facts if len(f["content"]) >= 10]

        return facts

    def _extract_keywords(self, content: str) -> List[str]:
        """Extract keywords from content."""
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'i', 'my', 'to', 'of', 'in', 'and', 'or'}
        words = re.findall(r'\b[a-zA-Z]{3,}\b', content.lower())
        return [w for w in words if w not in stop_words][:5]

    def reset_session(self):
        """Reset seen facts for a new session."""
        self.seen_facts.clear()


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Data structures
    'DirectiveResult',
    'SessionReviewResult',
    'ConsolidationResult',
    'DecayResult',
    'TurnScanResult',

    # Handlers
    'UserDirectiveHandler',
    'SessionEndReviewer',
    'MemoryConsolidator',
    'MemoryDecay',
    'TurnScanner',

    # Utilities
    'contains_sensitive_data',
    'contains_health_data',
    'is_session_end_command',
    'parse_memory_command',
]
