"""API endpoint tests — B2.1 through B2.3."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import app, _bootstrap_dev_tenant
from backend.middleware import reset_rate_limits
from backend.storage_json import JsonStorageBackend
from shared.models import ProjectCreate


DEV_KEY = "hb_live_dev000000000000000000000000000000"
AUTH_HEADERS = {"Authorization": f"Bearer {DEV_KEY}"}


@pytest.fixture
async def client(tmp_path: Path):
    """Test client with fresh storage per test."""
    reset_rate_limits()
    storage = JsonStorageBackend(data_dir=tmp_path)
    await storage.initialize()
    app.state.storage = storage
    await _bootstrap_dev_tenant(storage)
    # Create the project referenced by sample_batch.json
    await storage.create_project(
        "dev", ProjectCreate(name="Sales Pipeline", slug="sales-pipeline")
    )
    # The fixture uses project_id="sales-pipeline" (slug), but our storage
    # generates a UUID.  For tests, override with a known project_id.
    async with storage._locks["projects"]:
        for row in storage._tables["projects"]:
            if row["slug"] == "sales-pipeline":
                row["project_id"] = "sales-pipeline"
                storage._persist("projects")
                break
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await storage.close()


def _load_fixture() -> dict:
    fp = Path(__file__).parent.parent / "shared" / "fixtures" / "sample_batch.json"
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════
#  HEALTH & AUTH
# ═══════════════════════════════════════════════════════════════════════════


class TestHealthAndAuth:
    async def test_health(self, client: AsyncClient):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_no_auth_header(self, client: AsyncClient):
        r = await client.get("/v1/agents")
        assert r.status_code == 401
        assert r.json()["error"] == "authentication_failed"

    async def test_invalid_key(self, client: AsyncClient):
        r = await client.get(
            "/v1/agents", headers={"Authorization": "Bearer bogus"}
        )
        assert r.status_code == 401

    async def test_valid_auth(self, client: AsyncClient):
        r = await client.get("/v1/agents", headers=AUTH_HEADERS)
        assert r.status_code == 200

    async def test_rate_limit_headers(self, client: AsyncClient):
        r = await client.get("/v1/agents", headers=AUTH_HEADERS)
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers


# ═══════════════════════════════════════════════════════════════════════════
#  INGESTION
# ═══════════════════════════════════════════════════════════════════════════


class TestIngestion:
    async def test_ingest_sample_batch(self, client: AsyncClient):
        batch = _load_fixture()
        r = await client.post(
            "/v1/ingest", json=batch, headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] == 22
        assert body["rejected"] == 0

    async def test_ingest_deduplication(self, client: AsyncClient):
        batch = _load_fixture()
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        r = await client.post(
            "/v1/ingest", json=batch, headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert r.json()["accepted"] == 22  # Accepted but deduped in storage

    async def test_ingest_invalid_event_type(self, client: AsyncClient):
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "bad-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "not_a_real_type",
            }],
        }
        r = await client.post(
            "/v1/ingest", json=batch, headers=AUTH_HEADERS
        )
        assert r.status_code == 207
        body = r.json()
        assert body["rejected"] == 1
        assert body["errors"][0]["error"] == "invalid_event_type"

    async def test_ingest_missing_event_id(self, client: AsyncClient):
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "heartbeat",
            }],
        }
        r = await client.post(
            "/v1/ingest", json=batch, headers=AUTH_HEADERS
        )
        assert r.status_code == 207


# ═══════════════════════════════════════════════════════════════════════════
#  QUERY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


class TestQueryEndpoints:
    async def _seed(self, client: AsyncClient):
        batch = _load_fixture()
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)

    async def test_list_agents(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get("/v1/agents", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) >= 1
        assert data[0]["agent_id"] == "lead-qualifier"

    async def test_get_agent_detail(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/agents/lead-qualifier", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert r.json()["agent_id"] == "lead-qualifier"
        assert "derived_status" in r.json()

    async def test_get_agent_not_found(self, client: AsyncClient):
        r = await client.get(
            "/v1/agents/nonexistent", headers=AUTH_HEADERS
        )
        assert r.status_code == 404

    async def test_get_agent_pipeline(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/agents/lead-qualifier/pipeline", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert body["agent_id"] == "lead-qualifier"
        assert body["queue"] is not None

    async def test_list_tasks(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get("/v1/tasks", headers=AUTH_HEADERS)
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) >= 2  # task_lead-4821 and task_lead-4822

    async def test_get_task_timeline(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/tasks/task_lead-4821/timeline", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == "task_lead-4821"
        assert body["derived_status"] == "completed"
        assert len(body["events"]) > 0
        assert len(body["action_tree"]) > 0

    async def test_list_events(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get("/v1/events", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) > 0
        # Should exclude heartbeats by default
        for e in data:
            assert e["event_type"] != "heartbeat"

    async def test_list_events_include_heartbeats(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/events?exclude_heartbeats=false", headers=AUTH_HEADERS
        )
        types = {e["event_type"] for e in r.json()["data"]}
        assert "heartbeat" in types

    async def test_get_metrics(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/metrics?range=30d", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert "summary" in body
        assert "timeseries" in body

    async def test_get_cost(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get(
            "/v1/cost?range=30d", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        body = r.json()
        assert body["call_count"] == 2

    async def test_get_cost_calls(self, client: AsyncClient):
        await self._seed(client)
        r = await client.get("/v1/cost/calls", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 2

    async def test_list_projects(self, client: AsyncClient):
        r = await client.get("/v1/projects", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) >= 1  # Default project

    async def test_create_and_get_project(self, client: AsyncClient):
        r = await client.post(
            "/v1/projects",
            json={"name": "New Project", "slug": "new-project"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201
        pid = r.json()["project_id"]

        r2 = await client.get(
            f"/v1/projects/{pid}", headers=AUTH_HEADERS
        )
        assert r2.status_code == 200
        assert r2.json()["name"] == "New Project"

    async def test_list_alert_rules(self, client: AsyncClient):
        r = await client.get("/v1/alerts/rules", headers=AUTH_HEADERS)
        assert r.status_code == 200

    async def test_create_alert_rule(self, client: AsyncClient):
        r = await client.post(
            "/v1/alerts/rules",
            json={
                "name": "Agent stuck",
                "condition_type": "agent_stuck",
                "condition_config": {"stuck_threshold_seconds": 300},
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201
        assert r.json()["name"] == "Agent stuck"
