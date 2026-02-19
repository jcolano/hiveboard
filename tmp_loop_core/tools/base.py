"""
TOOL_BASE
=========

Base classes and registry for the Agentic Loop tool system.

Tools are code-based capabilities that the LLM can invoke during agentic execution.
Each tool defines its schema (for the LLM to know how to call it) and an execute
method (for runtime).

Architecture
------------
::

    BaseTool (abstract)
    ├── definition property → ToolDefinition (name, description, parameters)
    └── execute(**kwargs)   → ToolResult (success, output, error, metadata)

    ToolRegistry
    ├── register(tool)      — Add tool to registry
    ├── execute(name, params) — Run tool with timeout (default 30s)
    ├── get_schemas()       — OpenAI function-calling format
    └── get_anthropic_schemas() — Anthropic tool-use format

Safety
------
- **Timeout**: Default 30s per tool execution via ThreadPoolExecutor (max 4 workers).
- **Output limiting**: Max 100KB per tool output (truncated with notice).
- **Error isolation**: All exceptions caught and returned as ToolResult with
  success=False, preventing tool failures from crashing the loop.

Execution Flow in the Loop
--------------------------
1. LLM generates tool call: ``{"tool": "file_read", "path": "..."}``
2. AgenticLoop calls ``tool_registry.execute(tool_name, parameters)``
3. Registry retrieves tool by name, executes with timeout
4. Tool returns ToolResult
5. Result injected back to LLM as a user message (not system message)

Usage::

    registry = ToolRegistry()
    registry.register(FileReadTool(allowed_paths=[...]))
    registry.register(HttpCallTool())

    result = registry.execute("file_read", {"path": "./data/SKILLS/moltbook/skill.md"})
"""

import signal
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError


# ============================================================================
# TOOL DEFINITION STRUCTURES
# ============================================================================

@dataclass
class ToolParameter:
    """Definition of a tool parameter."""
    name: str
    type: str  # "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    default: Optional[Any] = None
    items: Optional[Dict] = None  # For array types

    def to_schema(self) -> Dict:
        """Convert to JSON Schema format."""
        schema = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        if self.items and self.type == "array":
            schema["items"] = self.items
        if self.type == "object":
            schema["additionalProperties"] = True
        return schema


@dataclass
class ToolDefinition:
    """Complete tool definition for LLM."""
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)

    def to_schema(self) -> Dict:
        """
        Convert to JSON Schema for LLM tool calling.

        Returns OpenAI-compatible format that can be converted to Anthropic format.
        """
        properties = {}
        required = []

        for param in self.parameters:
            properties[param.name] = param.to_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }

    def to_anthropic_schema(self) -> Dict:
        """Convert to Anthropic tool format."""
        properties = {}
        required = []

        for param in self.parameters:
            properties[param.name] = param.to_schema()
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }


# ============================================================================
# TOOL RESULT
# ============================================================================

@dataclass
class ToolResult:
    """Result of tool execution."""
    success: bool
    output: str
    error: Optional[str] = None
    metadata: Optional[Dict] = None

    def to_dict(self) -> Dict:
        result = {
            "success": self.success,
            "output": self.output,
        }
        if self.error:
            result["error"] = self.error
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"[ERROR] {self.error or 'Unknown error'}"


# ============================================================================
# BASE TOOL CLASS
# ============================================================================

