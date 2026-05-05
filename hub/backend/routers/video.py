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

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
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
# Phase 4 placeholder — when WebSocket support lands, add a sibling handler
# here:
#
#   @router.websocket("/{full_path:path}")
#   async def proxy_ws(full_path: str, websocket: WebSocket) -> None:
#       base = _resolve_sidecar_base()  # reuse the same lookup
#       ...bridge between client websocket and upstream websocket...
#
# Don't touch the proxy() function above to add it — keep the two handlers
# independent so the HTTP path stays simple.
# ---------------------------------------------------------------------------
