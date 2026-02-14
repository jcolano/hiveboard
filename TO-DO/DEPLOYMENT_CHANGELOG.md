# HiveBoard — Deployment Changelog

Tracks what has been implemented toward production, referencing the [Production Migration Plan](./PRODUCTION_MIGRATION_PLAN.md).

---

## 2026-02-14 — Auth Flow + Production Migration Phases 1-4

**Commit:** `ee337de` (and prior commits in this session)
**Branch:** `main`
**Tests:** 333 passed, 0 failures

### Auth System (completed prior to migration phases)

All auth endpoints are implemented, tested, and deployed:

| Feature | Endpoints | Status |
|---------|-----------|--------|
| Registration | `POST /v1/auth/register` | Done |
| Login (password) | `POST /v1/auth/login` | Done |
| Workspace slug check | `GET /v1/auth/check-slug` | Done |
| Accept invite | `POST /v1/auth/accept-invite` | Done |
| Send invite | `POST /v1/auth/invite` | Done |
| List invites | `GET /v1/invites` | Done |
| Cancel invite | `DELETE /v1/invites/{invite_id}` | Done |
| Create API key | `POST /v1/api-keys` | Done |
| List API keys | `GET /v1/api-keys` | Done |
| Revoke API key | `DELETE /v1/api-keys/{key_id}` | Done |
| Project CRUD | `POST/GET/PUT/DELETE /v1/projects/*` | Done |
| Project slug uniqueness | Enforced in create + update | Done |
| Tenant slug uniqueness | Enforced in register + check-slug | Done |
| 1-email-1-tenant rule | Global email uniqueness across all tenants | Done |

**Key files:**
- `src/backend/app.py` — all endpoint handlers
- `src/backend/auth.py` — JWT, password hashing, code/key/token generation
- `src/backend/middleware.py` — dual auth (API key + JWT), public paths
- `src/shared/models.py` — request/response Pydantic models
- `src/shared/storage.py` — storage protocol (auth codes, invites, global email lookup)
- `src/backend/storage_json.py` — JSON file implementations

**Frontend spec:** `docs/WEBAPP/AUTH_API_SPEC.md` (7 sections, complete)
**Frontend pages:** `docs/WEBAPP/login.html`, `register.html`, `home.html`, `accept-invite.html`

### Centralized Config System

Replaced all environment variables with `config.json` at project root.

| File | Purpose |
|------|---------|
| `src/backend/config.py` | Config loader: config.json → env var fallback → default |
| `config.example.json` | Committed template (copy to `config.json`) |
| `config.json` | Gitignored — real values per machine |

**Config fields:** `dev_key`, `dev_password`, `jwt_secret`, `jwt_expiry`, `data_dir`, `mode`, `ws_gateway_endpoint`, `ws_gateway_region`

### Phase 1: Backend CORS + WS Mode Init

**Commit:** `ee337de`

| File | Change |
|------|--------|
| `src/backend/app.py` (line ~207) | CORS middleware conditional: only added when `mode != "production"` |
| `src/backend/app.py` (lifespan) | Initializes `app.state.ws_mode` (`"local"` or `"bridge"`) and `app.state.ws_bridge` based on config |
| `src/backend/middleware.py` (line 25) | Added `"/ws/"` to `PUBLIC_PREFIXES` — bridge endpoints bypass auth middleware |

**Behavior:** In local mode (default), everything works exactly as before. CORS is on, ws_mode is `"local"`, ws_bridge is `None`.

### Phase 2: SDK Endpoint Resolution

**Commit:** `ee337de`

| File | Change |
|------|--------|
| `src/sdk/hiveloop/__init__.py` | Added `_resolve_endpoint()` — searches `./loophive.cfg` then `~/.loophive/loophive.cfg`, defaults to `https://mlbackend.net/loophive` |
| `src/sdk/hiveloop/__init__.py` | `HiveBoard.__init__` and `init()` — `endpoint` default changed from `"https://api.hiveboard.io"` to `None`, resolved via `_resolve_endpoint()` |
| `src/sdk/pyproject.toml` | Version bumped `0.1.0` → `0.1.1` |
| `.gitignore` | Added `loophive.cfg` |

