"""Auth middleware and rate limiting for HiveBoard API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from shared.enums import RATE_LIMIT_INGEST, RATE_LIMIT_QUERY

# Paths that skip authentication
PUBLIC_PATHS = {
    "/health", "/docs", "/openapi.json", "/dashboard",
    "/v1/auth/login",
    "/v1/auth/register",
    "/v1/auth/send-code",
    "/v1/auth/verify-code",
    "/v1/auth/accept-invite",
}
PUBLIC_PREFIXES = ("/v1/stream", "/static")


class AuthMiddleware(BaseHTTPMiddleware):
    """Dual auth: API key (hb_ prefix) or JWT token."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip auth for public paths, WebSocket, and static files
        path = request.url.path.rstrip("/")
        if path in PUBLIC_PATHS or any(request.url.path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": "Missing or invalid Authorization header. Use: Bearer {api_key_or_jwt}",
                    "status": 401,
                },
            )

        token = auth_header[7:]  # Strip "Bearer "

        # Detect auth type by prefix: hb_ = API key, otherwise = JWT
        if token.startswith("hb_"):
            return await self._auth_api_key(request, call_next, token)
        else:
            return await self._auth_jwt(request, call_next, token)

    async def _auth_api_key(
        self, request: Request, call_next: RequestResponseEndpoint, raw_key: str
    ) -> Response:
        """Existing API key authentication path."""
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

        # Inject auth context
        request.state.tenant_id = info.tenant_id
        request.state.key_type = info.key_type
        request.state.key_id = info.key_id
        request.state.auth_type = "api_key"
        request.state.user_id = None
        request.state.user_role = None

        # Fire-and-forget touch
        def _log_task_exception(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception():
                logging.getLogger(__name__).warning(
                    "touch_api_key failed: %s", t.exception()
                )

        task = asyncio.create_task(storage.touch_api_key(info.key_id))
        task.add_done_callback(_log_task_exception)

        return await call_next(request)

    async def _auth_jwt(
        self, request: Request, call_next: RequestResponseEndpoint, token: str
    ) -> Response:
        """JWT token authentication path."""
        from backend.auth import decode_token

        claims = decode_token(token)
        if claims is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "authentication_failed",
                    "message": "Invalid or expired token",
                    "status": 401,
                },
            )

        # Inject auth context
        request.state.tenant_id = claims["tid"]
        request.state.key_type = None
        request.state.key_id = None
        request.state.auth_type = "jwt"
        request.state.user_id = claims["sub"]
        request.state.user_role = claims["role"]

        return await call_next(request)


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
        if path in PUBLIC_PATHS or any(request.url.path.startswith(p) for p in PUBLIC_PREFIXES):
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
