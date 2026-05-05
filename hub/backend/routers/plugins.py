"""Plugin admin API — list, install, update, uninstall, restart, health.

This is the surface the Plugins tab in Settings calls. Lifecycle is
delegated to services.plugin_manager.PluginManager.

Phase 0:
- list / get / restart / health / uninstall: real implementations
- install / update / check-updates: 501 NotImplemented (stub) — the
  GitHub-release wiring lands in Phase 5
- marketplace: hardcoded list (cortex-vision only)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.plugin_manager import (
    InstalledPlugin,
    get_manager,
)

logger = logging.getLogger("cortex.hub.plugins")

router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InstallRequest(BaseModel):
    plugin_id: str
    variant: str = "auto"
    version: str = "latest"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_installed() -> list[dict[str, Any]]:
    mgr = get_manager()
    return [p.to_api_dict() for p in mgr.list_installed()]


@router.get("/marketplace")
async def list_marketplace() -> list[dict[str, Any]]:
    return get_manager().marketplace()


@router.post("/install", status_code=501)
async def install(req: InstallRequest) -> dict[str, Any]:
    """Phase 0: stub. Real install lands in Phase 5.

    The 501 forces dev users into the documented hand-edit-registry.json
    flow — which is the right amount of friction for Phase 0 since
    there's nothing to install yet (cortex-vision hasn't published a
    GitHub release).
    """
    raise HTTPException(
        status_code=501,
        detail={
            "error": "install_not_implemented",
            "plugin": req.plugin_id,
            "message": (
                "Plugin installs land in Phase 5 of the v0.18 cycle. For "
                "now, run the sidecar from source and register it via "
                "%APPDATA%/Cortex/plugins/registry.json. See "
                "cortex-vision/HANDOFF.md → 'Testing it end-to-end'."
            ),
        },
    )


@router.post("/{plugin_id}/update", status_code=501)
async def update(plugin_id: str) -> dict[str, Any]:
    raise HTTPException(
        status_code=501,
        detail={
            "error": "update_not_implemented",
            "plugin": plugin_id,
            "message": "Plugin updates land in Phase 5.",
        },
    )


@router.delete("/{plugin_id}")
async def uninstall(
    plugin_id: str,
    keep_user_data: bool = Query(default=True),
) -> dict[str, Any]:
    mgr = get_manager()
    if mgr.get(plugin_id) is None:
        raise HTTPException(404, f"Plugin not registered: {plugin_id}")
    mgr.uninstall(plugin_id, keep_user_data=keep_user_data)
    return {"ok": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/restart")
async def restart(plugin_id: str) -> dict[str, Any]:
    mgr = get_manager()
    plugin = mgr.get(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin not registered: {plugin_id}")

    if plugin.executable is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "dev_mode_no_restart",
                "plugin": plugin_id,
                "message": (
                    "This plugin is registered in dev mode (no executable). "
                    "Restart the sidecar process yourself."
                ),
            },
        )

    try:
        mgr.restart(plugin_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("Restart failed for %s", plugin_id)
        raise HTTPException(500, f"Restart failed: {e}")
    return {"ok": True, "plugin": mgr.get(plugin_id).to_api_dict()}  # type: ignore[union-attr]


@router.get("/{plugin_id}/health")
async def health(plugin_id: str) -> dict[str, Any]:
    mgr = get_manager()
    h = await mgr.health(plugin_id)
    return {
        "plugin_id": h.plugin_id,
        "running": h.running,
        "healthy": h.healthy,
        "last_check": h.last_check,
        "detail": h.detail,
        "error": h.error,
    }


@router.get("/check-updates")
async def check_updates(
    plugin_id: str | None = Query(default=None),
) -> dict[str, str | None]:
    """Phase 0: returns {pid: None} for everything (no update available).
    Phase 5 hits GitHub releases."""
    return await get_manager().check_updates(plugin_id)


@router.get("/{plugin_id}")
async def get_plugin_detail(plugin_id: str) -> dict[str, Any]:
    plugin = get_manager().get(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin not registered: {plugin_id}")
    return plugin.to_api_dict()