**Resolution priority:**
1. Explicit `endpoint=` parameter (highest)
2. `loophive.cfg` file in cwd or `~/.loophive/`
3. Default: `https://mlbackend.net/loophive` (lowest)

### Phase 3: Frontend Environment Detection

**Commit:** `ee337de`

| File | Change |
|------|--------|
| `src/static/js/common.js` | Replaced CONFIG block: `_isLocal` detection, `wsUrl` field, production endpoint (`mlbackend.net/loophive`), dev key only for local, login redirect when no API key |
| `src/static/js/hiveboard.js` (lines 1-13) | Removed duplicate `const CONFIG = {...}`, now reads from common.js |
| `src/static/js/hiveboard.js` (`connectWebSocket`) | Branches on `CONFIG.wsUrl`: set → AWS API Gateway, null/empty → local direct WS |
| `src/static/js/hiveboard.js` (subscribe message) | Added `token: CONFIG.apiKey` for defensive re-registration (IN-1) |

**Note:** `CONFIG.wsUrl` is currently `''` (empty/falsy). After AWS API Gateway setup (Phase 5), replace with real `wss://` URL in common.js.

### Phase 4: WebSocket Bridge

**Commit:** `ee337de`

**New file: `src/backend/ws_bridge.py`**
- `BridgeConnection` class — tracks connectionId, tenant, subscription
- `WebSocketBridge` class — manages connections in memory, pushes via boto3
  - Mirrors `WebSocketManager` broadcast API: `broadcast_events()`, `broadcast_agent_status_change()`, `broadcast_agent_stuck()`, `clear_stuck()`
  - Connection management: `register()`, `unregister()`, `is_registered()`, `subscribe()`, `unsubscribe()`
  - Push: `_push()` via `apigatewaymanagementapi.post_to_connection()` with `GoneException` auto-cleanup

**Changes to `src/backend/app.py`:**

| Area | Change |
|------|--------|
| `_get_broadcaster()` helper | Returns `ws_bridge` in production, `ws_manager` locally |
| Ingest handler (Step 9) | All 4 broadcast calls use `_get_broadcaster()` instead of direct `ws_manager` (IN-2) |
| `POST /ws/connect` | Authenticates API key, registers connectionId. Returns 501 if bridge not active (IN-3) |
| `POST /ws/disconnect` | Unregisters connectionId. Returns 501 if bridge not active (IN-3) |
| `POST /ws/message` | Dispatches by action (subscribe/unsubscribe/ping). Defensively re-registers unknown connectionIds (IN-1). Returns 501 if bridge not active (IN-3) |

**Implementation notes addressed:**
- **IN-1:** Defensive re-registration on subscribe/ping for unknown connectionIds (after backend restart)
- **IN-2:** All broadcast call sites use `_get_broadcaster()` — no direct `ws_manager` calls in ingest
- **IN-3:** All `/ws/*` endpoints return 501 when bridge is not active (local mode)

---

## Remaining (Not Yet Deployed)

| Phase | Description | Owner |
|-------|-------------|-------|
| Phase 5 | AWS API Gateway WebSocket setup | Manual (owner) |
| Phase 6 | Integration testing (deploy to server + S3) | Joint |
| Phase 7 | PyPI publish `loophive` v0.1.1 | Owner |
| — | Replace `CONFIG.wsUrl` placeholder in `common.js` with real AWS URL | After Phase 5 |
| — | Replace `ws_gateway_endpoint` placeholder in production `config.json` | After Phase 5 |
| — | IIS URL Rewrite rule for `/loophive/` | Owner |
| — | IIS CORS configuration for `hiveboard.net` | Owner |
