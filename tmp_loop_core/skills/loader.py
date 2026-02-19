"""
SKILL_LOADER
============

Skill loading and management for the Agentic Loop Framework.

Skills are markdown-based behavior definitions (NOT executable code) that teach
agents how to perform specific tasks. Each skill lives in its own directory:

    data/SKILLS/{skill_id}/
    ├── skill.json    # Metadata: name, description, triggers, requires, files, enabled
    ├── skill.md      # Main instruction content (the agent reads this at runtime)
    └── *.md          # Auxiliary reference files (e.g. heartbeat.md)

Two-Level Skill Hierarchy
-------------------------
1. **Global skills** — ``data/SKILLS/{skill_id}/``
   Shared across all agents. Registered in ``data/SKILLS/registry.json``.

2. **Private skills** — ``data/AGENTS/{agent_id}/skills/{skill_id}/``
   Agent-specific. Can override global skills with the same ID.
   Supports nested vendor structure: ``data/AGENTS/{id}/skills/vendor/skill_id/``

Loading Process
---------------
- ``load_all()`` first loads global skills from registry.json, then scans the
  directory for any unregistered skills.
- Then loads agent-private skills, which can override global skills with the same ID.
- Each agent gets its own SkillLoader instance (cached in AgentManager).

Context Injection (how skills reach the LLM)
---------------------------------------------
When a skill is activated, ``build_skill_content_prompt()`` embeds the full
skill content directly into the system prompt (used by ``agent.run(skill_id=...)``).
When no skill is specified, no skills are loaded into the system prompt.

Skill Matching
--------------
The companion ``SkillMatcher`` (matcher.py) selects the best skill for a task
using LLM-based matching (primary) or trigger keyword matching (fallback).
See matcher.py for details.

skill.json Structure
--------------------
::

    {
        "id": "loopcolony",
        "name": "loopColony",
        "description": "Team workspace for human-agent collaboration",
        "triggers": ["join loopColony", "check loopColony", ...],
        "requires": {"tools": ["http_request", "file_read", "file_write"]},
        "files": ["skill.md", "heartbeat.md"],
        "enabled": true,
        "metadata": {"category": "collaboration"}
    }

- ``triggers``: Keywords used by SkillMatcher for matching incoming messages.
- ``requires.tools``: Declarative — documents which tools the skill needs.
  Does NOT auto-register tools; they must be in the agent's enabled_tools config.
- ``files``: Lists which .md files the skill uses. Auto-discovered if omitted.
"""

import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

# Optional: requests for URL fetching
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ============================================================================
# EXCEPTIONS
# ============================================================================

class SkillLoadError(Exception):
    """Error loading a skill."""
    def __init__(self, skill_id: str, message: str):
        self.skill_id = skill_id
        super().__init__(f"Failed to load skill '{skill_id}': {message}")


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Skill:
    """Loaded skill data."""
    id: str
    name: str
    description: str
    content: str  # Main skill.md content
    files: Dict[str, str] = field(default_factory=dict)  # Additional files: name -> content
    triggers: List[str] = field(default_factory=list)
    enabled: bool = True
    is_deleted: bool = False  # Soft delete flag
    requires: Dict[str, Any] = field(default_factory=dict)  # Required tools, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "global"  # "global" or "private" - indicates where skill came from

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "files": self.files,
            "triggers": self.triggers,
            "enabled": self.enabled,
            "is_deleted": self.is_deleted,
            "requires": self.requires,
            "metadata": self.metadata,
            "source": self.source
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Skill':
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            content=data.get("content", ""),
            files=data.get("files", {}),
            triggers=data.get("triggers", []),
            enabled=data.get("enabled", True),
            is_deleted=data.get("is_deleted", False),
            requires=data.get("requires", {}),
            metadata=data.get("metadata", {}),
            source=data.get("source", "global")
        )


# ============================================================================
# SKILL LOADER
# ============================================================================

