"""
Remote Tools for Desktop Clients.

These tools allow agents to perform operations on connected
desktop clients (read files, list directories, execute processes, etc.).

The tools queue requests to desktop clients and wait for responses
via the polling-based communication model.
"""

import asyncio
import uuid
from typing import Optional, List

from ..tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from .client_registry import get_client_registry
from .request_queue import get_request_queue, RequestType, RequestPriority
from .models import ResponseStatus


class RemoteFileReadTool(BaseTool):
    """
    Read a file from a connected desktop client.

    This tool requests file read access from a desktop client,
    which will prompt the user if necessary. The file content
    is returned once the client responds.
    """

    def __init__(self, default_timeout: int = 60):
        """
        Initialize the tool.

        Args:
            default_timeout: Default timeout in seconds for operations
        """
        self.default_timeout = default_timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="remote_file_read",
            description=(
                "Read a file from a user's local machine via their connected desktop client. "
                "The user will be prompted to approve access if not already granted. "
                "Use this when you need to access files on the user's computer."
            ),
            parameters=[
                ToolParameter(
                    name="client_id",
                    type="string",
                    description="ID of the desktop client to read from",
                    required=True
                ),
                ToolParameter(
                    name="path",
                    type="string",
                    description="Absolute path to the file on the client's machine",
                    required=True
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="File encoding (default: utf-8)",
                    required=False,
                    default="utf-8"
                ),
            ]
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute the file read operation."""
        client_id = kwargs.get("client_id")
        path = kwargs.get("path")
        encoding = kwargs.get("encoding", "utf-8")

        if not client_id:
            return ToolResult(
                success=False,
                output="",
                error="client_id is required"
            )

        if not path:
            return ToolResult(
                success=False,
                output="",
                error="path is required"
            )

        # Check if client is connected
        registry = get_client_registry()
        client = registry.get_client(client_id)

        if client is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Client not found: {client_id}"
            )

        if not client.is_online:
            return ToolResult(
                success=False,
                output="",
                error=f"Client is offline: {client_id}"
            )

        # Queue the operation request
        queue = get_request_queue()
        operation_id = f"op_{uuid.uuid4().hex[:12]}"

        # For now, we need a capability - in full implementation,
        # this would check/request capability first
        payload = {
            "operation_id": operation_id,
            "capability_id": "pending",  # Would be actual capability ID
            "operation": {
                "type": "file:read",
                "path": path,
                "encoding": encoding
            }
        }

        # Use synchronous callback approach for tool execution
        result_holder = {"status": None, "payload": None, "completed": False}

        def on_response(request_id, status, response_payload):
            result_holder["status"] = status
            result_holder["payload"] = response_payload
            result_holder["completed"] = True

        request_id = queue.queue_request(
            client_id=client_id,
            request_type=RequestType.OPERATION_EXECUTE,
            payload=payload,
            priority=RequestPriority.NORMAL,
            expiry_seconds=self.default_timeout,
            callback=on_response
        )

        # Wait for response (with timeout)
        # In a real async context, this would use await
        # For synchronous tool execution, we poll
        import time
        start_time = time.time()
        while not result_holder["completed"]:
            if time.time() - start_time > self.default_timeout:
                queue.cancel_request(request_id)
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Operation timed out after {self.default_timeout} seconds"
                )
            time.sleep(0.1)

        # Process response
        status = result_holder["status"]
        response = result_holder["payload"]

        if status == ResponseStatus.COMPLETED:
            content = response.get("content", "") if response else ""
            return ToolResult(
                success=True,
                output=content,
                metadata={
                    "path": path,
                    "client_id": client_id,
                    "encoding": encoding
                }
            )
        elif status == ResponseStatus.DENIED:
            return ToolResult(
                success=False,
                output="",
                error="User denied access to the file"
            )
        elif status == ResponseStatus.FAILED:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return ToolResult(
                success=False,
                output="",
                error=f"Operation failed: {error_msg}"
            )
        else:
            return ToolResult(
                success=False,
                output="",
                error=f"Unexpected response status: {status}"
            )


class RemoteDirectoryListTool(BaseTool):
    """
    List directory contents on a connected desktop client.

    This tool requests directory listing from a desktop client,
    which will prompt the user if necessary.
    """

    def __init__(self, default_timeout: int = 60):
        self.default_timeout = default_timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="remote_directory_list",
            description=(
                "List files and directories in a folder on the user's local machine "
                "via their connected desktop client. The user will be prompted to approve "
                "access if not already granted."
            ),
            parameters=[
                ToolParameter(
                    name="client_id",
                    type="string",
                    description="ID of the desktop client",
                    required=True
                ),
                ToolParameter(
                    name="path",
                    type="string",
                    description="Absolute path to the directory",
                    required=True
                ),
                ToolParameter(
                    name="recursive",
                    type="boolean",
                    description="Whether to list subdirectories recursively",
                    required=False,
                    default=False
                ),
                ToolParameter(
                    name="pattern",
                    type="string",
                    description="Glob pattern to filter results (e.g., '*.py')",
                    required=False
                ),
            ]
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute the directory listing operation."""
        client_id = kwargs.get("client_id")
        path = kwargs.get("path")
        recursive = kwargs.get("recursive", False)
        pattern = kwargs.get("pattern")

        if not client_id or not path:
            return ToolResult(
                success=False,
                output="",
                error="client_id and path are required"
            )

        # Check client status
        registry = get_client_registry()
        client = registry.get_client(client_id)

        if client is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Client not found: {client_id}"
            )

        if not client.is_online:
            return ToolResult(
                success=False,
                output="",
                error=f"Client is offline: {client_id}"
            )

        # Queue the operation
        queue = get_request_queue()
        operation_id = f"op_{uuid.uuid4().hex[:12]}"

        payload = {
            "operation_id": operation_id,
            "capability_id": "pending",
            "operation": {
                "type": "directory:list",
                "path": path,
                "recursive": recursive,
                "pattern": pattern
            }
        }

        result_holder = {"status": None, "payload": None, "completed": False}

        def on_response(request_id, status, response_payload):
            result_holder["status"] = status
            result_holder["payload"] = response_payload
            result_holder["completed"] = True

        request_id = queue.queue_request(
            client_id=client_id,
            request_type=RequestType.OPERATION_EXECUTE,
            payload=payload,
            priority=RequestPriority.NORMAL,
            expiry_seconds=self.default_timeout,
            callback=on_response
        )

        # Wait for response
        import time
        start_time = time.time()
        while not result_holder["completed"]:
            if time.time() - start_time > self.default_timeout:
                queue.cancel_request(request_id)
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Operation timed out after {self.default_timeout} seconds"
                )
            time.sleep(0.1)

        status = result_holder["status"]
        response = result_holder["payload"]

        if status == ResponseStatus.COMPLETED:
            entries = response.get("entries", []) if response else []
            # Format output as a readable list
            output_lines = []
            for entry in entries:
                entry_type = "DIR " if entry.get("is_directory") else "FILE"
                name = entry.get("name", "")
                size = entry.get("size", "")
                size_str = f" ({size} bytes)" if size and not entry.get("is_directory") else ""
                output_lines.append(f"{entry_type} {name}{size_str}")

            return ToolResult(
                success=True,
                output="\n".join(output_lines) if output_lines else "(empty directory)",
                metadata={
                    "path": path,
                    "client_id": client_id,
                    "entry_count": len(entries),
                    "recursive": recursive
                }
            )
        elif status == ResponseStatus.DENIED:
            return ToolResult(
                success=False,
                output="",
                error="User denied access to the directory"
            )
        else:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return ToolResult(
                success=False,
                output="",
                error=f"Operation failed: {error_msg}"
            )


