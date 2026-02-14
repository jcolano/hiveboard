# AWS WebSocket API Gateway — Setup & Integration Guide

A reusable reference for adding real-time push notifications to any web application using AWS API Gateway WebSocket APIs with an HTTP backend (e.g., FastAPI, Express, Django).

---

## Architecture Overview

```
Browser (JS)
    │
    │  wss://<API_ID>.execute-api.<REGION>.amazonaws.com/<STAGE>?token=xxx
    ▼
AWS API Gateway (WebSocket API)
    │
    │  HTTP POST (proxy integration)
    │  Headers: connectionId, token
    ▼
Your HTTP Backend (e.g., https://api.example.com/ws/*)
    │
    │  boto3 apigatewaymanagementapi
    │  post_to_connection(ConnectionId, Data)
    ▼
AWS API Gateway ──► Browser (push)
```

**Key idea:** API Gateway owns the WebSocket lifecycle. Your backend is plain HTTP — it receives POST requests from API Gateway on connect/disconnect/message events, and pushes messages back to clients via the Management API (boto3).

---

## Placeholder Reference

Throughout this guide, replace these placeholders with your actual values:

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `<API_ID>` | Your WebSocket API ID | `kt2quqdplj` |
| `<REGION>` | AWS region | `us-east-1` |
| `<STAGE>` | Deployment stage name | `production` |
| `<BACKEND_BASE>` | Your backend base URL | `https://api.example.com` |
| `<CONNECT_INTEG_ID>` | Integration ID for `$connect` | `g0cji4o` |
| `<DISCONNECT_INTEG_ID>` | Integration ID for `$disconnect` | `qj0758r` |
| `<DEFAULT_INTEG_ID>` | Integration ID for `$default` | `s8aayl1` |
| `<AWS_ACCOUNT_ID>` | Your AWS account ID (or `*`) | `123456789012` |

---

## 1. AWS API Gateway Configuration

### 1.1 Routes

Configure three routes in the WebSocket API, each pointing to an HTTP endpoint on your backend:

| Route | Integration URI | Purpose |
|-------|----------------|---------|
| `$connect` | `POST <BACKEND_BASE>/ws/connect` | Client opens WebSocket |
| `$disconnect` | `POST <BACKEND_BASE>/ws/disconnect` | Client closes WebSocket |
| `$default` | `POST <BACKEND_BASE>/ws/message` | Client sends a message |

All three use **HTTP_PROXY** integration type with `WHEN_NO_MATCH` passthrough behavior.

### 1.2 Request Parameter Mapping (Critical Step)

API Gateway does **not** automatically forward `context.connectionId` to HTTP integrations. You must explicitly map it as a request header on each integration.

**View current integrations:**
```bash
aws apigatewayv2 get-integrations --api-id <API_ID>
```

**Add the connectionId mapping to each integration:**

```bash
# $connect route
aws apigatewayv2 update-integration \
  --api-id <API_ID> \
  --integration-id <CONNECT_INTEG_ID> \
  --request-parameters '{"integration.request.header.connectionId": "context.connectionId"}'

# $disconnect route
aws apigatewayv2 update-integration \
  --api-id <API_ID> \
  --integration-id <DISCONNECT_INTEG_ID> \
  --request-parameters '{"integration.request.header.connectionId": "context.connectionId"}'

# $default route
aws apigatewayv2 update-integration \
  --api-id <API_ID> \
  --integration-id <DEFAULT_INTEG_ID> \
  --request-parameters '{"integration.request.header.connectionId": "context.connectionId"}'
```

