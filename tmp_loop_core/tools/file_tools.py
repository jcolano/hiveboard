"""
FILE_TOOLS
==========

File read and write tools for the Agentic Loop Framework.

These tools are **sandboxed per-agent** — they can only access files within
explicitly allowed directories. This is critical for security in a multi-agent
system where agents should not read each other's data.

Security Model
--------------
- **Path sandboxing**: Each tool instance receives a list of allowed_paths.
  Any path outside these directories is rejected.
- **Symlink validation**: Resolved paths checked against allowed directories
  to prevent symlink escapes.
- **Path traversal prevention**: ``..`` segments are resolved before checking.
- **Max file size**: file_read truncates at 100KB with a notice.

Per-Agent Allowed Paths (set by AgentManager)
----------------------------------------------
- ``file_read``: skills_dir, agent/memory, agent/runs, sandbox
- ``file_write``: agent/memory, agent/runs, sandbox

This means agents can read skill files (to follow skill.md instructions) and
their own memory/runs, but cannot read other agents' directories.

Why file_read matters for skills
---------------------------------
Skills are NOT embedded in the system prompt — only their paths are listed.
The agent must use ``file_read`` to read skill.md before following it. This
keeps token usage low and allows skills to be updated without redeploying.

Usage::

    read_tool = FileReadTool(allowed_paths=["./data/SKILLS", "./data/AGENTS/main/memory"])
    result = read_tool.execute(path="./data/SKILLS/loopcolony/skill.md")

    write_tool = FileWriteTool(allowed_paths=["./data/AGENTS/main/runs"])
    result = write_tool.execute(path="./data/AGENTS/main/runs/result.txt", content="Hello!")
"""

from pathlib import Path
from typing import List

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


# ============================================================================
# FILE READ TOOL
# ============================================================================

class FileReadTool(BaseTool):
    """
    Read files from allowed directories.

    Security: Only allows reading from explicitly configured paths.
    """

    def __init__(self, allowed_paths: List[str] = None, agent_base_dir: str = None):
        """
        Initialize the file read tool.

        Args:
            allowed_paths: List of directory paths that are allowed for reading.
                          Supports both absolute and relative paths.
            agent_base_dir: Base directory for resolving relative paths (e.g. agent dir).
                           If set, relative paths like "memory/x.json" resolve here
                           before falling back to CWD.
        """
        self.allowed_paths = []
        self.agent_base_dir = Path(agent_base_dir).resolve() if agent_base_dir else None
        if allowed_paths:
            for p in allowed_paths:
                try:
                    self.allowed_paths.append(Path(p).resolve())
                except Exception:
                    pass

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_read",
            description="Read content from a file. Only reads from allowed directories. Supports text files (.md, .txt, .json, .csv, .py, etc.).",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path to the file to read"
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="File encoding (default: utf-8)",
                    required=False,
                    default="utf-8"
                )
            ]
        )

    def execute(self, path: str, encoding: str = "utf-8") -> ToolResult:
        """
        Read file content.

        Args:
            path: Path to the file to read
            encoding: File encoding (default: utf-8)

        Returns:
            ToolResult with file content or error
        """
        try:
            # Try agent-relative resolution first for relative paths
            raw = Path(path)
            if self.agent_base_dir and not raw.is_absolute():
                candidate = (self.agent_base_dir / raw).resolve()
                if self._is_allowed(candidate):
                    file_path = candidate
                else:
                    file_path = raw.resolve()
            else:
                file_path = raw.resolve()

            # Security: Check if path is within allowed directories
            if not self._is_allowed(file_path):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: {path} is outside allowed directories"
                )

            if not file_path.exists():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"File not found: {path}"
                )

            if not file_path.is_file():
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Not a file: {path}"
                )

            # Read file content
            content = file_path.read_text(encoding=encoding)

            # Limit content size to prevent context overflow
            max_size = 100000  # ~100KB
            if len(content) > max_size:
                content = content[:max_size] + f"\n\n[TRUNCATED - file exceeds {max_size} characters]"

            return ToolResult(
                success=True,
                output=content,
                metadata={
                    "path": str(file_path),
                    "size_bytes": file_path.stat().st_size,
                    "extension": file_path.suffix,
                    "truncated": len(content) > max_size
                }
            )

        except UnicodeDecodeError as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot decode file with {encoding} encoding: {e}"
            )
        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {path}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Error reading file: {str(e)}"
            )

    def _is_allowed(self, file_path: Path) -> bool:
        """
        Check if path is within allowed directories.

        Security checks:
        - Path must be within allowed directories
        - No symlinks pointing outside allowed directories
        - Path traversal (..) is blocked by resolve()
        """
        if not self.allowed_paths:
            # If no paths configured, deny all
            return False

        # Check for symlinks pointing outside allowed paths
        if file_path.is_symlink():
            try:
                real_path = file_path.resolve(strict=False)
                # Symlink target must also be in allowed paths
                if not self._path_in_allowed(real_path):
                    return False
            except (OSError, RuntimeError):
                # Broken symlink or circular - deny
                return False

        return self._path_in_allowed(file_path)

    def _path_in_allowed(self, file_path: Path) -> bool:
        """Check if path is within any allowed directory."""
        for allowed in self.allowed_paths:
            try:
                file_path.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    def add_allowed_path(self, path: str) -> None:
        """Add a path to the allowed list."""
        try:
            self.allowed_paths.append(Path(path).resolve())
        except Exception:
            pass


