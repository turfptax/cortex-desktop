"""Cortex Hub — FastAPI backend.

Unified web interface for:
  - Chat with local LM Studio model
  - Training pipeline management
  - Pi Zero interaction
"""

import asyncio
import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from routers import (
    chat,
    training,
    pi,
    games,
    data,
    learning,
    overseer,
    plugins as plugins_router,
    transcribe,
    video,
    voice,
)
from routers import settings as settings_router
from services.plugin_manager import PluginManager, set_manager
from services.video_overseer_bridge import VideoOverseerBridge

# --- Logging setup ---
LOG_DIR = Path(os.environ.get("APPDATA", ".")) / "Cortex" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "cortex-hub.log"

# Ring buffer for in-memory log access via API
_log_buffer: deque[str] = deque(maxlen=500)


class BufferHandler(logging.Handler):
    """Push formatted log lines into the in-memory ring buffer."""
    def emit(self, record):
        try:
            _log_buffer.append(self.format(record))
        except Exception:
            pass


_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s  %(message)s",
                         datefmt="%H:%M:%S")

# File handler: 2 MB max, keep 3 old files (≤8 MB total)
_fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3,
                          encoding="utf-8")
_fh.setFormatter(_fmt)

# In-memory handler
_bh = BufferHandler()
_bh.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_fh, _bh])
# Quiet noisy libs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("cortex.hub")
logger.info("Cortex Hub starting — log file: %s", LOG_FILE)

# v0.18.0-dev.25 (2026-05-19): app version reads from cortex_desktop
# package, not hardcoded. Previously a bare "0.1.0" leaked into
# /openapi.json AND into the frontend's UpdateCard fallback when the
# /check-update response hadn't arrived yet, making Tory's first-launch
# header read "0.1.0" until he manually clicked "Check for update".
try:
    from cortex_desktop import __version__ as _cd_version
except ImportError:
    _cd_version = "0.0.0-unknown"
app = FastAPI(title="Cortex Hub", version=_cd_version)

# CORS for Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(training.router, prefix="/api/training", tags=["training"])
app.include_router(pi.router, prefix="/api/pi", tags=["pi"])
app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(learning.router, prefix="/api/learning", tags=["learning"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(overseer.router, prefix="/api/overseer", tags=["overseer"])
app.include_router(transcribe.router, prefix="/api/transcribe", tags=["transcribe"])
app.include_router(voice.router, prefix="/api/voice", tags=["voice"])
app.include_router(plugins_router.router, prefix="/api/plugins", tags=["plugins"])
app.include_router(video.router, prefix="/api/video", tags=["video"])


# --- Plugin sidecar lifecycle ----------------------------------------------
# Owns the registry at %APPDATA%/Cortex/plugins/registry.json. Spawns
# auto_start=true plugins on boot, polls health every 5s, gracefully
# stops everything on shutdown. See services/plugin_manager.py.

_health_task: asyncio.Task | None = None
_bridge_task: asyncio.Task | None = None


@app.on_event("startup")
async def _start_plugins() -> None:
    global _health_task, _bridge_task
    manager = PluginManager()
    set_manager(manager)
    app.state.plugins = manager

    for plugin in manager.list_installed():
        if plugin.auto_start:
            try:
                manager.start(plugin.id)
            except Exception as exc:
                logger.error(
                    "Failed to auto-start plugin %s: %s", plugin.id, exc
                )

    _health_task = asyncio.create_task(manager.health_loop())

    # Once-per-launch update check. Fire-and-forget so a slow GitHub
    # response doesn't delay the Hub coming up. Result lands in each
    # plugin's latest_available_version field, which the Plugins tab
    # reads off /api/plugins.
    async def _initial_check_updates() -> None:
        try:
            result = await manager.check_updates()
            avail = {pid: v for pid, v in result.items() if v}
            if avail:
                logger.info("Plugin updates available: %s", avail)
        except Exception as exc:
            logger.warning("Initial check_updates failed: %s", exc)

    asyncio.create_task(_initial_check_updates())

    # Video overseer bridge — polls cortex-vision for completed-but-
    # unpushed sessions and forwards each as a Pi note. Idempotent and
    # self-healing; safe to start unconditionally regardless of whether
    # cortex-vision is currently registered. See
    # services/video_overseer_bridge.py.
    bridge = VideoOverseerBridge(manager)
    app.state.video_bridge = bridge
    _bridge_task = bridge.start()


@app.on_event("shutdown")
async def _stop_plugins() -> None:
    global _health_task, _bridge_task

    bridge: VideoOverseerBridge | None = getattr(
        app.state, "video_bridge", None
    )
    if bridge is not None:
        bridge.stop()
        if _bridge_task is not None:
            _bridge_task.cancel()
            try:
                await _bridge_task
            except (asyncio.CancelledError, Exception):
                pass

    manager: PluginManager | None = getattr(app.state, "plugins", None)
    if manager is None:
        return

    manager.stop_health_loop()
    if _health_task is not None:
        _health_task.cancel()
        try:
            await _health_task
        except (asyncio.CancelledError, Exception):
            pass

    for plugin in manager.list_installed():
        try:
            manager.stop(plugin.id, graceful=True)
        except Exception as exc:
            logger.error("Failed to stop plugin %s: %s", plugin.id, exc)


@app.get("/api/debug/logs")
async def debug_logs(tail: int = Query(100, ge=1, le=500)):
    """Return recent log lines from the in-memory ring buffer."""
    lines = list(_log_buffer)
    return {"lines": lines[-tail:], "total": len(lines), "log_file": str(LOG_FILE)}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "lmstudio_url": settings.lmstudio_url,
        "pi_url": settings.pi_base_url,
        "training_dir": settings.training_dir,
    }


@app.get("/api/hub/status")
async def hub_status():
    """Hub availability endpoint — called by Pi before dream training.

    Returns whether this Hub is available to run training, what GPU is
    present, and any discovered LM Studio servers on the network.
    """
    import platform
    gpu_info = "unknown"
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            gpu_info = result.stdout.strip()
    except Exception:
        pass

    return {
        "available": True,
        "hostname": platform.node(),
        "gpu": gpu_info,
        "training_dir": settings.training_dir,
        "lmstudio_url": settings.lmstudio_url,
    }


# Serve pre-built frontend when running in desktop mode
# Set CORTEX_STATIC_DIR to the frontend dist/ directory
_static_dir = os.environ.get("CORTEX_STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
