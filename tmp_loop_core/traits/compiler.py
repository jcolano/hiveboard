"""
TRAIT COMPILER
==============

Translates agent trait values into system prompt modifications.

The compilation pipeline:
1. Check global kill switch
2. Check per-agent kill switch
3. For each trait: check per-trait kill switch, resolve knob contributions
4. Aggregate knob values via weighted average
5. Select prompt instructions from threshold templates
6. Assemble behavioral profile section

Knob resolution with multiple contributors:
    knob_value = sum(trait_value * weight) / sum(weight)

Inverse traits (e.g., agreeableness -> pushback_tendency):
    trait_value is inverted (1.0 - value) before contribution.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TraitCompiler:
    """Compiles agent trait values into system prompt behavioral instructions."""

    def __init__(self, traits_dir: Path):
        """
        Initialize the trait compiler by loading all config files.

        Args:
            traits_dir: Path to the traits config directory
                        (e.g., data/loopCore/CONFIG/traits/)
        """
        self.traits_dir = Path(traits_dir)
        self._system_config: Dict[str, Any] = {}
        self._registry: Dict[str, Any] = {}
        self._knobs: Dict[str, Dict[str, Any]] = {}
        self._templates: Dict[str, Any] = {}
        self._trait_definitions: Dict[str, Dict[str, Any]] = {}

        self._load_all()

    def _load_all(self) -> None:
        """Load all trait configuration files."""
        self._load_system_config()
        self._load_registry()
        self._load_knobs()
        self._load_templates()
        self._load_trait_definitions()

    def reload(self) -> None:
        """Hot-reload all configuration files."""
        self._load_all()

    def _load_json(self, filename: str) -> Dict:
        """Load a JSON file from the traits directory."""
        path = self.traits_dir / filename
        if not path.exists():
            logger.warning("Trait config file not found: %s", path)
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_system_config(self) -> None:
        """Load _system.json (global kill switch)."""
        data = self._load_json("_system.json")
        self._system_config = data.get("trait_system", {})

    def _load_registry(self) -> None:
        """Load _registry.json (trait index)."""
        self._registry = self._load_json("_registry.json")

    def _load_knobs(self) -> None:
        """Load _knobs.json and index by knob_id."""
        data = self._load_json("_knobs.json")
        self._knobs = {}
        for knob in data.get("knobs", []):
            self._knobs[knob["knob_id"]] = knob

    def _load_templates(self) -> None:
        """Load _compiler_templates.json."""
        data = self._load_json("_compiler_templates.json")
        self._templates = data.get("templates", {})

    def _load_trait_definitions(self) -> None:
        """Load each trait definition file listed in the registry."""
        self._trait_definitions = {}
        for entry in self._registry.get("traits", []):
            ref = entry["ref"]
            filename = entry["file"]
            trait_data = self._load_json(filename)
            if trait_data:
                self._trait_definitions[ref] = trait_data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return whether the global trait system is enabled."""
        return self._system_config.get("enabled", False)

    def get_default_trait_values(self) -> Dict[str, float]:
        """
        Return a dict of {trait_ref: default_value} for all active traits.

        Used when creating new agents to populate their initial trait values.
        """
        defaults = {}
        for ref, definition in self._trait_definitions.items():
            if definition.get("status") == "active":
                defaults[ref] = definition.get("default", 0.5)
        return defaults

    def get_active_trait_refs(self) -> List[str]:
        """Return list of refs for all active traits."""
        return [
            ref
            for ref, defn in self._trait_definitions.items()
            if defn.get("status") == "active"
        ]

    def compile(self, agent_id: str, traits_block: Dict[str, Any]) -> str:
        """
        Compile an agent's trait values into a behavioral prompt section.

        Args:
            agent_id: The agent's ID (for logging).
            traits_block: The agent's traits config dict, e.g.:
                {
                    "enabled": true,
                    "values": {"conscientiousness:1": 0.8, ...}
                }

        Returns:
            A string to append to the system prompt. Empty string if
            traits are disabled at any level or no instructions are generated.
        """
        # --- Global kill switch ---
        if not self.is_enabled():
            logger.debug("Trait system globally disabled, skipping agent '%s'", agent_id)
            return ""

        # --- Per-agent kill switch ---
        if not traits_block.get("enabled", True):
            logger.debug("Traits disabled for agent '%s'", agent_id)
            return ""

        values = traits_block.get("values", {})
        if not values:
            logger.debug("No trait values for agent '%s'", agent_id)
            return ""

        disabled_traits = set(self._system_config.get("disabled_traits", []))

        # --- Collect knob contributions ---
        # knob_id -> list of (effective_value, weight)
        knob_contributions: Dict[str, List[Tuple[float, float]]] = {}
        traits_processed = 0

        for trait_ref, trait_value in values.items():
            # Check per-trait kill switch
            if trait_ref in disabled_traits:
                logger.info(
                    "Trait '%s' skipped for agent '%s': globally disabled",
                    trait_ref,
                    agent_id,
                )
                continue

            # Look up trait definition
            definition = self._trait_definitions.get(trait_ref)
            if not definition:
                logger.warning(
                    "Trait '%s' for agent '%s': definition not found, skipping",
                    trait_ref,
                    agent_id,
                )
                continue

            status = definition.get("status", "active")
            if status == "deprecated":
                logger.warning(
                    "Trait '%s' for agent '%s' is deprecated; consider migrating to %s",
                    trait_ref,
                    agent_id,
                    definition.get("superseded_by", "N/A"),
                )
            elif status == "retired":
                logger.warning(
                    "Trait '%s' for agent '%s' is retired; still processing for compatibility",
                    trait_ref,
                    agent_id,
                )

            # Clamp trait value to defined range
            trait_range = definition.get("range", [0.0, 1.0])
            clamped_value = max(trait_range[0], min(trait_range[1], float(trait_value)))

            # Process behavioral mappings
            for mapping in definition.get("behavioral_mappings", []):
                knob_id = mapping["knob"]
                weight = mapping.get("weight", 1.0)
                effect = mapping.get("effect", "linear")

                # Apply inversion for linear_inverse
                effective_value = clamped_value
                if effect == "linear_inverse":
                    effective_value = 1.0 - clamped_value

                if knob_id not in knob_contributions:
                    knob_contributions[knob_id] = []
                knob_contributions[knob_id].append((effective_value, weight))

            traits_processed += 1

        if not knob_contributions:
            logger.info(
                "Trait compilation for agent '%s': no active knob contributions", agent_id
            )
            return ""

        # --- Resolve knob values via weighted average ---
        resolved_knobs: Dict[str, float] = {}
        for knob_id, contributions in knob_contributions.items():
            total_weighted = sum(v * w for v, w in contributions)
            total_weight = sum(w for _, w in contributions)

            if total_weight > 0:
                raw_value = total_weighted / total_weight
            else:
                raw_value = 0.5

            # Clamp to knob's value_range
            knob_def = self._knobs.get(knob_id, {})
            knob_range = knob_def.get("value_range", [0.0, 1.0])
            resolved = max(knob_range[0], min(knob_range[1], raw_value))
            resolved_knobs[knob_id] = resolved

            contributing = [
                f"{trait_ref}={val:.2f}*{w:.1f}"
                for (val, w), trait_ref in zip(
                    contributions,
                    [
                        tr
                        for tr in values
                        if any(
                            m["knob"] == knob_id
                            for m in self._trait_definitions.get(tr, {}).get(
                                "behavioral_mappings", []
                            )
                        )
                    ],
                )
            ]
            logger.debug(
                "Knob '%s' resolved to %.3f (contributors: %s)",
                knob_id,
                resolved,
                ", ".join(contributing),
            )

        # --- Select prompt instructions from templates ---
        instructions: List[str] = []
        for knob_id, knob_value in resolved_knobs.items():
            template = self._templates.get(knob_id)
            if not template:
                logger.warning("No template found for knob '%s'", knob_id)
                continue

            instruction = self._select_instruction(knob_id, knob_value, template)
            if instruction:
                instructions.append(instruction)

        if not instructions:
            logger.info(
                "Trait compilation for agent '%s': no instructions generated", agent_id
            )
            return ""

        # --- Assemble behavioral profile ---
        lines = [
            "## Behavioral Profile",
            "",
            "The following behavioral guidelines shape how you approach tasks. "
            "Follow them consistently.",
            "",
        ]
        for instruction in instructions:
            lines.append(f"- {instruction}")

        prompt = "\n".join(lines)

        logger.info(
            "Trait compilation complete for agent '%s': "
            "%d traits processed, %d knobs resolved, %d chars of prompt",
            agent_id,
            traits_processed,
            len(resolved_knobs),
            len(prompt),
        )

        return prompt

    def _select_instruction(
        self, knob_id: str, value: float, template: Dict[str, Any]
    ) -> Optional[str]:
        """
        Select the prompt instruction for a knob value from its template thresholds.

        The threshold ranges are checked with inclusive lower bound and exclusive
        upper bound, except the last range which is inclusive on both ends.
        """
        thresholds = template.get("thresholds", [])
        for i, threshold in enumerate(thresholds):
            range_bounds = threshold.get("range", [0.0, 1.0])
            low, high = range_bounds[0], range_bounds[1]

            # Last threshold: inclusive on both ends
            is_last = i == len(thresholds) - 1
            if is_last:
                if low <= value <= high:
                    instruction = threshold["instruction"]
                    logger.debug(
                        "Knob '%s' (%.3f) -> [%.1f, %.1f]: %s",
                        knob_id,
                        value,
                        low,
                        high,
                        instruction[:80],
                    )
                    return instruction
            else:
                if low <= value < high:
                    instruction = threshold["instruction"]
                    logger.debug(
                        "Knob '%s' (%.3f) -> [%.1f, %.1f): %s",
                        knob_id,
                        value,
                        low,
                        high,
                        instruction[:80],
                    )
                    return instruction

        logger.warning("No threshold matched for knob '%s' with value %.3f", knob_id, value)
        return None