# ============================================================================
# FILE WRITE TOOL
# ============================================================================

class FileWriteTool(BaseTool):
    """
    Write files to allowed directories.

    Security: Only allows writing to explicitly configured paths.
    Creates parent directories if needed.
    """

    def __init__(self, allowed_paths: List[str] = None, agent_base_dir: str = None):
        """
        Initialize the file write tool.

        Args:
            allowed_paths: List of directory paths that are allowed for writing.
            agent_base_dir: Base directory for resolving relative paths (e.g. agent dir).
        """
        self.allowed_paths = []
        self.agent_base_dir = Path(agent_base_dir).resolve() if agent_base_dir else None
        if allowed_paths:
            for p in allowed_paths:
                try:
                    self.allowed_paths.append(Path(p).resolve())
                except Exception:
                    pass

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_write",
            description="Write content to a file. Creates parent directories if needed. Only writes to allowed directories.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path to the file to write"
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="Content to write to the file"
                ),
                ToolParameter(
                    name="mode",
                    type="string",
                    description="Write mode: 'overwrite' (replace) or 'append' (add to end)",
                    required=False,
                    enum=["overwrite", "append"],
                    default="overwrite"
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="File encoding (default: utf-8)",
                    required=False,
                    default="utf-8"
                )
            ]
        )

    def execute(
        self,
        path: str,
        content: str,
        mode: str = "overwrite",
        encoding: str = "utf-8"
    ) -> ToolResult:
        """
        Write content to file.

        Args:
            path: Path to the file to write
            content: Content to write
            mode: "overwrite" or "append"
            encoding: File encoding (default: utf-8)

        Returns:
            ToolResult with success status
        """
        try:
            # Try agent-relative resolution first for relative paths
            raw = Path(path)
            if self.agent_base_dir and not raw.is_absolute():
                candidate = (self.agent_base_dir / raw).resolve()
                if self._is_allowed(candidate):
                    file_path = candidate
                else:
                    file_path = raw.resolve()
            else:
                file_path = raw.resolve()

            # Security: Check if path is within allowed directories
            if not self._is_allowed(file_path):
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: {path} is outside allowed directories"
                )

            # Create parent directories
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            if mode == "append":
                with open(file_path, 'a', encoding=encoding) as f:
                    f.write(content)
            else:
                file_path.write_text(content, encoding=encoding)

            return ToolResult(
                success=True,
                output=f"Successfully wrote {len(content)} characters to {path}",
                metadata={
                    "path": str(file_path),
                    "size_bytes": len(content.encode(encoding)),
                    "mode": mode
                }
            )

        except PermissionError:
            return ToolResult(
                success=False,
                output="",
                error=f"Permission denied: {path}"
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Error writing file: {str(e)}"
            )

    def _is_allowed(self, file_path: Path) -> bool:
        """
        Check if path is within allowed directories.

        Security checks:
        - Path must be within allowed directories
        - No symlinks pointing outside allowed directories
        - Parent directory symlinks are also checked
        - Path traversal (..) is blocked by resolve()
        """
        if not self.allowed_paths:
            return False

        # For writes, also check parent directories for symlinks
        # that could escape the sandbox
        current = file_path
        while current != current.parent:
            if current.exists() and current.is_symlink():
                try:
                    real_path = current.resolve(strict=False)
                    if not self._path_in_allowed(real_path):
                        return False
                except (OSError, RuntimeError):
                    return False
            current = current.parent

        return self._path_in_allowed(file_path)

    def _path_in_allowed(self, file_path: Path) -> bool:
        """Check if path is within any allowed directory."""
        for allowed in self.allowed_paths:
            try:
                file_path.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    def add_allowed_path(self, path: str) -> None:
        """Add a path to the allowed list."""
        try:
            self.allowed_paths.append(Path(path).resolve())
        except Exception:
            pass