class RemoteProcessExecuteTool(BaseTool):
    """
    Execute a whitelisted process on a connected desktop client.

    Only executables that the user has pre-approved can be run.
    """

    def __init__(self, default_timeout: int = 120):
        self.default_timeout = default_timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="remote_process_execute",
            description=(
                "Execute a command on the user's local machine via their desktop client. "
                "Only whitelisted executables (like npm, git, python) can be run. "
                "The user must have pre-approved the executable."
            ),
            parameters=[
                ToolParameter(
                    name="client_id",
                    type="string",
                    description="ID of the desktop client",
                    required=True
                ),
                ToolParameter(
                    name="executable",
                    type="string",
                    description="Path or name of the executable (must be whitelisted)",
                    required=True
                ),
                ToolParameter(
                    name="args",
                    type="array",
                    description="Command line arguments",
                    required=False,
                    items={"type": "string"}
                ),
                ToolParameter(
                    name="working_directory",
                    type="string",
                    description="Working directory for the process",
                    required=False
                ),
                ToolParameter(
                    name="timeout",
                    type="integer",
                    description="Timeout in seconds (default: 120)",
                    required=False,
                    default=120
                ),
            ]
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute the process."""
        client_id = kwargs.get("client_id")
        executable = kwargs.get("executable")
        args = kwargs.get("args", [])
        working_directory = kwargs.get("working_directory")
        timeout = kwargs.get("timeout", self.default_timeout)

        if not client_id or not executable:
            return ToolResult(
                success=False,
                output="",
                error="client_id and executable are required"
            )

        # Check client status
        registry = get_client_registry()
        client = registry.get_client(client_id)

        if client is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Client not found: {client_id}"
            )

        if not client.is_online:
            return ToolResult(
                success=False,
                output="",
                error=f"Client is offline: {client_id}"
            )

        # Queue the operation
        queue = get_request_queue()
        operation_id = f"op_{uuid.uuid4().hex[:12]}"

        payload = {
            "operation_id": operation_id,
            "capability_id": "pending",
            "operation": {
                "type": "process:execute",
                "executable": executable,
                "args": args,
                "working_directory": working_directory,
                "timeout": timeout,
                "capture_output": True
            }
        }

        result_holder = {"status": None, "payload": None, "completed": False}

        def on_response(request_id, status, response_payload):
            result_holder["status"] = status
            result_holder["payload"] = response_payload
            result_holder["completed"] = True

        request_id = queue.queue_request(
            client_id=client_id,
            request_type=RequestType.OPERATION_EXECUTE,
            payload=payload,
            priority=RequestPriority.NORMAL,
            expiry_seconds=timeout + 10,  # Extra buffer
            callback=on_response
        )

        # Wait for response
        import time
        start_time = time.time()
        actual_timeout = timeout + 10
        while not result_holder["completed"]:
            if time.time() - start_time > actual_timeout:
                queue.cancel_request(request_id)
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Operation timed out after {timeout} seconds"
                )
            time.sleep(0.1)

        status = result_holder["status"]
        response = result_holder["payload"]

        if status == ResponseStatus.COMPLETED:
            stdout = response.get("stdout", "") if response else ""
            stderr = response.get("stderr", "") if response else ""
            exit_code = response.get("exit_code", -1) if response else -1

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"[stderr]\n{stderr}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            return ToolResult(
                success=exit_code == 0,
                output=output,
                error=None if exit_code == 0 else f"Process exited with code {exit_code}",
                metadata={
                    "exit_code": exit_code,
                    "executable": executable,
                    "client_id": client_id
                }
            )
        elif status == ResponseStatus.DENIED:
            return ToolResult(
                success=False,
                output="",
                error="User denied execution or executable is not whitelisted"
            )
        else:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return ToolResult(
                success=False,
                output="",
                error=f"Execution failed: {error_msg}"
            )


class ListConnectedClientsTool(BaseTool):
    """
    List all connected desktop clients.

    This tool helps agents discover which clients are available
    for remote operations.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_connected_clients",
            description=(
                "List all desktop clients connected to the system. "
                "Use this to discover which clients are available for remote file "
                "and process operations."
            ),
            parameters=[
                ToolParameter(
                    name="online_only",
                    type="boolean",
                    description="Only show currently online clients",
                    required=False,
                    default=True
                ),
            ]
        )

    def execute(self, **kwargs) -> ToolResult:
        """List connected clients."""
        online_only = kwargs.get("online_only", True)

        registry = get_client_registry()
        clients = registry.list_clients(online_only=online_only)

        if not clients:
            return ToolResult(
                success=True,
                output="No desktop clients connected.",
                metadata={"count": 0}
            )

        output_lines = ["Connected Desktop Clients:", ""]
        for client in clients:
            status = "🟢 Online" if client.is_online else "🔴 Offline"
            output_lines.append(
                f"  {client.client_id}: {client.platform} v{client.client_version} - {status}"
            )
            output_lines.append(
                f"    Capabilities: {client.active_capabilities_count}"
            )

        return ToolResult(
            success=True,
            output="\n".join(output_lines),
            metadata={
                "count": len(clients),
                "clients": [c.client_id for c in clients]
            }
        )


# Factory function to create all remote tools
def create_remote_tools(default_timeout: int = 60) -> List[BaseTool]:
    """
    Create instances of all remote desktop client tools.

    Args:
        default_timeout: Default timeout for operations

    Returns:
        List of tool instances
    """
    return [
        RemoteFileReadTool(default_timeout=default_timeout),
        RemoteDirectoryListTool(default_timeout=default_timeout),
        RemoteProcessExecuteTool(default_timeout=120),
        ListConnectedClientsTool(),
    ]
