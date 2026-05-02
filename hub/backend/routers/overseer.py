"""Overseer router — Hub-side bridge to the Pi's overseer plugin.

Slice 3d. Two responsibilities:

1. **Pass-through proxy** — most endpoints forward straight to
   /plugins/overseer/* on the Pi via pi_client.plugin_call. This keeps
   the frontend talking to a single origin (the Hub) without needing
   to know Pi creds or fall back when the Pi is offline.

2. **Local Claude Code session scanner** — the .jsonl files Claude Code
   writes live on the user's machine (this Hub), not the Pi. The Hub
   walks ~/.claude/projects/ to find them, then offers /api/overseer/
   import to upload + ingest them on the Pi via the existing
   /files/uploads + /plugins/overseer/imports/from-path two-step.

Claude Desktop conversations on Windows are stored in Electron's
IndexedDB at %APPDATA%/Claude/IndexedDB/, which requires a separate
LevelDB-aware reader. Not handled in this slice; documented as
follow-up. (Note: Claude Desktop's "Code mode" appears to write to
~/.claude/projects/ too, so single-source scanning still picks up much
of the Desktop conversation history.)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from services import pi_client
from services.pi_client import _headers as _pi_headers

router = APIRouter()
log = logging.getLogger("cortex.hub.overseer")


# ── Models ──────────────────────────────────────────────────────

class ImportRequest(BaseModel):
    paths: list[str]                        # absolute paths on this machine
    source: str = "claude-code"
    # If True (default), skip files whose hash already exists on Pi.
    # The Pi-side dedup will skip them anyway, but skipping uploads
    # avoids the multi-MB transfer.
    skip_already_imported: bool = True


class TickRequest(BaseModel):
    pass


class BackfillRequest(BaseModel):
    kind: str = "all"                       # all|sessions|notes|imports
    session_limit: int = 200
    note_limit: int = 500
    max_cost_usd: float = 1.0
    max_calls: int | None = None


class DeleteImportRequest(BaseModel):
    id: str
    remove_file: bool = True


# ── Pass-through proxies ────────────────────────────────────────

@router.get("/status")
async def status():
    """Proxy: GET /plugins/overseer/status"""
    return await pi_client.plugin_call("overseer", "GET", "/status")


@router.get("/working-memory")
async def working_memory(rebuild: int = 0):
    """Proxy: GET /plugins/overseer/working-memory"""
    payload = {"rebuild": rebuild} if rebuild else None
    return await pi_client.plugin_call(
        "overseer", "GET", "/working-memory", payload)


@router.get("/loop")
async def loop_status():
    """Proxy: GET /plugins/overseer/loop"""
    return await pi_client.plugin_call("overseer", "GET", "/loop")


@router.post("/tick-now")
async def tick_now(_req: TickRequest | None = None):
    """Proxy: POST /plugins/overseer/tick-now"""
    return await pi_client.plugin_call("overseer", "POST", "/tick-now", {})


@router.post("/backfill")
async def backfill(req: BackfillRequest):
    """Proxy: POST /plugins/overseer/backfill"""
    return await pi_client.plugin_call(
        "overseer", "POST", "/backfill",
        req.dict(exclude_none=True),
        timeout=600.0,
    )


@router.get("/imports")
async def list_imports(source: str = "", limit: int = 100, offset: int = 0):
    payload = {"limit": limit, "offset": offset}
    if source:
        payload["source"] = source
    return await pi_client.plugin_call(
        "overseer", "GET", "/imports", payload)


@router.post("/imports/delete")
async def delete_import(req: DeleteImportRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/imports/delete",
        req.dict(),
    )


@router.get("/llm/stats")
async def llm_stats(days: int = 7):
    return await pi_client.plugin_call(
        "overseer", "GET", "/llm/stats", {"days": days})


@router.get("/llm/calls")
async def llm_calls(limit: int = 20):
    return await pi_client.plugin_call(
        "overseer", "GET", "/llm/calls", {"limit": limit})


@router.get("/themes")
async def themes(limit: int = 20):
    return await pi_client.plugin_call(
        "overseer", "GET", "/themes", {"limit": limit})


@router.get("/episodes")
async def episodes(limit: int = 20):
    return await pi_client.plugin_call(
        "overseer", "GET", "/episodes", {"limit": limit})


@router.get("/questions")
async def questions(limit: int = 50):
    return await pi_client.plugin_call(
        "overseer", "GET", "/questions", {"limit": limit})


@router.get("/patterns")
async def patterns(limit: int = 50):
    return await pi_client.plugin_call(
        "overseer", "GET", "/patterns", {"limit": limit})


@router.get("/drift")
async def drift(limit: int = 50):
    return await pi_client.plugin_call(
        "overseer", "GET", "/drift", {"limit": limit})


@router.get("/future-notes")
async def future_notes():
    return await pi_client.plugin_call("overseer", "GET", "/future-notes")


# ── Slice 3e proxies ────────────────────────────────────────────

@router.get("/projects")
async def list_projects():
    return await pi_client.plugin_call("overseer", "GET", "/projects")


@router.post("/projects/classify")
async def classify_now():
    return await pi_client.plugin_call(
        "overseer", "POST", "/projects/classify", {})


class ProjectSettingRequest(BaseModel):
    project: str
    treat_as: str   # auto | human | automation | ignore


@router.post("/projects/setting")
async def set_project_setting(req: ProjectSettingRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/projects/setting", req.dict())


@router.get("/rollups")
async def list_rollups(project: str = "", limit: int = 100):
    payload = {"limit": limit}
    if project:
        payload["project"] = project
    return await pi_client.plugin_call(
        "overseer", "GET", "/rollups", payload)


class ChatRequest(BaseModel):
    message: str
    backend: str | None = None
    max_tokens: int = 800
    temperature: float = 0.7


@router.post("/chat")
async def chat(req: ChatRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat", req.dict(exclude_none=True),
        timeout=120.0,
    )


@router.get("/chat/history")
async def chat_history(limit: int = 50):
    return await pi_client.plugin_call(
        "overseer", "GET", "/chat/history", {"limit": limit})


@router.post("/chat/clear")
async def chat_clear():
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/clear", {})


@router.get("/notifications")
async def notifications(include_dismissed: int = 0, limit: int = 100):
    return await pi_client.plugin_call(
        "overseer", "GET", "/notifications",
        {"include_dismissed": include_dismissed, "limit": limit})


class DismissNotificationRequest(BaseModel):
    id: int | None = None
    all: bool = False


@router.post("/notifications/dismiss")
async def dismiss_notification(req: DismissNotificationRequest):
    body: dict = {}
    if req.id is not None:
        body["id"] = req.id
    if req.all:
        body["all"] = True
    return await pi_client.plugin_call(
        "overseer", "POST", "/notifications/dismiss", body)


@router.get("/budget")
async def budget():
    return await pi_client.plugin_call("overseer", "GET", "/budget")


# ── Slice 3f: dialectic ─────────────────────────────────────────

@router.get("/dialectic")
async def list_dialectic(status: str = "", severity: str = "",
                          artifact_type: str = "",
                          limit: int = 100, offset: int = 0):
    payload: dict = {"limit": limit, "offset": offset}
    if status:
        payload["status"] = status
    if severity:
        payload["severity"] = severity
    if artifact_type:
        payload["artifact_type"] = artifact_type
    return await pi_client.plugin_call(
        "overseer", "GET", "/dialectic", payload)


@router.get("/dialectic/get")
async def get_dialectic(id: int):
    return await pi_client.plugin_call(
        "overseer", "GET", "/dialectic/get", {"id": id})


class ResolveDialecticRequest(BaseModel):
    id: int
    resolution: str            # opus | gemma | third | productive
    resolution_text: str = ""


@router.post("/dialectic/resolve")
async def resolve_dialectic(req: ResolveDialecticRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/dialectic/resolve", req.dict())


@router.get("/dialectic/counts")
async def dialectic_counts():
    return await pi_client.plugin_call(
        "overseer", "GET", "/dialectic/counts")


# ── Local Claude Code .jsonl scanner ────────────────────────────

def _claude_projects_dir() -> Path:
    """~/.claude/projects/ on Windows/Linux/Mac — Claude Code's per-session
    .jsonl files live here, one folder per project."""
    return Path.home() / ".claude" / "projects"


@router.get("/scan/claude-code")
async def scan_claude_code(limit: int = 500):
    """Walk ~/.claude/projects/ for .jsonl session files.

    Returns metadata for each found file: path, session_id (filename
    stem = the UUID Claude Code assigns), project_folder (the encoded
    folder name; the actual cwd is inside the file and gets extracted
    on Pi during import), size_bytes, mtime.

    Sorted by mtime descending — newest first.
    """
    base = _claude_projects_dir()
    if not base.is_dir():
        return {
            "ok": True, "found": [], "total": 0,
            "scanned_dir": str(base),
            "note": ("~/.claude/projects/ does not exist on this machine "
                     "— Claude Code may not be installed."),
        }

    found: list[dict] = []
    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            try:
                stat = jsonl.stat()
            except OSError:
                continue
            found.append({
                "path": str(jsonl),
                "session_id": jsonl.stem,
                "project_folder": project_dir.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "mtime_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.gmtime(stat.st_mtime)) + "Z",
            })

    found.sort(key=lambda x: x["mtime"], reverse=True)
    return {
        "ok": True,
        "scanned_dir": str(base),
        "total": len(found),
        "found": found[:limit],
    }


# ── Upload + ingest pipeline ────────────────────────────────────

_UPLOAD_TIMEOUT_S = 600.0     # large .jsonls take a moment over WiFi


async def _already_imported_hashes(source: str) -> set[str]:
    """Pull the set of file_hashes already in imported_sessions on Pi
    so we can skip re-uploading huge files we've seen."""
    out: set[str] = set()
    offset = 0
    while True:
        page = await pi_client.plugin_call(
            "overseer", "GET", "/imports",
            {"source": source, "limit": 500, "offset": offset},
        )
        if not page.get("ok"):
            break
        rows = page.get("imports") or []
        for r in rows:
            h = r.get("file_hash") or ""
            if h:
                out.add(h)
        if len(rows) < 500:
            break
        offset += 500
    return out


