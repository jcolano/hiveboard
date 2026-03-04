# HiveBoard Open-Source Readiness — Implementation Plan

## Item 1: Remove/replace all hardcoded API keys and production URLs

### 1a. `src/backend/app.py` (line 192)
- Replace hardcoded `"hb_read_dev000000000000000000000000000000"` with a value generated from the dev_key config
- Generate the read key deterministically from `dev_key` (e.g. `"hb_read_" + dev_key[8:]`) or read it from a new config field `dev_read_key`

### 1b. `src/static/js/common.js` (line 18)
- Change the local fallback accessId from the hardcoded key to empty string `''`
- The dashboard will prompt the user to enter their access ID instead of auto-authenticating with a baked-in key

### 1c. `README.md` (lines 32, 47, 496)
- Replace `hb_live_dev000000000000000000000000000000` with `hb_live_YOUR_KEY_HERE`
- Replace live demo URLs pointing to `hiveboard.net` with `localhost:8000` examples

### 1d. `docs/TROUBLESHOOTING.md` (lines 16, 57, 60, 123)
- Replace all instances of `hb_live_dev000000000000000000000000000000` with `hb_live_YOUR_KEY_HERE`
- Keep the instructional context intact

---

## Item 2: Remove the AWS API Gateway URL from `common.js`

**File:** `src/static/js/common.js` (line 15)
- Replace `'wss://85g4pm5cg9.execute-api.us-east-1.amazonaws.com/production/'` with `null`
- The production WebSocket URL should come from a config endpoint or be set by the deployer, not baked into JS
- Add a comment: `// Set your WebSocket gateway URL here for production deployments`

---

## Item 3: Sanitize `docs/specs/create-user-manually.txt`

**File:** `docs/specs/create-user-manually.txt`
- Replace production URL `https://mlbackend.net/loophive` with `http://localhost:8000`
- Replace API key with `hb_live_YOUR_KEY_HERE`
- Replace credentials (`jc@demo.com`, `demo1234`) with generic placeholders (`user@example.com`, `your-password`)

---

## Item 4: Fix the JWT default secret

**File:** `src/backend/auth.py` (line 15)
- Change the default from `"hiveboard-dev-secret-change-in-production"` to `None`
- Add a startup check: if `jwt_secret` is not set in config, generate a random one and log a warning
- This prevents accidentally running production with a known default secret

---

## Item 5: Update README project structure to match reality

**File:** `README.md` (lines 435-459)
- Replace the current incorrect structure with the actual layout:
  ```
  hiveboard/
  ├── src/
  │   ├── backend/          # FastAPI server
  │   │   ├── app.py        # Main application + all routes
  │   │   ├── auth.py       # JWT + password hashing
  │   │   ├── config.py     # Configuration loader
  │   │   ├── middleware.py  # Auth + rate limiting
  │   │   ├── storage_json.py # JSON file storage (MVP)
  │   │   ├── websocket.py  # Local WebSocket manager
  │   │   ├── ws_bridge.py  # AWS API Gateway bridge (optional)
  │   │   ├── aggregator.py # Event aggregation
  │   │   ├── alerting.py   # Alert rule evaluation
  │   │   └── llm_pricing.py # LLM cost estimation
  │   ├── sdk/              # HiveLoop Python SDK
  │   │   └── hiveloop/     # Package source
  │   ├── shared/           # Shared models + enums
  │   └── static/           # Dashboard HTML/JS/CSS
  ├── docs/                 # Specs, guides, user manual
  ├── config.example.json   # Configuration template
  ├── pyproject.toml        # Python project config
  ├── LICENSE               # MIT
  └── README.md
  ```

---

## Item 6: Add `CONTRIBUTING.md`

Create `CONTRIBUTING.md` with:
- Development setup instructions (clone, create venv, install with `pip install -e ".[backend,sdk,dev]"`)
- How to run the server locally
- How to run tests
- Code style guidelines (Python 3.11+, type hints, async/await patterns)
- PR process (fork, branch, test, submit)
- Issue reporting guidelines

---

## Item 7: Add `SECURITY.md`

Create `SECURITY.md` with:
- Responsible disclosure policy
- Contact method for reporting vulnerabilities (email or GitHub Security Advisories)
- Scope of what counts as a security issue
- Expected response timeline

---

## Item 8: Un-ignore `tests/` so contributors can run them

**File:** `.gitignore`
- Note: The `tests/` directory does not currently exist in the repo
- Remove `tests/` from `.gitignore`
- Create a minimal `tests/` directory with:
  - `tests/__init__.py`
  - `tests/conftest.py` — shared fixtures (test client, mock storage)
  - `tests/test_health.py` — basic smoke test (GET /health returns 200)
  - `tests/test_ingest.py` — basic ingest validation test
- This gives contributors a working test harness to build on

---

## Item 9: Fix install instructions

**File:** `README.md` (lines 140-152)
- Replace `pip install -r requirements.txt` with:
  ```bash
  pip install -e ".[backend]"
  ```
