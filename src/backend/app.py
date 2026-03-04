"""HiveBoard API Server — FastAPI application.

Run with: uvicorn backend.app:app --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__ as BACKEND_VERSION
from backend.middleware import AuthMiddleware, RateLimitMiddleware
from backend.storage_json import JsonStorageBackend
from backend.llm_pricing import LlmPricingEngine
from shared.enums import PRUNE_INTERVAL_SECONDS


# ═══════════════════════════════════════════════════════════════════════════
#  APP LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = logging.getLogger("hiveboard.retention")
    storage = JsonStorageBackend()
    await storage.initialize()

    # Prune stale events before serving requests
    result = await storage.prune_events()
    if result["total_pruned"] > 0:
        logger.info(
            "Startup pruning: %d events removed (ttl=%d, cold=%d), %d remaining",
            result["total_pruned"],
            result["ttl_pruned"],
            result["cold_pruned"],
            storage._total_event_count(),
        )

    app.state.storage = storage
    # Initialize LLM pricing engine
    pricing = LlmPricingEngine()
    await pricing.initialize()
    app.state.pricing = pricing
    # Bootstrap: create default tenant + key if none exist
    await _bootstrap_dev_tenant(storage)
    # Initialize WebSocket mode (local direct WS vs production AWS bridge)
    from backend.config import get as _cfg
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
            logging.getLogger(__name__).warning(
                "Production mode but no ws_gateway_endpoint — falling back to local WS"
            )
            app.state.ws_bridge = None
            app.state.ws_mode = "local"
    else:
        app.state.ws_bridge = None
        app.state.ws_mode = "local"
    # Start background tasks
    ping_task = asyncio.create_task(_ws_ping_loop())
    prune_task = asyncio.create_task(_prune_loop(storage))
    yield
    prune_task.cancel()
    ping_task.cancel()
    await storage.close()


async def _ws_ping_loop():
    """Send WebSocket pings every 30 seconds."""
    from backend.websocket import ws_manager
    while True:
        await asyncio.sleep(30)
        await ws_manager.ping_all()


async def _prune_loop(storage: JsonStorageBackend):
    """Periodically prune expired events and old aggregates."""
    logger = logging.getLogger("hiveboard.retention")
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            result = await storage.prune_events()
            total = result["total_pruned"]
            if total > 0:
                logger.info(
                    "Event pruning: %d removed (ttl=%d, cold=%d), %d remaining",
                    total,
                    result["ttl_pruned"],
                    result["cold_pruned"],
                    storage._total_event_count(),
                )
            # Prune old aggregate buckets
            agg_result = await storage.prune_aggregates()
            for table, count in agg_result.items():
                if count > 0:
                    logger.info("Aggregate pruning: %s — %d buckets removed", table, count)
        except Exception:
            logger.exception("Event pruning failed")


async def _bootstrap_dev_tenant(storage: JsonStorageBackend):
    """Create a dev tenant, API key, and owner user on first run.

    The dev key is read from config.json (or HIVEBOARD_DEV_KEY env var).
    If unset, bootstrap is skipped (no hardcoded key in source).
    """
    from backend.config import get as _cfg
    raw_key = _cfg("dev_key")
    if not raw_key:
        return
    tenant = await storage.get_tenant("dev")
    if tenant is not None:
        return
    await storage.create_tenant("dev", "Development", "dev")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await storage.create_api_key(
        key_id="dev-key",
        tenant_id="dev",
        key_hash=key_hash,
        key_prefix=raw_key[:8],
        key_type="live",
        label="Development API Key",
    )
    # Bootstrap dev read key (access_id for dashboard)
    from shared.enums import DEFAULT_ACCESS_ID_LABEL
    dev_read_key = _cfg("dev_read_key")
    if not dev_read_key:
        # Generate a deterministic read key from the dev_key
        dev_read_key = "hb_read_" + raw_key[8:] if len(raw_key) > 8 else "hb_read_" + hashlib.sha256(raw_key.encode()).hexdigest()[:32]
    read_hash = hashlib.sha256(dev_read_key.encode()).hexdigest()
    await storage.create_api_key(
        key_id="dev-read-key",
        tenant_id="dev",
        key_hash=read_hash,
        key_prefix=dev_read_key[:12],
        key_type="read",
        label=DEFAULT_ACCESS_ID_LABEL,
    )
    # Bootstrap dev owner user
    from backend.auth import hash_password
    dev_password = _cfg("dev_password")
    if not dev_password:
        return
    try:
        await storage.create_user(
            user_id="dev-owner",
            tenant_id="dev",
            email="admin@hiveboard.dev",
            password_hash=hash_password(dev_password),
            name="Dev Admin",
            role="owner",
        )
    except ValueError:
        pass  # Already exists


# ═══════════════════════════════════════════════════════════════════════════
#  APP CREATION
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="HiveBoard API",
    version=BACKEND_VERSION,
    description="Observability platform for AI agents",
    lifespan=lifespan,
)

# CORS — only in local mode; IIS handles CORS in production
from backend.config import get as _cfg
if _cfg("mode", "local") != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Middleware stack (order matters: rate limit wraps auth wraps routes)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)


# ═══════════════════════════════════════════════════════════════════════════
#  ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════════

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc):
    if isinstance(exc.detail, dict):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail if isinstance(exc.detail, str) else "error",
            "message": str(exc.detail),
            "status": exc.status_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    field_errors = []
    for err in exc.errors():
        field_errors.append({
            "field": ".".join(str(loc) for loc in err.get("loc", [])),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        })
    return JSONResponse(
        status_code=400,
        content={
            "error": "validation_error",
            "message": "Request validation failed",
            "status": 400,
            "details": {"fields": field_errors},
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
#  HEALTH + DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "ok", "version": BACKEND_VERSION}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Redirect to the dashboard served from /static/."""
    static_dir = Path(__file__).parent.parent / "static"
    index = static_dir / "fleet.html"
    if index.exists():
        return HTMLResponse(content=index.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)


# Mount static dashboard files
_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ═══════════════════════════════════════════════════════════════════════════
#  INCLUDE ROUTE MODULES
# ═══════════════════════════════════════════════════════════════════════════

from backend.routes.ingest import router as ingest_router
from backend.routes.agents import router as agents_router
from backend.routes.tasks import router as tasks_router
from backend.routes.events import router as events_router
from backend.routes.insights import router as insights_router
from backend.routes.projects import router as projects_router
from backend.routes.alerts import router as alerts_router
from backend.routes.auth_routes import router as auth_router
from backend.routes.admin import router as admin_router
from backend.routes.ws import router as ws_router

app.include_router(ingest_router)
app.include_router(agents_router)
app.include_router(tasks_router)
app.include_router(events_router)
app.include_router(insights_router)
app.include_router(projects_router)
app.include_router(alerts_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(ws_router)
