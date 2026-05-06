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
    InstallLocked,
    get_manager,
    is_marketplace_id,
    marketplace_default_port,
)


def _install_locked_response(plugin_id: str, action: str, exc: Exception) -> HTTPException:
    """Shared 409 shape for install/update/uninstall when handles are
    still held. Points the user at the Hub log so they can see which
    process or path is the holdout."""
    return HTTPException(
        status_code=409,
        detail={
            "error": "install_locked",
            "plugin": plugin_id,
            "action": action,
            "message": (
                f"Could not {action} {plugin_id} — a process is still "
                f"holding the install directory or its port. The Hub "
                f"tried to force-release any holders before retrying. "
                f"See %APPDATA%/Cortex/logs/cortex-hub.log for which "
                f"process or path was the holdout. Manual recovery: "
                f"close any open File Explorer / antivirus scan windows "
                f"on the install dir, kill any stray plugin .exe in "
                f"Task Manager, or reboot."
            ),
            "underlying": str(exc)[:300],
        },
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


class DevRegisterRequest(BaseModel):
    """Request body for /api/plugins/dev-register.

    The agent / curl / dev-mode UI calls this to track a sidecar
    that's already running externally — for instance, when the
    developer is iterating on the plugin's source tree. The Hub
    writes the registry from inside its own (non-sandboxed) process,
    which sidesteps the UWP %APPDATA% redirect issue that bites
    direct file edits from agents on Windows.
    """

    plugin_id: str
    host: str = "127.0.0.1"
    port: int | None = None  # falls back to marketplace default


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


@router.post("/install")
async def install(req: InstallRequest) -> dict[str, Any]:
    """Download, verify, extract, register, and start a plugin from
    its GitHub release. Synchronous — bundles are 50-150 MB so this
    can take 30-90s on residential connections. Returns the
    InstalledPlugin entry on success.

    On failure the previous installation (if any) is rolled back and
    the error surfaces with enough detail to act on (404 unknown
    asset, 400 bad SHA256, 502 GitHub unreachable, etc.)."""
    import httpx as _httpx  # local to keep top of file lean

    mgr = get_manager()
    variant_arg = None if req.variant in (None, "auto") else req.variant
    try:
        plugin = await mgr.install(
            req.plugin_id, variant=variant_arg, version=req.version
        )
    except InstallLocked as e:
        raise _install_locked_response(req.plugin_id, "install", e)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        # Bad SHA256 / unknown plugin id / bad variant — caller-fixable
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        # Platform / environment issues
        raise HTTPException(409, str(e))
    except _httpx.HTTPError as e:
        raise HTTPException(
            502,
            detail={
                "error": "github_unreachable",
                "plugin": req.plugin_id,
                "message": f"Could not fetch release metadata: {e}",
            },
        )
    except Exception as e:
        logger.exception("Install failed for %s", req.plugin_id)
        raise HTTPException(500, f"Install failed: {e}")
    return {"ok": True, "plugin": plugin.to_api_dict()}


@router.post("/dev-register")
async def dev_register(req: DevRegisterRequest) -> dict[str, Any]:
    """Register a sidecar that's already running externally as a
    dev-mode plugin (executable=null, auto_start=false).

    The Hub does the registry write from its own process context,
    so this works regardless of how the caller's filesystem is
    sandboxed (notably: agents running through the Microsoft Store
    Claude Desktop on Windows, whose %APPDATA% writes get redirected
    into the app's per-package container — see
    feedback_uwp_appdata_sandbox_redirect.md).

    Phase 0/dev only. Phase 5's real install flow has its own
    download + extract + register path; that lives in install().
    """
    if not is_marketplace_id(req.plugin_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_plugin",
                "plugin": req.plugin_id,
                "message": (
                    "dev-register only accepts plugin_ids that exist in the "
                    "marketplace. This guards against accidentally tracking "
                    "arbitrary processes as plugins."
                ),
            },
        )

    port = req.port if req.port is not None else marketplace_default_port(req.plugin_id)
    if port is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_port",
                "plugin": req.plugin_id,
                "message": (
                    "No port supplied and no default_port in the marketplace "
                    "entry. Pass port explicitly."
                ),
            },
        )

    plugin = InstalledPlugin(
        id=req.plugin_id,
        version="dev",
        variant="dev",
        host=req.host,
        port=port,
        executable=None,
        install_dir=None,
        auto_start=False,
    )
    get_manager().upsert(plugin)
    logger.info(
        "Registered %s in dev mode at %s:%d via dev-register",
        req.plugin_id,
        req.host,
        port,
    )
    return {"ok": True, "plugin": plugin.to_api_dict()}


@router.post("/{plugin_id}/update")
async def update(plugin_id: str) -> dict[str, Any]:
    """Update an installed plugin to the latest GitHub release. Same
    rollback semantics as install — if the new version fails to
    extract or start, the previous one is restored from a temp
    backup."""
    import httpx as _httpx

    mgr = get_manager()
    if mgr.get(plugin_id) is None:
        raise HTTPException(404, f"Plugin not installed: {plugin_id}")
    try:
        plugin = await mgr.update(plugin_id)
    except InstallLocked as e:
        raise _install_locked_response(plugin_id, "update", e)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except _httpx.HTTPError as e:
        raise HTTPException(
            502,
            detail={
                "error": "github_unreachable",
                "plugin": plugin_id,
                "message": f"Could not fetch release metadata: {e}",
            },
        )
    except Exception as e:
        logger.exception("Update failed for %s", plugin_id)
        raise HTTPException(500, f"Update failed: {e}")
    return {"ok": True, "plugin": plugin.to_api_dict()}


@router.delete("/{plugin_id}")
async def uninstall(
    plugin_id: str,
    keep_user_data: bool = Query(default=True),
) -> dict[str, Any]:
    mgr = get_manager()
    if mgr.get(plugin_id) is None:
        raise HTTPException(404, f"Plugin not registered: {plugin_id}")
    try:
        mgr.uninstall(plugin_id, keep_user_data=keep_user_data)
    except InstallLocked as e:
        # Registry entry intentionally preserved by the manager so the
        # user can retry / reboot. Surface a 409 with action context.
        raise _install_locked_response(plugin_id, "uninstall", e)
    except Exception as e:
        logger.exception("Uninstall failed for %s", plugin_id)
        raise HTTPException(500, f"Uninstall failed: {e}")
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
    """Hit GitHub for the latest release tag of each installed plugin.
    Returns {plugin_id: latest_version_or_None_if_current}. Synchronous —
    callers wait on the GitHub round-trip.

    Side effect: caches the result on each plugin (latest_available_version
    + last_update_check_at) so the Plugins tab can show "Update available"
    without re-querying.
    """
    return await get_manager().check_updates(plugin_id)


@router.post("/check-updates")
async def check_updates_refresh(
    plugin_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Manual "Check for updates now" — same as GET but POST-shaped so
    the UI's intent (refresh the cache) is explicit. Wired to the
    Plugins tab's "Check for updates" button. Returns the same
    {plugin_id: latest_or_None} map."""
    result = await get_manager().check_updates(plugin_id)
    return {"ok": True, "checked_at": _utc_now(), "result": result}


def _utc_now() -> str:
    """Local copy to avoid pulling the helper out of plugin_manager
    just for one timestamp."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/{plugin_id}")
async def get_plugin_detail(plugin_id: str) -> dict[str, Any]:
    plugin = get_manager().get(plugin_id)
    if plugin is None:
        raise HTTPException(404, f"Plugin not registered: {plugin_id}")
    return plugin.to_api_dict()
