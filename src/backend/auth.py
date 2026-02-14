"""Password hashing, JWT tokens, and auth utilities for HiveBoard."""

from __future__ import annotations

import hashlib
import secrets
import time
from uuid import uuid4

import bcrypt
import jwt

from backend.config import get as _cfg

JWT_SECRET = _cfg("jwt_secret", "hiveboard-dev-secret-change-in-production")
JWT_EXPIRY = int(_cfg("jwt_expiry", 3600))
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with a random salt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, tenant_id: str, role: str) -> tuple[str, int]:
    """Create a JWT token. Returns (token, expires_in_seconds)."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + JWT_EXPIRY,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, JWT_EXPIRY


def decode_token(token: str) -> dict | None:
    """Validate and decode a JWT token. Returns None if invalid or expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def generate_api_key(key_type: str) -> tuple[str, str, str]:
    """Generate an API key. Returns (raw_key, key_hash, key_prefix).

    Format: hb_{type}_{32_hex_chars}
    """
    hex_part = secrets.token_hex(16)  # 32 hex chars
    raw_key = f"hb_{key_type}_{hex_part}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:12]
    return raw_key, key_hash, key_prefix


def generate_invite_token() -> tuple[str, str]:
    """Generate an invite token. Returns (raw_token, token_hash)."""
    raw_token = str(uuid4())
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, token_hash
