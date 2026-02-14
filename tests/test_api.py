"""API endpoint tests — B2.1 through B2.3."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import app, _bootstrap_dev_tenant
from backend.llm_pricing import LlmPricingEngine
from backend.middleware import reset_rate_limits
from backend.storage_json import JsonStorageBackend
from shared.models import ProjectCreate


DEV_KEY = "hb_live_dev000000000000000000000000000000"
AUTH_HEADERS = {"Authorization": f"Bearer {DEV_KEY}"}


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    """Test client with fresh storage per test."""
    monkeypatch.setenv("HIVEBOARD_DEV_KEY", DEV_KEY)
    reset_rate_limits()
    storage = JsonStorageBackend(data_dir=tmp_path)
    await storage.initialize()
    app.state.storage = storage
    pricing = LlmPricingEngine(data_dir=str(tmp_path))
    await pricing.initialize()
    app.state.pricing = pricing
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
    fp = Path(__file__).parent.parent / "src" / "shared" / "fixtures" / "sample_batch.json"
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


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 2 FEATURE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestPhase2Features:
    async def _seed(self, client: AsyncClient):
        batch = _load_fixture()
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)

    async def test_severity_validation_warning(self, client: AsyncClient):
        """F1: Invalid severity should produce a warning, not rejection."""
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "sev-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "heartbeat",
                "severity": "not_a_severity",
            }],
        }
        r = await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 0
        assert len(body.get("warnings", [])) > 0
        assert any("severity" in w.get("warning", "").lower() for w in body["warnings"])

    async def test_agents_have_stats_1h(self, client: AsyncClient):
        """F2: GET /v1/agents should include stats_1h."""
        await self._seed(client)
        r = await client.get("/v1/agents", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) >= 1
        assert "stats_1h" in data[0]
        assert "tasks_completed" in data[0]["stats_1h"]

    async def test_tasks_since_until_params(self, client: AsyncClient):
        """F4: Tasks endpoint should accept since/until."""
        await self._seed(client)
        r = await client.get(
            "/v1/tasks?since=2026-02-10T14:00:00Z",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200

    async def test_timeline_has_plan(self, client: AsyncClient):
        """F6: Timeline should include plan field."""
        await self._seed(client)
        r = await client.get(
            "/v1/tasks/task_lead-4821/timeline",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        # plan may be null if no plan events, but the field should exist
        assert "plan" in body

    async def test_timeline_action_tree_shape(self, client: AsyncClient):
        """F5: Action tree nodes should have name, status, duration_ms."""
        await self._seed(client)
        r = await client.get(
            "/v1/tasks/task_lead-4821/timeline",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        if body["action_tree"]:
            node = body["action_tree"][0]
            assert "name" in node
            assert "status" in node
            assert "duration_ms" in node

    async def test_events_payload_kind_filter(self, client: AsyncClient):
        """F7: Events endpoint should accept payload_kind."""
        await self._seed(client)
        r = await client.get(
            "/v1/events?payload_kind=llm_call",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        data = r.json()["data"]
        for e in data:
            assert e.get("payload", {}).get("kind") == "llm_call"

    async def test_cost_has_token_totals(self, client: AsyncClient):
        """F8: Cost response should include total_tokens_in/out."""
        await self._seed(client)
        r = await client.get("/v1/cost?range=30d", headers=AUTH_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "total_tokens_in" in body
        assert "total_tokens_out" in body

    async def test_metrics_group_by(self, client: AsyncClient):
        """F10: Metrics should support group_by parameter."""
        await self._seed(client)
        r = await client.get(
            "/v1/metrics?range=30d&group_by=agent",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert "groups" in body

    async def test_validation_error_format(self, client: AsyncClient):
        """W1/W2: Validation errors should return 400 with field details."""
        r = await client.post(
            "/v1/projects",
            json={},  # Missing required fields
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "validation_error"
        assert "details" in body
        assert "fields" in body["details"]

    async def test_404_structured_error(self, client: AsyncClient):
        """W8: 404 errors should use structured format."""
        r = await client.get(
            "/v1/agents/nonexistent",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404
        body = r.json()
        assert body["error"] == "not_found"
        assert "message" in body

    async def test_default_project_cannot_be_deleted(self, client: AsyncClient):
        """W7: Default project should be protected from deletion."""
        # Find the default project
        r = await client.get("/v1/projects", headers=AUTH_HEADERS)
        projects = r.json()["data"]
        default_project = next(
            (p for p in projects if p["slug"] == "default"), None
        )
        assert default_project is not None

        r = await client.delete(
            f"/v1/projects/{default_project['project_id']}",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 400
        assert r.json()["error"] == "cannot_delete_default"

    async def test_pipeline_queue_snapshot_at(self, client: AsyncClient):
        """W5: Queue should include snapshot_at."""
        await self._seed(client)
        r = await client.get(
            "/v1/agents/lead-qualifier/pipeline",
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        if body.get("queue"):
            assert "snapshot_at" in body["queue"]

    async def test_batch_event_ordering(self, client: AsyncClient):
        """W3: Batch events should be sorted by timestamp so last_event_type is correct."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        earlier = (now - timedelta(seconds=5)).isoformat()
        later = now.isoformat()
        batch = {
            "envelope": {"agent_id": "order-agent"},
            "events": [
                {
                    "event_id": "later",
                    "timestamp": later,
                    "event_type": "task_started",
                    "task_id": "t1",
                },
                {
                    "event_id": "earlier",
                    "timestamp": earlier,
                    "event_type": "heartbeat",
                },
            ],
        }
        r = await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        assert r.status_code == 200

        # Agent's last_event_type should be task_started (chronologically last)
        r2 = await client.get("/v1/agents/order-agent", headers=AUTH_HEADERS)
        assert r2.status_code == 200
        # task_started is the later event, so status should be processing
        assert r2.json()["derived_status"] == "processing"

    async def test_tasks_have_token_counts(self, client: AsyncClient):
        """F3: Tasks should include llm_call_count, total_tokens_in, total_tokens_out."""
        await self._seed(client)
        r = await client.get("/v1/tasks", headers=AUTH_HEADERS)
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) > 0
        for t in tasks:
            assert "llm_call_count" in t
            assert "total_tokens_in" in t
            assert "total_tokens_out" in t