class SkillLoader:
    """Load and manage skills."""

    def __init__(
        self,
        skills_dir: str = None,
        global_skills_dir: Path = None,
        agent_skills_dir: Path = None
    ):
        """
        Initialize skill loader.

        New per-agent structure (preferred):
            global_skills_dir: Path to global SKILLS directory (inherited by all agents)
            agent_skills_dir: Path to agent's private skills directory

        Legacy support:
            skills_dir: Path to the SKILLS directory (treated as global)

        Args:
            skills_dir: Legacy path to SKILLS directory
            global_skills_dir: Path to global skills (data/SKILLS)
            agent_skills_dir: Path to agent-private skills (data/AGENTS/{id}/skills)
        """
        # Handle both new and legacy initialization
        if global_skills_dir is not None:
            self.global_skills_dir = Path(global_skills_dir)
        elif skills_dir is not None:
            self.global_skills_dir = Path(skills_dir)
        else:
            self.global_skills_dir = None

        self.agent_skills_dir = Path(agent_skills_dir) if agent_skills_dir else None

        # For backward compatibility
        self.skills_dir = self.global_skills_dir

        self._skills: Dict[str, Skill] = {}
        self._global_skills: Dict[str, Skill] = {}  # Track global vs private
        self._private_skills: Dict[str, Skill] = {}
        self._deleted_skills: Dict[str, Skill] = {}  # Track soft-deleted skills
        self._registry: Dict[str, Any] = {}

    # ==================== Registry Management ====================

    def load_registry(self) -> Dict[str, Any]:
        """Load the skills registry."""
        registry_path = self.skills_dir / "registry.json"
        if registry_path.exists():
            try:
                self._registry = json.loads(registry_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse registry.json: {e}")
                self._registry = {"version": "1.0.0", "skills": []}
        else:
            self._registry = {"version": "1.0.0", "skills": []}
        return self._registry

    def save_registry(self) -> None:
        """Save the skills registry."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._registry["last_updated"] = datetime.now().isoformat()
        registry_path = self.skills_dir / "registry.json"
        registry_path.write_text(json.dumps(self._registry, indent=2), encoding='utf-8')

    def _add_to_registry(self, skill_id: str) -> None:
        """Add skill to registry if not present."""
        if "skills" not in self._registry:
            self._registry["skills"] = []

        existing_ids = [s["id"] for s in self._registry["skills"]]
        if skill_id not in existing_ids:
            self._registry["skills"].append({
                "id": skill_id,
                "path": f"{skill_id}/",
                "enabled": True
            })
            self.save_registry()

    def _remove_from_registry(self, skill_id: str) -> None:
        """Remove skill from registry."""
        if "skills" in self._registry:
            self._registry["skills"] = [
                s for s in self._registry["skills"] if s["id"] != skill_id
            ]
            self.save_registry()

    # ==================== Skill Loading ====================

    def load_skill(self, skill_id: str, source: str = "global") -> Optional[Skill]:
        """
        Load a single skill by ID.

        Args:
            skill_id: The skill identifier
            source: Where to load from ("global" or "private")

        Returns:
            Loaded Skill object or None if not found
        """
        # Determine which directory to use
        if source == "private" and self.agent_skills_dir:
            base_dir = self.agent_skills_dir
            # Check if we have a cached path for this skill (supports nested vendor folders)
            private_paths = getattr(self, '_private_skill_paths', {})
            if skill_id in private_paths:
                skill_dir = base_dir / private_paths[skill_id]
            else:
                # Try direct path first, then search
                skill_dir = base_dir / skill_id
                if not (skill_dir / "skill.json").exists():
                    # Search for skill in nested directories
                    for skill_json_path in base_dir.glob(f"**/{skill_id}/skill.json"):
                        skill_dir = skill_json_path.parent
                        break
                    else:
                        # Also try matching by skill.json content id
                        for skill_json_path in base_dir.glob("**/skill.json"):
                            try:
                                data = json.loads(skill_json_path.read_text(encoding='utf-8'))
                                if data.get("id") == skill_id:
                                    skill_dir = skill_json_path.parent
                                    break
                            except:
                                pass
        else:
            base_dir = self.global_skills_dir
            if not base_dir:
                return None
            skill_dir = base_dir / skill_id

        skill_json_path = skill_dir / "skill.json"
        skill_md_path = skill_dir / "skill.md"

        if not skill_json_path.exists():
            return None

        try:
            # Load metadata
            metadata = json.loads(skill_json_path.read_text(encoding='utf-8'))

            # Load main skill.md
            content = ""
            if skill_md_path.exists():
                content = skill_md_path.read_text(encoding='utf-8')

            # Load additional files listed in metadata
            files = {}
            for file_entry in metadata.get("files", []):
                # Handle both formats: {"name": "file.md"} or just "file.md"
                if isinstance(file_entry, dict):
                    file_name = file_entry.get("name", "")
                else:
                    file_name = str(file_entry) if file_entry else ""
                if file_name and file_name != "skill.md":
                    file_path = skill_dir / file_name
                    if file_path.exists():
                        files[file_name] = file_path.read_text(encoding='utf-8')

            # Also scan for any .md files not in metadata
            for md_file in skill_dir.glob("*.md"):
                if md_file.name != "skill.md" and md_file.name not in files:
                    files[md_file.name] = md_file.read_text(encoding='utf-8')

            skill = Skill(
                id=skill_id,
                name=metadata.get("name", skill_id),
                description=metadata.get("description", ""),
                content=content,
                files=files,
                triggers=metadata.get("triggers", []),
                enabled=metadata.get("enabled", True),
                is_deleted=metadata.get("is_deleted", False),
                requires=metadata.get("requires", {}),
                metadata=metadata,
                source=source
            )

            # Store in appropriate tracking dict
            if skill.is_deleted:
                self._deleted_skills[skill_id] = skill
            else:
                self._skills[skill_id] = skill
                if source == "global":
                    self._global_skills[skill_id] = skill
                else:
                    self._private_skills[skill_id] = skill

            return skill

        except Exception as e:
            raise SkillLoadError(skill_id, str(e))

    def load_all(self) -> Dict[str, Skill]:
        """
        Load all skills from both global and private directories.

        Global skills are loaded first, then agent-private skills.
        Private skills can override global skills with same ID.
        All loaded skills are available (no enabled_skills filter).

        Returns:
            Dictionary of skill_id -> Skill
        """
        # Clear existing
        self._skills.clear()
        self._global_skills.clear()
        self._private_skills.clear()
        self._deleted_skills.clear()

        # Load global skills first
        if self.global_skills_dir and self.global_skills_dir.exists():
            self.load_registry()  # Load registry for global skills
            for entry in self._registry.get("skills", []):
                if entry.get("enabled", True):
                    try:
                        self.load_skill(entry["id"], source="global")
                    except SkillLoadError as e:
                        print(f"Warning: {e}")

            # Also scan directory for skills not in registry
            for skill_dir in self.global_skills_dir.iterdir():
                if skill_dir.is_dir() and (skill_dir / "skill.json").exists():
                    skill_id = skill_dir.name
                    if skill_id not in self._skills:
                        try:
                            self.load_skill(skill_id, source="global")
                        except SkillLoadError as e:
                            print(f"Warning: {e}")

        # Load agent-private skills (can override global)
        # Supports both flat structure (skills/my_skill/) and nested vendor structure (skills/vendor/my_skill/)
        if self.agent_skills_dir and self.agent_skills_dir.exists():
            # Use glob to find all skill.json files recursively
            for skill_json_path in self.agent_skills_dir.glob("**/skill.json"):
                skill_dir = skill_json_path.parent
                # Get skill_id from skill.json content (more reliable than folder name)
                try:
                    skill_data = json.loads(skill_json_path.read_text(encoding='utf-8'))
                    skill_id = skill_data.get("id", skill_dir.name)
                    # Store the relative path from agent_skills_dir for loading
                    rel_path = skill_dir.relative_to(self.agent_skills_dir)
                    self._private_skill_paths = getattr(self, '_private_skill_paths', {})
                    self._private_skill_paths[skill_id] = rel_path
                    self.load_skill(skill_id, source="private")
                except SkillLoadError as e:
                    print(f"Warning: {e}")
                except Exception as e:
                    print(f"Warning: Failed to load skill from {skill_dir}: {e}")

        return self._skills

    def get_skill(self, skill_id: str, include_deleted: bool = False) -> Optional[Skill]:
        """Get a loaded skill by ID."""
        # Check active skills first
        if skill_id in self._skills:
            return self._skills.get(skill_id)

        # Check deleted skills if requested
        if include_deleted and skill_id in self._deleted_skills:
            return self._deleted_skills.get(skill_id)

        # Try to load from private first (supports nested vendor folders), then global
        if self.agent_skills_dir:
            # load_skill handles nested directory search
            self.load_skill(skill_id, source="private")
        if skill_id not in self._skills and self.global_skills_dir:
            self.load_skill(skill_id, source="global")

        # Check again after loading
        if skill_id in self._skills:
            return self._skills.get(skill_id)
        if include_deleted and skill_id in self._deleted_skills:
            return self._deleted_skills.get(skill_id)

        return None

    def list_skills(self, include_deleted: bool = False) -> List[str]:
        """List all loaded skill IDs."""
        ids = list(self._skills.keys())
        if include_deleted:
            ids.extend(self._deleted_skills.keys())
        return ids

    def list_global_skills(self) -> List[str]:
        """List all global skill IDs."""
        return list(self._global_skills.keys())

    def list_private_skills(self, include_deleted: bool = False) -> List[str]:
        """List all agent-private skill IDs."""
        ids = list(self._private_skills.keys())
        if include_deleted:
            # Add deleted private skills
            for skill_id, skill in self._deleted_skills.items():
                if skill.source == "private":
                    ids.append(skill_id)
        return ids

    def list_deleted_skills(self) -> List[str]:
        """List all soft-deleted skill IDs."""
        return list(self._deleted_skills.keys())

    def get_deleted_skill(self, skill_id: str) -> Optional[Skill]:
        """Get a deleted skill by ID."""
        return self._deleted_skills.get(skill_id)

    def list_all_skill_ids(self) -> List[str]:
        """List all skill IDs from registry (including not loaded)."""
        self.load_registry()
        return [s["id"] for s in self._registry.get("skills", [])]

    def get_skills_by_source(self, source: str = None, include_deleted: bool = False) -> Dict[str, Skill]:
        """
        Get skills filtered by source.

        Args:
            source: "global", "private", or None for all
            include_deleted: Include soft-deleted skills

        Returns:
            Dictionary of skill_id -> Skill
        """
        if source == "global":
            result = self._global_skills.copy()
        elif source == "private":
            result = self._private_skills.copy()
            if include_deleted:
                for skill_id, skill in self._deleted_skills.items():
                    if skill.source == "private":
                        result[skill_id] = skill
        else:
            result = self._skills.copy()
            if include_deleted:
                result.update(self._deleted_skills)
        return result

    # ==================== URL Fetching ====================

    def fetch_from_url(self, skill_id: str, url: str,
                       additional_files: List[Dict[str, str]] = None) -> Optional[Skill]:
        """
        Fetch a skill from a URL and save locally.

        Args:
            skill_id: ID to assign to the skill
            url: URL to fetch skill.md from
            additional_files: Optional list of {"name": "file.md", "url": "..."}

        Returns:
            Loaded Skill object or None on failure
        """
        if not REQUESTS_AVAILABLE:
            print("Error: requests package not available for URL fetching")
            return None

        try:
            # Create skill directory
            skill_dir = self.skills_dir / skill_id
            skill_dir.mkdir(parents=True, exist_ok=True)

            # Fetch main skill.md
            response = requests.get(url, timeout=30, headers={
                "User-Agent": "AgenticLoop/1.0"
            })
            response.raise_for_status()
            content = response.text

            # Save skill.md
            (skill_dir / "skill.md").write_text(content, encoding='utf-8')

            # Fetch additional files if provided
            files_metadata = [{"name": "skill.md", "url": url}]
            if additional_files:
                for file_info in additional_files:
                    file_name = file_info.get("name")
                    file_url = file_info.get("url")
                    if file_name and file_url:
                        try:
                            file_response = requests.get(file_url, timeout=30, headers={
                                "User-Agent": "AgenticLoop/1.0"
                            })
                            file_response.raise_for_status()
                            (skill_dir / file_name).write_text(
                                file_response.text, encoding='utf-8'
                            )
                            files_metadata.append({"name": file_name, "url": file_url})
                        except Exception as e:
                            print(f"Warning: Failed to fetch {file_name}: {e}")

            # Create skill.json metadata
            metadata = {
                "id": skill_id,
                "name": skill_id.replace("_", " ").replace("-", " ").title(),
                "description": f"Skill fetched from {url}",
                "source": {
                    "type": "url",
                    "url": url,
                    "last_fetched": datetime.now().isoformat()
                },
                "files": files_metadata,
                "triggers": [skill_id],
                "enabled": True
            }
            (skill_dir / "skill.json").write_text(
                json.dumps(metadata, indent=2), encoding='utf-8'
            )

            # Update registry
            self._add_to_registry(skill_id)

            # Load and return the skill
            return self.load_skill(skill_id)

        except Exception as e:
            print(f"Failed to fetch skill from {url}: {e}")
            return None

    def refresh_skill(self, skill_id: str) -> Optional[Skill]:
        """
        Refresh a skill from its source URL.

        Args:
            skill_id: ID of the skill to refresh

        Returns:
            Updated Skill object or None if refresh fails
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return None

        source = skill.metadata.get("source", {})
        if source.get("type") != "url":
            print(f"Skill {skill_id} is not from a URL source")
            return skill

        url = source.get("url")
        if not url:
            return skill

        # Re-fetch from URL
        additional_files = []
        for file_entry in skill.metadata.get("files", []):
            if file_entry.get("name") != "skill.md" and file_entry.get("url"):
                additional_files.append(file_entry)

        return self.fetch_from_url(skill_id, url, additional_files)

    # ==================== Skill Creation ====================

    def create_skill(self, skill_id: str, name: str, description: str,
                     content: str, triggers: List[str] = None,
                     metadata: Dict[str, Any] = None) -> Skill:
        """
        Create a new skill locally.

        Args:
            skill_id: Unique identifier for the skill
            name: Human-readable name
            description: Brief description
            content: Main skill.md content
            triggers: List of trigger phrases
            metadata: Additional metadata

        Returns:
            Created Skill object
        """
        skill_dir = self.skills_dir / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write skill.md
        (skill_dir / "skill.md").write_text(content, encoding='utf-8')

        # Create skill.json
        skill_metadata = {
            "id": skill_id,
            "name": name,
            "description": description,
            "source": {
                "type": "local",
                "created_at": datetime.now().isoformat()
            },
            "files": [{"name": "skill.md"}],
            "triggers": triggers or [skill_id],
            "enabled": True,
            **(metadata or {})
        }
        (skill_dir / "skill.json").write_text(
            json.dumps(skill_metadata, indent=2), encoding='utf-8'
        )

        # Update registry
        self._add_to_registry(skill_id)

        # Load and return
        return self.load_skill(skill_id)

    def delete_skill(self, skill_id: str, remove_files: bool = False) -> bool:
        """
        Delete a skill.

        Args:
            skill_id: ID of skill to delete
            remove_files: If True, also delete the skill directory

        Returns:
            True if successful
        """
        # Remove from loaded skills
        if skill_id in self._skills:
            del self._skills[skill_id]

        # Remove from registry
        self._remove_from_registry(skill_id)

        # Optionally remove files
        if remove_files:
            skill_dir = self.skills_dir / skill_id
            if skill_dir.exists():
                import shutil
                shutil.rmtree(skill_dir)

        return True

    # ==================== Prompt Building ====================

    def build_skill_content_prompt(self, skill_id: str) -> str:
        """
        Build a prompt with the full skill content embedded.

        Args:
            skill_id: ID of the skill

        Returns:
            Formatted skill content prompt
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return ""

        lines = [f"## Skill: {skill.name}", ""]
        lines.append(f"**Description:** {skill.description}")
        lines.append("")
        lines.append("### Instructions")
        lines.append("")
        lines.append(skill.content)

        # Include additional files if any
        if skill.files:
            lines.append("")
            lines.append("### Additional Resources")
            for file_name, file_content in skill.files.items():
                lines.append("")
                lines.append(f"#### {file_name}")
                lines.append("")
                lines.append(file_content)

        return "\n".join(lines)

    def match_skill(self, query: str) -> Optional[Skill]:
        """
        Find a skill that matches the given query based on triggers.

        Args:
            query: User query to match against skill triggers

        Returns:
            Best matching Skill or None
        """
        query_lower = query.lower()
        best_match = None
        best_score = 0

        for skill in self._skills.values():
            if not skill.enabled:
                continue

            for trigger in skill.triggers:
                trigger_lower = trigger.lower()
                # Check if trigger appears in query
                if trigger_lower in query_lower:
                    # Score based on trigger length (longer = more specific)
                    score = len(trigger_lower)
                    if score > best_score:
                        best_score = score
                        best_match = skill

        return best_match
