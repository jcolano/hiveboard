"""Basic ingest endpoint tests."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_ingest_rejects_unauthenticated(client):
    """Ingest without API key should return 401."""
    resp = await client.post("/v1/ingest", json={
        "envelope": {"sdk": "test", "sdk_version": "0.1", "agent_id": "test-agent"},
        "events": [],
    })
    assert resp.status_code == 401
