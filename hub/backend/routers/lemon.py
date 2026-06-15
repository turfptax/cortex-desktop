"""Lemon Squeezer dispatch-export connector routes.

The connector itself (pull from Pi -> push to Lemon) lives in
services/lemon_export.py and runs on a background loop from the app
lifespan. These routes back the Settings toggle card + the System
reporting panel: status (with health + aggregates), recent history, a
live enable/disable, and a manual trigger.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from config import settings
from services import lemon_export

router = APIRouter()


@router.post("/export")
async def export_now() -> dict:
    """Pull graded dispatches from the Pi and push to Lemon Squeezer now.
    Idempotent; advances the cursor only on a confirmed persist."""
    return await lemon_export.run_once()


@router.get("/status")
async def export_status(request: Request) -> dict:
    exporter = getattr(request.app.state, "lemon_exporter", None)
    return {
        "enabled": settings.lemon_export_enabled,
        "running": exporter.is_running() if exporter else False,
        "reachable": await lemon_export.lemon_reachable(),
        "lemon_url": settings.lemon_url,
        "interval_s": settings.lemon_export_interval_s,
        "cursor": lemon_export.read_cursor(),
        "stats": lemon_export.history_stats(),
    }


@router.get("/history")
async def export_history(limit: int = 50) -> dict:
    return {"history": lemon_export.read_history(limit)}


class EnableBody(BaseModel):
    enabled: bool


@router.post("/enable")
async def export_enable(body: EnableBody, request: Request) -> dict:
    """Persist the on/off intent AND apply it live to the running poller,
    so the toggle takes effect without a Hub restart."""
    # Persist to config.json (reuse the settings router's writer so there's
    # one config path, no second source of truth).
    from routers.settings import _load_config, _save_config
    cfg = _load_config()
    cfg["lemon_export_enabled"] = body.enabled
    _save_config(cfg)
    # Apply live.
    settings.lemon_export_enabled = body.enabled
    exporter = getattr(request.app.state, "lemon_exporter", None)
    if exporter:
        exporter.set_enabled(body.enabled)
    return await export_status(request)
