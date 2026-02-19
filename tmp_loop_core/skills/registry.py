"""
PER-AGENT SKILL REGISTRY
=========================

Per-agent skill registry that gives each agent a curated list of skills
with optional heartbeat metadata. This replaces the global skill registry
for agents that have their own ``skills/registry.json``.

Registry Format (per-agent)
----------------------------
::

    data/AGENTS/{agent_id}/skills/registry.json
    {
        "version": "1.0",
        "skills": [
            {
                "name": "loopcolony",
                "description": "Team workspace - posts, tasks, DMs, notifications.",
                "path": "loopcolony/skill.md",
                "heartbeat": {
                    "interval_minutes": 10,
                    "prompt": "Check loopColony for notifications"
                }
            }
        ]
    }

Path Resolution
----------------
Each entry's ``path`` is resolved in order:
1. ``AGENTS/{agent_id}/skills/{path}`` — agent-private copy (takes priority)
2. ``SKILLS/{path}`` — global skills directory (fallback)

Backward Compatibility
-----------------------
If a registry.json uses the old SkillLoader format (entries have ``"id"``
instead of ``"name"``), ``load()`` returns None so the caller falls back
to the legacy SkillLoader.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SkillRegistryEntry:
    """A single skill entry in a per-agent registry."""

    name: str
    description: str
    path: str  # Relative path to skill.md (e.g. "loopcolony/skill.md")
    resolved_path: Optional[str] = None  # Absolute path after resolution
    heartbeat: Optional[Dict] = None  # {"interval_minutes": 10, "prompt": "..."}


class AgentSkillRegistry:
    """
    Per-agent skill registry.

    Loads a curated skill list from an agent's ``skills/registry.json``,
    resolves file paths, and builds the skills prompt for the LLM.
    """

    def __init__(self, entries: List[SkillRegistryEntry]):
        self.entries = entries

    @classmethod
    def load(cls, registry_path: Path) -> Optional["AgentSkillRegistry"]:
        """
        Load a per-agent registry from a JSON file.

        Returns None if:
        - File doesn't exist
        - File uses the old SkillLoader format (has "id" key instead of "name")

        Args:
            registry_path: Path to skills/registry.json

        Returns:
            AgentSkillRegistry or None
        """
        if not registry_path.exists():
            return None

        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        skills_list = data.get("skills", [])
        if not skills_list:
            return None

        # Detect old SkillLoader format: entries have "id" instead of "name"
        first = skills_list[0]
        if isinstance(first, str):
            # Flat list of skill names — convert to entries
            skills_list = [{"name": s, "path": f"{s}/skill.md", "description": s} for s in skills_list]
            first = skills_list[0]
        if isinstance(first, dict) and "id" in first and "name" not in first:
            return None

        entries = []
        for item in skills_list:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            entries.append(
                SkillRegistryEntry(
                    name=name,
                    description=item.get("description", ""),
                    path=item.get("path", ""),
                    heartbeat=item.get("heartbeat"),
                )
            )

        if not entries:
            return None

        return cls(entries)

    def resolve_paths(
        self,
        agent_skills_dir: Path,
        global_skills_dir: Path,
    ) -> None:
        """
        Resolve each entry's relative path to an absolute path.

        Tries agent-private directory first, then global skills directory.

        Args:
            agent_skills_dir: e.g. data/AGENTS/scout/skills
            global_skills_dir: e.g. data/SKILLS
        """
        for entry in self.entries:
            if not entry.path:
                continue

            # Try agent-private first
            agent_path = agent_skills_dir / entry.path
            if agent_path.exists():
                entry.resolved_path = str(agent_path.resolve())
                continue

            # Fallback to global
            global_path = global_skills_dir / entry.path
            if global_path.exists():
                entry.resolved_path = str(global_path.resolve())
                continue

            # Path not found — leave resolved_path as None

    def build_single_skill_prompt(self, skill_id: str) -> str:
        """Build inline content prompt for a single skill by name.

        Loads the skill's content files and embeds them directly in the prompt.
        File selection logic:
        - If heartbeat.md exists and no explicit heartbeat.files, inline only heartbeat.md
        - If heartbeat.files is set, inline only those files
        - Otherwise, inline all .md files from the skill directory

        Returns empty string if skill not found or has no resolved path.
        """
        entry = next((e for e in self.entries if e.name == skill_id), None)
        if not entry or not entry.resolved_path:
            return ""

        resolved_path = Path(entry.resolved_path)
        skill_dir = resolved_path.parent

        lines = [f"## Active Skill: {entry.name}", ""]
        lines.append(f"**Description:** {entry.description}")
        lines.append("")

        # Determine which files to inline
        heartbeat_md = skill_dir / "heartbeat.md"
        hb_files = (entry.heartbeat or {}).get("files") if entry.heartbeat else None

        if heartbeat_md.exists() and not hb_files:
            # Has heartbeat.md -- inline only that for focused execution
            try:
                content = heartbeat_md.read_text(encoding="utf-8")
                lines.append('<file name="heartbeat.md">')
                lines.append(content.rstrip())
                lines.append("</file>")
            except OSError:
                pass
        elif hb_files:
            # Registry specifies exactly which files to inline
            for filename in hb_files:
                fpath = skill_dir / filename
                if fpath.exists():
                    try:
                        content = fpath.read_text(encoding="utf-8")
                        lines.append(f'<file name="{filename}">')
                        lines.append(content.rstrip())
                        lines.append("</file>")
                    except OSError:
                        pass
        else:
            # No heartbeat.md -> inline all .md files
            for md_file in sorted(skill_dir.glob("*.md")):
                try:
                    content = md_file.read_text(encoding="utf-8")
                    lines.append(f'<file name="{md_file.name}">')
                    lines.append(content.rstrip())
                    lines.append("</file>")
                except OSError:
                    pass

        lines.append("")
        lines.append(
            "Skill content is pre-loaded above. Follow the instructions EXACTLY. "
            "Do NOT use file_read to re-read these files."
        )

        return "\n".join(lines)

    def get_heartbeat_skills(self) -> List[SkillRegistryEntry]:
        """
        Return entries whose skill directory contains a heartbeat.md file.

        Detection is based on file existence, not registry metadata.

        Returns:
            List of entries with heartbeat.md in their skill directory
        """
        return [
            e for e in self.entries
            if e.resolved_path and (Path(e.resolved_path).parent / "heartbeat.md").exists()
        ]

    # ------------------------------------------------------------------
    # Mutation helpers (static — operate on the registry file directly)
    # ------------------------------------------------------------------

    @staticmethod
    def upsert_entry(
        registry_path: Path,
        name: str,
        description: str,
        path: str,
        heartbeat: Optional[Dict] = None,
    ) -> None:
        """
        Add or update a skill entry in the per-agent registry.json.

        Creates the file (and parent dirs) if it doesn't exist.
        If an entry with the same ``name`` already exists it is replaced.

        Args:
            registry_path: Absolute path to skills/registry.json
            name: Skill name (unique key)
            description: Short description
            path: Relative path to skill.md (e.g. "loopcolony/skill.md")
            heartbeat: Optional heartbeat config dict
        """
        # Load or create
        data = {"version": "1.0", "skills": []}
        if registry_path.exists():
            try:
                data = json.loads(registry_path.read_text(encoding="utf-8"))
                if not isinstance(data.get("skills"), list):
                    data["skills"] = []
            except (json.JSONDecodeError, OSError):
                data = {"version": "1.0", "skills": []}

        # Build the new entry dict
        entry: Dict = {"name": name, "description": description, "path": path}
        if heartbeat:
            entry["heartbeat"] = heartbeat

        # Replace existing or append
        replaced = False
        for i, existing in enumerate(data["skills"]):
            if isinstance(existing, dict) and existing.get("name") == name:
                data["skills"][i] = entry
                replaced = True
                break
        if not replaced:
            data["skills"].append(entry)

        # Persist
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def remove_entry(registry_path: Path, name: str) -> None:
        """
        Remove a skill entry by name from the per-agent registry.json.

        Does nothing if the file doesn't exist or the entry isn't found.
        Keeps the file (with empty skills list) even if no entries remain.

        Args:
            registry_path: Absolute path to skills/registry.json
            name: Skill name to remove
        """
        if not registry_path.exists():
            return

        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        skills = data.get("skills", [])
        original_len = len(skills)
        data["skills"] = [
            s for s in skills if not (isinstance(s, dict) and s.get("name") == name)
        ]

        if len(data["skills"]) != original_len:
            registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
