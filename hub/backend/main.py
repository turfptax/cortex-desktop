"""Cortex Hub — FastAPI backend.

Unified web interface for:
  - Chat with local LM Studio model
  - Training pipeline management
  - Pi Zero interaction
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from routers import chat, training, pi, games, data
from routers import settings as settings_router

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
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])


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
