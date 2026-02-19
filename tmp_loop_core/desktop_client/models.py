"""
Pydantic models for Desktop Client API.

These models define the request/response contracts between the
loopCore backend and Windows desktop clients.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ============ ENUMS ============

class CapabilityType(str, Enum):
    """Types of capabilities that can be requested."""
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    FILE_WATCH = "file:watch"
    DIRECTORY_LIST = "directory:list"
    DIRECTORY_WATCH = "directory:watch"
    PROCESS_EXECUTE = "process:execute"
    PROCESS_WATCH = "process:watch"
    CONTEXT_STREAM = "context:stream"
    CLIPBOARD_READ = "clipboard:read"
    CLIPBOARD_WRITE = "clipboard:write"
    SCREENSHOT = "screen:capture"
    SYSTEM_INFO = "system:info"


class RequestType(str, Enum):
    """Types of requests that can be queued for clients."""
    CAPABILITY_REQUEST = "capability_request"
    OPERATION_EXECUTE = "operation_execute"
    OPERATION_CANCEL = "operation_cancel"


class RequestPriority(str, Enum):
    """Priority levels for queued requests."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ResponseStatus(str, Enum):
    """Status of client responses."""
    GRANTED = "granted"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ACKNOWLEDGED = "acknowledged"


class OperationType(str, Enum):
    """Types of operations that can be executed."""
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    DIRECTORY_LIST = "directory:list"
    PROCESS_EXECUTE = "process:execute"
    CONTEXT_STREAM = "context:stream"


# ============ AUTHENTICATION ============

class ClientAuthRequest(BaseModel):
    """Request to authenticate a desktop client."""
    client_id: str = Field(..., description="Persistent client identifier (UUID)")
    client_version: str = Field(..., description="Client software version")
    platform: str = Field(..., description="Platform: windows, macos, linux")
    device_fingerprint: Optional[str] = Field(None, description="Optional hardware binding")


class ClientAuthResponse(BaseModel):
    """Response after successful client authentication."""
    token: str = Field(..., description="Bearer token for subsequent requests")
    token_expires_at: datetime = Field(..., description="Token expiration time")
    refresh_token: str = Field(..., description="Token for renewal")
    server_version: str = Field(..., description="Backend server version")
    poll_interval_ms: int = Field(default=10000, description="Recommended polling interval")


class RefreshTokenRequest(BaseModel):
    """Request to refresh an expiring token."""
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    """Response with new tokens."""
    token: str
    token_expires_at: datetime
    refresh_token: str


# ============ CAPABILITY MODELS ============

class PathScope(BaseModel):
    """Scope definition for path-based capabilities."""
    path: str = Field(..., description="Absolute path or pattern")
    recursive: bool = Field(default=False, description="Include subdirectories")
    pattern: Optional[str] = Field(None, description="Glob pattern filter")
    exclude_patterns: Optional[List[str]] = Field(None, description="Patterns to exclude")


class CommandScope(BaseModel):
    """Scope definition for command execution capabilities."""
    executable: str = Field(..., description="Full path to executable")
    allowed_args: Optional[List[str]] = Field(None, description="Whitelist of allowed arguments")
    arg_patterns: Optional[List[str]] = Field(None, description="Regex patterns for allowed args")
    working_directory: Optional[str] = Field(None, description="Required working directory")


class CapabilityScope(BaseModel):
    """Scope of a capability - what resources it covers."""
    paths: Optional[List[PathScope]] = None
    commands: Optional[List[CommandScope]] = None
    context_types: Optional[List[str]] = None


class CapabilityConstraints(BaseModel):
    """Constraints on a capability."""
    max_file_size_bytes: Optional[int] = None
    max_total_bytes: Optional[int] = None
    max_operations_per_minute: Optional[int] = None
    max_concurrent_operations: Optional[int] = None
    time_window_seconds: Optional[int] = None
    require_confirmation: Optional[bool] = None


class CapabilityRequest(BaseModel):
    """A request for a specific capability."""
    type: CapabilityType
    scope: CapabilityScope
    constraints: Optional[CapabilityConstraints] = None


class CapabilityRequestPayload(BaseModel):
    """Payload for requesting capabilities from a client."""
    agent_id: str = Field(..., description="Which agent is requesting")
    capabilities: List[CapabilityRequest]
    reason: str = Field(..., description="Human-readable explanation")


class Capability(BaseModel):
    """A granted capability."""
    id: str = Field(..., description="Unique capability identifier")
    type: CapabilityType
    scope: CapabilityScope
    constraints: Optional[CapabilityConstraints] = None
    agent_id: str = Field(..., description="Agent this capability belongs to")
    granted_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    usage_count: int = Field(default=0)


class CapabilityGrantPayload(BaseModel):
    """Payload when client grants capabilities."""
    granted_capabilities: List[Capability]
    denied_capabilities: Optional[List[Dict[str, str]]] = None


# ============ OPERATION MODELS ============

class FileReadOperation(BaseModel):
    """Operation to read a file."""
    type: Literal["file:read"] = "file:read"
    path: str
    encoding: Optional[str] = "utf-8"
    offset: Optional[int] = None
    length: Optional[int] = None


