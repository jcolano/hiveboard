"""
SKILLS EDITOR
=============

AI-assisted skill creation and editing via the admin UI.

This provides a multi-step workflow where the LLM helps the user create
well-structured skills without needing to write skill.json / skill.md by hand.

Workflow
--------
1. User describes intent → LLM generates a hypothesis + dynamic form fields.
2. User fills in the form (text, textarea, select, multiselect, checkbox, number).
3. LLM generates skill.json, skill.md, and any auxiliary .md files.
4. System saves everything to ``data/AGENTS/{agent_id}/skills/{skill_id}/``.
5. Form data saved as ``_editor_form.json`` alongside skill for future editing.

Design Decisions
----------------
- **Separate LLM calls for JSON vs markdown**: Avoids the LLM truncation bug
  where markdown inside JSON gets cut off at ~15-20% of max_tokens.
  Metadata (skill.json) is generated via ``complete_json()``, then each .md
  file is generated separately via ``complete()`` as plain text.
- **Referenced file detection**: Regex scans skill.md for references to other
  files (e.g. "see heartbeat.md") and generates each one separately.
- **Skills saved as private**: Created skills go to the agent's private skills
  directory, not the global skills directory.

Usage::

    editor = SkillEditor(llm_client)
    hypothesis = editor.generate_hypothesis("I want a skill for daily standups")
    skill_files = editor.generate_skill(hypothesis, user_answers)
    editor.save_skill(agent_id, "daily_standup", skill_files, form_data)
"""

import json
import re
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
from enum import Enum


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class FormFieldType(str, Enum):
    """Types of form fields."""
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    MULTISELECT = "multiselect"
    CHECKBOX = "checkbox"
    NUMBER = "number"


@dataclass
class FormField:
    """A single field in the dynamic form."""
    id: str
    type: FormFieldType
    question: str
    description: Optional[str] = None
    options: Optional[List[str]] = None  # For select/multiselect
    default: Any = None
    required: bool = True
    placeholder: Optional[str] = None

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['type'] = self.type.value
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class SkillHypothesis:
    """LLM's initial hypothesis about the skill."""
    name: str
    description: str
    suggested_id: str
    suggested_triggers: List[str]
    suggested_tools: List[str]
    suggested_files: List[str]
    reasoning: str  # Why the LLM made these choices

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class EditorForm:
    """The dynamic form for skill creation."""
    hypothesis: SkillHypothesis
    fields: List[FormField]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {
            "hypothesis": self.hypothesis.to_dict(),
            "fields": [f.to_dict() for f in self.fields],
            "created_at": self.created_at
        }


@dataclass
class SkillFiles:
    """Generated skill files."""
    skill_json: Dict
    skill_md: str
    additional_files: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "skill_json": self.skill_json,
            "skill_md": self.skill_md,
            "additional_files": self.additional_files
        }


# ============================================================================
# PROMPTS
# ============================================================================

HYPOTHESIS_PROMPT = """You are a skill designer for an AI agent framework. The user wants to create a new skill.

A "skill" is a set of instructions that teaches an AI agent how to perform a specific task. Skills consist of:
1. skill.json - Metadata (id, name, version, description, triggers, required tools)
2. skill.md - Main instructions (process, steps, output format, error handling)
3. Optional additional .md files for complex skills

Based on the user's intent, generate:
1. A hypothesis about what the skill should do
2. A dynamic form with questions to refine the skill

Available tools the agent can use:
- file_read: Read files from allowed directories
- file_write: Write files to allowed directories
- http_request: Make HTTP API requests
- webpage_fetch: Fetch and parse web pages

Respond with a JSON object in this exact format:
{{
  "hypothesis": {{
    "name": "Human-readable skill name",
    "description": "Clear description of what the skill does",
    "suggested_id": "snake_case_id",
    "suggested_triggers": ["phrase 1", "phrase 2", "phrase 3"],
    "suggested_tools": ["tool1", "tool2"],
    "suggested_files": ["skill.md"],
    "reasoning": "Why you made these choices"
  }},
  "fields": [
    {{
      "id": "field_id",
      "type": "text|textarea|select|multiselect|checkbox|number",
      "question": "Question to ask the user",
      "description": "Optional help text",
      "options": ["option1", "option2"],
      "default": "default value",
      "required": true,
      "placeholder": "Optional placeholder"
    }}
  ]
}}

Guidelines for form fields:
- Ask 3-7 focused questions
- Include questions about: input format, output format, specific steps, error handling
- Use "select" for single choice, "multiselect" for multiple choices
- Make questions clear and specific
- Provide sensible defaults when possible
- Don't ask about things already clear from the intent

User's intent: {intent}
"""