**What this does:** Maps the internal `context.connectionId` (API Gateway's unique identifier for each WebSocket connection) into an HTTP header called `connectionId` that your backend can read.

**Why it's needed:** Without this, the backend has no way to know which WebSocket connection is making the request — the connectionId is only available through explicit parameter mapping.

> **Note:** Query string parameters (e.g., `?token=xxx`) pass through automatically in HTTP_PROXY mode. Only `context.*` variables need explicit mapping.

### 1.3 Verify Configuration

```bash
# Check routes
aws apigatewayv2 get-routes --api-id <API_ID>

# Check integrations (should show RequestParameters with connectionId mapping)
aws apigatewayv2 get-integrations --api-id <API_ID>

# Check the stage
aws apigatewayv2 get-stage --api-id <API_ID> --stage-name <STAGE>
```

### 1.4 Deploy After Changes

After updating integrations, you must redeploy the stage:

```bash
aws apigatewayv2 create-deployment --api-id <API_ID> --stage-name <STAGE>
```

---

## 2. IAM Permissions

The IAM user/role running your backend needs permission to push messages back through the Management API.

**Required policy — `execute-api:ManageConnections`:**

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "execute-api:ManageConnections",
            "Resource": "arn:aws:execute-api:<REGION>:<AWS_ACCOUNT_ID>:<API_ID>/<STAGE>/POST/@connections/*"
        }
    ]
}
```

This allows the backend to call `post_to_connection()` and `delete_connection()` via boto3.

---

## 3. Server-Side Integration (Python / FastAPI Example)

The patterns below use FastAPI and Python, but the concepts apply to any HTTP backend.

### 3.1 Route Handlers

Three POST endpoints that API Gateway calls:

**`POST /ws/connect`**
- Reads `connectionId` from headers (from parameter mapping) and `token` from query params (passthrough)
- Authenticates the token (JWT, API key, etc.)
- Stores the connection record in your database:
  ```json
  {
    "user_id": "user_xyz",
    "connection_id": "AwsConnectionId123=",
    "connected_at": "2026-02-06T10:30:45Z"
  }
  ```
- Recommended: enforce one active connection per user (delete old record on reconnect)

**`POST /ws/disconnect`**
- Reads `connectionId` from headers
- Deletes the connection record from your database

**`POST /ws/message`**
- Reads `connectionId` from headers + message body
- Handles client heartbeats and re-registration
- If the connection is unknown, the client can re-register by sending `{ "token": "..." }`

### 3.2 Push Service (boto3)

Use boto3's `apigatewaymanagementapi` client to push messages to connected clients:

```python
import boto3
import json

APIGW_ENDPOINT = "https://<API_ID>.execute-api.<REGION>.amazonaws.com/<STAGE>"

client = boto3.client(
    "apigatewaymanagementapi",
    endpoint_url=APIGW_ENDPOINT,
    region_name="<REGION>"
)

def push_to_client(connection_id: str, payload: dict) -> bool:
    """Push a message to a single connected client."""
    try:
        client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8")
        )
        return True
    except client.exceptions.GoneException:
        # Connection is stale — clean up the record from your database
        delete_connection_record(connection_id)
        return False
```

**Key behaviors to implement:**
- `push_to_client(user_id, payload)` — look up connection_id for user, push message
- `push_to_clients(user_ids, payload)` — push to multiple users in a loop
- Always catch `GoneException` for stale connections and auto-delete the record
- Fail silently (return `False`) if user is not connected — this is normal

### 3.3 Example Notification Triggers

Adapt these to your application's events:

| Event | Push type | Recipients |
|-------|-----------|------------|
| New content created | `new_content` | Relevant users except author |
| New comment/reply | `new_comment` | Thread participants |
| Status change | `status_update` | Affected user(s) |
| Scheduled alert | `alert_triggered` | Alert owner |
| Notification created/read | `unread_counts` | Target user |

---

## 4. Client-Side Integration (JavaScript)

### 4.1 Connection Setup

```javascript
const WS_URL = 'wss://<API_ID>.execute-api.<REGION>.amazonaws.com/<STAGE>';

function connectWebSocket() {
    const token = getAuthToken();  // Your auth token retrieval
    const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);

    ws.onopen = () => console.log('WebSocket connected');
    ws.onclose = (e) => handleDisconnect(e);
    ws.onerror = (e) => console.error('WebSocket error', e);
    ws.onmessage = (e) => handleMessage(JSON.parse(e.data));
}
```

**Tip:** Consider disabling WebSocket on localhost during development and using polling instead, since API Gateway routes to your production backend.

### 4.2 Handling Messages

Define a handler that dispatches by message type:

```javascript
function handleMessage(data) {
    switch (data.type) {
        case 'new_content':    refreshFeed(); showToast(data); break;
        case 'new_comment':    refreshThread(data.thread_id);  break;
        case 'status_update':  updateStatusDisplay(data);      break;
        case 'unread_counts':  updateBadges(data.counts);      break;
        case 'heartbeat':      break;  // keepalive, no action
        default:               console.warn('Unknown message type:', data.type);
    }
}
```

### 4.3 Reconnection Strategy

Implement exponential backoff for automatic reconnection:

```javascript
let reconnectDelay = 5000;  // Start at 5 seconds
const MAX_DELAY = 60000;    // Cap at 60 seconds
let consecutiveFailures = 0;

