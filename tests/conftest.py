"""Shared test fixtures for HiveBoard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from backend.app import app  # noqa: E402


@pytest.fixture
async def client():
    """Async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
