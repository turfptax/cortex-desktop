"""Video plugin proxy — forwards /api/video/* to the cortex-vision sidecar.

This router has no business logic. Adding a new endpoint to cortex-vision
automatically becomes available to the frontend with zero changes here.

WebSocket pass-through is NOT implemented in Phase 0 — it's the Phase 4
ask. The router shape is intentionally kept open so a sibling
@router.websocket("/{full_path:path}") handler can be dropped in
alongside this without restructuring. httpx doesn't speak WebSocket; the
Phase 4 implementation will use FastAPI's native WebSocket + a
`websockets`-package bridge to the upstream sidecar.

Phase 0 contract:
- All HTTP methods proxied verbatim
- 503 with structured error body when the sidecar isn't installed/running
- 502 + structured body when the sidecar is supposed to be up but the
  socket connection fails mid-flight
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from services.plugin_manager import get_plugin

logger = logging.getLogger("cortex.hub.video")

router = APIRouter()
PLUGIN_ID = "cortex-vision"

# Hop-by-hop / framing headers that must NOT be forwarded.
# Includes `date` and `server` because uvicorn generates its own —
# forwarding the upstream's would double-stamp them on every response.
_HOP_BY_HOP = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "upgrade",
    "date",
    "server",
})


def _resolve_sidecar_base() -> str:
    """Look up the registered cortex-vision sidecar. 503 if not running."""
    plugin = get_plugin(PLUGIN_ID)
    if plugin is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "plugin_not_installed",
                "plugin": PLUGIN_ID,
                "message": (
                    "Cortex Vision plugin is not installed. Install it from "
                    "Settings → Plugins."
                ),
                "install_url": "/settings/plugins",
            },
        )
    if not plugin.is_running:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "plugin_not_running",
                "plugin": PLUGIN_ID,
                "message": (
                    "Cortex Vision is registered but not running. Open "
                    "Settings → Plugins to start it."
                ),
                "install_url": "/settings/plugins",
            },
        )
    return f"http://{plugin.host}:{plugin.port}"


def _forwardable_headers(request: Request) -> dict[str, str]:
    return {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


def _response_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v
        for k, v in upstream_headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy(full_path: str, request: Request) -> Any:
    """Forward any /api/video/<path> request to the sidecar verbatim."""
    base = _resolve_sidecar_base()
    url = f"{base}/api/video/{full_path}"

    body = await request.body()
    headers = _forwardable_headers(request)

    # Open the client outside `async with` so its lifetime extends across
    # the StreamingResponse iteration. The closer in the iterator below
    # shuts down both the request and the client when the body is fully
    # streamed (or the client disconnects).
    timeout = httpx.Timeout(60.0, connect=2.0)
    client = httpx.AsyncClient(timeout=timeout)
    try:
        req = client.build_request(
            method=request.method,
            url=url,
            content=body,
            headers=headers,
            params=request.query_params,
        )
        upstream = await client.send(req, stream=True)
    except httpx.ConnectError as e:
        await client.aclose()
        logger.warning("Sidecar connection refused: %s", e)
        return JSONResponse(
            status_code=502,
            content={
                "error": "plugin_connection_refused",
                "plugin": PLUGIN_ID,
                "message": (
                    "Cortex Vision is registered as running but rejected "
                    "the connection. It may be restarting."
                ),
            },
        )
    except httpx.TimeoutException:
        await client.aclose()
        return JSONResponse(
            status_code=504,
            content={
                "error": "plugin_timeout",
                "plugin": PLUGIN_ID,
                "message": "Cortex Vision did not respond within 60s.",
            },
        )
    except Exception:
        await client.aclose()
        raise

    async def streamer():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        streamer(),
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# WebSocket pass-through (Phase 4 — live mode).
#
# Bridges a browser WebSocket to the cortex-vision sidecar's live event
# stream. cortex-vision's protocol is one-way (sidecar -> consumer) but
# we still forward client-to-upstream messages in case future versions
# accept commands over the same socket.
#
# Scoped to /live/ws specifically rather than a path glob so it can't
# accidentally shadow future HTTP routes the sidecar adds.
# ---------------------------------------------------------------------------


def _resolve_sidecar_for_ws() -> tuple[str, int] | None:
    """Resolve sidecar host/port for a WebSocket connection. Returns
    None if the plugin isn't installed or isn't running — the caller
    closes the client socket with the appropriate close code."""
    plugin = get_plugin(PLUGIN_ID)
    if plugin is None or not plugin.is_running:
        return None
    return plugin.host, plugin.port


@router.websocket("/live/ws")
async def proxy_live_ws(client_ws: WebSocket) -> None:
    """Bridge a browser WebSocket to the sidecar's live event stream."""
    target = _resolve_sidecar_for_ws()
    if target is None:
        # Per RFC 6455: 1011 = "internal error" / sidecar unavailable.
        # We accept first to send a structured close reason — some browsers
        # don't surface the close reason if the connection is rejected
        # before accept().
        await client_ws.accept()
        await client_ws.close(
            code=1011, reason="Cortex Vision plugin not installed or not running"
        )
        return

    host, port = target
    upstream_url = f"ws://{host}:{port}/api/video/live/ws"
    await client_ws.accept()

    try:
        async with websockets.connect(upstream_url) as upstream:

            async def upstream_to_client() -> None:
                # Returns naturally when the upstream closes
                # (websockets exits the async-iter on close frame).
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except Exception:
                    # Upstream disconnect / send-after-client-close — nothing
                    # to recover; the sibling task or the gather wrapper
                    # finalizes the close.
                    pass

            async def client_to_upstream() -> None:
                # WebSocketDisconnect is raised when the browser closes;
                # treat as a normal exit.
                try:
                    while True:
                        msg = await client_ws.receive_text()
                        await upstream.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            # Run both directions concurrently. As soon as EITHER side
            # finishes (upstream closes, browser closes, error), cancel
            # the still-blocked task so the gather wrapper exits and we
            # can close both sockets cleanly. The naive
            # asyncio.gather(...) blocks forever waiting on the side
            # that's still doing receive_text() / aiter_raw().
            tasks = [
                asyncio.create_task(upstream_to_client()),
                asyncio.create_task(client_to_upstream()),
            ]
            try:
                _, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                # Drain so the cancellations actually run before we exit
                # the websockets context manager.
                for t in pending:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            except Exception as inner:
                logger.warning("WebSocket bridge gather error: %s", inner)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket bridge error: %s", exc)
        try:
            await client_ws.close(code=1011, reason="upstream sidecar error")
        except Exception:
            pass