GENERATE_SKILL_JSON_PROMPT = """You are a skill designer. Generate the skill metadata (skill.json) based on the hypothesis and user's answers.

Hypothesis:
{hypothesis}

User's Answers:
{answers}

Generate ONLY the skill.json metadata. Respond with a JSON object in this exact format:
{{
  "id": "{skill_id}",
  "name": "Skill Name",
  "version": "1.0.0",
  "description": "Description (1-2 sentences)",
  "author": "user",
  "source": {{
    "type": "editor",
    "created_at": "{created_at}"
  }},
  "files": ["skill.md", "additional_file.md"],
  "triggers": ["trigger1", "trigger2", "trigger3"],
  "requires": {{
    "tools": ["tool1"]
  }},
  "enabled": true
}}

Guidelines:
- List all files the skill will need in the "files" array
- Include 2-4 natural trigger phrases users might say
- Only include tools that are actually needed (file_read, file_write, http_request, webpage_fetch)
- Keep description concise (1-2 sentences)

DO NOT include skill_md content - only the JSON metadata.
"""

GENERATE_SKILL_MD_PROMPT = """You are a skill designer. Generate the skill.md instructions based on the hypothesis and user's answers.

Hypothesis:
{hypothesis}

User's Answers:
{answers}

Files this skill will use: {files_list}

Generate the COMPLETE skill.md markdown content.

IMPORTANT: Output ONLY raw markdown. Do NOT wrap in JSON or code blocks.
Start directly with the title (# Skill Name) and continue with full content.

Structure:
1. # Title
2. ## Purpose (1-2 sentences)
3. ## Process (step-by-step instructions)
4. ## Output Format (what the agent should produce)
5. ## Success Criteria (how to know it worked)

Guidelines:
- Be direct and actionable
- Use bullet points for lists
- Reference additional files where appropriate (e.g., "Read `templates.md` for examples")
- Include error handling guidance

SIZE CONSTRAINTS:
- Keep CONCISE: 50-100 lines maximum
- No filler text or excessive explanation
- Focus on WHAT to do, not lengthy WHY

CRITICAL: You MUST end your response with exactly this line:
<!-- END_OF_FILE -->

OUTPUT THE COMPLETE SKILL.MD CONTENT NOW:
"""

GENERATE_SINGLE_FILE_PROMPT = """You are a skill designer. Generate the content for a single supporting file that is referenced by a skill.

The skill.md references this file: {filename}

Here is the skill.md content for context:
{skill_md}

Here is the skill hypothesis for context:
{hypothesis}

Generate the COMPLETE markdown content for {filename}.

IMPORTANT: Output ONLY the raw markdown content. Do NOT wrap in JSON or code blocks.
Start directly with the markdown title (# Title) and continue with the full content.

The file should:
- Be a complete, self-contained markdown document
- Start with a clear title (# Title)
- Contain practical, actionable content that supports the main skill
- Match what the skill.md expects to find in this file
- Include relevant examples, templates, strategies, or reference data as appropriate

SIZE CONSTRAINTS:
- Keep the file CONCISE: aim for 30-80 lines maximum
- Use bullet points and tables instead of long paragraphs
- Include only essential examples (2-3 per category, not exhaustive lists)
- Be direct - no filler text or excessive preamble

CRITICAL: You MUST end your response with exactly this line:
<!-- END_OF_FILE -->

OUTPUT THE COMPLETE FILE CONTENT NOW:
"""

