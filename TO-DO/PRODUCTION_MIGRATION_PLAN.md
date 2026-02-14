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
                        │  Also: Login / Register / Home webapp              │          │
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

### 3.4 Backend Config File

**New file:** `src/backend/config.json`

```json
{
  "mode": "production",
  "ws_gateway_endpoint": "https://{api-id}.execute-api.{region}.amazonaws.com/{stage}",
  "ws_gateway_region": "us-east-1"
}
```

Local development (no file or):

```json
{
  "mode": "local"
}
```

**How it's loaded:** `app.py` reads this file at startup. If not present or `mode` is `"local"`, the backend uses direct WebSocket (current behavior). If `mode` is `"production"`, it uses AWS API Gateway Management API for WebSocket push.

### 3.5 Changes to `src/backend/app.py`

| Line(s)     | Current                               | Change                                                       |
|-------------|---------------------------------------|--------------------------------------------------------------|
| 160-167     | `CORSMiddleware(allow_origins=["*"])` | **Remove entirely.** IIS handles CORS in production. For local dev, conditionally add based on config.json `mode` field. |
| 224-231     | `GET /dashboard` route                | Keep for local dev. In production, S3 serves the frontend.   |
| 234-237     | `app.mount("/static", ...)`           | Keep for local dev. Harmless in production (IIS won't route to it). |
| 1373-1404   | `@app.websocket("/v1/stream")`        | **Keep** for local development. Add **4 new HTTP endpoints** for AWS API GW WebSocket (see Section 6). |

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
├── login.html              (webapp — already exists)
├── register.html           (webapp — already exists)
└── home.html               (webapp — already exists)
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
// In production, if no API key, redirect to login
if (!_isLocal && !CONFIG.apiKey) {
  window.location.href = '/login.html';
}
```

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
│  subscribe   ──► HTTP POST ──┼──► https://mlbackend.net/loophive/ws/subscribe
│  unsubscribe ──► HTTP POST ──┼──► https://mlbackend.net/loophive/ws/unsubscribe
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

### 6.2 AWS API Gateway — Route Configuration

**1 WebSocket API** with 4 routes:

#### Route: `$connect`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/connect`
- **Method:** POST
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  integration.request.header.X-Forwarded-Token = route.request.querystring.token
  ```

#### Route: `$disconnect`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/disconnect`
- **Method:** POST
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```

#### Route: `subscribe`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/subscribe`
- **Method:** POST
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```
- **Body passthrough:** The JSON body `{ channels: [...], filters: {...} }` is forwarded as-is.

#### Route: `unsubscribe`
- **Integration type:** HTTP_PROXY
- **Integration URI:** `https://mlbackend.net/loophive/ws/unsubscribe`
- **Method:** POST
- **Request Parameter Mapping:**
  ```
  integration.request.header.connectionId = context.connectionId
  ```
- **Body passthrough:** The JSON body `{ channels: [...] }` is forwarded as-is.

### 6.3 Backend — New HTTP Endpoints for WebSocket Bridge

**New file:** `src/backend/ws_bridge.py`

This module provides 4 new POST endpoints that replace the direct WebSocket handler for production. The existing `WebSocketManager` is adapted to work with connectionId strings instead of WebSocket objects.

```python
"""WebSocket bridge — HTTP endpoints for AWS API Gateway WebSocket integration.

In production, the dashboard connects to AWS API Gateway (WebSocket API).
API Gateway forwards connect/disconnect/subscribe/unsubscribe as HTTP POST
requests to these endpoints, with connectionId in the request header.

The backend pushes messages to clients via the API Gateway Management API.
"""
```

#### Endpoint: `POST /ws/connect`

```python
@app.post("/ws/connect")
async def ws_connect(request: Request):
    """Called by AWS API Gateway on $connect."""
    connection_id = request.headers.get("connectionId")
    token = request.headers.get("X-Forwarded-Token", "")

    if not connection_id or not token:
        return JSONResponse({"error": "missing connectionId or token"}, status_code=400)

    # Authenticate same as current WebSocket handler
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    info = await request.app.state.storage.authenticate(key_hash)
    if info is None:
        return JSONResponse({"error": "invalid API key"}, status_code=403)

    # Store connectionId in the bridge manager
    ws_bridge.register(connection_id, info.tenant_id, info.key_id)
    return JSONResponse({"status": "connected"})
```

#### Endpoint: `POST /ws/disconnect`

```python
@app.post("/ws/disconnect")
async def ws_disconnect(request: Request):
    """Called by AWS API Gateway on $disconnect."""
    connection_id = request.headers.get("connectionId")
    if connection_id:
        ws_bridge.unregister(connection_id)
    return JSONResponse({"status": "disconnected"})
```

#### Endpoint: `POST /ws/subscribe`

```python
@app.post("/ws/subscribe")
async def ws_subscribe(request: Request):
    """Called by AWS API Gateway when client sends subscribe action."""
    connection_id = request.headers.get("connectionId")
    body = await request.json()

    channels = body.get("channels", [])
    filters = body.get("filters", {})
    ws_bridge.subscribe(connection_id, channels, filters)
    return JSONResponse({"status": "subscribed", "channels": channels})
```

#### Endpoint: `POST /ws/unsubscribe`

```python
@app.post("/ws/unsubscribe")
async def ws_unsubscribe(request: Request):
    """Called by AWS API Gateway when client sends unsubscribe action."""
    connection_id = request.headers.get("connectionId")
    body = await request.json()

    channels = body.get("channels", [])
    ws_bridge.unsubscribe(connection_id, channels)
    return JSONResponse({"status": "unsubscribed", "channels": channels})
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
# In lifespan()
config = _load_config()  # reads config.json

if config.get("mode") == "production":
    from backend.ws_bridge import WebSocketBridge
    bridge = WebSocketBridge(
        gateway_endpoint=config["ws_gateway_endpoint"],
        region=config.get("ws_gateway_region", "us-east-1"),
    )
    app.state.ws_bridge = bridge
    app.state.ws_mode = "bridge"
else:
    from backend.websocket import ws_manager
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

### 6.6 Frontend — WebSocket URL in Subscribe Message

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
1. Client sends `subscribe` → API Gateway forwards to `/ws/subscribe` → Backend receives connectionId it doesn't know → Backend registers it on the spot (defensive programming)
2. If backend push fails with `GoneException` → connection is cleaned up
3. Dashboard reconnect logic already handles disconnections with exponential backoff

---

## 7. CORS Strategy

### 7.1 Production

**IIS handles all CORS.** The Python backend must NOT add CORS headers, or browsers will see double CORS headers and reject requests.

### 7.2 Local Development

The Python backend adds CORS middleware **only** when `config.json` mode is `"local"` (or config.json doesn't exist):

```python
config = _load_config()
if config.get("mode") != "production":
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
User opens https://hiveboard.net/static/index.html?apiKey=hb_live_xxx
    │
    ▼ JS: CONFIG.endpoint = "https://mlbackend.net/loophive"
    │
    ▼ fetch("https://mlbackend.net/loophive/v1/agents", { headers: { Authorization: "Bearer hb_live_xxx" } })
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
    │   Header: X-Forwarded-Token = "hb_live_xxx"
    │
    ▼ Backend authenticates token, stores connectionId in memory
    │
    ▼ Dashboard sends: { action: "subscribe", channels: ["events","agents"] }
    │
    ▼ subscribe route fires
    │
    ▼ API Gateway HTTP POST → https://mlbackend.net/loophive/ws/subscribe
    │   Header: connectionId = "abc123def"
    │   Body: { channels: ["events","agents"], filters: {...} }
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

## 9. User Journey — Registration to Dashboard

```
1. User visits https://hiveboard.net
       │
       ▼
2. Clicks "Register" → /register.html
       │ Enters: name, email, password, company
       │
       ▼ POST to backend (mlbackend.net/loophive/v1/auth/register)
       │
3. Backend creates:
       │ ├── User account
       │ ├── Tenant (company)
       │ ├── Default project ("My Project")
       │ └── Default API key (hb_live_xxxx)
       │
       ▼
4. User redirected to /login.html
       │ Enters: email, password
       │
       ▼ POST to backend (mlbackend.net/loophive/v1/auth/login)
       │
5. Backend returns: JWT/session + user info + API keys
       │
       ▼
6. User lands on /home.html
       │ Sees: tenant name, projects list, API keys
       │ Can: create more projects, generate more API keys
       │ Can: copy SDK install instructions with their API key
       │
       ▼
7. User clicks "Open Dashboard"
       │
       ▼ Redirect to /static/index.html?apiKey=hb_live_xxxx
       │ (or pass via localStorage/sessionStorage)
       │
       ▼
8. Dashboard loads, CONFIG detects production mode,
   fetches data from mlbackend.net/loophive, connects
   WebSocket via AWS API Gateway.
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
| `src/backend/app.py` | **Modify** | (1) Add `_load_config()` helper to read `config.json`. (2) Conditionally add CORS middleware only in local mode. (3) Add 4 new POST endpoints for WS bridge: `/ws/connect`, `/ws/disconnect`, `/ws/subscribe`, `/ws/unsubscribe`. (4) In ingest handler, after storing events, branch on `ws_mode` to push via bridge or direct WS. (5) In lifespan, initialize bridge or ws_manager based on mode. |
| `src/backend/ws_bridge.py` | **New** | `WebSocketBridge` class + `BridgeConnection` class. Manages connectionIds in memory, uses boto3 to push via API Gateway Management API. Reuses `Subscription` from `websocket.py`. |
| `src/backend/config.json` | **New** | Backend configuration: `mode`, `ws_gateway_endpoint`, `ws_gateway_region`. |
| `src/backend/websocket.py` | **No change** | Kept as-is for local development WebSocket support. |

### Frontend Files

| File | Change | Details |
|------|--------|---------|
| `src/static/js/common.js` | **Modify** | Replace CONFIG block with environment-detecting version. Add `_isLocal` detection. Add `wsUrl` field. Add login redirect for production with no API key. |
| `src/static/js/hiveboard.js` | **Modify** | (1) Remove duplicate CONFIG declaration (lines 5-13). (2) Update `connectWebSocket()` to use `CONFIG.wsUrl` when set. |
| `src/static/insights.html` | **No change** | Already uses `apiFetch()` from common.js, inherits CONFIG. |
| `src/static/index.html` | **No change** | Already loads common.js before hiveboard.js. |

### New Config Files

| File | Purpose |
|------|---------|
| `src/backend/config.json` | Backend mode (local/production), AWS API Gateway endpoint |
| `loophive.cfg` (in developer's project) | SDK endpoint override for local development |

### PyPI

| Action | Details |
|--------|---------|
| Publish `loophive` v0.1.1 | Updated default endpoint + config file resolution |

---

## 11. Implementation Order

### Phase 1: Backend Config + CORS (30 min)

1. Create `src/backend/config.json` (local mode default)
2. Add `_load_config()` to `app.py`
3. Make CORS middleware conditional on config mode
4. Test: local mode still works as before

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
2. Add 4 new POST endpoints to `app.py`
3. Add bridge initialization in lifespan based on config
4. Modify ingest handler to broadcast via bridge in production mode
5. Test locally with mock bridge

### Phase 5: AWS API Gateway Setup (1-2 hours, manual in AWS Console)

1. Create WebSocket API in API Gateway
2. Configure 4 routes with HTTP integrations
3. Add request parameter mapping for connectionId
4. Deploy to `prod` stage
5. Note the API Gateway URL
6. Update `config.json` with real `ws_gateway_endpoint`
7. Update `common.js` with real `CONFIG.wsUrl`

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
| 1 | AWS API Gateway WebSocket API ID and region — needed for `config.json` and `common.js` | **Blocked until AWS setup** |
| 2 | `boto3` dependency in backend — must be installed on Windows Server | **Add to backend requirements** |
| 3 | AWS credentials on Windows Server for API Gateway Management API calls | **Must configure AWS CLI or env credentials** |
| 4 | Auth endpoints (register/login) — needed for user journey | **In progress (owner handling)** |

### Risks

| # | Risk | Mitigation |
|---|------|------------|
| 1 | Backend restart loses all WebSocket connectionIds | Dashboard reconnects automatically. On resubscribe, backend re-learns connectionId. |
| 2 | JSON file storage under concurrent load | Acceptable for hackathon. Monitor for race conditions. Plan DB migration post-hackathon. |
| 3 | IIS URL Rewrite for POST bodies | IIS URL Rewrite handles body passthrough transparently. No special config needed. |
| 4 | API Gateway WebSocket idle timeout (10 min default) | Dashboard already sends periodic subscribe/ping. Increase idle timeout in API GW if needed (max 10 min). |
| 5 | Double CORS if config.json is wrong | Clear documentation: production `config.json` MUST set `mode: "production"`. |

### Deferred (Post-Hackathon)

- Database migration (PostgreSQL)
- Custom domain for WebSocket API (`wss://ws.hiveboard.net`)
- Rate limiting per API key in production
- Structured logging to file
- Monitoring and alerting
- CDN caching for S3 static assets
- SDK: additional config file locations (e.g., `/etc/loophive/loophive.cfg`)