function handleDisconnect(event) {
    if (intentionalClose) return;  // Don't reconnect if we closed on purpose

    // Optional: detect repeated auth failures before first success → force logout
    if (consecutiveFailures >= 3 && !hasEverConnected) {
        logout();
        return;
    }

    setTimeout(() => {
        connectWebSocket();
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_DELAY);
        consecutiveFailures++;
    }, reconnectDelay);
}
```

### 4.4 Silence Watchdog

If no server message is received within a timeout window, the client proactively pings to re-register the connection. This prevents the connection from becoming "unknown" to the server after idle periods.

```javascript
const SILENCE_TIMEOUT = 120000;  // 2 minutes
const CHECK_INTERVAL  = 30000;   // Check every 30 seconds
let lastMessageTime = Date.now();

// Update on every received message
ws.onmessage = (e) => {
    lastMessageTime = Date.now();
    handleMessage(JSON.parse(e.data));
};

// Periodic check
setInterval(() => {
    if (Date.now() - lastMessageTime > SILENCE_TIMEOUT && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'ping', token: getAuthToken() }));
    }
}, CHECK_INTERVAL);
```

---

## 5. Debug & Troubleshooting

### 5.1 Recommended Debug Endpoints

Add these to your backend (protected by admin/dev auth):

| Endpoint | Purpose |
|----------|---------|
| `GET /debug/ws-connections` | List all active WebSocket connection records |
| `POST /debug/ws-push-test/{user_id}` | Send a test push to a specific user |
| `DELETE /debug/ws-connections/stale` | Clean up stale connection records |

### 5.2 Common Issues

**"connectionId header missing"**
→ Request parameter mapping not configured on the integration. Re-run the `update-integration` commands from Section 1.2, then redeploy the stage.

**Push silently fails (no error, no delivery)**
→ Check IAM permissions for `execute-api:ManageConnections`. Verify the connection record exists in your database.

**`GoneException` when pushing**
→ Normal. The client disconnected but the record wasn't cleaned up yet. Your push service should auto-delete stale records on this exception.

**Client keeps reconnecting**
→ Check server logs for auth failures on `/ws/connect`. Ensure the token is valid and the user exists.

**WebSocket works locally but not in production (or vice versa)**
→ If you disabled WebSocket on localhost by design, this is expected. Check environment detection logic.

### 5.3 Useful AWS CLI Commands

```bash
# List all routes
aws apigatewayv2 get-routes --api-id <API_ID>

# List all integrations (check RequestParameters)
aws apigatewayv2 get-integrations --api-id <API_ID>

# Check deployment status
aws apigatewayv2 get-stage --api-id <API_ID> --stage-name <STAGE>

# Redeploy after changes
aws apigatewayv2 create-deployment --api-id <API_ID> --stage-name <STAGE>
```

---

## 6. Data Flow Summary

```
1. Browser connects:
   wss://<API_ID>.execute-api.<REGION>.amazonaws.com/<STAGE>?token=xxx
       │
       ▼
2. API Gateway ($connect route):
   POST <BACKEND_BASE>/ws/connect
   Headers: { connectionId: "Abc123=" }
   Query:   { token: "xxx" }
       │
       ▼
3. Backend authenticates token, stores connection:
   database ← { user_id, connection_id, connected_at }
       │
       ▼
4. Something happens in your app (new content, alert, etc.):
   push_to_client(user_id, { type: "new_content", ... })
       │
       ▼
5. boto3 calls Management API:
   post_to_connection(ConnectionId="Abc123=", Data=payload)
       │
       ▼
6. API Gateway delivers to browser's WebSocket:
   ws.onmessage → handleMessage(data)
```

---

## 7. Quick-Start Checklist

Use this when setting up WebSocket push on a new project:

- [ ] Create a WebSocket API in API Gateway
- [ ] Configure three routes: `$connect`, `$disconnect`, `$default`
- [ ] Set up HTTP_PROXY integrations pointing to your backend endpoints
- [ ] **Add `connectionId` request parameter mapping on every integration** (Section 1.2)
- [ ] Deploy the stage
- [ ] Attach `execute-api:ManageConnections` IAM policy to your backend's role/user
- [ ] Implement backend handlers for connect, disconnect, and message
- [ ] Implement the push service using boto3 `apigatewaymanagementapi`
- [ ] Handle `GoneException` to auto-clean stale connections
- [ ] Implement client-side connection with auth token in query string
- [ ] Implement reconnection with exponential backoff
- [ ] Implement silence watchdog for idle connection recovery
- [ ] Add debug endpoints for connection inspection and test pushes
- [ ] Test end-to-end: connect → trigger event → verify push arrives in browser
