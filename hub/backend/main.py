"""Cortex Hub — FastAPI backend.

Unified web interface for:
  - Chat with local LM Studio model
  - Training pipeline management
  - Pi Zero interaction
"""

import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from routers import chat, training, pi, games, data, learning
from routers import settings as settings_router

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

app = FastAPI(title="Cortex Hub", version="0.1.0")

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


# Serve pre-built frontend when running in desktop mode
# Set CORTEX_STATIC_DIR to the frontend dist/ directory
_static_dir = os.environ.get("CORTEX_STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