EXPAND_OTHER_PROMPT = """You are helping improve a skill creation form. The user filled out a form but added extra information in an "Other" field that wasn't captured by the existing questions.

Existing form fields:
{existing_fields}

User's "Other" input:
{other_content}

Analyze the user's "Other" input and convert it into 1-3 proper form fields that would capture this information in future edits. For each piece of information in "Other", create a structured question.

Respond with a JSON object:
{{
  "new_fields": [
    {{
      "id": "field_id",
      "type": "text|textarea|select|multiselect|checkbox|number",
      "question": "Question to ask the user",
      "description": "Optional help text",
      "options": ["option1", "option2"],
      "default": null,
      "required": false,
      "placeholder": "Optional placeholder"
    }}
  ],
  "answers": {{
    "field_id": "The user's answer extracted from their Other input"
  }}
}}

Guidelines:
- Extract specific, answerable questions from the "Other" text
- Provide the user's answer as parsed from their input
- Use appropriate field types (textarea for long text, select for choices, etc.)
- Make field IDs unique and descriptive (snake_case)
- If the "Other" content is just clarification, still convert it to a field
- Don't duplicate existing fields
"""

IMPORT_SKILL_PROMPT = """You are analyzing a skill definition written in markdown. Generate the metadata (skill.json) for this skill.

Skill markdown content:
{skill_md}

Based on the markdown content, generate the skill.json metadata. Respond with a JSON object:
{{
  "id": "snake_case_skill_id",
  "name": "Human Readable Name",
  "version": "1.0.0",
  "description": "Brief description of what the skill does (1-2 sentences)",
  "author": "imported",
  "source": {{
    "type": "imported",
    "imported_at": "{imported_at}"
  }},
  "files": [{{"name": "skill.md"}}],
  "triggers": ["trigger phrase 1", "trigger phrase 2", "trigger phrase 3"],
  "requires": {{
    "tools": []
  }},
  "enabled": true
}}

Guidelines:
- Generate a clear, descriptive id in snake_case (e.g., "email_composer", "code_reviewer")
- Write a concise but informative description
- Create 2-4 trigger phrases that users might say to invoke this skill
- Identify any tools the skill might need (file_read, file_write, http_request, webpage_fetch)
- If the markdown has a title (# Title), use it for the name
"""


# ============================================================================
# SKILL EDITOR
# ============================================================================

