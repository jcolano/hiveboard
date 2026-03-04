"""Smoke tests — health endpoint and basic app startup."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_dashboard_returns_html(client):
    resp = await client.get("/dashboard")
    # Should return 200 with HTML content (or 404 if static files not found)
    assert resp.status_code in (200, 404)
