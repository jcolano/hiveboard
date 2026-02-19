"""
RATE_LIMIT
==========

Rate limiting for the Agentic Loop Framework.

Prevents resource exhaustion from:
- Too many requests per second/minute
- Too many concurrent sessions
- Token budget overruns

Usage:
    from loop_core.ratelimit import RateLimiter, RateLimitExceeded

    limiter = RateLimiter(
        requests_per_minute=60,
        requests_per_second=5,
        max_concurrent=10
    )

    # Check before processing
    if not limiter.acquire("user_123"):
        raise RateLimitExceeded("Rate limit exceeded")

    try:
        # Process request
        pass
    finally:
        limiter.release("user_123")
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from datetime import datetime, timezone
from collections import deque


# ============================================================================
# EXCEPTIONS
# ============================================================================

class RateLimitExceeded(Exception):
    """Rate limit has been exceeded."""

    def __init__(self, message: str, retry_after: float = None):
        self.retry_after = retry_after
        super().__init__(message)


# ============================================================================
# RATE LIMITER
# ============================================================================

@dataclass
class RateLimitStats:
    """Statistics for rate limiting."""
    total_requests: int = 0
    requests_allowed: int = 0
    requests_denied: int = 0
    current_concurrent: int = 0
    peak_concurrent: int = 0

    def to_dict(self) -> Dict:
        return {
            "total_requests": self.total_requests,
            "requests_allowed": self.requests_allowed,
            "requests_denied": self.requests_denied,
            "current_concurrent": self.current_concurrent,
            "peak_concurrent": self.peak_concurrent,
            "denial_rate": self.requests_denied / max(1, self.total_requests)
        }


class RateLimiter:
    """
    Token bucket rate limiter with concurrency control.

    Features:
    - Requests per second limiting
    - Requests per minute limiting
    - Maximum concurrent requests
    - Per-client tracking
    - Token budget tracking
    """

    def __init__(
        self,
        requests_per_second: float = 10.0,
        requests_per_minute: float = 100.0,
        max_concurrent: int = 20,
        token_budget_per_minute: int = 100000,
        enable_per_client: bool = True
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Max requests per second (0 = unlimited)
            requests_per_minute: Max requests per minute (0 = unlimited)
            max_concurrent: Max concurrent requests (0 = unlimited)
            token_budget_per_minute: Max tokens per minute (0 = unlimited)
            enable_per_client: Track limits per client ID
        """
        self.requests_per_second = requests_per_second
        self.requests_per_minute = requests_per_minute
        self.max_concurrent = max_concurrent
        self.token_budget_per_minute = token_budget_per_minute
        self.enable_per_client = enable_per_client

        # Global tracking
        self._lock = threading.RLock()
        self._second_window: deque = deque()  # Timestamps in current second
        self._minute_window: deque = deque()  # Timestamps in current minute
        self._concurrent: Dict[str, int] = {}  # client_id -> count
        self._total_concurrent: int = 0
        self._token_window: deque = deque()  # (timestamp, tokens) tuples

        # Per-client tracking
        self._client_second: Dict[str, deque] = {}
        self._client_minute: Dict[str, deque] = {}

        # Statistics
        self.stats = RateLimitStats()

    def acquire(self, client_id: str = "default", tokens: int = 0) -> bool:
        """
        Attempt to acquire a request slot.

        Args:
            client_id: Client identifier for per-client limiting
            tokens: Estimated tokens for this request

        Returns:
            True if request is allowed, False if rate limited

        Raises:
            RateLimitExceeded: If rate limit exceeded and blocking
        """
        with self._lock:
            now = time.time()
            self.stats.total_requests += 1

            # Clean old entries
            self._cleanup_windows(now)

            # Check global rate limits
            if not self._check_global_limits(now):
                self.stats.requests_denied += 1
                return False

            # Check per-client limits
            if self.enable_per_client and not self._check_client_limits(client_id, now):
                self.stats.requests_denied += 1
                return False

            # Check concurrent limit
            if self.max_concurrent > 0 and self._total_concurrent >= self.max_concurrent:
                self.stats.requests_denied += 1
                return False

            # Check token budget
            if self.token_budget_per_minute > 0 and tokens > 0:
                current_tokens = sum(t for _, t in self._token_window)
                if current_tokens + tokens > self.token_budget_per_minute:
                    self.stats.requests_denied += 1
                    return False
                self._token_window.append((now, tokens))

            # Record request
            self._second_window.append(now)
            self._minute_window.append(now)

            if self.enable_per_client:
                if client_id not in self._client_second:
                    self._client_second[client_id] = deque()
                    self._client_minute[client_id] = deque()
                self._client_second[client_id].append(now)
                self._client_minute[client_id].append(now)

            # Track concurrent
            self._concurrent[client_id] = self._concurrent.get(client_id, 0) + 1
            self._total_concurrent += 1

            # Update stats
            self.stats.requests_allowed += 1
            self.stats.current_concurrent = self._total_concurrent
            if self._total_concurrent > self.stats.peak_concurrent:
                self.stats.peak_concurrent = self._total_concurrent

            return True

    def release(self, client_id: str = "default") -> None:
        """
        Release a request slot after completion.

        Args:
            client_id: Client identifier
        """
        with self._lock:
            if client_id in self._concurrent and self._concurrent[client_id] > 0:
                self._concurrent[client_id] -= 1
                self._total_concurrent = max(0, self._total_concurrent - 1)
                self.stats.current_concurrent = self._total_concurrent

    def record_tokens(self, tokens: int) -> None:
        """
        Record actual tokens used (for post-request tracking).

        Args:
            tokens: Tokens consumed
        """
        if self.token_budget_per_minute > 0:
            with self._lock:
                now = time.time()
                self._token_window.append((now, tokens))

    def get_retry_after(self) -> float:
        """
        Get seconds until rate limit resets.

        Returns:
            Seconds to wait before retrying
        """
        with self._lock:
            now = time.time()

            # Check which limit is blocking
            if self.requests_per_second > 0 and len(self._second_window) >= self.requests_per_second:
                oldest = self._second_window[0]
                return max(0, 1.0 - (now - oldest))

            if self.requests_per_minute > 0 and len(self._minute_window) >= self.requests_per_minute:
                oldest = self._minute_window[0]
                return max(0, 60.0 - (now - oldest))

            return 0.0

    def get_status(self, client_id: str = "default") -> Dict:
        """
        Get current rate limit status.

        Args:
            client_id: Client identifier

        Returns:
            Status dictionary
        """
        with self._lock:
            now = time.time()
            self._cleanup_windows(now)

            status = {
                "requests_in_last_second": len(self._second_window),
                "requests_in_last_minute": len(self._minute_window),
                "limit_per_second": self.requests_per_second,
                "limit_per_minute": self.requests_per_minute,
                "concurrent": self._total_concurrent,
                "max_concurrent": self.max_concurrent,
                "retry_after": self.get_retry_after(),
            }

            if self.token_budget_per_minute > 0:
                current_tokens = sum(t for _, t in self._token_window)
                status["tokens_in_last_minute"] = current_tokens
                status["token_budget"] = self.token_budget_per_minute

            if self.enable_per_client and client_id in self._client_minute:
                status["client_requests_in_minute"] = len(self._client_minute[client_id])

            return status

    def reset(self) -> None:
        """Reset all rate limit state."""
        with self._lock:
            self._second_window.clear()
            self._minute_window.clear()
            self._concurrent.clear()
            self._total_concurrent = 0
            self._token_window.clear()
            self._client_second.clear()
            self._client_minute.clear()
            self.stats = RateLimitStats()

    def _cleanup_windows(self, now: float) -> None:
        """Remove expired entries from sliding windows."""
        # Clean second window (entries older than 1 second)
        while self._second_window and now - self._second_window[0] > 1.0:
            self._second_window.popleft()

        # Clean minute window (entries older than 60 seconds)
        while self._minute_window and now - self._minute_window[0] > 60.0:
            self._minute_window.popleft()

        # Clean token window
        while self._token_window and now - self._token_window[0][0] > 60.0:
            self._token_window.popleft()

        # Clean per-client windows
        for client_id in list(self._client_second.keys()):
            window = self._client_second[client_id]
            while window and now - window[0] > 1.0:
                window.popleft()

        for client_id in list(self._client_minute.keys()):
            window = self._client_minute[client_id]
            while window and now - window[0] > 60.0:
                window.popleft()

    def _check_global_limits(self, now: float) -> bool:
        """Check global rate limits."""
        if self.requests_per_second > 0:
            if len(self._second_window) >= self.requests_per_second:
                return False

        if self.requests_per_minute > 0:
            if len(self._minute_window) >= self.requests_per_minute:
                return False

        return True

    def _check_client_limits(self, client_id: str, now: float) -> bool:
        """Check per-client rate limits."""
        # Per-client limits are typically stricter
        client_per_second = self.requests_per_second / 2  # 50% of global
        client_per_minute = self.requests_per_minute / 2

        if client_id in self._client_second:
            if len(self._client_second[client_id]) >= client_per_second:
                return False

        if client_id in self._client_minute:
            if len(self._client_minute[client_id]) >= client_per_minute:
                return False

        return True


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def configure_rate_limiter(
    requests_per_second: float = 10.0,
    requests_per_minute: float = 100.0,
    max_concurrent: int = 20,
    token_budget_per_minute: int = 100000
) -> RateLimiter:
    """Configure and return the global rate limiter."""
    global _rate_limiter
    _rate_limiter = RateLimiter(
        requests_per_second=requests_per_second,
        requests_per_minute=requests_per_minute,
        max_concurrent=max_concurrent,
        token_budget_per_minute=token_budget_per_minute
    )
    return _rate_limiter


