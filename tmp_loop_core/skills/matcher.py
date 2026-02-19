"""
SKILL_MATCHER
=============

Intelligent skill matching for tasks.

Selects the most appropriate skill for a given task based on the task
description and all available skills (global + agent-private).

Two Matching Strategies
-----------------------
1. **LLM-based matching** (primary):
   Sends the task description + available skill list to the LLM.
   LLM returns: ``{skill_id, confidence, reason}``
   Confidence levels: "high", "medium", "low", "none".
   Private skills are listed first (higher priority).

2. **Trigger-based matching** (fallback):
   Used when LLM is unavailable or returns "none".
   Checks if any skill's trigger phrases appear in the task description.
   Scores by trigger length (longer = more specific = higher score).
   Also checks if the skill_id itself appears in the task text.

Used By
-------
- **Task scheduler**: When a task has no explicit skill_id, the scheduler
  calls ``match()`` at runtime to auto-select the best skill.
- **Chat / API**: On-demand matching for interactive sessions.

Relationship to Keywords
------------------------
Skill triggers and keywords are different systems:
- **Triggers** (here): Text hints for skill selection ("join loopColony").
- **Keywords** (keyword_resolver.py): ``$VARIABLE$`` placeholders resolved
  at task execution time (``$CREDENTIALS:loopcolony$``).

Usage::

    matcher = SkillMatcher(skill_loader=skill_loader, llm_client=llm_client)

    result = matcher.match_with_details(
        task_description="Post an update to loopColony",
        agent_id="main"
    )
    # result.skill_id = "loopcolony", result.confidence = "high"
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class SkillMatch:
    """Result of skill matching."""
    skill_id: Optional[str]
    confidence: str  # "high", "medium", "low", "none"
    reason: str


# ============================================================================
# SKILL MATCHER
# ============================================================================

class SkillMatcher:
    """
    Matches tasks to the most appropriate skill using LLM reasoning.

    Designed to be reusable across different execution contexts:
    - Task scheduler
    - Chat interfaces
    - API calls
    - Messaging integrations (WhatsApp, etc.)
    """

    MATCH_PROMPT = """You are a skill selector. Given a task description and a list of available skills,
determine which skill (if any) is best suited to accomplish the task.

TASK DESCRIPTION:
{task_description}

AVAILABLE SKILLS:
{skill_list}

INSTRUCTIONS:
1. Analyze what the task requires
2. Review each skill's description
3. Select the skill that best matches the task requirements
4. If no skill is a good fit, respond with "none"

Consider:
- Does the skill's purpose align with the task?
- Would using this skill improve task execution quality?
- Is the match clear and unambiguous?

Respond with a JSON object in this exact format:
{{
    "skill_id": "<skill_id or null>",
    "confidence": "<high|medium|low|none>",
    "reason": "<brief explanation>"
}}

