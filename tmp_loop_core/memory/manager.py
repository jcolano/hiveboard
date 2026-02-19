"""
MEMORY_MANAGER
==============

Memory management for the Agentic Loop Framework.

Two distinct systems managed here:

1. Sessions (Conversation Persistence)
---------------------------------------
Sessions store the full conversation history for an agent's work. When an
agent runs, it loads the session to get context of prior messages, and appends
new messages after each turn.

Storage: ``data/AGENTS/{agent_id}/sessions/session_{session_id}.json``

Session JSON contains:
- ``session_id``, ``agent_id``, ``created_at``, ``updated_at``
- ``status``: "active", "paused", or "completed"
- ``conversation``: Array of all messages (user + assistant + tool results)
- ``summary``: Optional session summary
- ``token_count``: Cumulative tokens used
- ``metadata``: Optional key-value pairs

Typical size: 3-6 KB per session (varies with conversation length).

Dependency analysis (what breaks if sessions are deleted):
- **Active sessions: CRITICAL.** Deleting an active session breaks the agent's
  ongoing conversation — it loses all prior context.
- **Completed sessions: Safe to delete.** No code references them after completion.
- **Stale sessions:** Sessions not updated for days are likely abandoned. Could
  be auto-marked as completed, then purged.

Cleanup status: **Auto-cleanup on status change.** When a session transitions to
completed or paused, old completed/paused sessions beyond
``MAX_COMPLETED_SESSIONS`` (20) are deleted, oldest first. Active sessions are
never touched. Per-session size limit: DEFAULT_MAX_SESSION_SIZE_MB = 10 MB.

2. Long-Term Memory (Topic-Based)
----------------------------------
Persistent knowledge stored across topics with index-based search.
Per-agent isolation with optional shared global facts.

Memory Structure (Per-Agent Isolation)::

    data/AGENTS/{agent_id}/memory/
    ├── sessions/
    │   └── session_{id}.json      # Conversation histories
    ├── topics.json                # Topic registry
    ├── index_{topic}.json         # Search indices
    ├── learning_store.json        # Captured patterns (from LearningManager)
    └── {topic}/
        └── content_{id}.json      # Topic content entries

Limits:
- DEFAULT_MAX_MEMORY_MB = 100 per agent
- DEFAULT_MAX_SESSION_SIZE_MB = 10 per session
"""

import json
import re
import uuid
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Session:
    """
    Session data structure — persistent conversation context for an agent.

    Lifecycle:
    1. Created when agent.run() is called with a session_id (or auto-generated).
    2. Loaded at the start of each execution to provide conversation history.
    3. Updated (appended to) after each turn — auto-saved by agent.py.
    4. Status progresses: active → paused/completed.
    5. Never auto-deleted — accumulates indefinitely.

    The conversation array is the critical piece: it's the agent's memory of
    what was said and done. Deleting an active session means the agent loses
    all context for that conversation thread.
    """
    session_id: str
    agent_id: str
    created_at: str
    updated_at: str
    status: str = "active"  # active, paused, completed
    metadata: Dict[str, Any] = field(default_factory=dict)
    conversation: List[Dict[str, Any]] = field(default_factory=list)
    summary: Optional[str] = None
    token_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "metadata": self.metadata,
            "conversation": self.conversation,
            "summary": self.summary,
            "token_count": self.token_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            agent_id=data.get("agent_id", "unknown"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            status=data.get("status", "active"),
            metadata=data.get("metadata", {}),
            conversation=data.get("conversation", []),
            summary=data.get("summary"),
            token_count=data.get("token_count", 0)
        )


