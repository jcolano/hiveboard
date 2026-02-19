"""
Request Queue for Desktop Clients.

Manages the queue of requests from the backend that clients
poll for and process.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from threading import Lock
import asyncio

from .models import (
    PendingRequest,
    RequestType,
    RequestPriority,
    ResponseStatus,
    CapabilityRequestPayload,
    OperationExecutePayload,
    OperationCancelPayload,
)


class RequestCallback:
    """Holds a callback for when a request is responded to."""

    def __init__(
        self,
        callback: Optional[Callable[[str, ResponseStatus, Optional[Dict]], None]] = None,
        future: Optional[asyncio.Future] = None
    ):
        self.callback = callback
        self.future = future
        self.responded = False
        self.response_status: Optional[ResponseStatus] = None
        self.response_payload: Optional[Dict] = None


class RequestQueue:
    """
    Queue for managing requests to desktop clients.

    Supports:
    - Queuing requests for specific clients
    - Priority-based ordering
    - Request expiration
    - Async waiting for responses
    """

    _instance: Optional["RequestQueue"] = None
    _lock = Lock()

    # Default request expiration
    DEFAULT_EXPIRY_SECONDS = 300  # 5 minutes

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # client_id -> list of PendingRequest
        self._queues: Dict[str, List[PendingRequest]] = defaultdict(list)
        # request_id -> RequestCallback
        self._callbacks: Dict[str, RequestCallback] = {}
        # request_id -> client_id (for reverse lookup)
        self._request_clients: Dict[str, str] = {}
        self._queue_lock = Lock()
        self._initialized = True

    def _generate_request_id(self) -> str:
        """Generate a unique request ID."""
        return f"req_{uuid.uuid4().hex[:12]}"

    def queue_request(
        self,
        client_id: str,
        request_type: RequestType,
        payload: Dict[str, Any],
        priority: RequestPriority = RequestPriority.NORMAL,
        expiry_seconds: Optional[int] = None,
        callback: Optional[Callable[[str, ResponseStatus, Optional[Dict]], None]] = None
    ) -> str:
        """
        Queue a request for a client.

        Args:
            client_id: Target client ID
            request_type: Type of request
            payload: Request payload
            priority: Request priority
            expiry_seconds: Seconds until request expires
            callback: Optional callback when response received

        Returns:
            The request ID
        """
        if expiry_seconds is None:
            expiry_seconds = self.DEFAULT_EXPIRY_SECONDS

        request_id = self._generate_request_id()
        now = datetime.utcnow()

        request = PendingRequest(
            request_id=request_id,
            type=request_type,
            payload=payload,
            created_at=now,
            expires_at=now + timedelta(seconds=expiry_seconds),
            priority=priority
        )

        with self._queue_lock:
            self._queues[client_id].append(request)
            self._request_clients[request_id] = client_id

            # Sort by priority (high first) then by created_at
            priority_order = {
                RequestPriority.HIGH: 0,
                RequestPriority.NORMAL: 1,
                RequestPriority.LOW: 2
            }
            self._queues[client_id].sort(
                key=lambda r: (priority_order[r.priority], r.created_at)
            )

            if callback:
                self._callbacks[request_id] = RequestCallback(callback=callback)

        return request_id

    async def queue_request_async(
        self,
        client_id: str,
        request_type: RequestType,
        payload: Dict[str, Any],
        priority: RequestPriority = RequestPriority.NORMAL,
        expiry_seconds: Optional[int] = None,
        timeout_seconds: Optional[int] = None
    ) -> tuple[ResponseStatus, Optional[Dict]]:
        """
        Queue a request and wait for the response.

        Args:
            client_id: Target client ID
            request_type: Type of request
            payload: Request payload
            priority: Request priority
            expiry_seconds: Seconds until request expires
            timeout_seconds: Seconds to wait for response (default: expiry_seconds)

        Returns:
            Tuple of (status, payload)

        Raises:
            asyncio.TimeoutError: If no response within timeout
        """
        if expiry_seconds is None:
            expiry_seconds = self.DEFAULT_EXPIRY_SECONDS
        if timeout_seconds is None:
            timeout_seconds = expiry_seconds

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        request_id = self._generate_request_id()
        now = datetime.utcnow()

        request = PendingRequest(
            request_id=request_id,
            type=request_type,
            payload=payload,
            created_at=now,
            expires_at=now + timedelta(seconds=expiry_seconds),
            priority=priority
        )

        with self._queue_lock:
            self._queues[client_id].append(request)
            self._request_clients[request_id] = client_id
            self._callbacks[request_id] = RequestCallback(future=future)

            # Sort by priority
            priority_order = {
                RequestPriority.HIGH: 0,
                RequestPriority.NORMAL: 1,
                RequestPriority.LOW: 2
            }
            self._queues[client_id].sort(
                key=lambda r: (priority_order[r.priority], r.created_at)
            )

        try:
            status, payload = await asyncio.wait_for(future, timeout=timeout_seconds)
            return status, payload
        except asyncio.TimeoutError:
            # Clean up on timeout
            self._remove_request(request_id)
            raise

    def get_pending_requests(
        self,
        client_id: str,
        limit: Optional[int] = None
    ) -> List[PendingRequest]:
        """
        Get pending requests for a client.

        Removes expired requests and returns active ones.
        """
        now = datetime.utcnow()

        with self._queue_lock:
            queue = self._queues.get(client_id, [])

            # Filter out expired requests
            active = []
            expired = []
            for req in queue:
                if req.expires_at < now:
                    expired.append(req)
                else:
                    active.append(req)

            # Update queue with only active requests
            self._queues[client_id] = active

            # Clean up expired request callbacks
            for req in expired:
                self._cleanup_request(req.request_id, expired=True)

            # Return limited results
            if limit:
                return active[:limit]
            return active

    def respond_to_request(
        self,
        request_id: str,
        status: ResponseStatus,
        payload: Optional[Dict] = None
    ) -> bool:
        """
        Record a response to a request.

        Returns True if the request existed and was responded to.
        """
        with self._queue_lock:
            client_id = self._request_clients.get(request_id)
            if client_id is None:
                return False

            # Remove from queue
            queue = self._queues.get(client_id, [])
            self._queues[client_id] = [r for r in queue if r.request_id != request_id]

            # Handle callback
            callback_info = self._callbacks.get(request_id)
            if callback_info:
                callback_info.responded = True
                callback_info.response_status = status
                callback_info.response_payload = payload

                # Call callback if provided
                if callback_info.callback:
                    try:
                        callback_info.callback(request_id, status, payload)
                    except Exception:
                        pass  # Don't let callback errors break the flow

                # Resolve future if provided
                if callback_info.future and not callback_info.future.done():
                    callback_info.future.set_result((status, payload))

                del self._callbacks[request_id]

            del self._request_clients[request_id]
            return True

    def _remove_request(self, request_id: str):
        """Remove a request without responding."""
        with self._queue_lock:
            client_id = self._request_clients.get(request_id)
            if client_id:
                queue = self._queues.get(client_id, [])
                self._queues[client_id] = [r for r in queue if r.request_id != request_id]
                del self._request_clients[request_id]

            if request_id in self._callbacks:
                del self._callbacks[request_id]

    def _cleanup_request(self, request_id: str, expired: bool = False):
        """Clean up a request's callback (called when request expires or is removed)."""
        callback_info = self._callbacks.get(request_id)
        if callback_info:
            if callback_info.future and not callback_info.future.done():
                if expired:
                    callback_info.future.set_exception(
                        TimeoutError(f"Request {request_id} expired")
                    )
            del self._callbacks[request_id]

        if request_id in self._request_clients:
            del self._request_clients[request_id]

    def cancel_request(self, request_id: str) -> bool:
        """
        Cancel a pending request.

        Returns True if the request existed and was cancelled.
        """
        with self._queue_lock:
            client_id = self._request_clients.get(request_id)
            if client_id is None:
                return False

            # Remove from queue
            queue = self._queues.get(client_id, [])
            self._queues[client_id] = [r for r in queue if r.request_id != request_id]

            # Handle callback with cancelled status
            callback_info = self._callbacks.get(request_id)
            if callback_info:
                if callback_info.future and not callback_info.future.done():
                    callback_info.future.set_result((ResponseStatus.CANCELLED, None))
                del self._callbacks[request_id]

            del self._request_clients[request_id]
            return True

    def get_queue_size(self, client_id: str) -> int:
        """Get the number of pending requests for a client."""
        return len(self._queues.get(client_id, []))

    def clear_client_queue(self, client_id: str) -> int:
        """
        Clear all pending requests for a client.

        Returns the number of requests cleared.
        """
        with self._queue_lock:
            queue = self._queues.get(client_id, [])
            count = len(queue)

            for req in queue:
                self._cleanup_request(req.request_id)

            self._queues[client_id] = []
            return count

    def cleanup_expired(self) -> int:
        """
        Clean up all expired requests across all clients.

        Returns the number of requests cleaned up.
        """
        now = datetime.utcnow()
        total_cleaned = 0

        with self._queue_lock:
            for client_id in list(self._queues.keys()):
                queue = self._queues[client_id]
                active = []
                for req in queue:
                    if req.expires_at < now:
                        self._cleanup_request(req.request_id, expired=True)
                        total_cleaned += 1
                    else:
                        active.append(req)
                self._queues[client_id] = active

        return total_cleaned