# ============================================================================
# DECORATOR
# ============================================================================

def rate_limited(client_id_param: str = None):
    """
    Decorator to apply rate limiting to a function.

    Args:
        client_id_param: Name of parameter to use as client ID

    Usage:
        @rate_limited(client_id_param="user_id")
        def process_request(user_id: str, data: dict):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            limiter = get_rate_limiter()

            # Extract client ID from kwargs if specified
            client_id = "default"
            if client_id_param and client_id_param in kwargs:
                client_id = str(kwargs[client_id_param])

            if not limiter.acquire(client_id):
                retry_after = limiter.get_retry_after()
                raise RateLimitExceeded(
                    f"Rate limit exceeded for {client_id}",
                    retry_after=retry_after
                )

            try:
                return func(*args, **kwargs)
            finally:
                limiter.release(client_id)

        return wrapper
    return decorator


# ============================================================================
# MAIN BLOCK (Test & Demo)
# ============================================================================

if __name__ == "__main__":
    print("Rate Limiter Module")
    print("=" * 60)

    # Create limiter with strict limits for testing
    limiter = RateLimiter(
        requests_per_second=3,
        requests_per_minute=10,
        max_concurrent=2
    )

    print("\n--- Testing Rate Limits ---")

    # Test rapid requests
    for i in range(5):
        allowed = limiter.acquire("test_client")
        print(f"Request {i+1}: {'Allowed' if allowed else 'Denied'}")
        if allowed:
            limiter.release("test_client")
        time.sleep(0.1)

    print(f"\nStatus: {limiter.get_status('test_client')}")
    print(f"\nStats: {limiter.stats.to_dict()}")

    # Test concurrent limits
    print("\n--- Testing Concurrent Limits ---")
    limiter.reset()

    acquired = []
    for i in range(4):
        if limiter.acquire(f"client_{i}"):
            acquired.append(i)
            print(f"Client {i}: Acquired")
        else:
            print(f"Client {i}: Denied (concurrent limit)")

    print(f"Concurrent: {limiter.stats.current_concurrent}")

    for i in acquired:
        limiter.release(f"client_{i}")

    print(f"After release: {limiter.stats.current_concurrent}")
