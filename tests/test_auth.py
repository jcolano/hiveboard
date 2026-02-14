"""Auth module unit tests — password hashing, JWT tokens, and generation utilities."""

from __future__ import annotations

import hashlib
import time

import pytest

from backend.auth import (
    create_token,
    decode_token,
    generate_api_key,
    generate_invite_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "correct-horse-battery-staple"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed) is True

    def test_wrong_password(self):
        hashed = hash_password("real-password")
        assert verify_password("wrong-password", hashed) is False

    def test_different_salts(self):
        password = "same-password"
        h1 = hash_password(password)
        h2 = hash_password(password)
        assert h1 != h2  # Different salts → different hashes
        assert verify_password(password, h1) is True
        assert verify_password(password, h2) is True


class TestJWT:
    def test_create_and_decode(self):
        token, expires_in = create_token("user-1", "tenant-1", "admin")
        assert isinstance(token, str)
        assert expires_in > 0

        claims = decode_token(token)
        assert claims is not None
        assert claims["sub"] == "user-1"
        assert claims["tid"] == "tenant-1"
        assert claims["role"] == "admin"

    def test_expired_token(self, monkeypatch):
        import backend.auth as auth_mod
        # Set expiry to 0 seconds
        monkeypatch.setattr(auth_mod, "JWT_EXPIRY", 0)
        token, _ = create_token("user-1", "tenant-1", "member")
        # Wait briefly for expiry
        time.sleep(0.1)
        claims = decode_token(token)
        assert claims is None

    def test_tampered_token(self):
        token, _ = create_token("user-1", "tenant-1", "member")
        # Tamper with the token payload
        tampered = token[:-5] + "XXXXX"
        claims = decode_token(tampered)
        assert claims is None

    def test_garbage_token(self):
        claims = decode_token("not-a-valid-jwt")
        assert claims is None


class TestGenerateApiKey:
    def test_correct_format(self):
        raw_key, key_hash, key_prefix = generate_api_key("live")
        assert raw_key.startswith("hb_live_")
        # 32 hex chars after prefix
        hex_part = raw_key[len("hb_live_"):]
        assert len(hex_part) == 32
        int(hex_part, 16)  # Should not raise

    def test_hash_matches(self):
        raw_key, key_hash, _ = generate_api_key("test")
        expected = hashlib.sha256(raw_key.encode()).hexdigest()
        assert key_hash == expected

    def test_prefix_is_first_12_chars(self):
        raw_key, _, key_prefix = generate_api_key("read")
        assert key_prefix == raw_key[:12]


class TestGenerateInviteToken:
    def test_uuid_format(self):
        raw_token, token_hash = generate_invite_token()
        # UUID has 36 chars with hyphens
        assert len(raw_token) == 36
        assert raw_token.count("-") == 4

    def test_hash_matches(self):
        raw_token, token_hash = generate_invite_token()
        expected = hashlib.sha256(raw_token.encode()).hexdigest()
        assert token_hash == expected
