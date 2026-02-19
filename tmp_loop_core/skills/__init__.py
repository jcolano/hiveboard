"""
SKILLS MODULE
=============

Skill loading and management system for the Agentic Loop Framework.
Skills are markdown-based instructions that teach agents specific behaviors.

Includes:
- SkillLoader: Load and manage skills from directories
- SkillEditor: AI-assisted skill creation and editing
- SkillMatcher: Intelligent skill matching for tasks
"""

from .loader import (
    Skill,
    SkillLoader,
    SkillLoadError
)

from .registry import (
    AgentSkillRegistry,
    SkillRegistryEntry
)

from .editor import (
    SkillEditor,
    EditorForm,
    SkillHypothesis,
    SkillFiles,
    FormField,
    FormFieldType,
    create_skill_editor
)

from .matcher import (
    SkillMatcher,
    SkillMatch,
    create_skill_matcher
)

__all__ = [
    # Loader
    'Skill',
    'SkillLoader',
    'SkillLoadError',
    # Editor
    'SkillEditor',
    'EditorForm',
    'SkillHypothesis',
    'SkillFiles',
    'FormField',
    'FormFieldType',
    'create_skill_editor',
    # Matcher
    'SkillMatcher',
    'SkillMatch',
    'create_skill_matcher',
    # Registry
    'AgentSkillRegistry',
    'SkillRegistryEntry',
]
