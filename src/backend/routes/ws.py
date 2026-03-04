"""WebSocket and WebSocket bridge endpoints."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

router = APIRouter(tags=["websocket"])


@router.websocket("/v1/stream")
async def websocket_stream(ws: WebSocket):
    """Real-time event and agent status streaming."""
    from backend.websocket import ws_manager

    # Auth via query param
    token = ws.query_params.get("token", "")
    if not token:
        await ws.close(code=4001, reason="Missing token parameter")
        return

    key_hash = hashlib.sha256(token.encode()).hexdigest()
    storage = ws.app.state.storage
    info = await storage.authenticate(key_hash)
    if info is None:
        await ws.close(code=4001, reason="Invalid API key")
        return

    conn = await ws_manager.accept(ws, info.tenant_id, info.key_id)
    if conn is None:
        return  # Connection limit exceeded — already closed

    try:
        while True:
            data = await ws.receive_json()
            conn.missed_pongs = 0  # Any message resets pong counter
            await ws_manager.handle_message(conn, data)
    except WebSocketDisconnect:
        ws_manager.disconnect(conn)
    except Exception:
        ws_manager.disconnect(conn)


@router.post("/ws/connect")
async def ws_bridge_connect(request: Request):
    """Called by AWS API Gateway on $connect."""
    if getattr(request.app.state, "ws_mode", "local") != "bridge":
        return JSONResponse({"error": "WebSocket bridge not active"}, status_code=501)

    connection_id = request.headers.get("connectionId")
    token = request.query_params.get("token", "")

    if not connection_id or not token:
        return JSONResponse(
            {"error": "missing connectionId or token"}, status_code=400
        )

    # Authenticate via API key (same as direct WebSocket handler)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    storage = request.app.state.storage
    info = await storage.authenticate(key_hash)
    if info is None:
        return JSONResponse({"error": "invalid API key"}, status_code=403)

    request.app.state.ws_bridge.register(connection_id, info.tenant_id, info.key_id)
    return JSONResponse({"status": "connected"})


@router.post("/ws/disconnect")
async def ws_bridge_disconnect(request: Request):
    """Called by AWS API Gateway on $disconnect."""
    if getattr(request.app.state, "ws_mode", "local") != "bridge":
        return JSONResponse({"error": "WebSocket bridge not active"}, status_code=501)

    connection_id = request.headers.get("connectionId")
    if connection_id:
        request.app.state.ws_bridge.unregister(connection_id)
    return JSONResponse({"status": "disconnected"})


@router.post("/ws/message")
async def ws_bridge_message(request: Request):
    """Called by AWS API Gateway on $default (all client messages)."""
    if getattr(request.app.state, "ws_mode", "local") != "bridge":
        return JSONResponse({"error": "WebSocket bridge not active"}, status_code=501)

    connection_id = request.headers.get("connectionId")
    if not connection_id:
        return JSONResponse({"error": "missing connectionId"}, status_code=400)

    body = await request.json()
    action = body.get("action", "")
    bridge = request.app.state.ws_bridge

    # IN-1: Defensive re-registration for unknown connectionIds
    if not bridge.is_registered(connection_id):
        token = body.get("token")
        if token:
            key_hash = hashlib.sha256(token.encode()).hexdigest()
            storage = request.app.state.storage
            info = await storage.authenticate(key_hash)
            if info:
                bridge.register(connection_id, info.tenant_id, info.key_id)
            else:
                return JSONResponse({"error": "invalid token"}, status_code=403)
        else:
            return JSONResponse(
                {"error": "unknown connection, include token to re-register"},
                status_code=400,
            )

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
        return JSONResponse({"status": "pong"})

    return JSONResponse({"status": "unknown_action"})