# ============================================================================
# FACTORY FUNCTIONS
# ============================================================================

def create_file_read_tool(config: dict = None) -> FileReadTool:
    """
    Create a FileReadTool with configuration.

    Args:
        config: Tool configuration with 'allowed_paths' key

    Returns:
        Configured FileReadTool instance
    """
    allowed_paths = []
    if config:
        allowed_paths = config.get("allowed_paths", [])
    return FileReadTool(allowed_paths=allowed_paths)


def create_file_write_tool(config: dict = None) -> FileWriteTool:
    """
    Create a FileWriteTool with configuration.

    Args:
        config: Tool configuration with 'allowed_paths' key

    Returns:
        Configured FileWriteTool instance
    """
    allowed_paths = []
    if config:
        allowed_paths = config.get("allowed_paths", [])
    return FileWriteTool(allowed_paths=allowed_paths)


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    import tempfile
    import os

    print("Agentic Loop File Tools")
    print("=" * 60)

    # Create temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Test directory: {tmpdir}")

        # Create tools with temp dir allowed
        read_tool = FileReadTool(allowed_paths=[tmpdir])
        write_tool = FileWriteTool(allowed_paths=[tmpdir])

        print(f"\n--- Tool Definitions ---")
        print(f"Read tool: {read_tool.name}")
        print(f"Write tool: {write_tool.name}")

        # Test write
        print(f"\n--- Test Write ---")
        test_file = os.path.join(tmpdir, "test.txt")
        result = write_tool.execute(
            path=test_file,
            content="Hello, World!\nThis is a test."
        )
        print(f"Success: {result.success}")
        print(f"Output: {result.output}")

        # Test read
        print(f"\n--- Test Read ---")
        result = read_tool.execute(path=test_file)
        print(f"Success: {result.success}")
        print(f"Content: {result.output}")
        print(f"Metadata: {result.metadata}")

        # Test append
        print(f"\n--- Test Append ---")
        result = write_tool.execute(
            path=test_file,
            content="\nAppended line.",
            mode="append"
        )
        print(f"Success: {result.success}")

        result = read_tool.execute(path=test_file)
        print(f"Content after append:\n{result.output}")

        # Test security - access denied
        print(f"\n--- Test Security (Access Denied) ---")
        result = read_tool.execute(path="/etc/passwd")
        print(f"Success: {result.success}")
        print(f"Error: {result.error}")

        # Test file not found
        print(f"\n--- Test File Not Found ---")
        result = read_tool.execute(path=os.path.join(tmpdir, "nonexistent.txt"))
        print(f"Success: {result.success}")
        print(f"Error: {result.error}")

        # Test schemas
        print(f"\n--- OpenAI Schema ---")
        import json
        print(json.dumps(read_tool.get_schema(), indent=2))
