"""Lemon Squeezer dispatch-export connector routes.

The connector itself (pull from Pi -> push to Lemon) lives in
services/lemon_export.py and runs on a background loop from the app
lifespan. These routes expose a manual trigger + status for the Hub /
debugging.
"""
from fastapi import APIRouter

from config import settings
from services import lemon_export

router = APIRouter()


@router.post("/export")
async def export_now() -> dict:
    """Pull graded dispatches from the Pi and push to Lemon Squeezer now.
    Idempotent; advances the cursor only on a confirmed persist."""
    return await lemon_export.run_once()


@router.get("/status")
async def export_status() -> dict:
    return {
        "enabled": settings.lemon_export_enabled,
        "lemon_url": settings.lemon_url,
        "interval_s": settings.lemon_export_interval_s,
        "cursor": lemon_export.read_cursor(),
    }
