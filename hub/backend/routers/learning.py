"""Learning API router — teacher-student knowledge transfer."""

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from config import settings

router = APIRouter()

LEDGER_PATH = Path(settings.training_dir) / "raw_data" / "learning_ledger.json"


def _load_ledger() -> dict:
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "processed_note_ids": [],
        "processed_session_ids": [],
        "last_sync_at": None,
        "cycles": [],
        "total_examples_generated": 0,
    }


@router.get("/status")
async def learning_status():
    """Return learning status: unprocessed counts, cycle history."""
    ledger = _load_ledger()

    # Get counts from Pi to calculate unprocessed
    unprocessed_notes = 0
    unprocessed_sessions = 0
    try:
        from services.pi_client import send_command_parsed
        notes_result = await send_command_parsed("query", {"table": "notes", "limit": 1})
        # We can't easily get total count from query, so just report what we've processed
    except Exception:
        pass

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
        "cycles": cycles[-10:],  # Last 10 cycles
    }


class LearnRequest(BaseModel):
    full_pipeline: bool = False


@router.post("/start")
async def start_learn_cycle(req: LearnRequest):
    """Start a learn cycle via the process manager."""
    import time
    from services.process_manager import start_job, _jobs

    # Check if already running — clean up stale zombies (>5 min with no live process)
    for job_id, existing in list(_jobs.items()):
        if existing.step != "07":
            continue
        d = existing.to_dict()  # polls process if alive
        if d.get("status") == "running":
            proc_alive = existing.process and existing.process.poll() is None
            age = time.time() - existing.start_time if existing.start_time else 999
            if proc_alive and age < 600:
                return {"ok": False, "error": "Learn cycle already running", "job_id": d["job_id"]}
            # Stale zombie — clean it up
            existing.status = "failed"
            existing.end_time = time.time()

    job = await start_job("07")

    return {
        "ok": True,
        "job_id": job.job_id,
        "message": "Learn cycle started",
        "full_pipeline": req.full_pipeline,
    }


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