If no skill is suitable, use:
{{
    "skill_id": null,
    "confidence": "none",
    "reason": "No skill matches the task requirements"
}}
"""

    def __init__(
        self,
        skill_loader: Any = None,
        llm_client: Any = None,
        global_skills_dir: Path = None,
        agents_dir: Path = None
    ):
        """
        Initialize the skill matcher.

        Args:
            skill_loader: Optional existing SkillLoader instance
            llm_client: LLM client for matching (must have complete_json method)
            global_skills_dir: Path to global skills directory
            agents_dir: Path to agents directory (for loading private skills)
        """
        self.skill_loader = skill_loader
        self.llm_client = llm_client
        self.global_skills_dir = Path(global_skills_dir) if global_skills_dir else None
        self.agents_dir = Path(agents_dir) if agents_dir else None

        # Cache for skill summaries
        self._skill_cache: Dict[str, List[Dict]] = {}

    def match(
        self,
        task_description: str,
        agent_id: str = None,
        include_global: bool = True
    ) -> Optional[str]:
        """
        Find the best matching skill for a task.

        Args:
            task_description: Description of what the task needs to accomplish
            agent_id: Agent ID to include private skills (optional)
            include_global: Whether to include global skills (default True)

        Returns:
            skill_id if a suitable skill found, None otherwise
        """
        result = self.match_with_details(task_description, agent_id, include_global)
        return result.skill_id

    def match_with_details(
        self,
        task_description: str,
        agent_id: str = None,
        include_global: bool = True
    ) -> SkillMatch:
        """
        Find the best matching skill with full details.

        Args:
            task_description: Description of what the task needs to accomplish
            agent_id: Agent ID to include private skills (optional)
            include_global: Whether to include global skills (default True)

        Returns:
            SkillMatch with skill_id, confidence, and reason
        """
        # Get available skills
        skills = self._get_available_skills(agent_id, include_global)

        if not skills:
            return SkillMatch(
                skill_id=None,
                confidence="none",
                reason="No skills available"
            )

        # Build skill list for prompt
        skill_list = self._build_skill_list(skills)

        # Ask LLM to select
        return self._llm_select_skill(task_description, skill_list)

    def _get_available_skills(
        self,
        agent_id: str = None,
        include_global: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get available skills for matching.

        Private skills are listed first (higher priority).
        Global skills follow (if include_global=True).
        """
        skills = []

        # Load private skills first (higher priority)
        if agent_id and self.agents_dir:
            private_skills = self._load_agent_skills(agent_id)
            for skill in private_skills:
                skill["source"] = "private"
                skills.append(skill)

        # Load global skills
        if include_global and self.global_skills_dir:
            global_skills = self._load_global_skills()
            for skill in global_skills:
                # Skip if already have private version
                if not any(s["id"] == skill["id"] for s in skills):
                    skill["source"] = "global"
                    skills.append(skill)

        # Also check skill_loader if available
        if self.skill_loader:
            self._add_from_loader(skills, agent_id, include_global)

        return skills

    def _load_agent_skills(self, agent_id: str) -> List[Dict]:
        """Load skills from agent's private directory."""
        skills_dir = self.agents_dir / agent_id / "skills"
        return self._load_skills_from_dir(skills_dir)

    def _load_global_skills(self) -> List[Dict]:
        """Load skills from global directory."""
        return self._load_skills_from_dir(self.global_skills_dir)

    def _load_skills_from_dir(self, skills_dir: Path) -> List[Dict]:
        """Load skill metadata from a directory."""
        import json

        skills = []
        if not skills_dir or not skills_dir.exists():
            return skills

        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_json = skill_dir / "skill.json"
            if not skill_json.exists():
                continue

            try:
                data = json.loads(skill_json.read_text(encoding='utf-8'))

                # Skip deleted skills
                if data.get("is_deleted", False):
                    continue

                # Skip disabled skills
                if not data.get("enabled", True):
                    continue

                skills.append({
                    "id": data.get("id", skill_dir.name),
                    "name": data.get("name", skill_dir.name),
                    "description": data.get("description", "No description"),
                    "triggers": data.get("triggers", [])
                })
            except Exception:
                continue

        return skills

    def _add_from_loader(
        self,
        skills: List[Dict],
        agent_id: str = None,
        include_global: bool = True
    ) -> None:
        """Add skills from the skill loader if available."""
        if not self.skill_loader:
            return

        # Ensure skills are loaded
        self.skill_loader.load_all()

        # Get skills from loader
        existing_ids = {s["id"] for s in skills}

        if include_global:
            for skill_id in self.skill_loader.list_global_skills():
                if skill_id not in existing_ids:
                    skill = self.skill_loader.get_skill(skill_id)
                    if skill and skill.enabled and not skill.is_deleted:
                        skills.append({
                            "id": skill.id,
                            "name": skill.name,
                            "description": skill.description,
                            "triggers": skill.triggers,
                            "source": "global"
                        })
                        existing_ids.add(skill_id)

        # Private skills from loader (if agent_id provided)
        if agent_id:
            for skill_id in self.skill_loader.list_private_skills():
                if skill_id not in existing_ids:
                    skill = self.skill_loader.get_skill(skill_id)
                    if skill and skill.enabled and not skill.is_deleted:
                        skills.insert(0, {  # Insert at front (priority)
                            "id": skill.id,
                            "name": skill.name,
                            "description": skill.description,
                            "triggers": skill.triggers,
                            "source": "private"
                        })
                        existing_ids.add(skill_id)

    def _build_skill_list(self, skills: List[Dict]) -> str:
        """Build formatted skill list for LLM prompt."""
        lines = []
        for skill in skills:
            source_tag = f"[{skill.get('source', 'unknown')}]"
            triggers = ", ".join(skill.get("triggers", []))
            lines.append(
                f"- {skill['id']} {source_tag}: {skill['description']}"
                + (f" (triggers: {triggers})" if triggers else "")
            )
        return "\n".join(lines) if lines else "No skills available"

    def _llm_select_skill(
        self,
        task_description: str,
        skill_list: str
    ) -> SkillMatch:
        """Use LLM to select the best skill."""
        if not self.llm_client:
            # No LLM available, fall back to trigger matching
            return self._fallback_trigger_match(task_description, skill_list)

        prompt = self.MATCH_PROMPT.format(
            task_description=task_description,
            skill_list=skill_list
        )

        try:
            result = self.llm_client.complete_json(
                prompt=prompt,
                system="You are a skill selector. Respond only with valid JSON.",
                caller="skill_matcher",
                max_tokens=500
            )

            if result:
                skill_id = result.get("skill_id")
                # Handle "none" string or null
                if skill_id in (None, "none", "null", ""):
                    skill_id = None

                return SkillMatch(
                    skill_id=skill_id,
                    confidence=result.get("confidence", "medium"),
                    reason=result.get("reason", "")
                )

        except Exception as e:
            print(f"[WARN] Skill matching LLM call failed: {e}")

        # Fall back to trigger matching
        return self._fallback_trigger_match(task_description, skill_list)

    def _fallback_trigger_match(
        self,
        task_description: str,
        skill_list: str
    ) -> SkillMatch:
        """
        Fallback to simple trigger-based matching when LLM unavailable.

        Checks if any skill triggers appear in the task description.
        """
        task_lower = task_description.lower()
        best_match = None
        best_score = 0

        # Parse skill list to extract triggers
        for line in skill_list.split("\n"):
            if not line.startswith("- "):
                continue

            # Extract skill_id
            parts = line[2:].split(":", 1)
            if not parts:
                continue

            skill_part = parts[0].strip()
            skill_id = skill_part.split()[0] if skill_part else None
            if not skill_id:
                continue

            # Check triggers
            if "(triggers:" in line:
                trigger_start = line.find("(triggers:") + 10
                trigger_end = line.find(")", trigger_start)
                triggers_str = line[trigger_start:trigger_end]
                triggers = [t.strip() for t in triggers_str.split(",")]

                for trigger in triggers:
                    if trigger.lower() in task_lower:
                        score = len(trigger)
                        if score > best_score:
                            best_score = score
                            best_match = skill_id

            # Also check if skill_id appears in task
            if skill_id.lower().replace("_", " ") in task_lower:
                score = len(skill_id)
                if score > best_score:
                    best_score = score
                    best_match = skill_id

        if best_match:
            return SkillMatch(
                skill_id=best_match,
                confidence="low",
                reason=f"Trigger match (LLM unavailable)"
            )

        return SkillMatch(
            skill_id=None,
            confidence="none",
            reason="No matching triggers found"
        )


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def create_skill_matcher(
    global_skills_dir: str = None,
    agents_dir: str = None,
    llm_client: Any = None
) -> SkillMatcher:
    """
    Create a SkillMatcher instance.

    Args:
        global_skills_dir: Path to global skills (default: data/SKILLS)
        agents_dir: Path to agents directory (default: data/AGENTS)
        llm_client: LLM client for matching (optional, uses default if None)

    Returns:
        Configured SkillMatcher instance
    """
    # Default paths
    if global_skills_dir is None:
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent.parent
        global_skills_dir = root / "data" / "SKILLS"

    if agents_dir is None:
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent.parent
        agents_dir = root / "data" / "AGENTS"

    # Default LLM client
    if llm_client is None:
        try:
            from llm_client import get_default_client
            llm_client = get_default_client()
        except ImportError:
            print("[WARN] LLM client not available, using trigger-based matching only")
            llm_client = None

    return SkillMatcher(
        global_skills_dir=global_skills_dir,
        agents_dir=agents_dir,
        llm_client=llm_client
    )