# Convenience functions for common request types

def queue_capability_request(
    client_id: str,
    agent_id: str,
    capabilities: List[Dict],
    reason: str,
    priority: RequestPriority = RequestPriority.NORMAL,
    callback: Optional[Callable] = None
) -> str:
    """Queue a capability request for a client."""
    queue = get_request_queue()
    payload = {
        "agent_id": agent_id,
        "capabilities": capabilities,
        "reason": reason
    }
    return queue.queue_request(
        client_id=client_id,
        request_type=RequestType.CAPABILITY_REQUEST,
        payload=payload,
        priority=priority,
        callback=callback
    )


async def request_capability_async(
    client_id: str,
    agent_id: str,
    capabilities: List[Dict],
    reason: str,
    timeout_seconds: int = 300
) -> tuple[ResponseStatus, Optional[Dict]]:
    """Request capabilities and wait for response."""
    queue = get_request_queue()
    payload = {
        "agent_id": agent_id,
        "capabilities": capabilities,
        "reason": reason
    }
    return await queue.queue_request_async(
        client_id=client_id,
        request_type=RequestType.CAPABILITY_REQUEST,
        payload=payload,
        timeout_seconds=timeout_seconds
    )


def queue_operation(
    client_id: str,
    operation_id: str,
    capability_id: str,
    operation: Dict,
    priority: RequestPriority = RequestPriority.NORMAL,
    callback: Optional[Callable] = None
) -> str:
    """Queue an operation for a client to execute."""
    queue = get_request_queue()
    payload = {
        "operation_id": operation_id,
        "capability_id": capability_id,
        "operation": operation
    }
    return queue.queue_request(
        client_id=client_id,
        request_type=RequestType.OPERATION_EXECUTE,
        payload=payload,
        priority=priority,
        callback=callback
    )


async def execute_operation_async(
    client_id: str,
    operation_id: str,
    capability_id: str,
    operation: Dict,
    timeout_seconds: int = 60
) -> tuple[ResponseStatus, Optional[Dict]]:
    """Execute an operation and wait for result."""
    queue = get_request_queue()
    payload = {
        "operation_id": operation_id,
        "capability_id": capability_id,
        "operation": operation
    }
    return await queue.queue_request_async(
        client_id=client_id,
        request_type=RequestType.OPERATION_EXECUTE,
        payload=payload,
        timeout_seconds=timeout_seconds
    )


# Singleton accessor
_queue: Optional[RequestQueue] = None


def get_request_queue() -> RequestQueue:
    """Get the singleton RequestQueue instance."""
    global _queue
    if _queue is None:
        _queue = RequestQueue()
    return _queue