@dataclass
class MemoryEntry:
    """A single memory content entry."""
    content_id: str
    topic_id: str
    title: str
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    sections: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content_id": self.content_id,
            "topic_id": self.topic_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "sections": self.sections
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryEntry':
        """Create from dictionary."""
        return cls(
            content_id=data["content_id"],
            topic_id=data.get("topic_id", "general"),
            title=data.get("title", "Untitled"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            metadata=data.get("metadata", {}),
            sections=data.get("sections", [])
        )

    def get_full_content(self) -> str:
        """Get all sections concatenated."""
        return "\n\n".join(
            f"## {s.get('title', 'Section')}\n{s.get('content', '')}"
            for s in self.sections
        )


@dataclass
class TopicIndex:
    """Index for a memory topic."""
    topic_id: str
    version: str = "1.0.0"
    last_updated: str = ""
    entries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "topic_id": self.topic_id,
            "version": self.version,
            "last_updated": self.last_updated,
            "entries": self.entries
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TopicIndex':
        """Create from dictionary."""
        return cls(
            topic_id=data.get("topic_id", "general"),
            version=data.get("version", "1.0.0"),
            last_updated=data.get("last_updated", ""),
            entries=data.get("entries", [])
        )


# ============================================================================
# MEMORY MANAGER
# ============================================================================

class MemoryManager:
    """Manage sessions and long-term memory with per-agent isolation."""

    # Default limits
    DEFAULT_MAX_MEMORY_MB = 100  # Max memory per agent in MB
    DEFAULT_MAX_ENTRIES_PER_TOPIC = 1000
    DEFAULT_MAX_SESSION_SIZE_MB = 10
    MAX_COMPLETED_SESSIONS = 20  # Keep last N completed/paused sessions

    def __init__(
        self,
        memory_dir: str = None,
        agent_id: str = None,
        agent_dir: Path = None,
        max_memory_mb: float = None,
        max_entries_per_topic: int = None
    ):
        """
        Initialize memory manager.

        New per-agent structure (preferred):
            agent_dir: Path to the agent's directory (e.g., data/AGENTS/main/)
                       Memory stored in: agent_dir/memory/
                       Sessions stored in: agent_dir/sessions/

        Legacy support:
            memory_dir: Path to the MEMORY directory
            agent_id: Optional agent ID for per-agent isolation.
                     If provided, memory is stored in agents/{agent_id}/

        Args:
            memory_dir: Legacy path to MEMORY directory
            agent_id: Agent ID (used for both new and legacy modes)
            agent_dir: New per-agent directory path (takes precedence over memory_dir)
            max_memory_mb: Maximum memory storage in MB (default 100MB)
            max_entries_per_topic: Maximum entries per topic (default 1000)
        """
        self.agent_id = agent_id

        # New per-agent directory structure takes precedence
        if agent_dir is not None:
            self.agent_dir = Path(agent_dir)
            self.memory_dir = self.agent_dir / "memory"
            self.sessions_dir = self.agent_dir / "sessions"
            self.base_memory_dir = self.agent_dir.parent  # For shared facts
        elif memory_dir is not None:
            # Legacy mode
            self.base_memory_dir = Path(memory_dir)
            if agent_id:
                self.memory_dir = self.base_memory_dir / "agents" / agent_id
            else:
                self.memory_dir = self.base_memory_dir
            self.sessions_dir = self.memory_dir / "sessions"
            self.agent_dir = None
        else:
            raise ValueError("Either agent_dir or memory_dir must be provided")

        # Create directories
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Memory limits
        self.max_memory_mb = max_memory_mb or self.DEFAULT_MAX_MEMORY_MB
        self.max_entries_per_topic = max_entries_per_topic or self.DEFAULT_MAX_ENTRIES_PER_TOPIC
        self.max_session_size_mb = self.DEFAULT_MAX_SESSION_SIZE_MB

        self._topics: Dict[str, Any] = {}
        self._topic_indexes: Dict[str, TopicIndex] = {}

        # Store base path for external access
        self.base_path = self.memory_dir

    # ========================================================================
    # MEMORY SIZE TRACKING
    # ========================================================================

    def get_memory_size_bytes(self) -> int:
        """
        Calculate total memory usage in bytes for this agent.

        Returns:
            Total bytes used by memory files
        """
        total = 0
        if self.memory_dir.exists():
            for path in self.memory_dir.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return total

    def get_memory_size_mb(self) -> float:
        """Get total memory usage in MB."""
        return self.get_memory_size_bytes() / (1024 * 1024)

    def is_memory_limit_exceeded(self) -> bool:
        """Check if memory limit is exceeded."""
        return self.get_memory_size_mb() > self.max_memory_mb

    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory usage statistics.

        Returns:
            Dict with size, limits, and usage percentage
        """
        size_mb = self.get_memory_size_mb()
        return {
            "agent_id": self.agent_id,
            "size_mb": round(size_mb, 2),
            "max_mb": self.max_memory_mb,
            "usage_percent": round((size_mb / self.max_memory_mb) * 100, 1),
            "limit_exceeded": size_mb > self.max_memory_mb,
            "topics_count": len(self.list_topics()),
            "sessions_count": len(list(self.sessions_dir.glob("session_*.json")))
        }

    def _check_memory_limit(self) -> None:
        """
        Check memory limit before write operations.

        Raises:
            MemoryError: If memory limit is exceeded
        """
        if self.is_memory_limit_exceeded():
            stats = self.get_memory_stats()
            raise MemoryError(
                f"Memory limit exceeded for agent '{self.agent_id}': "
                f"{stats['size_mb']}MB / {stats['max_mb']}MB"
            )

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    def create_session(self, agent_id: str, session_id: str = None,
                       metadata: Dict[str, Any] = None) -> Session:
        """
        Create a new session.

        Args:
            agent_id: ID of the agent owning the session
            session_id: Optional session ID (auto-generated if not provided)
            metadata: Optional metadata for the session

        Returns:
            Created Session object
        """
        if session_id is None:
            session_id = str(uuid.uuid4())[:8]

        now = datetime.now().isoformat()
        session = Session(
            session_id=session_id,
            agent_id=agent_id,
            created_at=now,
            updated_at=now,
            status="active",
            metadata=metadata or {},
            conversation=[],
            summary=None,
            token_count=0
        )

        self.save_session(session)
        return session

    def load_session(self, session_id: str) -> Optional[Session]:
        """
        Load a session by ID.

        Args:
            session_id: The session ID to load

        Returns:
            Session object or None if not found
        """
        session_path = self.sessions_dir / f"session_{session_id}.json"
        if not session_path.exists():
            return None

        try:
            data = json.loads(session_path.read_text(encoding='utf-8'))
            return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load session {session_id}: {e}")
            return None

    def save_session(self, session: Session) -> None:
        """
        Save a session to disk.

        Args:
            session: The Session object to save

        Raises:
            MemoryError: If memory limit exceeded
        """
        self._check_memory_limit()

        session.updated_at = datetime.now().isoformat()
        session_path = self.sessions_dir / f"session_{session.session_id}.json"

        # Check session size limit
        session_data = json.dumps(session.to_dict(), indent=2)
        session_size_mb = len(session_data.encode('utf-8')) / (1024 * 1024)
        if session_size_mb > self.max_session_size_mb:
            raise MemoryError(
                f"Session {session.session_id} exceeds size limit: "
                f"{session_size_mb:.2f}MB / {self.max_session_size_mb}MB"
            )

        session_path.write_text(session_data, encoding='utf-8')

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: The session ID to delete

        Returns:
            True if deleted, False if not found
        """
        session_path = self.sessions_dir / f"session_{session_id}.json"
        if session_path.exists():
            session_path.unlink()
            return True
        return False

    def append_to_session(self, session_id: str, messages: List[Dict[str, Any]]) -> bool:
        """
        Append messages to a session's conversation.

        Args:
            session_id: The session ID
            messages: List of message dicts to append

        Returns:
            True if successful, False if session not found
        """
        session = self.load_session(session_id)
        if session is None:
            return False

        session.conversation.extend(messages)
        self.save_session(session)
        return True

    def update_session_status(self, session_id: str, status: str) -> bool:
        """
        Update a session's status.

        When a session transitions to completed or paused, old completed/paused
        sessions beyond MAX_COMPLETED_SESSIONS are automatically deleted.

        Args:
            session_id: The session ID
            status: New status (active, paused, completed)

        Returns:
            True if successful, False if session not found
        """
        session = self.load_session(session_id)
        if session is None:
            return False

        session.status = status
        self.save_session(session)

        if status in ("completed", "paused"):
            self._cleanup_old_sessions()

        return True

    def _cleanup_old_sessions(self) -> int:
        """Delete oldest completed/paused sessions beyond MAX_COMPLETED_SESSIONS.

        Active sessions are never touched. Among completed and paused sessions,
        keeps the most recently updated ones and deletes the rest.

        Returns:
            Number of sessions deleted.
        """
        # Load all sessions with their updated_at and status
        inactive = []
        for path in self.sessions_dir.glob("session_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                status = data.get("status", "active")
                if status in ("completed", "paused"):
                    inactive.append((data.get("updated_at", ""), path))
            except (json.JSONDecodeError, KeyError, OSError):
                continue

        if len(inactive) <= self.MAX_COMPLETED_SESSIONS:
            return 0

        # Sort newest first by updated_at
        inactive.sort(key=lambda x: x[0], reverse=True)

        to_delete = inactive[self.MAX_COMPLETED_SESSIONS:]
        deleted = 0
        for _, path in to_delete:
            try:
                path.unlink()
                deleted += 1
            except OSError as e:
                logger.warning("Failed to delete old session %s: %s", path.name, e)

        if deleted:
            logger.info(
                "Cleaned up %d old sessions (kept %d inactive)",
                deleted, self.MAX_COMPLETED_SESSIONS,
            )

        return deleted

    def list_sessions(self, agent_id: str = None, status: str = None) -> List[Dict[str, Any]]:
        """
        List sessions with optional filtering.

        Args:
            agent_id: Filter by agent ID
            status: Filter by status

        Returns:
            List of session summary dicts
        """
        sessions = []
        for path in self.sessions_dir.glob("session_*.json"):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))

                # Apply filters
                if agent_id and data.get("agent_id") != agent_id:
                    continue
                if status and data.get("status") != status:
                    continue

                sessions.append({
                    "session_id": data["session_id"],
                    "agent_id": data.get("agent_id"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "status": data.get("status"),
                    "metadata": data.get("metadata", {}),
                    "message_count": len(data.get("conversation", []))
                })
            except (json.JSONDecodeError, KeyError):
                continue

        # Sort by updated_at descending
        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    def get_session_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get the conversation history for a session.

        Args:
            session_id: The session ID

        Returns:
            List of message dicts
        """
        session = self.load_session(session_id)
        if session is None:
            return []
        return session.conversation

    # ========================================================================
    # LONG-TERM MEMORY - TOPICS
    # ========================================================================

    def load_topics(self) -> Dict[str, Any]:
        """Load the topics index."""
        topics_path = self.memory_dir / "topics.json"
        if topics_path.exists():
            try:
                self._topics = json.loads(topics_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                self._topics = {"version": "1.0.0", "topics": []}
        else:
            self._topics = {"version": "1.0.0", "topics": []}
        return self._topics

    def save_topics(self) -> None:
        """Save the topics index."""
        self._topics["last_updated"] = datetime.now().isoformat()
        topics_path = self.memory_dir / "topics.json"
        topics_path.write_text(
            json.dumps(self._topics, indent=2),
            encoding='utf-8'
        )

    def _ensure_topic(self, topic_id: str, name: str = None,
                      description: str = None) -> None:
        """Ensure a topic exists in the topics index."""
        self.load_topics()
        existing_ids = [t["id"] for t in self._topics.get("topics", [])]

        if topic_id not in existing_ids:
            self._topics["topics"].append({
                "id": topic_id,
                "name": name or topic_id.replace("_", " ").title(),
                "description": description or f"Memory entries for {topic_id}",
                "index_file": f"index_{topic_id}.json",
                "content_dir": f"{topic_id}/",
                "entry_count": 0
            })
            self.save_topics()

            # Create topic directory
            topic_dir = self.memory_dir / topic_id
            topic_dir.mkdir(parents=True, exist_ok=True)

    def list_topics(self) -> List[Dict[str, Any]]:
        """List all memory topics."""
        self.load_topics()
        return self._topics.get("topics", [])

    def get_topic(self, topic_id: str) -> Optional[Dict[str, Any]]:
        """Get a topic by ID."""
        self.load_topics()
        for topic in self._topics.get("topics", []):
            if topic["id"] == topic_id:
                return topic
        return None

    # ========================================================================
    # LONG-TERM MEMORY - INDEXES
    # ========================================================================

    def get_topic_index(self, topic_id: str) -> Optional[TopicIndex]:
        """
        Load a topic's index.

        Args:
            topic_id: The topic ID

        Returns:
            TopicIndex object or None
        """
        if topic_id in self._topic_indexes:
            return self._topic_indexes[topic_id]

        index_path = self.memory_dir / f"index_{topic_id}.json"
        if not index_path.exists():
            return None

        try:
            data = json.loads(index_path.read_text(encoding='utf-8'))
            index = TopicIndex.from_dict(data)
            self._topic_indexes[topic_id] = index
            return index
        except (json.JSONDecodeError, KeyError):
            return None

    def save_topic_index(self, index: TopicIndex) -> None:
        """Save a topic's index."""
        index.last_updated = datetime.now().isoformat()
        index_path = self.memory_dir / f"index_{index.topic_id}.json"
        index_path.write_text(
            json.dumps(index.to_dict(), indent=2),
            encoding='utf-8'
        )
        self._topic_indexes[index.topic_id] = index

    def _add_to_index(self, topic_id: str, content_file: str,
                      section_id: str, summary: str,
                      keywords: List[str]) -> None:
        """Add an entry to a topic's index."""
        index = self.get_topic_index(topic_id)
        if index is None:
            index = TopicIndex(topic_id=topic_id)

        index_id = f"idx_{len(index.entries) + 1:03d}"
        index.entries.append({
            "index_id": index_id,
            "content_file": content_file,
            "section_id": section_id,
            "summary": summary,
            "keywords": keywords,
            "created_at": datetime.now().isoformat(),
            "relevance_score": 1.0
        })

        self.save_topic_index(index)

        # Update topic entry count
        self.load_topics()
        for topic in self._topics.get("topics", []):
            if topic["id"] == topic_id:
                topic["entry_count"] = len(index.entries)
                break
        self.save_topics()

    # ========================================================================
    # LONG-TERM MEMORY - CONTENT
    # ========================================================================

    def add_memory(self, topic_id: str, title: str, content: str,
                   keywords: List[str] = None,
                   metadata: Dict[str, Any] = None) -> str:
        """
        Add new content to memory with automatic indexing.

        Args:
            topic_id: Topic to add content to
            title: Title for the content
            content: The actual content text
            keywords: Optional keywords for indexing
            metadata: Optional metadata

        Returns:
            Content ID of the created entry

        Raises:
            MemoryError: If memory limit exceeded or topic entry limit reached
        """
        # Check memory limit
        self._check_memory_limit()

        # Check topic entry limit
        index = self.get_topic_index(topic_id)
        if index and len(index.entries) >= self.max_entries_per_topic:
            raise MemoryError(
                f"Topic '{topic_id}' has reached entry limit: "
                f"{len(index.entries)} / {self.max_entries_per_topic}"
            )

        # Ensure topic exists
        self._ensure_topic(topic_id)

        # Create content file
        content_id = f"content_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        content_dir = self.memory_dir / topic_id
        content_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now().isoformat()
        content_data = MemoryEntry(
            content_id=content_id,
            topic_id=topic_id,
            title=title,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            sections=[{
                "section_id": "main",
                "title": title,
                "content": content,
                "updated_at": now
            }]
        )

        content_path = content_dir / f"{content_id}.json"
        content_path.write_text(
            json.dumps(content_data.to_dict(), indent=2),
            encoding='utf-8'
        )

        # Extract keywords if not provided
        if keywords is None:
            keywords = self._extract_keywords(content)

        # Add to index
        self._add_to_index(
            topic_id=topic_id,
            content_file=f"{content_id}.json",
            section_id="main",
            summary=title,
            keywords=keywords
        )

        return content_id

    def get_memory(self, topic_id: str, content_id: str) -> Optional[MemoryEntry]:
        """
        Get a memory entry by topic and content ID.

        Args:
            topic_id: The topic ID
            content_id: The content ID

        Returns:
            MemoryEntry object or None
        """
        content_path = self.memory_dir / topic_id / f"{content_id}.json"
        if not content_path.exists():
            return None

        try:
            data = json.loads(content_path.read_text(encoding='utf-8'))
            return MemoryEntry.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_content(self, topic_id: str, content_id: str,
                    section_id: str = None) -> Optional[str]:
        """
        Get content text from a memory entry.

        Args:
            topic_id: The topic ID
            content_id: The content ID
            section_id: Optional specific section ID

        Returns:
            Content text or None
        """
        entry = self.get_memory(topic_id, content_id)
        if entry is None:
            return None

        if section_id:
            for section in entry.sections:
                if section.get("section_id") == section_id:
                    return section.get("content")
            return None
        else:
            return entry.get_full_content()

    def update_memory(self, topic_id: str, content_id: str,
                      content: str = None, title: str = None,
                      section_id: str = "main") -> bool:
        """
        Update an existing memory entry.

        Args:
            topic_id: The topic ID
            content_id: The content ID
            content: New content text (optional)
            title: New title (optional)
            section_id: Section to update

        Returns:
            True if updated, False if not found
        """
        entry = self.get_memory(topic_id, content_id)
        if entry is None:
            return False

        now = datetime.now().isoformat()
        entry.updated_at = now

        if title:
            entry.title = title

        if content:
            # Find and update section
            found = False
            for section in entry.sections:
                if section.get("section_id") == section_id:
                    section["content"] = content
                    section["updated_at"] = now
                    found = True
                    break

            if not found:
                # Add new section
                entry.sections.append({
                    "section_id": section_id,
                    "title": section_id.replace("_", " ").title(),
                    "content": content,
                    "updated_at": now
                })

        # Save
        content_path = self.memory_dir / topic_id / f"{content_id}.json"
        content_path.write_text(
            json.dumps(entry.to_dict(), indent=2),
            encoding='utf-8'
        )
        return True

    def delete_memory(self, topic_id: str, content_id: str) -> bool:
        """
        Delete a memory entry.

        Args:
            topic_id: The topic ID
            content_id: The content ID

        Returns:
            True if deleted, False if not found
        """
        content_path = self.memory_dir / topic_id / f"{content_id}.json"
        if not content_path.exists():
            return False

        content_path.unlink()

        # Remove from index
        index = self.get_topic_index(topic_id)
        if index:
            index.entries = [
                e for e in index.entries
                if e.get("content_file") != f"{content_id}.json"
            ]
            self.save_topic_index(index)

        return True

    # ========================================================================
    # SEARCH
    # ========================================================================

    def search_memory(self, query: str, topic_id: str = None,
                      limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search memory indexes for relevant entries.

        Args:
            query: Search query
            topic_id: Optional topic to search within
            limit: Maximum results to return

        Returns:
            List of search results with scores
        """
        results = []
        query_words = set(query.lower().split())

        topics = self.load_topics()
        search_topics = [
            t for t in topics.get("topics", [])
            if topic_id is None or t["id"] == topic_id
        ]

        for topic in search_topics:
            index = self.get_topic_index(topic["id"])
            if not index:
                continue

            for entry in index.entries:
                # Simple keyword matching
                entry_keywords = set(k.lower() for k in entry.get("keywords", []))
                entry_summary = entry.get("summary", "").lower()

                # Score based on keyword overlap and summary match
                keyword_overlap = len(query_words & entry_keywords)
                summary_matches = sum(1 for w in query_words if w in entry_summary)
                score = keyword_overlap * 2 + summary_matches

                if score > 0:
                    results.append({
                        "score": score,
                        "topic_id": topic["id"],
                        "content_file": entry.get("content_file"),
                        "section_id": entry.get("section_id"),
                        "summary": entry.get("summary"),
                        "keywords": entry.get("keywords", []),
                        "entry": entry
                    })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _extract_keywords(self, content: str, max_keywords: int = 10) -> List[str]:
        """
        Extract keywords from content.

        Args:
            content: Text content
            max_keywords: Maximum keywords to extract

        Returns:
            List of keywords
        """
        # Common stop words to filter
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'can', 'to', 'of',
            'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
            'through', 'during', 'before', 'after', 'above', 'below',
            'between', 'under', 'again', 'further', 'then', 'once', 'and',
            'but', 'or', 'nor', 'so', 'yet', 'both', 'each', 'few', 'more',
            'most', 'other', 'some', 'such', 'no', 'not', 'only', 'own',
            'same', 'than', 'too', 'very', 'just', 'also', 'now', 'this',
            'that', 'these', 'those', 'it', 'its', 'if', 'when', 'where',
            'what', 'which', 'who', 'how', 'why', 'all', 'any', 'about'
        }

        # Extract words (3+ characters)
        words = re.findall(r'\b[a-zA-Z]{3,}\b', content.lower())

        # Count word frequencies
        word_counts: Dict[str, int] = {}
        for word in words:
            if word not in stop_words:
                word_counts[word] = word_counts.get(word, 0) + 1

        # Sort by frequency and return top keywords
        sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        return [word for word, count in sorted_words[:max_keywords]]

    # ========================================================================
    # PROMPT BUILDING
    # ========================================================================

    def build_memory_prompt(self, query: str, topic_id: str = None,
                            max_entries: int = 5) -> str:
        """
        Build a memory context prompt based on query relevance.

        Args:
            query: User query to match against
            topic_id: Optional topic to search within
            max_entries: Maximum entries to include

        Returns:
            Formatted memory prompt string
        """
        results = self.search_memory(query, topic_id, limit=max_entries)

        if not results:
            return ""

        lines = ["## Relevant Memory", ""]
        lines.append("The following information from memory may be relevant:")
        lines.append("")

        for result in results:
            topic = result["topic_id"]
            summary = result["summary"]
            keywords = result.get("keywords", [])
            content_file = result.get("content_file", "")
            section_id = result.get("section_id", "main")

            lines.append(f"**[{topic}]** {summary}")
            if keywords:
                lines.append(f"  - Keywords: {', '.join(keywords[:5])}")
            lines.append(f"  - Source: {topic}/{content_file}#{section_id}")
            lines.append("")

        lines.append("Use file_read to access full content if needed.")

        return "\n".join(lines)

    def build_session_summary_prompt(self, session_id: str,
                                     max_messages: int = 10) -> str:
        """
        Build a prompt summarizing recent session conversation.

        Args:
            session_id: Session to summarize
            max_messages: Maximum recent messages to include

        Returns:
            Formatted session summary prompt
        """
        session = self.load_session(session_id)
        if session is None:
            return ""

        conversation = session.conversation[-max_messages:]
        if not conversation:
            return ""

        lines = ["## Session Context", ""]
        lines.append(f"Continuing session `{session_id}` (status: {session.status})")
        lines.append("")

        if session.summary:
            lines.append(f"**Summary:** {session.summary}")
            lines.append("")

        lines.append("**Recent conversation:**")
        for msg in conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate long messages
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"- {role}: {content}")

        return "\n".join(lines)