class BaseTool(ABC):
    """
    Base class for all tools.

    Subclasses must implement:
    - definition property: Returns ToolDefinition with schema
    - execute method: Performs the actual work
    """

    # Stored credentials — tools that need base_url/auth_token/workspace_id
    # can be pre-configured so the LLM doesn't need to provide them.
    _credentials: Optional[Dict[str, str]] = None

    def set_credentials(self, **creds) -> None:
        """Store default credentials (base_url, auth_token, workspace_id, etc.).

        When set, execute() implementations should use these instead of
        whatever the LLM provides — the LLM often hallucinates placeholders.
        """
        self._credentials = dict(creds)

    def _apply_credentials(self, kwargs: dict) -> dict:
        """Override LLM-provided credentials with stored defaults.

        Returns a new dict with base_url, auth_token, workspace_id,
        and mail_account_id replaced by stored values (if set).
        """
        if not self._credentials:
            return kwargs
        out = dict(kwargs)
        for key in ("base_url", "auth_token", "workspace_id", "mail_account_id"):
            if key in self._credentials and self._credentials[key]:
                out[key] = self._credentials[key]
        return out

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the tool's definition (schema for LLM)."""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Parameters as defined in the tool's schema

        Returns:
            ToolResult with success status and output
        """
        pass

    @property
    def name(self) -> str:
        """Get tool name from definition."""
        return self.definition.name

    def get_schema(self) -> Dict:
        """Get the tool schema in OpenAI format."""
        return self.definition.to_schema()

    def get_anthropic_schema(self) -> Dict:
        """Get the tool schema in Anthropic format."""
        return self.definition.to_anthropic_schema()


# ============================================================================
# TOOL REGISTRY
# ============================================================================

class ToolRegistry:
    """
    Registry for managing and executing tools.

    Handles:
    - Tool registration and discovery
    - Schema generation for LLM
    - Tool execution by name with timeout
    - Output size limiting
    """

    DEFAULT_TIMEOUT = 30  # seconds
    MAX_OUTPUT_SIZE = 100000  # ~100KB

    def __init__(self, default_timeout: int = DEFAULT_TIMEOUT, max_output_size: int = MAX_OUTPUT_SIZE):
        self._tools: Dict[str, BaseTool] = {}
        self._extension_tools: set = set()  # tool names loaded from EXTENSIONS/
        self.default_timeout = default_timeout
        self.max_output_size = max_output_size
        self._executor = ThreadPoolExecutor(max_workers=4)

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def mark_as_extension(self, name: str, source_file: str = "") -> None:
        """Mark a tool as an extension (loaded from EXTENSIONS/ folder)."""
        self._extension_tools.add(name)
        tool = self._tools.get(name)
        if tool and source_file:
            tool._extension_source_file = source_file

    def is_extension(self, name: str) -> bool:
        """Check if a tool is an extension."""
        return name in self._extension_tools

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_schemas(self, format: str = "openai") -> List[Dict]:
        """
        Get all tool schemas for LLM.

        Args:
            format: "openai" or "anthropic"

        Returns:
            List of tool schemas
        """
        schemas = []
        for tool in self._tools.values():
            if format == "anthropic":
                schemas.append(tool.get_anthropic_schema())
            else:
                schemas.append(tool.get_schema())
        return schemas

    def get_enabled_schemas(self, enabled_tools: List[str], format: str = "openai") -> List[Dict]:
        """
        Get schemas only for enabled tools.

        Args:
            enabled_tools: List of tool names to include
            format: "openai" or "anthropic"

        Returns:
            List of tool schemas for enabled tools
        """
        schemas = []
        for name in enabled_tools:
            tool = self._tools.get(name)
            if tool:
                if format == "anthropic":
                    schemas.append(tool.get_anthropic_schema())
                else:
                    schemas.append(tool.get_schema())
        return schemas

    def execute(self, tool_name: str, parameters: Dict, timeout: int = None) -> ToolResult:
        """
        Execute a tool by name with parameters.

        Args:
            tool_name: Name of the tool to execute
            parameters: Parameters to pass to the tool
            timeout: Timeout in seconds (uses default if None)

        Returns:
            ToolResult from tool execution
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown tool: {tool_name}"
            )

        timeout = timeout or self.default_timeout

        try:
            # Execute with timeout using thread pool
            future = self._executor.submit(tool.execute, **parameters)
            result = future.result(timeout=timeout)

            # Truncate output if too large
            if result.output and len(result.output) > self.max_output_size:
                result = ToolResult(
                    success=result.success,
                    output=result.output[:self.max_output_size] + f"\n\n[TRUNCATED - output exceeded {self.max_output_size} characters]",
                    error=result.error,
                    metadata={
                        **(result.metadata or {}),
                        "truncated": True,
                        "original_size": len(result.output)
                    }
                )

            return result

        except FuturesTimeoutError:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{tool_name}' timed out after {timeout} seconds"
            )
        except TypeError as e:
            # Parameter mismatch
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid parameters for {tool_name}: {e}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool execution error: {str(e)}"
            )

    def get_tool_descriptions(self) -> str:
        """Get formatted descriptions of all tools for display."""
        lines = []
        for name, tool in self._tools.items():
            defn = tool.definition
            lines.append(f"- {name}: {defn.description}")
        return "\n".join(lines)

    def get_tool_summaries(self) -> List[Dict[str, str]]:
        """Return [{name, description}] for all tools -- no schemas.

        Used by the atomic agentic loop Phase 1, where the LLM only needs
        to know tool names and one-line descriptions to pick which tool to
        call, without the full parameter schemas.
        """
        summaries = []
        for name, tool in self._tools.items():
            defn = tool.definition
            summaries.append({"name": name, "description": defn.description})
        return summaries

    def get_tool_details(self) -> List[Dict]:
        """Return detailed info for all tools -- used by admin panel GET /tools."""
        details = []
        for name, tool in self._tools.items():
            defn = tool.definition
            params = []
            for p in defn.parameters:
                param_info = {"name": p.name, "type": p.type, "description": p.description}
                if not p.required:
                    param_info["required"] = False
                else:
                    param_info["required"] = True
                if p.enum:
                    param_info["enum"] = p.enum
                params.append(param_info)
            entry = {
                "name": name,
                "description": defn.description,
                "type": "extension" if name in self._extension_tools else "built-in",
                "parameters": params,
            }
            source_file = getattr(tool, "_extension_source_file", None)
            if source_file:
                entry["source_file"] = source_file
            details.append(entry)
        return details

    def get_single_schema(self, tool_name: str, format: str = "anthropic") -> Optional[Dict]:
        """Return the full schema for ONE tool by name.

        Used by the atomic agentic loop Phase 2, where only the selected
        tool's schema is sent to the LLM for parameter generation.

        Args:
            tool_name: Name of the tool
            format: "openai" or "anthropic"

        Returns:
            Tool schema dict, or None if tool not found
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return None
        if format == "anthropic":
            return tool.get_anthropic_schema()
        return tool.get_schema()


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Agentic Loop Tool System")
    print("=" * 60)

    # Create a simple test tool
    class EchoTool(BaseTool):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name="echo",
                description="Echo back the input message",
                parameters=[
                    ToolParameter(
                        name="message",
                        type="string",
                        description="The message to echo"
                    )
                ]
            )

        def execute(self, message: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"Echo: {message}",
                metadata={"length": len(message)}
            )

    # Test registry
    registry = ToolRegistry()
    registry.register(EchoTool())

    print(f"Registered tools: {registry.list_tools()}")
    print(f"\nTool descriptions:\n{registry.get_tool_descriptions()}")

    # Get schemas
    print(f"\n--- OpenAI Schema ---")
    import json
    schemas = registry.get_schemas("openai")
    print(json.dumps(schemas, indent=2))

    print(f"\n--- Anthropic Schema ---")
    schemas = registry.get_schemas("anthropic")
    print(json.dumps(schemas, indent=2))

    # Execute tool
    print(f"\n--- Execute Tool ---")
    result = registry.execute("echo", {"message": "Hello, World!"})
    print(f"Success: {result.success}")
    print(f"Output: {result.output}")
    print(f"Metadata: {result.metadata}")

    # Test unknown tool
    print(f"\n--- Unknown Tool ---")
    result = registry.execute("unknown", {})
    print(f"Success: {result.success}")
    print(f"Error: {result.error}")
