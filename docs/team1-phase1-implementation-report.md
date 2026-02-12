# Team 1 — Phase 1 Implementation Report

> **Date:** 2026-02-11
> **Scope:** B1 (Storage Layer) + B2 (API Server) — full backend implementation
> **Status:** Complete — 72 tests passing

---

## Summary

The entire Team 1 backend is implemented and tested. The server is ready for Team 2 to wire the SDK and Dashboard against.

**Dev API key:** `hb_live_dev000000000000000000000000000000` (auto-created on first run)
**Run server:** `uvicorn backend.app:app --reload` (port 8000)
**Run tests:** `python -m pytest tests/ -v`

---

## What Was Built

| Phase | Description | File(s) | Tests |
|-------|-------------|---------|-------|
| **B1.2** | JsonStorageBackend — all 35 StorageBackend methods | `backend/storage_json.py` | 48 |
| **B1.3** | Storage test suite (protocol-level, reusable for MS SQL) | `tests/conftest.py`, `tests/test_storage.py` | ✓ |
| **B2.1** | FastAPI app + auth middleware + rate limiting + error formatting | `backend/app.py`, `backend/middleware.py` | 5 |
| **B2.2** | Ingestion endpoint — 10-step pipeline | `backend/app.py` (`POST /v1/ingest`) | 4 |
| **B2.3** | All 27 query endpoints | `backend/app.py` | 15 |
| **B2.4** | WebSocket streaming (subscription, broadcasting, ping/pong) | `backend/websocket.py` | wired |
| **B2.5** | Alerting engine — 6 condition types + cooldown + dispatch | `backend/alerting.py` | wired |

**Total: 72 tests, all passing in ~1.2 seconds.**

---

## Files Created

```
backend/
├── __init__.py
├── app.py              ← FastAPI app (27 endpoints + WebSocket + ingestion)
├── middleware.py        ← Auth (Bearer → SHA-256 → authenticate) + rate limiting
├── storage_json.py     ← JSON file storage (35 methods) + derive_agent_status()
├── websocket.py        ← WebSocket manager (subscriptions, broadcasting)
└── alerting.py         ← Alert evaluator (6 conditions, cooldown, dispatch)

tests/
├── __init__.py
├── conftest.py         ← Test fixtures (fresh storage per test, sample batch loader)
├── test_storage.py     ← 48 storage tests (tenant, auth, projects, ingestion, queries, metrics, cost, pipeline, alerts, agent status derivation)
└── test_api.py         ← 24 API tests (health, auth, ingestion, all query endpoints)
```

---

## Endpoints Implemented

### Priority 1 (Team 2 needs these first)

| # | Endpoint | Method | Purpose |
|---|----------|--------|---------|
| B2.3.1 | `/v1/agents` | GET | Agent list with derived status, sort by attention/name/last_seen |
| B2.3.2 | `/v1/agents/{agent_id}` | GET | Agent detail with stats_1h |
| B2.3.3 | `/v1/agents/{agent_id}/pipeline` | GET | Queue, TODOs, scheduled, issues |
| B2.3.4 | `/v1/tasks` | GET | Task list with derived status, filters, sort |
| B2.3.5 | `/v1/tasks/{task_id}/timeline` | GET | Full task timeline with action tree + error chains |
| B2.3.6 | `/v1/events` | GET | Activity stream with all filters |
| B2.3.12–21 | `/v1/projects/*` | CRUD | Full project management (10 endpoints) |

### Priority 2

| # | Endpoint | Method | Purpose |
|---|----------|--------|---------|
| B2.3.7 | `/v1/metrics` | GET | Aggregated metrics with timeseries buckets |
| B2.3.8 | `/v1/cost` | GET | Cost summary by agent/model |
| B2.3.9 | `/v1/cost/calls` | GET | Individual LLM calls, paginated |
| B2.3.10 | `/v1/cost/timeseries` | GET | Cost in time buckets |
| B2.3.11 | `/v1/llm-calls` | GET | LLM calls with totals wrapper |
| B2.3.22–26 | `/v1/alerts/*` | CRUD | Alert rules + history (5 endpoints) |

### Infrastructure

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check — both teams use this to verify server is running |
| `/dashboard` | GET | Serves v3 prototype HTML (interim until Team 2 delivers) |
| `/v1/ingest` | POST | The critical write path — 10-step ingestion pipeline |
| `/v1/stream` | WebSocket | Real-time event + agent status streaming |

---

## Key Design Decisions

### 1. Agent Status Derivation — Single Implementation

`derive_agent_status(agent, now)` lives in `backend/storage_json.py` and is the **one source of truth** for the priority cascade:

```
1. stuck:            last_heartbeat is None OR age > stuck_threshold_seconds
2. error:            last_event_type in (task_failed, action_failed)
3. waiting_approval: last_event_type = approval_requested
4. processing:       last_event_type in (task_started, action_started)
5. idle:             everything else
```

Called from: storage queries, REST responses (`GET /v1/agents`), WebSocket broadcasts, and alerting.

### 2. Ingestion Pipeline — 10 Steps

```
1.  Authenticate (derive tenant_id from API key)
2.  Validate envelope (agent_id required, batch size limits)
3.  Per-event validation (required fields, event_type enum, field sizes)
3b. Payload convention validation (advisory — warn but don't reject)
4.  Expand envelope (merge agent metadata, set received_at, severity defaults)
5.  Validate project_id (if present, must exist for tenant)
6.  Batch INSERT events (dedup by tenant_id + event_id)
7.  Update agents cache (upsert agent profile)
8.  Update project_agents junction
9.  Broadcast to WebSocket subscribers
10. Evaluate alert rules
```

### 3. Dev Bootstrap

On first run, the server auto-creates:
- Tenant: `dev` / "Development"
- API key: `hb_live_dev000000000000000000000000000000` (type: `live`)

This means you can start testing immediately with no setup.

### 4. WebSocket Streaming

- Auth via query param: `ws://localhost:8000/v1/stream?token={api_key}`
- Subscribe to channels: `events`, `agents`
- Filter by: project_id, environment, group, agent_id, event_types, min_severity
- Broadcasts: `event.new`, `agent.status_changed`, `agent.stuck`
- Ping/pong keep-alive (30s interval, 3-miss disconnect)
- Max 5 connections per API key

### 5. Alerting Engine — 6 Conditions

| Condition | Trigger |
|-----------|---------|
| `agent_stuck` | Agent heartbeat older than threshold |
| `task_failed` | `task_failed` event in batch |
| `error_rate` | Failed actions / total actions > threshold% in window |
| `duration_exceeded` | `task_completed` with duration_ms > threshold |
| `heartbeat_lost` | No heartbeat from specific agent in window |
| `cost_threshold` | LLM cost exceeds threshold_usd in window |

All respect cooldown. Actions: `webhook` (logged for MVP), `email` (logged for MVP).

---

## Known Limitations (MVP)

1. **JSON file storage grows fast.** With 10 agents at 30-second heartbeats + task events, ~35K events/day. The JSON file will be several MB within a day. The MS SQL Server adapter is a practical necessity once real testing begins.

2. **Write-through persistence.** Every mutation writes the full JSON file. Acceptable for MVP, not for production.

3. **Rate limiting is in-memory.** Resets on server restart. Fine for MVP.

4. **WebSocket not tested end-to-end in automated tests.** The manager, subscriptions, and broadcasting logic are unit-testable, but full WebSocket integration requires a real connection. Manually verified via smoke test.

5. **Alert actions are logged, not dispatched.** Webhook POST and email sending are stubbed — they log the intent. Real dispatch is a future task.

6. **Dashboard serving is interim.** `GET /dashboard` serves `docs/hiveboard-dashboard-v3.html`. When Team 2 delivers their dashboard, this route should be removed or redirected.

---

## What's Left (B3 — Hardening)

| Task | Description | Priority |
|------|-------------|----------|
| B3.1 | Rate limiting refinement (separate ingest/query counters) | Low — already functional |
| B3.2 | Request validation hardening (query param ranges, invalid values) | Low |
| B3.3 | Heartbeat compaction (background task, hourly) | Medium — needed when simulator runs |
| B3.4 | Data retention (background task, daily, per-plan limits) | Medium |
| B3.5 | Graceful shutdown (close WebSockets, flush alerts) | Low |

None of these block Team 2. The server is fully functional for integration.

---

## Test Coverage Summary

### Storage Tests (48)
- Tenant & Auth: create, get, authenticate, revoke, list, touch (9 tests)
- Projects: create, list, archive, update (4 tests)
- Ingestion: insert batch, deduplication, agent upsert, junction (4 tests)
- Queries: events (filters, pagination, time range), tasks (status, sort) (11 tests)
- Metrics & Cost: summary, calls, filter by model, by_agent, by_model (5 tests)
- Pipeline: all sections, todo lifecycle, empty agent (3 tests)
- Alerts: CRUD, history, last_for_rule, filter enabled (4 tests)
- Agent Status Derivation: all 5 states + priority ordering (8 tests)

### API Tests (24)
- Health & Auth: health, no auth, invalid key, valid auth, rate limit headers (5 tests)
- Ingestion: sample batch, dedup, invalid type, missing field (4 tests)
- Query Endpoints: agents, agent detail, not found, pipeline, tasks, timeline, events, heartbeats, metrics, cost, cost calls, projects, create project, alert rules, create alert (15 tests)
