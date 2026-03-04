# HiveBoard + HiveLoop Integration Troubleshooting

Common issues when connecting a HiveLoop-instrumented app (e.g., loopCore) to a HiveBoard backend.

---

## 1. No events arriving — "Connection error. Retry..." in SDK logs

**Symptom**: HiveLoop SDK logs show `Connection error. Retry 1/5...` then `Exhausted 5 retries. Dropping N events.`

**Cause**: The SDK `endpoint` points to the wrong host or port.

**Fix**: Verify `hiveloop.init(endpoint=...)` matches the HiveBoard server address. Local dev uses port **8451**:
```python
hb = hiveloop.init(
    api_key="hb_live_YOUR_KEY_HERE",
    endpoint="http://localhost:8451",
)
```

---

## 2. No events arriving — no SDK log output at all

**Symptom**: No HiveLoop-related messages in the app logs. No connection errors, no retries, nothing.

**Cause A — SDK logger not configured**: HiveLoop logs to `hiveloop.*` loggers. If your app only configures its own logger (e.g., `loop_core`), SDK messages are silently discarded by Python's default logging behavior.

**Fix**: Add a handler for the `hiveloop` logger:
```python
import logging
logging.getLogger("hiveloop").setLevel(logging.DEBUG)
logging.getLogger("hiveloop").addHandler(logging.StreamHandler())
```

**Cause B — Init order**: If your app starts/registers agents **before** calling `hiveloop.init()` and wiring it in, agents register with `_hiveloop = None` and never emit events.

**Fix**: Initialize HiveLoop and call `set_hiveloop(hb)` **before** any code that starts or registers agents:
```python
# CORRECT ORDER:
hb = hiveloop.init(api_key="...", endpoint="...")
get_manager().set_hiveloop(hb)   # Wire in BEFORE agents start
get_runtime()                     # This auto-starts agents
```

---

## 3. Dashboard returns 401 Unauthorized on all API calls

**Symptom**: Browser console shows `HTTP 401` on `/v1/agents`, `/v1/tasks`, etc.

**Cause A — Dev bootstrap didn't run**: The `HIVEBOARD_DEV_KEY` environment variable is not set. Without it, the bootstrap silently skips and creates no tenant, no API keys.

**Fix**: Set the env var before starting the server:
```bash
# Bash / Git Bash
export HIVEBOARD_DEV_KEY=hb_live_YOUR_KEY_HERE

# PowerShell
$env:HIVEBOARD_DEV_KEY = "hb_live_YOUR_KEY_HERE"
```
Then delete the `data/` folder and restart the server so bootstrap runs fresh.

**Cause B — Stale data folder**: The data folder was created before the access_id feature was added, so the read key (`hb_read_dev...`) doesn't exist.

**Fix**: Delete the `data/` folder (wherever the server writes it — check your working directory) and restart. The bootstrap will create both keys (live + read).

**Cause C — Wrong data folder**: The server creates `data/` relative to the working directory. If you start from `src/`, it creates `src/data/`. If you start from the project root, it creates `data/`.

**Fix**: Always start from the same directory, or check which `data/` folder has recent timestamps.

---

## 4. Events partially rejected — "Unknown event_type"

**Symptom**: SDK logs show `Batch partially rejected: N accepted, M rejected. Errors: [... invalid_event_type ...]`

**Cause**: The instrumented app emits custom event types (e.g., `config_snapshot`, `agent_started`, `state_mutation`) that aren't in HiveBoard's `EventType` enum.

**Fix (SDK-side, v0.2.0+)**: The SDK's `_emit_event()` now automatically remaps unknown event types to `"custom"` and preserves the original type in `payload["original_event_type"]`. If you see this error, upgrade to HiveLoop SDK >= 0.2.0.

---

## 5. Browser shows stale dashboard — changes not visible

**Symptom**: You updated JS files but the browser still shows old behavior.

**Cause**: Browser caching. Static JS/CSS files are cached aggressively.

**Fix**: Bump the version query string on all JS/CSS includes in HTML files:
```html
<script src="js/common.js?v=20260219"></script>
```
Or hard-refresh the browser with `Ctrl+Shift+R`.

---

## 6. WEBAPP pages can't reach the server

**Symptom**: WEBAPP HTML files (opened from `file://`) show `ERR_CONNECTION_REFUSED`.

**Cause**: The `API_BASE` URL in WEBAPP files is hardcoded to the wrong port.

**Fix**: All WEBAPP files must point to `http://localhost:8451` for local dev. Search for `localhost:` in all WEBAPP HTML files and verify the port.

---

## Quick health checks

**Is the server running?**
```bash
curl http://localhost:8451/v1/projects
```

**Are API keys created?**
```bash
cat src/data/api_keys.json   # Should have both hb_live_ and hb_read_ entries
```

**Is ingest working?**
```bash
curl -s -X POST http://localhost:8451/v1/ingest \
  -H "Authorization: Bearer hb_live_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"envelope":{"sdk":"test","sdk_version":"0.1","agent_id":"test"},"events":[{"event_id":"test-001","event_type":"heartbeat","agent_id":"test","timestamp":"2026-01-01T00:00:00Z","payload":{"kind":"heartbeat"}}]}'
```
Expected: `{"accepted":1,"rejected":0,...}`

**Are events on disk?**
```bash
ls src/data/events/   # Should have .json files per agent_id
```
