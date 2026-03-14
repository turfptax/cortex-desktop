"""Learning API router — teacher-student knowledge transfer.

Runs the learn cycle in-process with multi-server parallel support.
"""

import logging
import threading

from fastapi import APIRouter
from pydantic import BaseModel

from services.learn_cycle import (
    LEDGER_PATH,
    is_running,
    last_error,
    get_progress,
    run_learn_cycle,
    load_servers,
    save_servers,
    scan_lmstudio,
    discover_lmstudio_servers,
    _load_ledger,
)

router = APIRouter()
logger = logging.getLogger("cortex.learn")

# Store the latest cycle result so the UI can poll for it
_last_result: dict | None = None


@router.get("/status")
async def learning_status():
    """Return learning status: processed counts, cycle history, progress."""
    ledger = _load_ledger()

    processed_notes = len(ledger.get("processed_note_ids", []))
    processed_sessions = len(ledger.get("processed_session_ids", []))
    cycles = ledger.get("cycles", [])
    last_cycle = cycles[-1] if cycles else None

    return {
        "ok": True,
        "processed_notes": processed_notes,
        "processed_sessions": processed_sessions,
        "total_examples": ledger.get("total_examples_generated", 0),
        "total_cycles": len(cycles),
        "last_sync_at": ledger.get("last_sync_at"),
        "last_cycle": last_cycle,
        "cycles": cycles[-10:],
        "is_running": is_running(),
        "last_error": last_error(),
        "progress": get_progress() if is_running() else None,
    }


class LearnRequest(BaseModel):
    full_pipeline: bool = False
    servers: list[dict] | None = None  # Optional server override


@router.post("/start")
async def start_learn_cycle(req: LearnRequest):
    """Start a learn cycle in a background thread."""
    global _last_result

    if is_running():
        return {"ok": False, "error": "Learn cycle already running"}

    _last_result = None

    def _run():
        global _last_result
        _last_result = run_learn_cycle(server_overrides=req.servers)

    thread = threading.Thread(target=_run, daemon=True, name="learn-cycle")
    thread.start()

    return {"ok": True, "message": "Learn cycle started"}


@router.get("/progress")
async def learn_progress():
    """Get real-time progress of the running learn cycle."""
    return {
        "ok": True,
        "running": is_running(),
        "progress": get_progress(),
        "last_error": last_error(),
    }


@router.get("/result")
async def learn_result():
    """Poll for the result of the last learn cycle."""
    return {
        "ok": True,
        "running": is_running(),
        "result": _last_result,
    }


# ── LM Studio server management ─────────────────────────────────

@router.get("/servers")
async def get_servers():
    """Get configured LM Studio servers with online status."""
    servers = load_servers()
    results = []
    for s in servers:
        info = scan_lmstudio(s["url"])
        results.append({
            **s,
            "online": info is not None if info else False,
            "models": info["models"] if info else [],
        })
    return {"ok": True, "servers": results}


class ServerConfig(BaseModel):
    servers: list[dict]


@router.post("/servers")
async def set_servers(config: ServerConfig):
    """Save LM Studio server configuration."""
    save_servers(config.servers)
    return {"ok": True, "message": f"Saved {len(config.servers)} server(s)"}


@router.post("/servers/scan")
async def scan_servers():
    """Scan the local network for LM Studio instances."""
    logger.info("Scanning network for LM Studio instances...")
    found = discover_lmstudio_servers()
    logger.info("Found %d LM Studio instance(s)", len(found))
    return {"ok": True, "found": found}


@router.post("/servers/check")
async def check_server(data: dict):
    """Check a single LM Studio URL."""
    url = data.get("url", "")
    if not url:
        return {"ok": False, "error": "URL required"}
    info = scan_lmstudio(url)
    if info:
        return {"ok": True, **info}
    return {"ok": False, "error": f"Cannot reach {url}"}


@router.get("/knowledge")
async def get_knowledge():
    """Return accumulated knowledge summaries from all cycles."""
    ledger = _load_ledger()
    cycles = ledger.get("cycles", [])

    summaries = []
    for cycle in cycles:
        summary = cycle.get("knowledge_summary", "")
        if summary:
            summaries.append({
                "cycle_id": cycle.get("cycle_id"),
                "date": cycle.get("started_at", ""),
                "summary": summary,
                "examples_generated": cycle.get("examples_generated", 0),
            })

    return {
        "ok": True,
        "summaries": summaries,
        "total_cycles": len(cycles),
        "total_examples": ledger.get("total_examples_generated", 0),
    }


@router.post("/reset")
async def reset_ledger():
    """Reset the learning ledger (start fresh)."""
    if LEDGER_PATH.exists():
        LEDGER_PATH.unlink()
    return {"ok": True, "message": "Learning ledger reset"}