- Add SDK install:
  ```bash
  pip install -e "./src/sdk"
  ```
- Add dev dependencies:
  ```bash
  pip install -e ".[backend,dev]"
  ```

---

## Item 10: Split `app.py` into route modules

**File:** `src/backend/app.py` (3,299 lines → ~6 smaller files)

Create `src/backend/routes/` package:
- `src/backend/routes/__init__.py` — router registry
- `src/backend/routes/ingest.py` — POST /v1/ingest (~330 lines)
- `src/backend/routes/agents.py` — /v1/agents/*, /v1/pipeline (~170 lines)
- `src/backend/routes/tasks.py` — /v1/tasks/* (~200 lines)
- `src/backend/routes/events.py` — /v1/events, /v1/metrics, /v1/cost/*, /v1/llm-calls (~250 lines)
- `src/backend/routes/insights.py` — /v1/insights/* (~500 lines)
- `src/backend/routes/projects.py` — /v1/projects/* (~230 lines)
- `src/backend/routes/alerts.py` — /v1/alerts/* (~110 lines)
- `src/backend/routes/auth.py` — /v1/auth/*, /v1/users/*, /v1/invites/*, /v1/api-keys/* (~550 lines)
- `src/backend/routes/admin.py` — /v1/admin/* (~60 lines)
- `src/backend/routes/websocket.py` — /ws, /ws/connect, /ws/disconnect, /ws/message (~130 lines)

Keep in `app.py` (~200 lines):
- App creation, lifespan, middleware
- Error handlers
- Health + dashboard routes
- Static file mounting
- Router includes
- Helper functions used across routes

Each route module uses `APIRouter` and is included via `app.include_router()`.

---

## Item 11: Add CI workflow (GitHub Actions)

Create `.github/workflows/ci.yml`:
- Trigger on push and PR to `main`
- Python 3.11 matrix
- Steps: checkout, install deps, run linter (ruff), run tests (pytest)
- Keep it simple — no deployment, just quality gates

---

## Item 12: Make `boto3` an optional dependency

### 12a. `pyproject.toml`
- Add a new optional dependency group:
  ```toml
  [project.optional-dependencies]
  aws = ["boto3>=1.34.0"]
  ```

### 12b. `src/backend/ws_bridge.py` (line 21)
- Wrap `import boto3` in a try/except:
  ```python
  try:
      import boto3
  except ImportError:
      boto3 = None
  ```
- Add a guard at the top of `WebSocketBridge.__init__()`:
  ```python
  if boto3 is None:
      raise ImportError("boto3 is required for production WebSocket bridge. Install with: pip install hiveboard[aws]")
  ```

---

## Item 13: Clean up and add `.env.example`

### 13a. Create `.env.example`
```env
# HiveBoard Configuration (environment variable overrides)
HIVEBOARD_DEV_KEY=hb_live_CHANGE_ME_00000000000000000000
HIVEBOARD_DEV_PASSWORD=change-me
HIVEBOARD_JWT_SECRET=change-me-in-production
HIVEBOARD_MODE=local
HIVEBOARD_WS_GATEWAY_ENDPOINT=
HIVEBOARD_WS_GATEWAY_REGION=us-east-1
```

### 13b. Update `.gitignore`
Add missing patterns:
```gitignore
# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Coverage
htmlcov/
.coverage
```

Remove `tests/` from `.gitignore` (covered in Item 8).

### 13c. Sanitize `docs/WEBAPP/*.html`
- Replace all 6 files' hardcoded `https://mlbackend.net/loophive` with a configurable pattern
- Add a small JS config block at the top that reads from `window.HIVEBOARD_CONFIG` or defaults to relative URLs

### 13d. Clean up internal docs
- Remove `docs/specs/HOW_TO_PUBLISH_PIP.txt` (internal dev note)
- Remove `docs/specs/create-user-manually.txt` after sanitizing (or keep sanitized version)
- Consider adding `docs/PRESENTATION/` to `.gitignore` (hackathon-specific content)

---

## Execution Order

The items will be implemented in dependency order:

1. **Item 4** — Fix JWT secret (auth.py) — no dependencies
2. **Item 1** — Remove hardcoded API keys (app.py, common.js, README, TROUBLESHOOTING)
3. **Item 2** — Remove AWS WebSocket URL (common.js)
4. **Item 3** — Sanitize create-user-manually.txt
5. **Item 12** — Make boto3 optional (ws_bridge.py, pyproject.toml)
6. **Item 10** — Split app.py into route modules — largest change, do before docs
7. **Item 5** — Update README project structure (after split is done)
8. **Item 9** — Fix install instructions in README
9. **Item 8** — Un-ignore tests/ and create test harness
10. **Item 11** — Add CI workflow
11. **Item 6** — Add CONTRIBUTING.md
12. **Item 7** — Add SECURITY.md
13. **Item 13** — .env.example, .gitignore updates, WEBAPP cleanup, internal docs cleanup