def _file_sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _upload_to_pi(path: Path) -> dict:
    """POST /files/uploads on the Pi with the file body + X-Filename.

    Returns the JSON response: {ok, filename, size, path, file_id}.
    """
    url = f"{settings.pi_base_url}/files/uploads"
    headers = _pi_headers()
    headers["Content-Type"] = "application/octet-stream"
    headers["X-Filename"] = path.name
    headers["X-Description"] = "Claude Code session import"
    headers["X-Tags"] = "claude-code,overseer-import"

    # Stream the file body
    with open(path, "rb") as f:
        body = f.read()  # 30MB-class is fine in memory; could stream later
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_S) as client:
        resp = await client.post(url, content=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def _import_one_path(path_str: str, source: str,
                           known_hashes: set[str]) -> dict:
    """Upload + trigger Pi-side import for one file. Returns a status dict."""
    path = Path(path_str)
    if not path.is_file():
        return {"ok": False, "src": path_str,
                "error": "file not found on Hub"}
    try:
        digest = _file_sha256(path)
    except Exception as e:
        return {"ok": False, "src": path_str,
                "error": "hash failed: {}".format(e)}

    if digest in known_hashes:
        return {"ok": True, "src": path_str, "file_hash": digest,
                "skipped": "already imported (hash match)"}

    # Step 1: upload to Pi
    try:
        upload = await _upload_to_pi(path)
    except Exception as e:
        return {"ok": False, "src": path_str,
                "error": "upload failed: {}".format(e)[:300]}
    pi_path = upload.get("path")
    if not pi_path:
        return {"ok": False, "src": path_str,
                "error": "upload returned no path: {}".format(upload)}

    # Step 2: trigger Pi-side ingest from that path
    ingest = await pi_client.plugin_call(
        "overseer", "POST", "/imports/from-path",
        {"path": pi_path, "source": source},
        timeout=300.0,
    )
    return {
        "ok": ingest.get("ok", False),
        "src": path_str,
        "uploaded_to": pi_path,
        "uploaded_size": upload.get("size"),
        "imported_id": ingest.get("imported_id"),
        "skipped": ingest.get("skipped"),
        "error": ingest.get("error"),
        "session_id": ingest.get("session_id"),
        "duration_minutes": ingest.get("duration_minutes"),
        "message_count": ingest.get("message_count"),
    }


@router.post("/import")
async def import_paths(req: ImportRequest):
    """Upload + ingest a list of local .jsonl files into the Pi's overseer.

    For each path: hash locally, skip if Pi already has that hash (when
    skip_already_imported=true), otherwise upload via /files/uploads and
    trigger /plugins/overseer/imports/from-path. Runs sequentially to
    avoid hammering the Pi WiFi link.

    Body: {paths: [...], source: "claude-code", skip_already_imported: true}
    Returns: {imported: [...], skipped: [...], failed: [...], counts: {...}}
    """
    if not req.paths:
        return {"ok": True,
                "imported": [], "skipped": [], "failed": [],
                "counts": {"requested": 0}}

    known_hashes: set[str] = set()
    if req.skip_already_imported:
        known_hashes = await _already_imported_hashes(req.source)

    imported: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for path_str in req.paths:
        result = await _import_one_path(path_str, req.source, known_hashes)
        if not result.get("ok"):
            failed.append(result)
        elif result.get("skipped"):
            skipped.append(result)
        else:
            imported.append(result)
            # Track this hash so subsequent paths in this same batch
            # don't double-upload (rare but cheap to guard).
            digest = _file_sha256(Path(path_str)) \
                if Path(path_str).is_file() else None
            if digest:
                known_hashes.add(digest)

    return {
        "ok": True,
        "source": req.source,
        "counts": {
            "requested": len(req.paths),
            "imported": len(imported),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
    }
