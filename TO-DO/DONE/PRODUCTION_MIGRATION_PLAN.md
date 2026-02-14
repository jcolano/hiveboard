# HiveBoard — Production Migration Plan

> **Date:** 2026-02-14
> **Deadline:** Hackathon — 48 hours
> **Storage:** JSON files (database migration deferred post-hackathon)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Infrastructure Map](#2-infrastructure-map)
3. [Backend — IIS + Python](#3-backend--iis--python)
4. [SDK — Config File Endpoint Resolution](#4-sdk--config-file-endpoint-resolution)
5. [Frontend — S3 + Environment Detection](#5-frontend--s3--environment-detection)
6. [WebSockets — AWS API Gateway](#6-websockets--aws-api-gateway)
7. [CORS Strategy](#7-cors-strategy)
8. [Data Flow — End to End](#8-data-flow--end-to-end)
9. [User Journey — Registration to Dashboard](#9-user-journey--registration-to-dashboard)
10. [File-by-File Change List](#10-file-by-file-change-list)
11. [Implementation Order](#11-implementation-order)
12. [Open Items & Risks](#12-open-items--risks)

---

## 1. Architecture Overview

```
┌──────────────────────┐        HTTPS POST         ┌───────────────────────────────────┐
│  User's Agentic      │  ───────────────────────►  │  mlbackend.net (Windows 2012)     │
│  System              │   /loophive/v1/ingest      │  IIS + SSL + URL Rewrite          │
│  (pip install        │                            │  ^loophive/(.*) → localhost:8451   │
│   loophive)          │                            │                                   │
│                      │                            │  ┌─────────────────────────────┐  │
│  SDK endpoint:       │                            │  │ Python Backend (port 8451)  │  │
│  mlbackend.net       │                            │  │ uvicorn backend.app:app     │  │
│  /loophive           │                            │  │ FastAPI + JSON storage      │  │
└──────────────────────┘                            │  └──────────┬──────────────────┘  │
                                                    └─────────────┼─────────────────────┘
                                                                  │
                                     ┌────────────────────────────┼──────────────────────┐
                                     │                            │                      │
                              HTTPS GET/POST               AWS API GW Mgmt API          │
                              (REST API calls)             (push to @connections)         │
                                     │                            │                      │
                                     ▼                            ▼                      │
                        ┌─────────────────────────────────────────────────────┐          │
                        │  hiveboard.net (AWS S3 + CloudFront)                │          │
                        │  Static frontend: index.html, insights.html,       │          │
                        │  css/, js/                                          │          │
                        │                                                     │          │
                        │  Also: Login / Register / Home / Accept-Invite     │          │
                        │                                                     │          │
                        │  WebSocket connects to:                            │          │
                        │  wss://{api-gw-id}.execute-api.{region}.aws/prod   │◄─────────┘
                        └─────────────────────────────────────────────────────┘
```

---

## 2. Infrastructure Map

| Component        | Location                                    | Domain                  | Managed By   |
|------------------|---------------------------------------------|-------------------------|--------------|
| Backend API      | Windows Server 2012, IIS, Python on :8451   | mlbackend.net/loophive  | IIS + NSSM   |
| Frontend         | AWS S3 + CloudFront                         | hiveboard.net           | AWS          |
| WebSocket API    | AWS API Gateway (WebSocket)                 | (assigned by AWS)       | AWS          |
| SSL (backend)    | IIS certificate on mlbackend.net            | mlbackend.net           | IIS          |
| SSL (frontend)   | CloudFront + ACM                            | hiveboard.net           | AWS          |
| SDK (PyPI)       | pypi.org/project/loophive                   | N/A (pip install)       | You          |

**SSL note:** `mlbackend.net` already has SSL via IIS. The IIS → Python path is `http://localhost:8451` (internal only, no SSL needed on loopback).

---

## 3. Backend — IIS + Python

### 3.1 IIS URL Rewrite Rule

```xml
<!-- In IIS URL Rewrite for mlbackend.net site -->
<rule name="LoopHive Backend">
  <match url="^loophive/(.*)" />
  <action type="Rewrite" url="http://localhost:8451/{R:1}" />
</rule>
```

**Result:** `https://mlbackend.net/loophive/v1/agents` → `http://localhost:8451/v1/agents`

### 3.2 Uvicorn Command

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8451
```

**Important:** Bind to `127.0.0.1` only — not `0.0.0.0`. IIS is the public face; Python is internal-only.

### 3.3 Process Management

Owner handles this (NSSM, Windows Service, or similar). The Python process must auto-restart on crash.

### 3.4 Backend Config (Existing Centralized System)

HiveBoard already has a centralized config system:

- **Config loader:** `src/backend/config.py` — reads `config.json` from the project root
- **Template:** `config.example.json` — committed to git (copy to `config.json` and fill in values)
- **Gitignored:** `config.json` is in `.gitignore` (secrets stay out of git)

**Production `config.json`** (on Windows Server):

```json
{
  "dev_key": "hb_live_ACTUAL_PRODUCTION_KEY_HERE",
  "dev_password": "a-strong-password-here",
  "jwt_secret": "a-strong-random-secret-here",
  "jwt_expiry": 3600,
  "data_dir": "data",
  "mode": "production",
  "ws_gateway_endpoint": "https://{api-id}.execute-api.{region}.amazonaws.com/{stage}",
  "ws_gateway_region": "us-east-1"
}
```

**Local development `config.json`** (developer's machine):

```json
{
  "dev_key": "hb_live_dev000000000000000000000000000000",
  "dev_password": "localdev",
  "jwt_secret": "dev-secret",
  "jwt_expiry": 3600,
  "data_dir": "data",
  "mode": "local"
}
```

**How it's used in code:**

```python
from backend.config import get as _cfg

mode = _cfg("mode", "local")
ws_endpoint = _cfg("ws_gateway_endpoint", "")
ws_region = _cfg("ws_gateway_region", "us-east-1")
```

> **No environment variables.** All configuration goes through `config.json`. The config loader has env var fallback for CI/test environments only.

### 3.5 Changes to `src/backend/app.py`

| Area | Current | Change |
|------|---------|--------|
| CORS middleware | `CORSMiddleware(allow_origins=["*"])` always added | Conditionally add only when `_cfg("mode", "local") != "production"`. IIS handles CORS in production. |
| `GET /dashboard` route | Serves HTML | Keep for local dev. In production, S3 serves the frontend. |
| `app.mount("/static", ...)` | Serves static files | Keep for local dev. Harmless in production (IIS won't route to it). |
| `@app.websocket("/v1/stream")` | Direct WebSocket | **Keep** for local development. Add **3 new HTTP endpoints** for AWS API GW WebSocket bridge (see Section 6). |
| Lifespan | Initializes storage | Add bridge initialization based on config mode (see Section 6.5). |
| Ingest handler | Broadcasts via ws_manager | Branch on mode to push via bridge or direct WS (see Section 6.5). |

### 3.6 Changes to `src/backend/middleware.py`

The 3 WebSocket bridge endpoints (`/ws/connect`, `/ws/disconnect`, `/ws/message`) are called by AWS API Gateway, not by users. They must bypass the auth middleware — authentication happens inside the handlers themselves.

```python
PUBLIC_PREFIXES = ("/v1/stream", "/static", "/ws/")
```

---

## 4. SDK — Config File Endpoint Resolution

### 4.1 Resolution Priority

```
1. Explicit endpoint= parameter in hiveloop.init()     ← highest
2. loophive.cfg file found in cwd or ~/.loophive/       ← fallback
3. Default: "https://mlbackend.net/loophive"            ← lowest
```

### 4.2 Config File Format

**File name:** `loophive.cfg`
**Search order:**
  1. Current working directory (`./loophive.cfg`)
  2. User home directory (`~/.loophive/loophive.cfg`)

```ini
[loophive]
endpoint = http://localhost:8451
```

> **Note:** `loophive.cfg` is in `.gitignore` so developers don't accidentally commit their local endpoint override.

### 4.3 Changes to `src/sdk/hiveloop/__init__.py`

**New helper function** (add before `HiveBoard` class):

```python
import configparser
from pathlib import Path

_DEFAULT_ENDPOINT = "https://mlbackend.net/loophive"

def _resolve_endpoint() -> str:
    """Resolve the backend endpoint from config file or default.

    Search order:
      1. ./loophive.cfg  (current working directory)
      2. ~/.loophive/loophive.cfg  (user home)
      3. Default production URL
    """
    candidates = [
        Path.cwd() / "loophive.cfg",
        Path.home() / ".loophive" / "loophive.cfg",
    ]
    for path in candidates:
        if path.is_file():
            cfg = configparser.ConfigParser()
            cfg.read(path)
            ep = cfg.get("loophive", "endpoint", fallback=None)
            if ep:
                logger.debug("Endpoint resolved from %s: %s", path, ep)
                return ep.strip().rstrip("/")
    return _DEFAULT_ENDPOINT
```

**Modified signatures:**

```python
# HiveBoard.__init__  (line 47)
def __init__(
    self,
    api_key: str,
    endpoint: str | None = None,    # ← Changed from "https://api.hiveboard.io"
    ...
) -> None:
    self._endpoint = endpoint or _resolve_endpoint()
    ...

# init()  (line 135)
def init(
    api_key: str,
    environment: str = "production",
    group: str = "default",
    endpoint: str | None = None,    # ← Changed from "https://api.hiveboard.io"
    ...
) -> HiveBoard:
    ...
```

### 4.4 Changes to `src/sdk/pyproject.toml`

```toml
version = "0.1.1"   # Bump for production endpoint change
```

### 4.5 SDK Usage — Production vs Local

**Production user** (no config file needed):
```python
import hiveloop
hb = hiveloop.init(api_key="hb_live_xxxxx")
# → endpoint defaults to https://mlbackend.net/loophive
```

**Local developer** (has `loophive.cfg` in project root):
```ini
# ./loophive.cfg
[loophive]
endpoint = http://localhost:8451
```
```python
import hiveloop
hb = hiveloop.init(api_key="hb_live_dev000000000000000000000000000000")
# → endpoint resolved from loophive.cfg: http://localhost:8451
```

**Explicit override** (always wins):
```python
hb = hiveloop.init(api_key="hb_live_xxx", endpoint="http://localhost:9999")
```

---

## 5. Frontend — S3 + Environment Detection

### 5.1 Files to Upload to S3

```
hiveboard.net/
├── static/
│   ├── index.html          (main dashboard)
│   ├── insights.html       (insights page)
│   ├── css/
│   │   └── hiveboard.css
│   └── js/
│       ├── common.js
│       └── hiveboard.js
├── login.html              (webapp — exists in docs/WEBAPP/)
├── register.html           (webapp — exists in docs/WEBAPP/)
├── home.html               (webapp — exists in docs/WEBAPP/)
└── accept-invite.html      (webapp — exists in docs/WEBAPP/)
```

### 5.2 Changes to `src/static/js/common.js`

**Replace the CONFIG block** (lines 8-18):

```javascript
// ── Environment Detection ────────────────────────
var _isLocal = (window.location.hostname === 'localhost'
  || window.location.hostname === '127.0.0.1');

var CONFIG = {
  endpoint: _isLocal
    ? window.location.origin
    : 'https://mlbackend.net/loophive',
  wsUrl: _isLocal
    ? null                              // null = derive from endpoint (current behavior)
    : 'wss://{api-gw-id}.execute-api.{region}.amazonaws.com/prod',
  apiKey: new URLSearchParams(window.location.search).get('apiKey')
    || localStorage.getItem('hiveboard_api_key')
    || (_isLocal ? 'hb_live_dev000000000000000000000000000000' : ''),
  pollInterval: 5000,
  maxStreamEvents: 50,
  refreshInterval: 30000,
};
```

**Key changes:**
- `endpoint` — `localhost` uses same-origin; production uses `mlbackend.net/loophive`
- `wsUrl` — `null` for local (derive from endpoint, current behavior); explicit AWS URL for production
- `apiKey` — dev key fallback only for local; in production, empty string forces login redirect
- `{api-gw-id}` and `{region}` will be replaced with actual values after creating the AWS WebSocket API

### 5.3 Changes to `src/static/js/hiveboard.js`

**Replace the CONFIG block** (lines 5-13):

Remove the duplicate CONFIG definition. `hiveboard.js` currently defines its own CONFIG that overrides common.js. Change to:

```javascript
// ═══════════════════════════════════════════════════
//  HIVEBOARD v2 — CONFIGURATION
// ═══════════════════════════════════════════════════

// CONFIG is defined in common.js (loaded first)
// hiveboard.js just reads from it.
```

The `<script>` load order in index.html already loads common.js first. But currently hiveboard.js re-declares CONFIG with `const`, which shadows it. This must be removed so both files share the same CONFIG from common.js.

**Replace the WebSocket connection** (lines 1830-1870):

```javascript
function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    var wsUrl;
    if (CONFIG.wsUrl) {
      // Production: use AWS API Gateway WebSocket URL
      wsUrl = CONFIG.wsUrl + '?token=' + encodeURIComponent(CONFIG.apiKey);
    } else {
      // Local: derive from HTTP endpoint (current behavior)
      var url = new URL(CONFIG.endpoint);
      var wsProto = url.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = wsProto + '//' + url.host + '/v1/stream?token=' + encodeURIComponent(CONFIG.apiKey);
    }
    ws = new WebSocket(wsUrl);

    ws.onopen = function() { ... };  // unchanged
    ws.onmessage = function(evt) { ... };  // unchanged
    ws.onclose = function() { ... };  // unchanged
    ws.onerror = function() { ... };  // unchanged
  } catch (err) {
    ...
  }
}
```

### 5.4 No API Key = Redirect to Login

**Add to common.js** (after CONFIG):

```javascript
// In production, if no API key and no JWT, redirect to login
if (!_isLocal && !CONFIG.apiKey) {
  window.location.href = '/login.html';
}
```

### 5.5 Dashboard Auth Strategy

For the hackathon, the dashboard continues to use **API keys** for authentication:

- User logs in → home.html shows their API key(s)
- User clicks "Open Dashboard" → redirects to `/static/index.html?apiKey=hb_live_xxx`
- Dashboard stores the API key in `localStorage` for subsequent visits
- REST API calls and WebSocket both use the API key

**Post-hackathon improvement:** Migrate the dashboard to use JWT for session auth. The API key should only be exposed for SDK usage (copy to code), not passed in browser URLs. JWTs are short-lived and more appropriate for browser sessions.

---

## 6. WebSockets — AWS API Gateway (HTTP Integration)

### 6.1 Architecture: No Lambda, No DynamoDB

AWS API Gateway WebSocket API routes use **HTTP integrations** that forward directly to the backend. The API Gateway passes `connectionId` as a mapped request header. The backend stores connections **in memory** (same as today, just adapted).

```
Dashboard Browser
    │
    ▼ wss://
┌──────────────────────────────┐
│  AWS API Gateway (WebSocket) │
│                              │
│  $connect    ──► HTTP POST ──┼──► https://mlbackend.net/loophive/ws/connect
│  $disconnect ──► HTTP POST ──┼──► https://mlbackend.net/loophive/ws/disconnect
│  $default    ──► HTTP POST ──┼──► https://mlbackend.net/loophive/ws/message
│                              │
│  Each integration maps:      │
│  connectionId ←              │
│    context.connectionId      │
│  (Request Parameter Mapping) │
└──────────────────────────────┘
         ▲
         │  API Gateway Management API
         │  POST @connections/{connId}
         │
┌────────┴─────────────────────┐
│  Python Backend (port 8451)  │
│  Pushes messages via boto3   │
└──────────────────────────────┘
```

> **Note:** We use **3 routes** ($connect, $disconnect, $default) instead of per-action routes. The `$default` route catches all client messages and the backend dispatches by `action` field. This aligns with the [AWS WebSocket Setup Guide](../docs/AWS_WEBSOCKET_SETUP_GUIDE.md) and requires fewer integrations to configure.

### 6.2 AWS API Gateway — Route Configuration

**1 WebSocket API** with 3 routes:

#### Route: `$connect`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/connect`
- **Method:** POST
- **Passthrough:** `WHEN_NO_MATCH`
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```
- **Query string passthrough:** `?token=xxx` passes through automatically in HTTP_PROXY mode (no mapping needed)

#### Route: `$disconnect`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/disconnect`
- **Method:** POST
- **Passthrough:** `WHEN_NO_MATCH`
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```

#### Route: `$default`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/message`
- **Method:** POST
- **Passthrough:** `WHEN_NO_MATCH`
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```
- **Body passthrough:** The JSON body (e.g., `{ "action": "subscribe", "channels": [...] }`) is forwarded as-is.

#### AWS CLI Setup Commands

See [AWS WebSocket Setup Guide](../docs/AWS_WEBSOCKET_SETUP_GUIDE.md) for detailed step-by-step instructions. Key commands:

```bash
# Add connectionId mapping to each integration (CRITICAL — without this, backend can't identify connections)
aws apigatewayv2 update-integration \
  --api-id <API_ID> \
  --integration-id <INTEGRATION_ID> \
  --request-parameters '{"integration.request.header.connectionId": "context.connectionId"}'

# Redeploy after changes
aws apigatewayv2 create-deployment --api-id <API_ID> --stage-name <STAGE>
```

#### IAM Permissions

The IAM user/role on the Windows Server needs `execute-api:ManageConnections` to push messages back:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "execute-api:ManageConnections",
            "Resource": "arn:aws:execute-api:<REGION>:<ACCOUNT_ID>:<API_ID>/<STAGE>/POST/@connections/*"
        }
    ]
}
```

### 6.3 Backend — New HTTP Endpoints for WebSocket Bridge

**New file:** `src/backend/ws_bridge.py`

This module provides 3 HTTP endpoints called by AWS API Gateway, plus a `WebSocketBridge` class that manages connections and pushes messages via boto3.

```python
"""WebSocket bridge — HTTP endpoints for AWS API Gateway WebSocket integration.

In production, the dashboard connects to AWS API Gateway (WebSocket API).
API Gateway forwards connect/disconnect/message as HTTP POST requests to
these endpoints, with connectionId in the request header.

The backend pushes messages to clients via the API Gateway Management API.
"""
```

#### Endpoint: `POST /ws/connect`

```python
@app.post("/ws/connect")
async def ws_connect(request: Request):
    """Called by AWS API Gateway on $connect."""
    connection_id = request.headers.get("connectionId")
    token = request.query_params.get("token", "")

    if not connection_id or not token:
        return JSONResponse({"error": "missing connectionId or token"}, status_code=400)

    # Authenticate same as current WebSocket handler (API key)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    info = await request.app.state.storage.authenticate(key_hash)
    if info is None:
        return JSONResponse({"error": "invalid API key"}, status_code=403)

    # Store connectionId in the bridge manager
    bridge = request.app.state.ws_bridge
    bridge.register(connection_id, info.tenant_id, info.key_id)
    return JSONResponse({"status": "connected"})
```

#### Endpoint: `POST /ws/disconnect`

```python
@app.post("/ws/disconnect")
async def ws_disconnect(request: Request):
    """Called by AWS API Gateway on $disconnect."""
    connection_id = request.headers.get("connectionId")
    if connection_id:
        request.app.state.ws_bridge.unregister(connection_id)
    return JSONResponse({"status": "disconnected"})
```

#### Endpoint: `POST /ws/message`

```python
@app.post("/ws/message")
async def ws_message(request: Request):
    """Called by AWS API Gateway on $default (all client messages)."""
    connection_id = request.headers.get("connectionId")
    if not connection_id:
        return JSONResponse({"error": "missing connectionId"}, status_code=400)

    body = await request.json()
    action = body.get("action", "")
    bridge = request.app.state.ws_bridge

    if action == "subscribe":
        channels = body.get("channels", [])
        filters = body.get("filters", {})
        bridge.subscribe(connection_id, channels, filters)
        return JSONResponse({"status": "subscribed", "channels": channels})

    elif action == "unsubscribe":
        channels = body.get("channels", [])
        bridge.unsubscribe(connection_id, channels)
        return JSONResponse({"status": "unsubscribed", "channels": channels})

    elif action == "ping":
        # Client heartbeat — if connectionId is unknown, re-register defensively
        token = body.get("token")
        if token and not bridge.is_registered(connection_id):
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            info = await request.app.state.storage.authenticate(key_hash)
            if info:
                bridge.register(connection_id, info.tenant_id, info.key_id)
        return JSONResponse({"status": "pong"})

    return JSONResponse({"status": "unknown_action"})
```

### 6.4 Backend — WebSocket Bridge Manager

**New file:** `src/backend/ws_bridge.py`

Adapts the existing `Subscription` class (from `websocket.py`) to work with connectionId strings instead of WebSocket objects. Uses `boto3` to push messages.

```python
class BridgeConnection:
    """A connection tracked by connectionId (not a live WebSocket)."""
    def __init__(self, connection_id: str, tenant_id: str, key_id: str):
        self.connection_id = connection_id
        self.tenant_id = tenant_id
        self.key_id = key_id
        self.subscription = Subscription()  # Reused from websocket.py

class WebSocketBridge:
    """Manages AWS API Gateway WebSocket connections."""

    def __init__(self, gateway_endpoint: str, region: str):
        self._connections: dict[str, BridgeConnection] = {}  # connId → BridgeConnection
        self._tenant_index: dict[str, list[str]] = defaultdict(list)  # tenantId → [connIds]
        self._apigw_client = boto3.client(
            'apigatewaymanagementapi',
            endpoint_url=gateway_endpoint,
            region_name=region,
        )

    def is_registered(self, connection_id: str) -> bool:
        return connection_id in self._connections

    def register(self, connection_id, tenant_id, key_id): ...
    def unregister(self, connection_id): ...
    def subscribe(self, connection_id, channels, filters): ...
    def unsubscribe(self, connection_id, channels): ...

    async def broadcast_events(self, tenant_id: str, events: list[dict]):
        """Push events to matching subscribers via API Gateway Management API."""
        for conn_id in self._tenant_index.get(tenant_id, []):
            conn = self._connections.get(conn_id)
            if not conn:
                continue
            for event in events:
                if conn.subscription.matches_event(event):
                    self._push(conn_id, {"type": "event.new", "data": event})

    async def broadcast_agent_status_change(self, tenant_id, agent_id, previous_status, new_status, **kwargs):
        """Push agent status change to subscribers — mirrors ws_manager API."""
        msg = {
            "type": "agent.status_changed",
            "data": {
                "agent_id": agent_id,
                "previous_status": previous_status,
                "new_status": new_status,
                **kwargs,
            },
        }
        for conn_id in self._tenant_index.get(tenant_id, []):
            conn = self._connections.get(conn_id)
            if conn and conn.subscription.matches_agent():
                self._push(conn_id, msg)

    def _push(self, connection_id: str, data: dict):
        """Send message to a specific connection via API Gateway."""
        try:
            self._apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(data).encode('utf-8'),
            )
        except self._apigw_client.exceptions.GoneException:
            # Client disconnected but $disconnect didn't fire
            self.unregister(connection_id)
        except Exception:
            logger.warning("Failed to push to connection %s", connection_id)
```

### 6.5 Backend — Choosing Local WS vs AWS Bridge

In `app.py` lifespan, based on `config.json`:

```python
from backend.config import get as _cfg

# In lifespan()
mode = _cfg("mode", "local")

if mode == "production":
    ws_endpoint = _cfg("ws_gateway_endpoint", "")
    ws_region = _cfg("ws_gateway_region", "us-east-1")
    if ws_endpoint:
        from backend.ws_bridge import WebSocketBridge
        bridge = WebSocketBridge(gateway_endpoint=ws_endpoint, region=ws_region)
        app.state.ws_bridge = bridge
        app.state.ws_mode = "bridge"
    else:
        logger.warning("Production mode but no ws_gateway_endpoint — falling back to local WS")
        app.state.ws_bridge = None
        app.state.ws_mode = "local"
else:
    app.state.ws_bridge = None
    app.state.ws_mode = "local"
```

In the ingest handler, after storing events:

```python
# After storing events in /v1/ingest handler
if app.state.ws_mode == "bridge":
    await app.state.ws_bridge.broadcast_events(tenant_id, stored_events)
else:
    from backend.websocket import ws_manager
    await ws_manager.broadcast_events(tenant_id, stored_events)
```

### 6.6 Frontend — WebSocket Connection

The subscribe message format stays the same. The only change is the initial connection URL:

```javascript
// In hiveboard.js connectWebSocket()
var wsUrl;
if (CONFIG.wsUrl) {
  // Production: AWS API Gateway WebSocket
  wsUrl = CONFIG.wsUrl + '?token=' + encodeURIComponent(CONFIG.apiKey);
} else {
  // Local: direct WebSocket to backend
  var url = new URL(CONFIG.endpoint);
  var wsProto = url.protocol === 'https:' ? 'wss:' : 'ws:';
  wsUrl = wsProto + '//' + url.host + '/v1/stream?token=' + encodeURIComponent(CONFIG.apiKey);
}
ws = new WebSocket(wsUrl);

// The onopen subscribe message is unchanged:
ws.send(JSON.stringify({
  action: 'subscribe',
  channels: ['events', 'agents'],
  filters: { environment: ..., min_severity: 'info' },
}));
```

### 6.7 Reconnection Behavior

When the Python backend restarts, all in-memory connectionIds are lost. Dashboard clients remain connected at the API Gateway level but the backend won't know about them.

**What happens:**
1. Client sends `subscribe` → API Gateway forwards to `/ws/message` → Backend receives connectionId it doesn't know → Backend logs a warning but returns OK
2. Client sends `ping` with token → Backend re-registers the connectionId defensively (see Section 6.3 ping handler)
3. If backend push fails with `GoneException` → connection is cleaned up
4. Dashboard reconnect logic already handles disconnections with exponential backoff

---

## 7. CORS Strategy

### 7.1 Production

**IIS handles all CORS.** The Python backend must NOT add CORS headers, or browsers will see double CORS headers and reject requests.

### 7.2 Local Development

The Python backend adds CORS middleware **only** when config mode is not `"production"`:

```python
from backend.config import get as _cfg

if _cfg("mode", "local") != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

### 7.3 IIS CORS Configuration (for reference)

The owner configures IIS to allow:
- **Origin:** `https://hiveboard.net`, `https://www.hiveboard.net`
- **Methods:** GET, POST, PUT, DELETE, OPTIONS
- **Headers:** Authorization, Content-Type
- **Credentials:** false (API key is in Authorization header, not cookies)

---

## 8. Data Flow — End to End

### 8.1 SDK User → Backend (Event Ingestion)

```
Developer's agentic system
    │
    │  import hiveloop
    │  hb = hiveloop.init(api_key="hb_live_xxxx")
    │  # endpoint defaults to https://mlbackend.net/loophive
    │
    │  agent = hb.agent("my-agent")
    │  with agent.task("t1") as task:
    │      task.llm_call(...)
    │
    ▼ HTTP POST (batched every 5s)
https://mlbackend.net/loophive/v1/ingest
    │
    ▼ IIS URL Rewrite
http://localhost:8451/v1/ingest
    │
    ▼ Backend processes & stores
    │
    ├── Stores events in JSON file storage
    ├── Updates agent status/metrics
    └── Broadcasts to connected dashboards:
        ├── [local]  direct WebSocket push
        └── [prod]   API Gateway Management API → @connections/{connId}
```

### 8.2 Dashboard → Backend (API Queries)

```
User opens https://hiveboard.net/static/index.html
    │
    │ (API key loaded from localStorage, set during login flow)
    │
    ▼ JS: CONFIG.endpoint = "https://mlbackend.net/loophive"
    │
    ▼ fetch("https://mlbackend.net/loophive/v1/agents",
    │       { headers: { Authorization: "Bearer hb_live_xxx" } })
    │
    ▼ IIS URL Rewrite → http://localhost:8451/v1/agents
    │
    ▼ Backend returns JSON → dashboard renders
```

### 8.3 Dashboard → AWS API Gateway → Backend (WebSocket)

```
Dashboard JS: new WebSocket("wss://{api-gw-id}.execute-api.{region}.amazonaws.com/prod?token=hb_live_xxx")
    │
    ▼ $connect route fires
    │
    ▼ API Gateway HTTP POST → https://mlbackend.net/loophive/ws/connect
    │   Header: connectionId = "abc123def"
    │   Query:  token = "hb_live_xxx"   (passthrough from wss:// URL)
    │
    ▼ Backend authenticates API key, stores connectionId in memory
    │
    ▼ Dashboard sends: { action: "subscribe", channels: ["events","agents"] }
    │
    ▼ $default route fires
    │
    ▼ API Gateway HTTP POST → https://mlbackend.net/loophive/ws/message
    │   Header: connectionId = "abc123def"
    │   Body: { "action": "subscribe", "channels": ["events","agents"], "filters": {...} }
    │
    ▼ Backend stores subscription for connectionId
    │
    │  ... later, SDK sends events via /v1/ingest ...
    │
    ▼ Backend matches events to subscriptions
    │
    ▼ Backend calls: boto3 apigatewaymanagementapi.post_to_connection(
    │     ConnectionId="abc123def", Data=json.dumps(event_msg))
    │
    ▼ AWS API Gateway pushes message to Dashboard WebSocket
    │
    ▼ Dashboard ws.onmessage receives event, updates UI
```

---

## 9. User Journeys

### 9.1 Registration to Dashboard

```
1. User visits https://hiveboard.net
       │
       ▼
2. Clicks "Register" → /register.html
       │ Enters: name, email, password, workspace name
       │ Frontend validates workspace slug in real-time
       │   (GET mlbackend.net/loophive/v1/auth/check-slug?slug=acme-inc)
       │
       ▼ POST to backend (mlbackend.net/loophive/v1/auth/register)
       │
3. Backend creates:
       │ ├── Tenant (workspace: "acme-inc")
       │ ├── User account (role: owner)
       │ ├── Default project ("My Project" / "my-project")
       │ └── Default API key (hb_live_xxxx)
       │
       ▼ Returns: user info + api_key
       │
4. Frontend stores API key, redirects to /login.html
       │ Enters: email, password
       │
       ▼ POST to backend (mlbackend.net/loophive/v1/auth/login)
       │
5. Backend returns: JWT + user info
       │ Frontend stores JWT in localStorage
       │
       ▼
6. User lands on /home.html
       │ Reads JWT from localStorage for API calls
       │ Sees: workspace name, projects list, API keys
       │ Can: create more projects, generate more API keys
       │ Can: invite team members, manage settings
       │ Can: copy SDK install instructions with their API key
       │
       ▼
7. User clicks "Open Dashboard"
       │
       ▼ Redirect to /static/index.html
       │ (API key loaded from localStorage)
       │
       ▼
8. Dashboard loads, CONFIG detects production mode,
   fetches data from mlbackend.net/loophive, connects
   WebSocket via AWS API Gateway.
```

### 9.2 Invited User Journey

```
1. Owner invites user@example.com from /home.html (Settings → Invites)
       │
       ▼ POST mlbackend.net/loophive/v1/auth/invite
       │   (backend validates: email not registered anywhere, no pending invite)
       │
       ▼ Returns: invite_token (MVP: shown in response + logged to console)
       │
2. Owner shares invite link with invitee:
       │   https://hiveboard.net/accept-invite.html?token={invite_token}
       │
       ▼
3. Invitee opens link → /accept-invite.html
       │ Page reads token from URL, calls backend to validate
       │ Enters: name, password
       │
       ▼ POST mlbackend.net/loophive/v1/auth/accept-invite
       │
4. Backend creates user in owner's tenant, returns JWT
       │
       ▼
5. Invitee redirected to /home.html (now a member of the workspace)
```

---

## 10. File-by-File Change List

### SDK Files

| File | Change | Details |
|------|--------|---------|
| `src/sdk/hiveloop/__init__.py` | **Modify** | Add `_resolve_endpoint()` helper. Change default `endpoint` param from `"https://api.hiveboard.io"` to `None`. Use `_resolve_endpoint()` when None. |
| `src/sdk/pyproject.toml` | **Modify** | Bump version to `0.1.1`. |

### Backend Files

| File | Change | Details |
|------|--------|---------|
| `src/backend/app.py` | **Modify** | (1) Make CORS middleware conditional on `_cfg("mode", "local") != "production"`. (2) Add 3 new POST endpoints for WS bridge: `/ws/connect`, `/ws/disconnect`, `/ws/message`. (3) In ingest handler, branch on `ws_mode` to push via bridge or direct WS. (4) In lifespan, initialize bridge or ws_manager based on mode. |
| `src/backend/ws_bridge.py` | **New** | `WebSocketBridge` class + `BridgeConnection` class. Manages connectionIds in memory, uses boto3 to push via API Gateway Management API. Reuses `Subscription` from `websocket.py`. Also contains the 3 POST endpoint handlers. |
| `src/backend/middleware.py` | **Modify** | Add `"/ws/"` to `PUBLIC_PREFIXES` so WebSocket bridge endpoints bypass auth (they authenticate internally). |
| `src/backend/websocket.py` | **No change** | Kept as-is for local development WebSocket support. |
| `config.example.json` | **Already updated** | Added `mode`, `ws_gateway_endpoint`, `ws_gateway_region` fields. |

### Frontend Files

| File | Change | Details |
|------|--------|---------|
| `src/static/js/common.js` | **Modify** | Replace CONFIG block with environment-detecting version. Add `_isLocal` detection. Add `wsUrl` field. Add login redirect for production with no API key. |
| `src/static/js/hiveboard.js` | **Modify** | (1) Remove duplicate CONFIG declaration (lines 5-13). (2) Update `connectWebSocket()` to use `CONFIG.wsUrl` when set. |
| `src/static/insights.html` | **No change** | Already uses `apiFetch()` from common.js, inherits CONFIG. |
| `src/static/index.html` | **No change** | Already loads common.js before hiveboard.js. |

### Config Files

| File | Status | Purpose |
|------|--------|---------|
| `config.example.json` | **Updated** | Template with all fields including `mode`, `ws_gateway_endpoint`, `ws_gateway_region` |
| `config.json` | **Gitignored** | Real config on each machine (local dev or production server) |
| `.gitignore` | **Updated** | Added `loophive.cfg` |
| `loophive.cfg` | **Gitignored** | SDK endpoint override for local development (created by developer, not committed) |

### PyPI

| Action | Details |
|--------|---------|
| Publish `loophive` v0.1.1 | Updated default endpoint + config file resolution |

---

## 11. Implementation Order

### Phase 1: Backend CORS + WS Mode Init (30 min)

1. Make CORS middleware conditional: `if _cfg("mode", "local") != "production":`
2. Add mode detection in lifespan: initialize `app.state.ws_mode` and `app.state.ws_bridge`
3. Add `"/ws/"` to `PUBLIC_PREFIXES` in `middleware.py`
4. Test: local mode still works as before (`mode` defaults to `"local"`)

### Phase 2: SDK Endpoint Resolution (30 min)

1. Add `_resolve_endpoint()` to `__init__.py`
2. Change `endpoint` default to `None` in `init()` and `HiveBoard.__init__()`
3. Bump version in `pyproject.toml`
4. Test: without config file → defaults to production URL
5. Test: with `loophive.cfg` → uses local URL

### Phase 3: Frontend Environment Detection (30 min)

1. Update CONFIG in `common.js` with `_isLocal` detection
2. Remove duplicate CONFIG from `hiveboard.js`
3. Update `connectWebSocket()` in `hiveboard.js` for dual WS mode
4. Add no-API-key login redirect
5. Test: local mode still works

### Phase 4: WebSocket Bridge (2-3 hours)

1. Create `src/backend/ws_bridge.py` with `WebSocketBridge` class
2. Add 3 new POST endpoints (`/ws/connect`, `/ws/disconnect`, `/ws/message`)
3. Modify ingest handler to broadcast via bridge in production mode
4. Test locally with mock bridge

### Phase 5: AWS API Gateway Setup (1-2 hours, manual in AWS Console)

Follow [AWS WebSocket Setup Guide](../docs/AWS_WEBSOCKET_SETUP_GUIDE.md) step by step:

1. Create WebSocket API in API Gateway
2. Configure 3 routes ($connect, $disconnect, $default) with HTTP integrations pointing to `https://mlbackend.net/loophive/ws/connect`, `/ws/disconnect`, `/ws/message`
3. **Critical:** Add `connectionId` request parameter mapping on every integration (Section 1.2 of the guide)
4. Attach `execute-api:ManageConnections` IAM policy to the server's AWS credentials
5. Deploy to `production` stage
6. Note the API Gateway URL: `wss://{api-id}.execute-api.{region}.amazonaws.com/production`
7. Update production `config.json` with real `ws_gateway_endpoint`
8. Update `common.js` with real `CONFIG.wsUrl`

### Phase 6: Integration Testing (1-2 hours)

1. Deploy backend to Windows Server with production `config.json`
2. Upload frontend to S3
3. Test SDK → backend event ingestion
4. Test dashboard API calls cross-origin
5. Test WebSocket connect → subscribe → receive events
6. Test reconnection after backend restart

### Phase 7: PyPI Publish (15 min)

1. Build: `python -m build`
2. Publish: `twine upload dist/*`

---

## 12. Open Items & Risks

### Must Address

| # | Item | Status |
|---|------|--------|
| 1 | AWS API Gateway WebSocket API ID and region — needed for `config.json` and `common.js` | **Blocked until AWS setup (Phase 5)** |
| 2 | `boto3` dependency in backend — must be installed on Windows Server | **Add to backend requirements** (already installed on server) |
| 3 | AWS credentials on Windows Server for API Gateway Management API calls | **Must have AWS CLI configured or IAM credentials available** |

### Completed

| # | Item | Status |
|---|------|--------|
| 4 | Auth endpoints (register, login, check-slug, accept-invite) | **Done** — all implemented and tested |
| 5 | Invite system (send, list, cancel invites) | **Done** — all implemented and tested |
| 6 | API key CRUD (create, list, revoke) | **Done** — all implemented and tested |
| 7 | Project CRUD + slug uniqueness | **Done** — all implemented and tested |
| 8 | Centralized config system (config.json + config.py) | **Done** — no env vars |
| 9 | `accept-invite.html` page | **Done** — exists in `docs/WEBAPP/` |
| 10 | Production fields in `config.example.json` | **Done** — `mode`, `ws_gateway_endpoint`, `ws_gateway_region` added |
| 11 | `loophive.cfg` in `.gitignore` | **Done** |

### Risks

| # | Risk | Mitigation |
|---|------|------------|
| 1 | Backend restart loses all WebSocket connectionIds | Dashboard reconnects automatically. On ping, backend re-registers connectionId defensively. |
| 2 | JSON file storage under concurrent load | Acceptable for hackathon. Monitor for race conditions. Plan DB migration post-hackathon. |
| 3 | IIS URL Rewrite for POST bodies | IIS URL Rewrite handles body passthrough transparently. No special config needed. |
| 4 | API Gateway WebSocket idle timeout (10 min default) | Dashboard already sends periodic ping. Increase idle timeout in API GW settings if needed. |
| 5 | Double CORS if config.json is wrong | Clear documentation: production `config.json` MUST set `mode: "production"`. |

### Implementation Notes

Details to handle during coding — not blockers, but easy to miss.

**IN-1: Defensive re-registration on subscribe (not just ping)**

After a backend restart, all in-memory connectionIds are lost. The first thing a reconnected client sends is `subscribe` (via `ws.onopen`), not `ping`. The `/ws/message` subscribe handler must also defensively re-register unknown connectionIds — same pattern as the ping handler:

```python
if action == "subscribe":
    # If connectionId is unknown (e.g., after backend restart), re-register
    if not bridge.is_registered(connection_id):
        token = body.get("token")
        if token:
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            info = await request.app.state.storage.authenticate(key_hash)
            if info:
                bridge.register(connection_id, info.tenant_id, info.key_id)
    # ... then subscribe as normal
```

This requires the frontend to include `token` in its subscribe message. Update `hiveboard.js`:
```javascript
ws.send(JSON.stringify({
  action: 'subscribe',
  token: CONFIG.apiKey,          // ← add this
  channels: ['events', 'agents'],
  filters: { ... },
}));
```

**IN-2: Branch ALL broadcast calls, not just `broadcast_events`**

The ingest pipeline in `app.py` calls multiple broadcast methods on `ws_manager`. Each call site must branch on `ws_mode`:

| Broadcast call | Where in `app.py` |
|---|---|
| `broadcast_events(tenant_id, events)` | After storing events |
| `broadcast_agent_status_change(tenant_id, agent_id, ...)` | After status derivation |
| `broadcast_agent_stuck(tenant_id, agent_id, ...)` | After stuck detection |

Recommended pattern — create a helper early in `app.py`:

```python
def _get_broadcaster(app):
    """Return the active broadcaster (bridge or ws_manager)."""
    if app.state.ws_mode == "bridge":
        return app.state.ws_bridge
    from backend.websocket import ws_manager
    return ws_manager
```

Then each call site becomes:
```python
broadcaster = _get_broadcaster(request.app)
await broadcaster.broadcast_events(tenant_id, stored_events)
```

The `WebSocketBridge` class must implement the same method signatures as `WebSocketManager` for all broadcast methods used in the ingest pipeline.

**IN-3: Guard `/ws/*` endpoints when bridge is None (local mode)**

The `/ws/*` endpoints are always registered on the app, but `app.state.ws_bridge` is `None` in local mode. Add a guard at the top of each handler:

```python
@app.post("/ws/connect")
async def ws_connect(request: Request):
    if request.app.state.ws_mode != "bridge":
        return JSONResponse({"error": "WebSocket bridge not active"}, status_code=501)
    # ... rest of handler
```

Alternatively, register the endpoints conditionally in lifespan — but the guard approach is simpler and avoids import-time side effects.

---

### Deferred (Post-Hackathon)

- Database migration (MS SQL Server)
- Custom domain for WebSocket API (`wss://ws.hiveboard.net`)
- Dashboard migration from API key auth to JWT-based sessions
- Rate limiting per API key in production
- Health check monitoring (hit `/health` from external uptime checker)
- Structured logging to file
- Monitoring and alerting
- CDN caching for S3 static assets
- SDK: additional config file locations (e.g., `/etc/loophive/loophive.cfg`)
