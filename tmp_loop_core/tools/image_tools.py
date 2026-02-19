"""
IMAGE_TOOLS
===========

Image generation tool for the Agentic Loop Framework.

``image_generate``
    Generate images using Google Gemini models. Two model options:
    - "flash" (gemini-2.5-flash-image): Fast generation, no size control
    - "pro" (gemini-3-pro-image-preview): Best quality, configurable
      aspect ratio and resolution

    Requires ``google-genai`` and ``Pillow`` packages. API key is read
    at execution time from ``apikeys/api_gemini.key``.

Usage::

    tool = ImageGenerateTool(agent_dir="/path/to/agent")
    result = tool.execute(
        prompt="A modern office building at sunset",
        output_path="office.jpg",
        model="flash",
    )
"""

import os
from pathlib import Path
from typing import Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# Resolve apikeys dir relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_API_KEY_PATH = _PROJECT_ROOT / "apikeys" / "api_gemini.key"


class ImageGenerateTool(BaseTool):
    """Generate images using Google Gemini."""

    def __init__(self, agent_dir: str):
        self.agent_dir = Path(agent_dir)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="image_generate",
            description=(
                "Generate an image from a text prompt using Google Gemini. "
                "Use 'flash' for fast generation or 'pro' for higher quality "
                "with configurable aspect ratio and resolution. Output is JPEG."
            ),
            parameters=[
                ToolParameter(
                    name="prompt",
                    type="string",
                    description="Detailed description of the image to generate",
                ),
                ToolParameter(
                    name="output_path",
                    type="string",
                    description="Filename relative to agent's runs directory (e.g. 'banner.jpg')",
                ),
                ToolParameter(
                    name="model",
                    type="string",
                    description="Gemini model: 'flash' (fast) or 'pro' (quality, supports aspect_ratio/resolution)",
                    required=False,
                    enum=["flash", "pro"],
                    default="flash",
                ),
                ToolParameter(
                    name="aspect_ratio",
                    type="string",
                    description="Aspect ratio (pro model only): '1:1', '3:2', '4:3', '5:4', '16:9', '9:16'",
                    required=False,
                ),
                ToolParameter(
                    name="resolution",
                    type="string",
                    description="Image resolution (pro model only): '1K', '2K', '4K'",
                    required=False,
                ),
                ToolParameter(
                    name="jpeg_quality",
                    type="integer",
                    description="JPEG quality 0-100",
                    required=False,
                    default=70,
                ),
            ],
        )

    def execute(
        self,
        prompt: str,
        output_path: str,
        model: str = "flash",
        aspect_ratio: str = None,
        resolution: str = None,
        jpeg_quality: int = 70,
        **kwargs,
    ) -> ToolResult:
        # Read API key at execution time
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

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="google-genai package not installed. Run: pip install google-genai",
            )

        try:
            from PIL import Image
            import io
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="Pillow package not installed. Run: pip install Pillow",
            )

        jpeg_quality = max(1, min(100, jpeg_quality))

        try:
            client = genai.Client(api_key=api_key)
            image_bytes = None

            if model == "pro":
                config = types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                )
                image_config_kwargs = {}
                if aspect_ratio:
                    image_config_kwargs["aspect_ratio"] = aspect_ratio
                if resolution:
                    image_config_kwargs["image_size"] = resolution
                if image_config_kwargs:
                    config.image_config = types.ImageConfig(**image_config_kwargs)

                response = client.models.generate_content(
                    model="gemini-3-pro-image-preview",
                    contents=[prompt],
                    config=config,
                )

                for part in response.parts:
                    if hasattr(part, "as_image"):
                        image = part.as_image()
                        if image:
                            image_bytes = image.image_bytes
                            break
            else:
                # Flash model
                response = client.models.generate_content(
                    model="gemini-2.5-flash-image",
                    contents=[prompt],
                )

                for part in response.parts:
                    if part.inline_data is not None:
                        image_bytes = part.inline_data.data
                        break

            if not image_bytes:
                return ToolResult(
                    success=False,
                    output="",
                    error="No image data in Gemini response. The model may have refused the prompt.",
                )

            # Save as JPEG
            full_path = self.agent_dir / "runs" / output_path
            full_path.parent.mkdir(parents=True, exist_ok=True)

            img = Image.open(io.BytesIO(image_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(full_path, format="JPEG", quality=jpeg_quality)

            size = full_path.stat().st_size
            return ToolResult(
                success=True,
                output=f"Image saved to {output_path} ({size} bytes, {img.size[0]}x{img.size[1]})",
                metadata={
                    "path": str(full_path),
                    "model": model,
                    "size_bytes": size,
                    "dimensions": f"{img.size[0]}x{img.size[1]}",
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Image generation failed: {str(e)}",
            )