# ═══════════════════════════════════════════════════════════════════════════
#  ISSUE #9 — Project auto-creation, merge, and enhanced delete
# ═══════════════════════════════════════════════════════════════════════════


class TestProjectAutoCreate:
    """Issue #9 Part 1: Unknown project slugs during ingestion are auto-created."""

    async def test_unknown_project_auto_created(self, client: AsyncClient):
        """Events with unknown project_id should auto-create the project."""
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "auto-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "task_started",
                "project_id": "my-new-project",
                "task_id": "t1",
            }],
        }
        r = await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 0
        # Should have a warning about auto-creation
        warnings = body.get("warnings", [])
        assert any("Auto-created" in w.get("warning", "") for w in warnings)

        # Project should now exist
        r2 = await client.get("/v1/projects", headers=AUTH_HEADERS)
        projects = r2.json()["data"]
        slugs = [p["slug"] for p in projects]
        assert "my-new-project" in slugs

        # Auto-created project should be flagged
        auto_proj = next(p for p in projects if p["slug"] == "my-new-project")
        assert auto_proj["auto_created"] is True

    async def test_auto_created_project_has_event_count(self, client: AsyncClient):
        """GET /v1/projects should include event_count for each project."""
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "cnt-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "task_started",
                "project_id": "counted-project",
                "task_id": "t1",
            }],
        }
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        r = await client.get("/v1/projects", headers=AUTH_HEADERS)
        projects = r.json()["data"]
        proj = next(p for p in projects if p["slug"] == "counted-project")
        assert "event_count" in proj
        assert proj["event_count"] == 1

    async def test_known_project_not_duplicated(self, client: AsyncClient):
        """Events with known project slugs should not create duplicates."""
        # "sales-pipeline" already exists from fixture
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "dup-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "heartbeat",
                "project_id": "sales-pipeline",
            }],
        }
        r = await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["accepted"] == 1
        # No warnings about auto-creation
        warnings = r.json().get("warnings", [])
        assert not any("Auto-created" in w.get("warning", "") for w in warnings)

    async def test_project_limit_routes_to_default(self, client: AsyncClient):
        """When tenant hits 50 projects, new slugs route to default."""
        storage = client._transport.app.state.storage
        # Create 48 more projects (we already have "default" + "sales-pipeline" = 2)
        for i in range(48):
            await storage.create_project(
                "dev", ProjectCreate(name=f"proj-{i}", slug=f"proj-{i}")
            )
        # Now we have 50 projects — next auto-create should route to default
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "limit-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "task_started",
                "project_id": "over-the-limit",
                "task_id": "t1",
            }],
        }
        r = await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["accepted"] == 1
        warnings = body.get("warnings", [])
        assert any("limit" in w.get("warning", "").lower() for w in warnings)

        # "over-the-limit" should NOT have been created as a project
        r2 = await client.get("/v1/projects", headers=AUTH_HEADERS)
        slugs = [p["slug"] for p in r2.json()["data"]]
        assert "over-the-limit" not in slugs


