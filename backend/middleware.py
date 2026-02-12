"""Auth middleware and rate limiting for HiveBoard API."""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from shared.enums import RATE_LIMIT_INGEST, RATE_LIMIT_QUERY

# Paths that skip authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/dashboard"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Extract API key, authenticate, inject tenant_id into request state."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip auth for public paths and WebSocket (handled separately)
        path = request.url.path.rstrip("/")
        if path in PUBLIC_PATHS or request.url.path.startswith("/v1/stream"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": "Missing or invalid Authorization header. Use: Bearer {api_key}",
                    "status": 401,
                },
            )

        raw_key = auth_header[7:]  # Strip "Bearer "
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        storage = request.app.state.storage
        info = await storage.authenticate(key_hash)
        if info is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": "Invalid API key",
                    "status": 401,
                },
            )

        # Read-only keys can't write
        if info.key_type == "read" and request.method in ("POST", "PUT", "DELETE"):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "insufficient_permissions",
                    "message": "Read-only API key cannot perform write operations",
                    "status": 403,
                },
            )

        # Inject auth context into request state
        request.state.tenant_id = info.tenant_id
        request.state.key_type = info.key_type
        request.state.key_id = info.key_id

        # Fire-and-forget touch
        import asyncio
        asyncio.create_task(storage.touch_api_key(info.key_id))

        response = await call_next(request)
        return response


# Module-level rate limit state — can be cleared between tests
_rate_limit_windows: dict[str, list[float]] = defaultdict(list)


def reset_rate_limits():
    """Clear all rate limit state. Called between tests."""
    _rate_limit_windows.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding window rate limiter per API key."""

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path.rstrip("/")
        if path in PUBLIC_PATHS or request.url.path.startswith("/v1/stream"):
            return await call_next(request)

        # Rate limit only applies after auth has set key_id
        key_id = getattr(request.state, "key_id", None)
        if not key_id:
            return await call_next(request)

        # Determine limit
        is_ingest = path == "/v1/ingest"
        limit = RATE_LIMIT_INGEST if is_ingest else RATE_LIMIT_QUERY

        now = time.time()
        window = _rate_limit_windows[key_id]

        # Prune old entries (older than 1 second)
        window[:] = [t for t in window if now - t < 1.0]

        remaining = max(0, limit - len(window))
        reset_at = int(now) + 1

        if remaining == 0:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit of {limit} requests/second exceeded",
                    "status": 429,
                    "details": {"retry_after_seconds": 1},
                },
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                },
            )

        window.append(now)
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining - 1)
        response.headers["X-RateLimit-Reset"] = str(reset_at)

        return response