class FileWriteOperation(BaseModel):
    """Operation to write a file."""
    type: Literal["file:write"] = "file:write"
    path: str
    content: str
    encoding: Optional[str] = "utf-8"
    mode: str = Field(default="overwrite", description="overwrite, append, or create_new")


class DirectoryListOperation(BaseModel):
    """Operation to list directory contents."""
    type: Literal["directory:list"] = "directory:list"
    path: str
    recursive: bool = False
    pattern: Optional[str] = None


class ProcessExecuteOperation(BaseModel):
    """Operation to execute a process."""
    type: Literal["process:execute"] = "process:execute"
    executable: str
    args: List[str] = Field(default_factory=list)
    working_directory: Optional[str] = None
    environment: Optional[Dict[str, str]] = None
    timeout: Optional[int] = None
    capture_output: bool = True


class ContextStreamOperation(BaseModel):
    """Operation to stream context to backend."""
    type: Literal["context:stream"] = "context:stream"
    source_path: str
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    max_file_size: Optional[int] = None
    max_total_size: Optional[int] = None


class OperationExecutePayload(BaseModel):
    """Payload for executing an operation."""
    operation_id: str
    capability_id: str = Field(..., description="Must have valid capability")
    operation: Dict[str, Any] = Field(..., description="Operation details")


class OperationCancelPayload(BaseModel):
    """Payload for cancelling an operation."""
    operation_id: str
    reason: Optional[str] = None


# ============ PENDING REQUEST ============

class PendingRequest(BaseModel):
    """A request queued for a client to process."""
    request_id: str
    type: RequestType
    payload: Dict[str, Any]
    created_at: datetime
    expires_at: datetime
    priority: RequestPriority = RequestPriority.NORMAL


class PendingRequestsResponse(BaseModel):
    """Response to polling for pending requests."""
    requests: List[PendingRequest]
    next_poll_ms: int = Field(default=10000, description="Suggested next poll interval")


# ============ CLIENT RESPONSE ============

class ClientResponse(BaseModel):
    """Client response to a pending request."""
    request_id: str
    status: ResponseStatus
    payload: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, str]] = None


# ============ OPERATION RESULTS ============

class FileMetadata(BaseModel):
    """Metadata about a file."""
    path: str
    size: int
    modified_at: datetime
    created_at: Optional[datetime] = None
    is_readonly: bool = False


class DirectoryEntry(BaseModel):
    """Entry in a directory listing."""
    name: str
    path: str
    is_directory: bool
    size: Optional[int] = None
    modified_at: Optional[datetime] = None


class OperationResult(BaseModel):
    """Result of an operation execution."""
    operation_id: str
    type: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, str]] = None
    duration_ms: int


class ProgressUpdate(BaseModel):
    """Progress update for long-running operations."""
    operation_id: str
    progress: int = Field(..., ge=0, le=100)
    current_step: Optional[str] = None
    bytes_processed: Optional[int] = None


# ============ STREAMING ============

class StreamStartRequest(BaseModel):
    """Request to start a streaming transfer."""
    operation_id: str
    total_chunks: Optional[int] = None
    total_bytes: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StreamStartResponse(BaseModel):
    """Response with stream information."""
    stream_id: str
    upload_url: str


class ChunkUploadRequest(BaseModel):
    """Request to upload a chunk."""
    chunk_index: int
    data: str = Field(..., description="Base64 encoded chunk data")
    checksum: str = Field(..., description="SHA256 of this chunk")


class StreamCompleteRequest(BaseModel):
    """Request to finalize a stream."""
    total_chunks: int
    total_bytes: int
    final_checksum: str = Field(..., description="SHA256 of complete data")


# ============ HEARTBEAT ============

class SystemStatus(BaseModel):
    """System status information from client."""
    memory_usage_mb: float
    disk_free_gb: float
    cpu_usage_percent: float


class HeartbeatRequest(BaseModel):
    """Keep-alive and status update."""
    active_capabilities: List[str] = Field(default_factory=list)
    system_status: Optional[SystemStatus] = None


class HeartbeatResponse(BaseModel):
    """Response to heartbeat."""
    server_time: datetime
    capabilities_to_revoke: List[str] = Field(default_factory=list)
    config_updates: Optional[Dict[str, Any]] = None


# ============ DISCONNECT ============

class DisconnectRequest(BaseModel):
    """Graceful disconnect request."""
    reason: str
    revoke_all_capabilities: bool = False


# ============ CLIENT INFO ============

class ClientInfo(BaseModel):
    """Information about a connected client."""
    client_id: str
    platform: str
    client_version: str
    connected_at: datetime
    last_seen_at: datetime
    is_online: bool
    active_capabilities_count: int


class ClientDetailInfo(BaseModel):
    """Detailed information about a client."""
    client_id: str
    platform: str
    client_version: str
    device_fingerprint: Optional[str] = None
    connected_at: datetime
    last_seen_at: datetime
    is_online: bool
    capabilities: List[Capability] = Field(default_factory=list)
    pending_requests_count: int = 0
    system_status: Optional[SystemStatus] = None