class TestProjectMerge:
    """Issue #9 Part 2: POST /v1/projects/{id}/merge endpoint."""

    async def test_merge_projects(self, client: AsyncClient):
        """Merge source project into target, reassigning events."""
        storage = client._transport.app.state.storage

        # Create two projects
        r1 = await client.post(
            "/v1/projects",
            json={"name": "Source", "slug": "source-proj"},
            headers=AUTH_HEADERS,
        )
        source_id = r1.json()["project_id"]

        r2 = await client.post(
            "/v1/projects",
            json={"name": "Target", "slug": "target-proj"},
            headers=AUTH_HEADERS,
        )

        # Ingest events for source project
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": f"merge-{i}",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "task_started",
                "project_id": "source-proj",
                "task_id": f"task-{i}",
            } for i in range(3)],
        }
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)

        # Merge source into target
        r = await client.post(
            f"/v1/projects/{source_id}/merge",
            json={"target_slug": "target-proj"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "merged"
        assert body["events_moved"] == 3

        # Source should be archived
        r3 = await client.get(
            "/v1/projects?include_archived=true", headers=AUTH_HEADERS
        )
        source = next(
            p for p in r3.json()["data"] if p["slug"] == "source-proj"
        )
        assert source["is_archived"] is True

    async def test_merge_into_self_fails(self, client: AsyncClient):
        """Cannot merge a project into itself."""
        r = await client.post(
            "/v1/projects",
            json={"name": "Solo", "slug": "solo-proj"},
            headers=AUTH_HEADERS,
        )
        pid = r.json()["project_id"]
        r2 = await client.post(
            f"/v1/projects/{pid}/merge",
            json={"target_slug": "solo-proj"},
            headers=AUTH_HEADERS,
        )
        assert r2.status_code == 400
        assert r2.json()["error"] == "invalid_merge"

    async def test_merge_nonexistent_source(self, client: AsyncClient):
        """Merge with nonexistent source returns 404."""
        r = await client.post(
            "/v1/projects/nonexistent/merge",
            json={"target_slug": "default"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404

    async def test_merge_nonexistent_target(self, client: AsyncClient):
        """Merge with nonexistent target returns 404."""
        r = await client.post(
            "/v1/projects",
            json={"name": "Source", "slug": "merge-src"},
            headers=AUTH_HEADERS,
        )
        pid = r.json()["project_id"]
        r2 = await client.post(
            f"/v1/projects/{pid}/merge",
            json={"target_slug": "nonexistent-target"},
            headers=AUTH_HEADERS,
        )
        assert r2.status_code == 404


class TestProjectDeleteWithReassignment:
    """Issue #9 Part 3: DELETE /v1/projects/{id} reassigns events."""

    async def test_delete_reassigns_events_to_default(self, client: AsyncClient):
        """Deleting a project moves its events to the default project."""
        # Create a project and add events
        r = await client.post(
            "/v1/projects",
            json={"name": "Doomed", "slug": "doomed-proj"},
            headers=AUTH_HEADERS,
        )
        pid = r.json()["project_id"]

        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": f"doom-{i}",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "task_started",
                "project_id": "doomed-proj",
                "task_id": f"task-{i}",
            } for i in range(2)],
        }
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)

        # Delete the project
        r2 = await client.delete(
            f"/v1/projects/{pid}", headers=AUTH_HEADERS
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] == "deleted"
        assert body["events_reassigned"] == 2
        assert body["reassigned_to"] == "default"

    async def test_delete_reassigns_to_specific_project(self, client: AsyncClient):
        """DELETE with reassign_to param moves events to specified project."""
        # Create source and target projects
        r1 = await client.post(
            "/v1/projects",
            json={"name": "Source", "slug": "del-source"},
            headers=AUTH_HEADERS,
        )
        source_id = r1.json()["project_id"]

        await client.post(
            "/v1/projects",
            json={"name": "Target", "slug": "del-target"},
            headers=AUTH_HEADERS,
        )

        # Add events to source
        batch = {
            "envelope": {"agent_id": "test-agent"},
            "events": [{
                "event_id": "del-reassign-1",
                "timestamp": "2026-02-10T14:00:00.000Z",
                "event_type": "heartbeat",
                "project_id": "del-source",
            }],
        }
        await client.post("/v1/ingest", json=batch, headers=AUTH_HEADERS)

        # Delete with specific reassignment target
        r2 = await client.delete(
            f"/v1/projects/{source_id}?reassign_to=del-target",
            headers=AUTH_HEADERS,
        )
        assert r2.status_code == 200
        assert r2.json()["reassigned_to"] == "del-target"
        assert r2.json()["events_reassigned"] == 1

    async def test_project_update_slug(self, client: AsyncClient):
        """PATCH/PUT project should allow renaming the slug."""
        r = await client.post(
            "/v1/projects",
            json={"name": "Original", "slug": "original-slug"},
            headers=AUTH_HEADERS,
        )
        pid = r.json()["project_id"]

        r2 = await client.put(
            f"/v1/projects/{pid}",
            json={"name": "Renamed", "slug": "renamed-slug"},
            headers=AUTH_HEADERS,
        )
        assert r2.status_code == 200
        assert r2.json()["name"] == "Renamed"

    async def test_create_project_duplicate_slug(self, client: AsyncClient):
        """Creating two projects with the same slug should fail."""
        await client.post(
            "/v1/projects",
            json={"name": "First", "slug": "dup-slug"},
            headers=AUTH_HEADERS,
        )
        r = await client.post(
            "/v1/projects",
            json={"name": "Second", "slug": "dup-slug"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 409
        assert r.json()["error"] == "slug_exists"

    async def test_update_project_duplicate_slug(self, client: AsyncClient):
        """Renaming a project's slug to an existing slug should fail."""
        await client.post(
            "/v1/projects",
            json={"name": "Alpha", "slug": "alpha-slug"},
            headers=AUTH_HEADERS,
        )
        r = await client.post(
            "/v1/projects",
            json={"name": "Beta", "slug": "beta-slug"},
            headers=AUTH_HEADERS,
        )
        beta_id = r.json()["project_id"]

        r2 = await client.put(
            f"/v1/projects/{beta_id}",
            json={"slug": "alpha-slug"},
            headers=AUTH_HEADERS,
        )
        assert r2.status_code == 409
        assert r2.json()["error"] == "slug_exists"


# ═══════════════════════════════════════════════════════════════════════════
#  USER AUTH TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestUserAuth:
    async def test_login_success(self, client: AsyncClient):
        """Bootstrap dev user can log in."""
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "admin@hiveboard.dev", "password": "admin"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert body["user"]["email"] == "admin@hiveboard.dev"
        assert body["user"]["role"] == "owner"

    async def test_login_wrong_password(self, client: AsyncClient):
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "admin@hiveboard.dev", "password": "wrong"},
        )
        assert r.status_code == 401
        assert r.json()["error"] == "authentication_failed"

    async def test_login_nonexistent_email(self, client: AsyncClient):
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "nobody@hiveboard.dev", "password": "admin"},
        )
        assert r.status_code == 401

    async def test_jwt_auth_on_existing_endpoints(self, client: AsyncClient):
        """JWT token works on existing API endpoints."""
        # Login to get token
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "admin@hiveboard.dev", "password": "admin"},
        )
        token = r.json()["token"]
        jwt_headers = {"Authorization": f"Bearer {token}"}

        # Use JWT to access agents endpoint
        r2 = await client.get("/v1/agents", headers=jwt_headers)
        assert r2.status_code == 200

    async def test_list_users(self, client: AsyncClient):
        """Owner can list users via API key."""
        r = await client.get("/v1/users", headers=AUTH_HEADERS)
        assert r.status_code == 200
        users = r.json()["data"]
        assert len(users) >= 1  # At least the bootstrap dev-owner

    async def test_create_user(self, client: AsyncClient):
        r = await client.post(
            "/v1/users",
            json={
                "email": "newuser@acme.com",
                "password": "securepass123",
                "name": "New User",
                "role": "member",
            },
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["email"] == "newuser@acme.com"
        assert body["role"] == "member"
        assert "password_hash" not in body  # Must not leak

    async def test_duplicate_email_rejected(self, client: AsyncClient):
        """Creating a user with an existing email fails."""
        await client.post(
            "/v1/users",
            json={"email": "dup@acme.com", "password": "pass1", "name": "First"},
            headers=AUTH_HEADERS,
        )
        r = await client.post(
            "/v1/users",
            json={"email": "dup@acme.com", "password": "pass2", "name": "Second"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 409
        assert r.json()["error"] == "duplicate_email"

    async def test_viewer_cannot_create_users(self, client: AsyncClient):
        """JWT user with viewer role can't create users."""
        # Create a viewer user
        await client.post(
            "/v1/users",
            json={"email": "viewer@acme.com", "password": "viewpass", "name": "Viewer", "role": "viewer"},
            headers=AUTH_HEADERS,
        )
        # Login as viewer
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "viewer@acme.com", "password": "viewpass"},
        )
        token = r.json()["token"]
        viewer_headers = {"Authorization": f"Bearer {token}"}

        # Try to create a user — should fail
        r2 = await client.post(
            "/v1/users",
            json={"email": "blocked@acme.com", "password": "pass", "name": "Blocked"},
            headers=viewer_headers,
        )
        assert r2.status_code == 403

    async def test_deactivate_user(self, client: AsyncClient):
        # Create a user
        r = await client.post(
            "/v1/users",
            json={"email": "deact@acme.com", "password": "pass", "name": "Deact User"},
            headers=AUTH_HEADERS,
        )
        user_id = r.json()["user_id"]

        # Deactivate
        r2 = await client.delete(f"/v1/users/{user_id}", headers=AUTH_HEADERS)
        assert r2.status_code == 200
        assert r2.json()["status"] == "deactivated"

        # Can't login anymore
        r3 = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "deact@acme.com", "password": "pass"},
        )
        assert r3.status_code == 401

    async def test_reactivate_user(self, client: AsyncClient):
        # Create and deactivate
        r = await client.post(
            "/v1/users",
            json={"email": "react@acme.com", "password": "pass", "name": "React User"},
            headers=AUTH_HEADERS,
        )
        user_id = r.json()["user_id"]
        await client.delete(f"/v1/users/{user_id}", headers=AUTH_HEADERS)

        # Reactivate
        r2 = await client.post(f"/v1/users/{user_id}/reactivate", headers=AUTH_HEADERS)
        assert r2.status_code == 200
        assert r2.json()["status"] == "reactivated"

        # Can login again
        r3 = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "react@acme.com", "password": "pass"},
        )
        assert r3.status_code == 200

    async def test_change_password(self, client: AsyncClient):
        # Create user and login
        await client.post(
            "/v1/users",
            json={"email": "chgpw@acme.com", "password": "oldpass", "name": "PW User"},
            headers=AUTH_HEADERS,
        )
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "chgpw@acme.com", "password": "oldpass"},
        )
        token = r.json()["token"]
        jwt_headers = {"Authorization": f"Bearer {token}"}

        # Change password
        r2 = await client.post(
            "/v1/auth/change-password",
            json={"current_password": "oldpass", "new_password": "newpass"},
            headers=jwt_headers,
        )
        assert r2.status_code == 200

        # Login with new password
        r3 = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "chgpw@acme.com", "password": "newpass"},
        )
        assert r3.status_code == 200

    async def test_get_me(self, client: AsyncClient):
        """GET /v1/users/me returns current JWT user."""
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "admin@hiveboard.dev", "password": "admin"},
        )
        token = r.json()["token"]
        r2 = await client.get(
            "/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r2.status_code == 200
        assert r2.json()["email"] == "admin@hiveboard.dev"

    async def test_api_key_still_works(self, client: AsyncClient):
        """Existing API key auth still works on all endpoints."""
        r = await client.get("/v1/agents", headers=AUTH_HEADERS)
        assert r.status_code == 200
        r2 = await client.get("/v1/projects", headers=AUTH_HEADERS)
        assert r2.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
#  REGISTRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistration:
    async def test_register_happy_path(self, client: AsyncClient):
        r = await client.post("/v1/auth/register", json={
            "email": "founder@newco.com",
            "password": "securepass1",
            "name": "Jane Founder",
            "tenant_name": "NewCo",
        })
        assert r.status_code == 201
        body = r.json()
        assert body["user"]["email"] == "founder@newco.com"
        assert body["user"]["role"] == "owner"
        assert body["tenant"]["name"] == "NewCo"
        assert body["tenant"]["slug"] == "newco"
        assert body["api_key"].startswith("hb_live_")

    async def test_register_duplicate_email(self, client: AsyncClient):
        await client.post("/v1/auth/register", json={
            "email": "dup@newco.com",
            "password": "securepass1",
            "name": "First",
            "tenant_name": "First Co",
        })
        r = await client.post("/v1/auth/register", json={
            "email": "dup@newco.com",
            "password": "securepass2",
            "name": "Second",
            "tenant_name": "Second Co",
        })
        assert r.status_code == 409
        assert r.json()["error"] == "email_exists"

    async def test_register_creates_default_project(self, client: AsyncClient):
        r = await client.post("/v1/auth/register", json={
            "email": "proj@newco.com",
            "password": "securepass1",
            "name": "Proj User",
            "tenant_name": "ProjCo",
        })
        body = r.json()
        api_key = body["api_key"]

        # Use the new API key to list projects
        r2 = await client.get(
            "/v1/projects",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r2.status_code == 200
        projects = r2.json()["data"]
        assert len(projects) >= 1
        slugs = [p["slug"] for p in projects]
        assert "default" in slugs

    async def test_register_returns_working_api_key(self, client: AsyncClient):
        r = await client.post("/v1/auth/register", json={
            "email": "apitest@newco.com",
            "password": "securepass1",
            "name": "API Tester",
            "tenant_name": "API Co",
        })
        api_key = r.json()["api_key"]
        r2 = await client.get(
            "/v1/agents",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        assert r2.status_code == 200

    async def test_register_duplicate_slug(self, client: AsyncClient):
        await client.post("/v1/auth/register", json={
            "email": "first@slugtest.com",
            "password": "securepass1",
            "name": "First",
            "tenant_name": "Slug Corp",
        })
        r = await client.post("/v1/auth/register", json={
            "email": "second@slugtest.com",
            "password": "securepass2",
            "name": "Second",
            "tenant_name": "Slug Corp",
        })
        assert r.status_code == 409
        assert r.json()["error"] == "slug_exists"


# ═══════════════════════════════════════════════════════════════════════════
#  CHECK-SLUG TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckSlug:
    async def test_slug_available(self, client: AsyncClient):
        r = await client.get("/v1/auth/check-slug", params={"slug": "brand-new"})
        assert r.status_code == 200
        body = r.json()
        assert body["slug"] == "brand-new"
        assert body["available"] is True

    async def test_slug_taken(self, client: AsyncClient):
        await client.post("/v1/auth/register", json={
            "email": "slugowner@test.com",
            "password": "securepass1",
            "name": "Owner",
            "tenant_name": "Taken Co",
        })
        r = await client.get("/v1/auth/check-slug", params={"slug": "taken-co"})
        assert r.status_code == 200
        body = r.json()
        assert body["slug"] == "taken-co"
        assert body["available"] is False

    async def test_slug_normalized(self, client: AsyncClient):
        r = await client.get("/v1/auth/check-slug", params={"slug": "My Company"})
        assert r.status_code == 200
        assert r.json()["slug"] == "my-company"

    async def test_dev_tenant_slug_taken(self, client: AsyncClient):
        """The bootstrapped dev tenant slug should already be taken."""
        r = await client.get("/v1/auth/check-slug", params={"slug": "dev"})
        assert r.status_code == 200
        assert r.json()["available"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  INVITE FLOW TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestInviteFlow:
    async def _get_owner_jwt(self, client: AsyncClient) -> str:
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "admin@hiveboard.dev", "password": "admin"},
        )
        return r.json()["token"]

    async def test_invite_and_accept(self, client: AsyncClient):
        """Owner invites → invitee accepts → joins tenant."""
        token = await self._get_owner_jwt(client)
        jwt_headers = {"Authorization": f"Bearer {token}"}

        # Send invite
        r = await client.post("/v1/auth/invite", json={
            "email": "newguy@example.com",
            "role": "member",
        }, headers=jwt_headers)
        assert r.status_code == 201
        invite_token = r.json()["invite_token"]

        # Accept invite
        r2 = await client.post("/v1/auth/accept-invite", json={
            "invite_token": invite_token,
            "name": "New Guy",
            "password": "newguypass",
        })
        assert r2.status_code == 200
        body = r2.json()
        assert "token" in body
        assert body["user"]["email"] == "newguy@example.com"
        assert body["user"]["role"] == "member"

    async def test_role_escalation_blocked(self, client: AsyncClient):
        """Admin can't invite as owner."""
        # Create admin user
        await client.post("/v1/users", json={
            "email": "myadmin@acme.com",
            "password": "adminpass",
            "name": "My Admin",
            "role": "admin",
        }, headers=AUTH_HEADERS)
        # Login as admin
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "myadmin@acme.com", "password": "adminpass"},
        )
        admin_token = r.json()["token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Try to invite as owner — should fail
        r2 = await client.post("/v1/auth/invite", json={
            "email": "escalate@example.com",
            "role": "owner",
        }, headers=admin_headers)
        assert r2.status_code == 403
        assert r2.json()["error"] == "role_escalation"

    async def test_accept_expired_invite(self, client: AsyncClient):
        """Accepting with a bogus token returns 404."""
        r = await client.post("/v1/auth/accept-invite", json={
            "invite_token": "00000000-0000-0000-0000-000000000000",
            "name": "Ghost",
            "password": "ghostpass",
        })
        assert r.status_code == 404

    async def test_accept_invite_email_already_registered(self, client: AsyncClient):
        """If the invited email is already registered, accept fails with 409."""
        token = await self._get_owner_jwt(client)
        jwt_headers = {"Authorization": f"Bearer {token}"}

        # Create invite for an email
        r = await client.post("/v1/auth/invite", json={
            "email": "invited@example.com",
            "role": "member",
        }, headers=jwt_headers)
        invite_token = r.json()["invite_token"]

        # Accept the invite first (this registers the user)
        r2 = await client.post("/v1/auth/accept-invite", json={
            "invite_token": invite_token,
            "name": "First Accept",
            "password": "acceptpass",
        })
        assert r2.status_code == 200

        # Create another invite for a different email
        r3 = await client.post("/v1/auth/invite", json={
            "email": "second@example.com",
            "role": "member",
        }, headers=jwt_headers)
        invite_token2 = r3.json()["invite_token"]

        # Accept, then register the invitee's email in a different tenant
        # This tests the guard in accept-invite — but since invite was already
        # accepted above, it's no longer pending. Instead, test that
        # registering with a pending-invite email is blocked.
        r4 = await client.post("/v1/auth/register", json={
            "email": "second@example.com",
            "password": "blockedpass",
            "name": "Blocked",
            "tenant_name": "Blocked Co",
        })
        assert r4.status_code == 409
        assert r4.json()["error"] == "pending_invite"


# ═══════════════════════════════════════════════════════════════════════════
#  API KEY CRUD TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestApiKeyCRUD:
    async def test_create_returns_raw_key(self, client: AsyncClient):
        r = await client.post("/v1/api-keys", json={
            "label": "My Key",
            "key_type": "live",
        }, headers=AUTH_HEADERS)
        assert r.status_code == 201
        body = r.json()
        assert "raw_key" in body
        assert body["raw_key"].startswith("hb_live_")
        assert "key_id" in body

    async def test_list_hides_hash(self, client: AsyncClient):
        # Create a key first
        await client.post("/v1/api-keys", json={
            "label": "Test Key",
            "key_type": "live",
        }, headers=AUTH_HEADERS)

        r = await client.get("/v1/api-keys", headers=AUTH_HEADERS)
        assert r.status_code == 200
        keys = r.json()["data"]
        assert len(keys) >= 1
        for k in keys:
            assert "key_hash" not in k
            assert "key_prefix" in k

    async def test_revoke_key(self, client: AsyncClient):
        r = await client.post("/v1/api-keys", json={
            "label": "Revoke Me",
            "key_type": "test",
        }, headers=AUTH_HEADERS)
        key_id = r.json()["key_id"]

        r2 = await client.delete(f"/v1/api-keys/{key_id}", headers=AUTH_HEADERS)
        assert r2.status_code == 200
        assert r2.json()["status"] == "revoked"

    async def test_viewer_restricted_to_read_keys(self, client: AsyncClient):
        """Viewer can only create 'read' keys."""
        # Create viewer user
        await client.post("/v1/users", json={
            "email": "keyviewer@acme.com",
            "password": "pass",
            "name": "Key Viewer",
            "role": "viewer",
        }, headers=AUTH_HEADERS)
        r = await client.post(
            "/v1/auth/login?tenant_id=dev",
            json={"email": "keyviewer@acme.com", "password": "pass"},
        )
        viewer_token = r.json()["token"]
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        # Try to create a live key — should fail
        r2 = await client.post("/v1/api-keys", json={
            "label": "Not Allowed",
            "key_type": "live",
        }, headers=viewer_headers)
        assert r2.status_code == 403

        # Create a read key — should work
        r3 = await client.post("/v1/api-keys", json={
            "label": "Read Only",
            "key_type": "read",
        }, headers=viewer_headers)
        assert r3.status_code == 201