class SkillEditor:
    """
    AI-assisted skill editor.

    Guides users through creating skills with LLM assistance.
    """

    EDITOR_FORM_FILENAME = "_editor_form.json"

    def __init__(self, llm_client, agents_dir: str = "./data/AGENTS"):
        """
        Initialize the skill editor.

        Args:
            llm_client: LLM client with complete_json method
            agents_dir: Base directory for agent data
        """
        self.llm_client = llm_client
        self.agents_dir = Path(agents_dir)

    def generate_hypothesis(self, intent: str) -> EditorForm:
        """
        Generate a skill hypothesis and form from user intent.

        Args:
            intent: User's description of what skill they want

        Returns:
            EditorForm with hypothesis and questions

        Raises:
            ValueError: If LLM fails to generate a valid response
        """
        if not self.llm_client:
            raise ValueError("LLM client not initialized. Please check your API key configuration.")

        prompt = HYPOTHESIS_PROMPT.format(intent=intent)

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You are a skill designer. Always respond with valid JSON.",
            max_tokens=8192  # Increased to avoid truncation
        )

        # Check for valid response
        if response is None:
            raise ValueError("LLM failed to generate response. Please check your API key and try again.")

        if "hypothesis" not in response:
            raise ValueError("LLM response missing 'hypothesis' field. Please try again.")

        # Parse response
        hypothesis = SkillHypothesis(
            name=response["hypothesis"].get("name", "Unnamed Skill"),
            description=response["hypothesis"].get("description", ""),
            suggested_id=response["hypothesis"].get("suggested_id", "new_skill"),
            suggested_triggers=response["hypothesis"].get("suggested_triggers", []),
            suggested_tools=response["hypothesis"].get("suggested_tools", []),
            suggested_files=response["hypothesis"].get("suggested_files", []),
            reasoning=response["hypothesis"].get("reasoning", "")
        )

        fields = []
        for f in response.get("fields", []):
            fields.append(FormField(
                id=f["id"],
                type=FormFieldType(f["type"]),
                question=f["question"],
                description=f.get("description"),
                options=f.get("options"),
                default=f.get("default"),
                required=f.get("required", True),
                placeholder=f.get("placeholder")
            ))

        return EditorForm(hypothesis=hypothesis, fields=fields)

    def generate_skill(
        self,
        form: EditorForm,
        answers: Dict[str, Any],
        skill_id: Optional[str] = None
    ) -> SkillFiles:
        """
        Generate skill files from form and answers.

        Uses separate LLM calls for skill_json and skill_md to avoid
        truncation issues when generating markdown inside JSON.

        Args:
            form: The editor form with hypothesis
            answers: User's answers to form questions
            skill_id: Override skill ID (uses hypothesis if not provided)

        Returns:
            SkillFiles with generated content
        """
        skill_id = skill_id or form.hypothesis.suggested_id
        created_at = datetime.now().isoformat()

        # Format answers for prompt
        answers_text = ""
        for field in form.fields:
            answer = answers.get(field.id, field.default)
            if answer is not None:
                answers_text += f"- {field.question}\n  Answer: {answer}\n\n"

        # Include "Other" field if provided (additional user instructions)
        other_answer = answers.get('_other', '').strip()
        if other_answer:
            answers_text += f"- Additional Instructions (from user):\n  {other_answer}\n\n"

        hypothesis_json = json.dumps(form.hypothesis.to_dict(), indent=2)

        # Step 1: Generate skill_json (metadata only)
        json_prompt = GENERATE_SKILL_JSON_PROMPT.format(
            hypothesis=hypothesis_json,
            answers=answers_text,
            skill_id=skill_id,
            created_at=created_at
        )

        skill_json = self.llm_client.complete_json(
            prompt=json_prompt,
            system="You are a skill designer. Generate skill metadata as JSON. Do NOT include skill_md content.",
            caller="skill_editor_json",
            max_tokens=2048  # Metadata is small
        )

        # Get file list for skill_md prompt
        files_list = ", ".join(skill_json.get("files", ["skill.md"]))

        # Step 2: Generate skill_md as raw markdown (not JSON-wrapped)
        md_prompt = GENERATE_SKILL_MD_PROMPT.format(
            hypothesis=hypothesis_json,
            answers=answers_text,
            files_list=files_list
        )

        skill_md = self.llm_client.complete(
            prompt=md_prompt,
            system="You are a skill designer. Generate complete skill.md instructions. Output raw markdown only, no JSON.",
            caller="skill_editor_md",
            max_tokens=8192
        )

        # Clean up skill_md - strip any accidental code blocks
        if skill_md:
            skill_md = skill_md.strip()
            if skill_md.startswith("```markdown"):
                skill_md = skill_md[11:]
            elif skill_md.startswith("```md"):
                skill_md = skill_md[5:]
            elif skill_md.startswith("```"):
                skill_md = skill_md[3:]
            if skill_md.endswith("```"):
                skill_md = skill_md[:-3]
            skill_md = skill_md.strip()

            # Check for completion marker
            if "<!-- END_OF_FILE -->" in skill_md:
                skill_md = skill_md.replace("<!-- END_OF_FILE -->", "").strip()
                print(f"[OK] skill.md generation complete (marker found)")
            else:
                print(f"[WARN] skill.md may be truncated (completion marker missing)")

        # Detect files referenced in skill_md - these will be generated separately
        referenced_files = self._detect_missing_files(skill_md, {})

        # Also check files listed in skill_json.files (except skill.md)
        listed_files = [f for f in skill_json.get("files", []) if f != "skill.md"]
        for f in listed_files:
            if f not in referenced_files:
                referenced_files.append(f)

        # Generate each additional file separately (one LLM call per file)
        additional_files = {}
        if referenced_files:
            additional_files = self._generate_files_separately(
                filenames=referenced_files,
                skill_md=skill_md,
                hypothesis=form.hypothesis
            )

        # Update skill_json.files to include all generated files
        all_files = ["skill.md"] + list(additional_files.keys())
        skill_json["files"] = all_files

        return SkillFiles(
            skill_json=skill_json,
            skill_md=skill_md,
            additional_files=additional_files
        )

    def _detect_missing_files(self, skill_md: str, additional_files: Dict[str, str]) -> List[str]:
        """
        Detect files referenced in skill_md but not present in additional_files.

        Looks for patterns like:
        - `filename.md` (backtick references)
        - Read/read filename.md
        - file: filename.md
        - Files listed that end in .md

        Args:
            skill_md: The skill markdown content
            additional_files: Dict of filename -> content

        Returns:
            List of missing filenames
        """
        import re

        referenced_files = set()

        # Pattern 1: backtick references like `filename.md`
        backtick_pattern = r'`([a-zA-Z0-9_-]+\.md)`'
        for match in re.findall(backtick_pattern, skill_md):
            if match != "skill.md":
                referenced_files.add(match)

        # Pattern 2: "Read filename.md" or "read the filename.md"
        read_pattern = r'[Rr]ead(?:\s+the)?\s+[`"]?([a-zA-Z0-9_-]+\.md)[`"]?'
        for match in re.findall(read_pattern, skill_md):
            if match != "skill.md":
                referenced_files.add(match)

        # Pattern 3: "- filename.md" in lists (common for file listings)
        list_pattern = r'^[\s]*[-*]\s*[`"]?([a-zA-Z0-9_-]+\.md)[`"]?'
        for match in re.findall(list_pattern, skill_md, re.MULTILINE):
            if match != "skill.md":
                referenced_files.add(match)

        # Find which referenced files are missing
        existing_files = set(additional_files.keys())
        missing = referenced_files - existing_files

        return list(missing)

    def _generate_files_separately(
        self,
        filenames: List[str],
        skill_md: str,
        hypothesis: SkillHypothesis
    ) -> Dict[str, str]:
        """
        Generate content for each file using separate LLM calls.

        Each file gets its own dedicated LLM call to avoid truncation issues
        and ensure complete, quality content for each file.

        Args:
            filenames: List of filenames to generate
            skill_md: The skill.md content for context
            hypothesis: The skill hypothesis for context

        Returns:
            Dict of filename -> generated content
        """
        if not filenames:
            return {}

        generated_files = {}

        for filename in filenames:
            try:
                content = self._generate_single_file(
                    filename=filename,
                    skill_md=skill_md,
                    hypothesis=hypothesis
                )
                if content:
                    generated_files[filename] = content
            except Exception as e:
                # Log error but continue with other files
                print(f"[WARN] Failed to generate {filename}: {e}")

        return generated_files

    def _generate_single_file(
        self,
        filename: str,
        skill_md: str,
        hypothesis: SkillHypothesis
    ) -> Optional[str]:
        """
        Generate content for a single file using a dedicated LLM call.

        Uses raw text completion (not JSON) to avoid truncation issues where
        the LLM would stop mid-JSON when generating markdown content.

        Args:
            filename: The filename to generate
            skill_md: The skill.md content for context
            hypothesis: The skill hypothesis for context

        Returns:
            Generated file content, or None if generation failed
        """
        prompt = GENERATE_SINGLE_FILE_PROMPT.format(
            filename=filename,
            skill_md=skill_md,
            hypothesis=json.dumps(hypothesis.to_dict(), indent=2)
        )

        # Use raw complete (not JSON) to avoid truncation issues
        # The LLM was stopping mid-JSON with end_turn at ~15% tokens
        response = self.llm_client.complete(
            prompt=prompt,
            system=f"You are a skill designer. Generate complete markdown content for {filename}. Output raw markdown only, no JSON wrapping.",
            caller="skill_editor_file",
            max_tokens=8192
        )

        if not response:
            return None

        # Clean up response - strip any accidental code blocks
        content = response.strip()
        if content.startswith("```markdown"):
            content = content[11:]
        elif content.startswith("```md"):
            content = content[5:]
        elif content.startswith("```"):
            content = content[3:]

        if content.endswith("```"):
            content = content[:-3]

        content = content.strip()

        # Check for completion marker
        if "<!-- END_OF_FILE -->" in content:
            content = content.replace("<!-- END_OF_FILE -->", "").strip()
            print(f"[OK] {filename} generation complete (marker found)")
        else:
            print(f"[WARN] {filename} may be truncated (completion marker missing)")

        return content

    def expand_other_field(
        self,
        form: EditorForm,
        other_content: str
    ) -> tuple[List[FormField], Dict[str, Any]]:
        """
        Convert "Other" field content into proper form fields.

        When users add extra information in the "Other" field, this method
        uses LLM to convert that into structured form fields for future edits.

        Args:
            form: The existing editor form
            other_content: Content from the "Other" field

        Returns:
            Tuple of (new_fields, answers_for_new_fields)
        """
        if not other_content or not other_content.strip():
            return [], {}

        # Format existing fields for context
        existing_fields_text = ""
        for field in form.fields:
            existing_fields_text += f"- {field.id}: {field.question}\n"

        prompt = EXPAND_OTHER_PROMPT.format(
            existing_fields=existing_fields_text,
            other_content=other_content
        )

        response = self.llm_client.complete_json(
            prompt=prompt,
            system="You are helping improve a skill creation form. Convert user's 'Other' input into structured form fields. Always respond with valid JSON."
        )

        new_fields = []
        new_answers = response.get("answers", {})

        for f in response.get("new_fields", []):
            new_fields.append(FormField(
                id=f.get("id", f"custom_{len(new_fields)}"),
                type=FormFieldType(f.get("type", "text")),
                question=f.get("question", ""),
                description=f.get("description"),
                options=f.get("options"),
                default=f.get("default"),
                required=f.get("required", False),
                placeholder=f.get("placeholder")
            ))

        return new_fields, new_answers

    def import_skill_from_md(
        self,
        agent_id: str,
        skill_md: str,
        skill_id: Optional[str] = None
    ) -> Path:
        """
        Import a skill from markdown content only.

        Uses LLM to generate the skill.json metadata from the markdown.

        Args:
            agent_id: Agent ID to save skill for
            skill_md: The skill's markdown content
            skill_id: Optional skill ID (auto-generated if not provided)

        Returns:
            Path to the saved skill directory
        """
        imported_at = datetime.now().isoformat()

        # Generate skill.json from markdown using LLM
        prompt = IMPORT_SKILL_PROMPT.format(
            skill_md=skill_md,
            imported_at=imported_at
        )

        skill_json = self.llm_client.complete_json(
            prompt=prompt,
            system="You are a skill metadata generator. Analyze the skill markdown and generate appropriate metadata. Always respond with valid JSON."
        )

        # Override ID if provided
        if skill_id:
            skill_json["id"] = skill_id
        else:
            skill_id = skill_json.get("id", f"imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            skill_json["id"] = skill_id

        # Ensure required fields
        if "enabled" not in skill_json:
            skill_json["enabled"] = True
        if "triggers" not in skill_json:
            skill_json["triggers"] = []

        # Save skill files
        skill_dir = self.agents_dir / agent_id / "skills" / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Save skill.json
        with open(skill_dir / "skill.json", 'w', encoding='utf-8') as f:
            json.dump(skill_json, f, indent=2)

        # Save skill.md
        with open(skill_dir / "skill.md", 'w', encoding='utf-8') as f:
            f.write(skill_md)

        return skill_dir

    def save_skill(
        self,
        agent_id: str,
        skill_id: str,
        skill_files: SkillFiles,
        form: EditorForm,
        answers: Dict[str, Any]
    ) -> Path:
        """
        Save skill files and editor form to disk.

        Args:
            agent_id: Agent ID to save skill for
            skill_id: Skill ID (folder name)
            skill_files: Generated skill files
            form: Editor form (for future editing)
            answers: User's answers (for future editing)

        Returns:
            Path to skill directory
        """
        # Determine skill directory
        skill_dir = self.agents_dir / agent_id / "skills" / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Save skill.json
        skill_json_path = skill_dir / "skill.json"
        with open(skill_json_path, 'w', encoding='utf-8') as f:
            json.dump(skill_files.skill_json, f, indent=2)

        # Save skill.md
        skill_md_path = skill_dir / "skill.md"
        with open(skill_md_path, 'w', encoding='utf-8') as f:
            f.write(skill_files.skill_md)

        # Save additional files
        for filename, content in skill_files.additional_files.items():
            file_path = skill_dir / filename
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

        # Save editor form for future editing
        editor_form_path = skill_dir / self.EDITOR_FORM_FILENAME
        editor_data = {
            "form": form.to_dict(),
            "answers": answers,
            "last_edited": datetime.now().isoformat()
        }
        with open(editor_form_path, 'w', encoding='utf-8') as f:
            json.dump(editor_data, f, indent=2)

        return skill_dir

    def _find_skill_dir(self, agent_id: str, skill_id: str) -> Optional[Path]:
        """
        Find the skill directory by skill_id.

        First tries direct folder name match, then searches by internal ID
        in skill.json files (for auto-generated folder names like sk_xxxxx).

        Args:
            agent_id: Agent ID
            skill_id: Skill ID to find

        Returns:
            Path to skill directory or None if not found
        """
        skills_base = self.agents_dir / agent_id / "skills"

        # Try direct folder name match first
        direct_path = skills_base / skill_id
        if direct_path.exists() and (direct_path / "skill.json").exists():
            return direct_path

        # Search by internal ID in skill.json files
        if skills_base.exists():
            for skill_json_path in skills_base.glob("*/skill.json"):
                try:
                    with open(skill_json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if data.get("id") == skill_id:
                        return skill_json_path.parent
                except (json.JSONDecodeError, IOError):
                    continue

        return None

    def load_skill_for_editing(
        self,
        agent_id: str,
        skill_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Load an existing skill's editor form for editing.

        Args:
            agent_id: Agent ID
            skill_id: Skill ID to load

        Returns:
            Dict with form and answers, or None if not editable
        """
        skill_dir = self._find_skill_dir(agent_id, skill_id)
        if not skill_dir:
            return None

        editor_form_path = skill_dir / self.EDITOR_FORM_FILENAME

        if not editor_form_path.exists():
            return None

        with open(editor_form_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def update_skill(
        self,
        agent_id: str,
        skill_id: str,
        answers: Dict[str, Any]
    ) -> SkillFiles:
        """
        Update an existing skill with new answers.

        Args:
            agent_id: Agent ID
            skill_id: Skill ID to update
            answers: Updated answers

        Returns:
            Regenerated skill files
        """
        # Find the actual skill directory (may differ from skill_id if auto-generated)
        skill_dir = self._find_skill_dir(agent_id, skill_id)
        if not skill_dir:
            raise ValueError(f"Skill {skill_id} not found")

        editor_form_path = skill_dir / self.EDITOR_FORM_FILENAME
        if not editor_form_path.exists():
            raise ValueError(f"Skill {skill_id} was not created with the editor")

        # Load existing form
        with open(editor_form_path, 'r', encoding='utf-8') as f:
            editor_data = json.load(f)

        # Reconstruct form
        form_data = editor_data["form"]
        hypothesis = SkillHypothesis(**form_data["hypothesis"])
        fields = [FormField(
            id=f["id"],
            type=FormFieldType(f["type"]),
            question=f["question"],
            description=f.get("description"),
            options=f.get("options"),
            default=f.get("default"),
            required=f.get("required", True),
            placeholder=f.get("placeholder")
        ) for f in form_data["fields"]]

        form = EditorForm(
            hypothesis=hypothesis,
            fields=fields,
            created_at=form_data["created_at"]
        )

        # Merge old answers with new
        merged_answers = {**editor_data.get("answers", {}), **answers}

        # Regenerate skill
        skill_files = self.generate_skill(form, merged_answers, skill_id)

        # Save updated skill to the SAME directory (not a new one)
        self._save_skill_to_dir(skill_dir, skill_files, form, merged_answers)

        return skill_files

    def _save_skill_to_dir(
        self,
        skill_dir: Path,
        skill_files: SkillFiles,
        form: EditorForm,
        answers: Dict[str, Any]
    ) -> None:
        """Save skill files to a specific directory."""
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Save skill.json
        with open(skill_dir / "skill.json", 'w', encoding='utf-8') as f:
            json.dump(skill_files.skill_json, f, indent=2)

        # Save skill.md
        with open(skill_dir / "skill.md", 'w', encoding='utf-8') as f:
            f.write(skill_files.skill_md)

        # Save additional files
        for filename, content in skill_files.additional_files.items():
            with open(skill_dir / filename, 'w', encoding='utf-8') as f:
                f.write(content)

        # Save editor form for future editing
        editor_data = {
            "form": form.to_dict(),
            "answers": answers,
            "last_edited": datetime.now().isoformat()
        }
        with open(skill_dir / self.EDITOR_FORM_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(editor_data, f, indent=2)

    def list_editable_skills(self, agent_id: str) -> List[Dict[str, Any]]:
        """
        List skills that were created with the editor (editable).

        Args:
            agent_id: Agent ID

        Returns:
            List of skill info dicts
        """
        skills_dir = self.agents_dir / agent_id / "skills"
        if not skills_dir.exists():
            return []

        editable = []
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            editor_form_path = skill_dir / self.EDITOR_FORM_FILENAME
            skill_json_path = skill_dir / "skill.json"

            if editor_form_path.exists() and skill_json_path.exists():
                with open(skill_json_path, 'r', encoding='utf-8') as f:
                    skill_data = json.load(f)
                with open(editor_form_path, 'r', encoding='utf-8') as f:
                    editor_data = json.load(f)

                editable.append({
                    "skill_id": skill_dir.name,
                    "name": skill_data.get("name"),
                    "description": skill_data.get("description"),
                    "last_edited": editor_data.get("last_edited"),
                    "created_at": editor_data.get("form", {}).get("created_at")
                })

        return editable


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_skill_editor(llm_client=None, agents_dir: str = "./data/AGENTS") -> SkillEditor:
    """
    Create a skill editor instance.

    Args:
        llm_client: Optional LLM client (auto-initialized if not provided)
        agents_dir: Base directory for agent data

    Returns:
        SkillEditor instance
    """
    if llm_client is None:
        from llm_client import get_default_client
        llm_client = get_default_client()

    return SkillEditor(llm_client, agents_dir)


# ============================================================================
# CLI USAGE
# ============================================================================

if __name__ == "__main__":
    print("Skill Editor Module")
    print("=" * 60)
    print("\nThis module provides AI-assisted skill creation.")
    print("\nUsage:")
    print("""
    from loop_core.skills.editor import SkillEditor

    # Initialize
    editor = SkillEditor(llm_client)

    # Generate hypothesis from intent
    form = editor.generate_hypothesis("I want a skill for summarizing documents")

    # Show form to user, collect answers...
    answers = {"input_format": "PDF", "summary_length": "short"}

    # Generate skill
    skill_files = editor.generate_skill(form, answers)

    # Save skill
    editor.save_skill("main", "doc_summarizer", skill_files, form, answers)
    """)
