"""
Desktop Client Module.

Provides the backend infrastructure for Windows desktop clients
to connect to loopCore and execute operations on behalf of agents.
"""

from .client_registry import (
    ClientRegistry,
    ConnectedClient,
    AuthToken,
    get_client_registry,
)

from .request_queue import (
    RequestQueue,
    RequestCallback,
    get_request_queue,
    queue_capability_request,
    request_capability_async,
    queue_operation,
    execute_operation_async,
)

from .tools import (
    RemoteFileReadTool,
    RemoteDirectoryListTool,
    RemoteProcessExecuteTool,
    ListConnectedClientsTool,
    create_remote_tools,
)

from .models import (
    # Enums
    CapabilityType,
    RequestType,
    RequestPriority,
    ResponseStatus,
    OperationType,
    # Auth
    ClientAuthRequest,
    ClientAuthResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    # Capabilities
    PathScope,
    CommandScope,
    CapabilityScope,
    CapabilityConstraints,
    CapabilityRequest,
    CapabilityRequestPayload,
    Capability,
    CapabilityGrantPayload,
    # Operations
    FileReadOperation,
    FileWriteOperation,
    DirectoryListOperation,
    ProcessExecuteOperation,
    ContextStreamOperation,
    OperationExecutePayload,
    OperationCancelPayload,
    # Requests
    PendingRequest,
    PendingRequestsResponse,
    ClientResponse,
    # Results
    FileMetadata,
    DirectoryEntry,
    OperationResult,
    ProgressUpdate,
    # Streaming
    StreamStartRequest,
    StreamStartResponse,
    ChunkUploadRequest,
    StreamCompleteRequest,
    # Heartbeat
    SystemStatus,
    HeartbeatRequest,
    HeartbeatResponse,
    # Disconnect
    DisconnectRequest,
    # Client Info
    ClientInfo,
    ClientDetailInfo,
)

__all__ = [
    # Registry
    "ClientRegistry",
    "ConnectedClient",
    "AuthToken",
    "get_client_registry",
    # Queue
    "RequestQueue",
    "RequestCallback",
    "get_request_queue",
    "queue_capability_request",
    "request_capability_async",
    "queue_operation",
    "execute_operation_async",
    # Tools
    "RemoteFileReadTool",
    "RemoteDirectoryListTool",
    "RemoteProcessExecuteTool",
    "ListConnectedClientsTool",
    "create_remote_tools",
    # Enums
    "CapabilityType",
    "RequestType",
    "RequestPriority",
    "ResponseStatus",
    "OperationType",
    # Auth models
    "ClientAuthRequest",
    "ClientAuthResponse",
    "RefreshTokenRequest",
    "RefreshTokenResponse",
    # Capability models
    "PathScope",
    "CommandScope",
    "CapabilityScope",
    "CapabilityConstraints",
    "CapabilityRequest",
    "CapabilityRequestPayload",
    "Capability",
    "CapabilityGrantPayload",
    # Operation models
    "FileReadOperation",
    "FileWriteOperation",
    "DirectoryListOperation",
    "ProcessExecuteOperation",
    "ContextStreamOperation",
    "OperationExecutePayload",
    "OperationCancelPayload",
    # Request models
    "PendingRequest",
    "PendingRequestsResponse",
    "ClientResponse",
    # Result models
    "FileMetadata",
    "DirectoryEntry",
    "OperationResult",
    "ProgressUpdate",
    # Stream models
    "StreamStartRequest",
    "StreamStartResponse",
    "ChunkUploadRequest",
    "StreamCompleteRequest",
    # Heartbeat models
    "SystemStatus",
    "HeartbeatRequest",
    "HeartbeatResponse",
    # Other models
    "DisconnectRequest",
    "ClientInfo",
    "ClientDetailInfo",
]
