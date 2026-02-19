"""
EXTRACT_TOOLS
=============

Structured document extraction tool for the Agentic Loop Framework.

``document_extract``
    Extract structured data from text using few-shot examples and an LLM.
    Wraps the vendored langextract library. Useful for resume screening,
    contract analysis, entity extraction, and other B2B skill templates.

    Requires a Gemini API key at ``apikeys/api_gemini.key``.

Usage::

    tool = DocumentExtractTool(agent_dir="/path/to/agent")
    result = tool.execute(
        text="John Smith is a 30-year-old engineer at Google.",
        prompt_description="Extract all people and their roles",
        examples=[{
            "text": "Jane Doe is a designer at Apple.",
            "extractions": [{"class": "person", "value": "Jane Doe",
                             "attributes": {"company": "Apple", "role": "designer"}}]
        }],
    )
"""

import json
from pathlib import Path
from typing import List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# Resolve apikeys dir relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_API_KEY_PATH = _PROJECT_ROOT / "apikeys" / "api_gemini.key"


class DocumentExtractTool(BaseTool):
    """Extract structured data from text using few-shot examples."""

    def __init__(self, agent_dir: str):
        self.agent_dir = Path(agent_dir)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="document_extract",
            description=(
                "Extract structured data from text using few-shot examples and an LLM. "
                "Provide the text to analyze, a description of what to extract, and "
                "example extractions. Returns classified entities with attributes and "
                "character positions. Useful for resume parsing, contract analysis, "
                "entity extraction, and similar tasks."
            ),
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="The text to extract structured data from",
                ),
                ToolParameter(
                    name="prompt_description",
                    type="string",
                    description=(
                        "Instructions for what to extract "
                        "(e.g. 'Extract all people mentioned and their roles')"
                    ),
                ),
                ToolParameter(
                    name="examples",
                    type="array",
                    description=(
                        'Few-shot examples as JSON array. Each element has "text" (string) '
                        'and "extractions" (array of {"class": str, "value": str, '
                        '"attributes": {str: str}})'
                    ),
                ),
                ToolParameter(
                    name="model_id",
                    type="string",
                    description="LLM model for extraction",
                    required=False,
                    default="gemini-2.5-flash",
                ),
                ToolParameter(
                    name="max_char_buffer",
                    type="integer",
                    description="Characters per inference chunk",
                    required=False,
                    default=1000,
                ),
                ToolParameter(
                    name="output_path",
                    type="string",
                    description="Save JSON results to file (relative to agent's runs dir)",
                    required=False,
                ),
                ToolParameter(
                    name="extraction_passes",
                    type="integer",
                    description="Number of extraction passes (higher = more thorough but slower)",
                    required=False,
                    default=1,
                ),
            ],
        )

    def execute(
        self,
        text: str,
        prompt_description: str,
        examples: list,
        model_id: str = "gemini-2.5-flash",
        max_char_buffer: int = 1000,
        output_path: Optional[str] = None,
        extraction_passes: int = 1,
        **kwargs,
    ) -> ToolResult:
        # Read API key
        if not _API_KEY_PATH.exists():
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Gemini API key not found at {_API_KEY_PATH}. "
                    "Create the file with your API key."
                ),
            )

        api_key = _API_KEY_PATH.read_text().strip()
        if not api_key:
            return ToolResult(
                success=False,
                output="",
                error="Gemini API key file is empty.",
            )

        # Import vendored langextract
        try:
            from loop_core.vendor.langextract.extraction import extract
            from loop_core.vendor.langextract.core.data import ExampleData, Extraction
        except ImportError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to import langextract: {e}",
            )

        # Convert example dicts to ExampleData objects
        try:
            example_objects = []
            for ex in examples:
                extractions = []
                for e in ex.get("extractions", []):
                    extractions.append(Extraction(
                        extraction_class=e["class"],
                        extraction_text=e["value"],
                        attributes=e.get("attributes"),
                    ))
                example_objects.append(ExampleData(
                    text=ex["text"],
                    extractions=extractions,
                ))
        except (KeyError, TypeError) as e:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Invalid examples format: {e}. Each example needs 'text' and "
                    "'extractions' (array of {{'class': str, 'value': str, 'attributes': {{...}}}})."
                ),
            )

        # Run extraction
        try:
            result = extract(
                text,
                prompt_description=prompt_description,
                examples=example_objects,
                api_key=api_key,
                model_id=model_id,
                max_char_buffer=max_char_buffer,
                extraction_passes=extraction_passes,
                show_progress=False,
                fetch_urls=False,
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Extraction failed: {e}",
            )

        # result is an AnnotatedDocument
        extractions = result.extractions or []

        # Format human-readable output
        lines = [f"Found {len(extractions)} extraction(s):"]
        json_results = []

        for i, ext in enumerate(extractions, 1):
            # Character span info
            span = ""
            if ext.char_interval and ext.char_interval.start_pos is not None:
                span = f" (chars {ext.char_interval.start_pos}-{ext.char_interval.end_pos}"
                if ext.alignment_status:
                    span += f", {ext.alignment_status.value}"
                span += ")"

            lines.append(f"\n{i}. [{ext.extraction_class}] \"{ext.extraction_text}\"{span}")

            if ext.attributes:
                for attr_key, attr_val in ext.attributes.items():
                    lines.append(f"   - {attr_key}: {attr_val}")

            # Build JSON record
            record = {
                "class": ext.extraction_class,
                "value": ext.extraction_text,
            }
            if ext.char_interval and ext.char_interval.start_pos is not None:
                record["char_start"] = ext.char_interval.start_pos
                record["char_end"] = ext.char_interval.end_pos
            if ext.alignment_status:
                record["alignment"] = ext.alignment_status.value
            if ext.attributes:
                record["attributes"] = ext.attributes
            json_results.append(record)

        formatted_text = "\n".join(lines)

        # Optionally save JSON results to file
        saved_path = None
        if output_path:
            full_path = self.agent_dir / "runs" / output_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(json.dumps(json_results, indent=2), encoding="utf-8")
            saved_path = str(full_path)
            formatted_text += f"\n\nResults saved to {output_path}"

        # Collect unique classes found
        classes_found = list(set(ext.extraction_class for ext in extractions))

        metadata = {
            "extraction_count": len(extractions),
            "classes_found": classes_found,
        }
        if saved_path:
            metadata["output_path"] = saved_path

        return ToolResult(
            success=True,
            output=formatted_text,
            metadata=metadata,
        )
