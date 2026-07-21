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
import hashlib
import logging
import mimetypes
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
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
        req.model_dump(),
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
        "overseer", "POST", "/projects/setting", req.model_dump())


@router.get("/rollups")
async def list_rollups(project: str = "", limit: int = 100):
    payload = {"limit": limit}
    if project:
        payload["project"] = project
    return await pi_client.plugin_call(
        "overseer", "GET", "/rollups", payload)


class ChatAttachmentRef(BaseModel):
    """Slice 8: one attachment ref echoed back from /chat/upload and
    submitted with /chat. The Pi reads bytes from pi_path itself; this
    struct just carries the metadata + filesystem pointer."""
    filename: str
    mime_type: str = ""
    size: int = 0
    pi_path: str
    file_id: int | None = None
    sha256: str = ""
    kind: str = "other"           # image | text | pdf | other


class ChatRequest(BaseModel):
    message: str = ""
    backend: str | None = None
    # dev.19: max_tokens is Optional and defaults to None. When the
    # frontend omits it, req.dict(exclude_none=True) skips the field
    # and the Pi-side handler uses ITS default (now 64000 — effectively
    # the model's output max for Opus 4.7). The Hub no longer dictates
    # a cap on overseer chat replies. Callers may still pass an
    # explicit value to clamp shorter (e.g. for cheap heartbeat pings).
    max_tokens: int | None = None
    temperature: float = 0.7
    # Slice 8: file attachments. Each entry must have come back from
    # POST /chat/upload (the Pi has the bytes on disk under uploads/).
    attachments: list[ChatAttachmentRef] | None = None
    # Slice 14: voice mode — when true the Pi appends a succinctness
    # directive to the system block (the reply will be spoken aloud).
    voice_mode: bool = False
    # Agent harness: pin the turn to the thread the UI is rendering.
    # None/0 = whatever thread is active on the Pi.
    thread_id: int | None = None


@router.post("/chat")
async def chat(req: ChatRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat", req.dict(exclude_none=True),
        timeout=180.0,    # bumped from 120s — vision turns add latency
    )


class QuickChatRequest(BaseModel):
    """Slice 14.7: router-tier chat. Cheaper, faster path that
    handles routine turns and escalates to /chat when needed."""
    message: str
    direct_override: bool = False
    # Agent harness: same thread pinning as /chat.
    thread_id: int | None = None


@router.post("/quick-chat")
async def quick_chat(req: QuickChatRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/quick-chat", req.dict(exclude_none=True),
        timeout=180.0,  # router calls are fast; escalations may take longer
    )


# ── Slice 8: chat file attachments ──────────────────────────────
#
# Two-step upload contract:
#   1. Frontend POSTs multipart files to /api/overseer/chat/upload
#      (this endpoint). Hub validates type+size, hashes, forwards each
#      to the Pi's /files/uploads (raw body, 100MB cap) tagged
#      'chat-attachment,overseer'. Returns a list of ChatAttachmentRefs.
#   2. Frontend POSTs /api/overseer/chat with `attachments` populated
#      from step 1. Pi reads bytes off disk, inlines text/pdf, builds
#      multimodal content blocks for images.
#
# We can't inline the bytes in the chat JSON body — the Pi's plugin
# route layer caps body at 1MB, and we want 5MB-per-file images. The
# /files/uploads endpoint is the existing well-trodden path (same one
# Claude Code session imports use) and it already streams to disk.

_CHAT_UPLOAD_MAX_BYTES = 5 * 1024 * 1024     # 5MB per file
_CHAT_UPLOAD_MAX_FILES = 10
_CHAT_UPLOAD_TIMEOUT_S = 60.0

# Accepted extensions. Mirrored on the Pi (chat.py classify_attachment_kind).
# Anything outside this set is rejected here to keep junk off disk.
_CHAT_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".csv", ".log", ".html",
    ".css", ".sh", ".sql", ".toml", ".ini", ".env",
}
_CHAT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_CHAT_PDF_EXTS = {".pdf"}
_CHAT_ALLOWED_EXTS = (
    _CHAT_TEXT_EXTS | _CHAT_IMAGE_EXTS | _CHAT_PDF_EXTS
)


def _classify_kind(filename: str, mime_type: str) -> str:
    ext = os.path.splitext(filename.lower())[1]
    mt = (mime_type or "").lower()
    if ext in _CHAT_IMAGE_EXTS or mt.startswith("image/"):
        return "image"
    if ext in _CHAT_PDF_EXTS or mt == "application/pdf":
        return "pdf"
    if ext in _CHAT_TEXT_EXTS or mt.startswith("text/"):
        return "text"
    return "other"


async def _upload_chat_attachment_to_pi(
        *, body: bytes, filename: str, mime_type: str,
        sha256: str) -> dict:
    """POST /files/uploads on the Pi with the file body. Same shape as
    the Claude Code import flow but with chat-specific tags so the
    overseer tab's Imports panel doesn't pick these up."""
    url = "{}/files/uploads".format(settings.pi_base_url)
    headers = _pi_headers()
    headers["Content-Type"] = "application/octet-stream"
    headers["X-Filename"] = filename
    headers["X-Description"] = "Overseer chat attachment"
    headers["X-Tags"] = "chat-attachment,overseer"
    async with httpx.AsyncClient(timeout=_CHAT_UPLOAD_TIMEOUT_S) as client:
        resp = await client.post(url, content=body, headers=headers)
        resp.raise_for_status()
        out = resp.json()
    return {
        "filename": out.get("filename") or filename,
        "size": out.get("size") or len(body),
        "pi_path": out.get("path") or "",
        "file_id": out.get("file_id"),
        "sha256": sha256,
        "mime_type": mime_type,
        "kind": _classify_kind(out.get("filename") or filename, mime_type),
    }


@router.post("/chat/upload")
async def chat_upload(files: list[UploadFile] = File(...)):
    """Validate + forward chat attachments to the Pi.

    Multipart form field name: `files`. One or more files, max 10,
    max 5MB each, restricted to image/text/pdf extensions. Returns
    `{ok, attachments: [ChatAttachmentRef, ...], rejected: [...]}` —
    rejected entries have `error` populated so the UI can surface why.
    """
    if not files:
        raise HTTPException(400, "no files in upload")
    if len(files) > _CHAT_UPLOAD_MAX_FILES:
        raise HTTPException(
            400, "too many files (max {})".format(_CHAT_UPLOAD_MAX_FILES))

    accepted: list[dict] = []
    rejected: list[dict] = []

    for upload in files:
        original = upload.filename or "unnamed"
        ext = os.path.splitext(original.lower())[1]
        # Read body once; we both hash and forward it.
        body = await upload.read()
        size = len(body)
        if ext not in _CHAT_ALLOWED_EXTS:
            rejected.append({
                "filename": original, "size": size,
                "error": "unsupported file type ({})".format(ext or "no ext"),
            })
            continue
        if size == 0:
            rejected.append({
                "filename": original, "size": 0,
                "error": "empty file",
            })
            continue
        if size > _CHAT_UPLOAD_MAX_BYTES:
            rejected.append({
                "filename": original, "size": size,
                "error": "too large (limit {}MB, got {} bytes)".format(
                    _CHAT_UPLOAD_MAX_BYTES // (1024 * 1024), size),
            })
            continue

        mime = (upload.content_type
                or mimetypes.guess_type(original)[0]
                or "application/octet-stream")
        sha = hashlib.sha256(body).hexdigest()
        try:
            ref = await _upload_chat_attachment_to_pi(
                body=body, filename=original,
                mime_type=mime, sha256=sha,
            )
            accepted.append(ref)
        except httpx.HTTPError as e:
            rejected.append({
                "filename": original, "size": size,
                "error": "Pi upload failed: {}".format(str(e)[:200]),
            })
        except Exception as e:
            log.exception("chat upload failed")
            rejected.append({
                "filename": original, "size": size,
                "error": "unexpected error: {}".format(str(e)[:200]),
            })

    return {
        "ok": True,
        "attachments": accepted,
        "rejected": rejected,
        "counts": {
            "uploaded": len(accepted),
            "rejected": len(rejected),
        },
    }


@router.get("/chat/history")
async def chat_history(limit: int = 50, thread_id: int = 0):
    """thread_id=0 means the active thread (Pi resolves it)."""
    params = {"limit": limit}
    if thread_id:
        params["thread_id"] = thread_id
    return await pi_client.plugin_call(
        "overseer", "GET", "/chat/history", params)


@router.post("/chat/clear")
async def chat_clear(req: Request):
    """Body may carry {thread_id} so the wipe targets the thread the
    UI was rendering, not whatever is active when the request lands."""
    body = await _json_or_empty(req)
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/clear", body)


# ── Agent harness (2026-07-10): chat threads + prompt library ──────
# Sends carry the thread_id the UI is rendering (pinned server-side);
# the frontend still switches the active pointer via
# /chat/threads/select so other surfaces follow along.


async def _json_or_empty(req: Request) -> dict:
    """Malformed/missing JSON becomes {} so the Pi handler returns a
    clean ok:false instead of this proxy raising a 500."""
    try:
        body = await req.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.get("/chat/threads")
async def chat_threads():
    return await pi_client.plugin_call(
        "overseer", "GET", "/chat/threads", {})


@router.post("/chat/threads/new")
async def chat_thread_new(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/threads/new", await _json_or_empty(req))


@router.post("/chat/threads/select")
async def chat_thread_select(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/threads/select", await _json_or_empty(req))


@router.post("/chat/threads/rename")
async def chat_thread_rename(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/threads/rename", await _json_or_empty(req))


@router.post("/chat/threads/delete")
async def chat_thread_delete(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/threads/delete", await _json_or_empty(req))


@router.get("/chat/prompts")
async def chat_prompts():
    return await pi_client.plugin_call(
        "overseer", "GET", "/chat/prompts", {})


@router.post("/chat/prompts/upsert")
async def chat_prompt_upsert(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/prompts/upsert", await _json_or_empty(req))


@router.post("/chat/prompts/delete")
async def chat_prompt_delete(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/prompts/delete", await _json_or_empty(req))


# ── Agent harness (2026-07-11): harness map + feedback + MCP ────────


@router.get("/harness-map")
async def harness_map():
    return await pi_client.plugin_call(
        "overseer", "GET", "/harness-map", {})


@router.get("/day")
async def day_detail(date: str):
    """Everything the corpus holds about one local day (any year)."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/day", {"date": date})


@router.get("/day/heat")
async def day_heat(year: int):
    """Per-day corpus aggregates for a year (sessions/logged/sleep)."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/day/heat", {"year": year})


@router.get("/feedback")
async def feedback_list(limit: int = 50, target_kind: str = ""):
    params: dict = {"limit": limit}
    if target_kind:
        params["target_kind"] = target_kind
    return await pi_client.plugin_call(
        "overseer", "GET", "/feedback", params)


@router.post("/feedback")
async def feedback_add(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/feedback", await _json_or_empty(req))


@router.post("/feedback/discuss")
async def feedback_discuss(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/feedback/discuss", await _json_or_empty(req))


@router.get("/feedback/summary")
async def feedback_summary():
    return await pi_client.plugin_call(
        "overseer", "GET", "/feedback/summary", {})


@router.get("/simples/snapshot")
async def simples_snapshot():
    """Read-only mirror of the phone's Simples plan (display state)."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/simples/snapshot", {})


@router.get("/mcp/connectors")
async def mcp_connectors():
    return await pi_client.plugin_call(
        "overseer", "GET", "/mcp/connectors", {})


@router.post("/mcp/connectors/upsert")
async def mcp_connector_upsert(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/mcp/connectors/upsert",
        await _json_or_empty(req))


@router.post("/mcp/connectors/delete")
async def mcp_connector_delete(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/mcp/connectors/delete",
        await _json_or_empty(req))


@router.post("/mcp/connectors/test")
async def mcp_connector_test(req: Request):
    return await pi_client.plugin_call(
        "overseer", "POST", "/mcp/connectors/test",
        await _json_or_empty(req), timeout=30.0)


@router.post("/chat/compress")
async def chat_compress(req: Request):
    """Slice 9.5 CP3: proxy for the Pi's chat compression endpoint.
    Body: {"keep_recent"?: int} — default 12 on the Pi side."""
    try:
        body = await req.json()
    except Exception:
        body = {}
    return await pi_client.plugin_call(
        "overseer", "POST", "/chat/compress", body or {})


@router.get("/notifications")
async def notifications(
    include_dismissed: int = 0,
    include_archived: int = 0,
    include_snoozed: int = 0,
    limit: int = 100,
):
    return await pi_client.plugin_call(
        "overseer", "GET", "/notifications", {
            "include_dismissed": include_dismissed,
            "include_archived": include_archived,
            "include_snoozed": include_snoozed,
            "limit": limit,
        })


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


class NotificationActionRequest(BaseModel):
    id: int
    action: str               # archive | snooze | touch
    snooze_days: int | None = None


@router.post("/notifications/action")
async def notification_action(req: NotificationActionRequest):
    """3i CP1: Archive / Snooze / Touch a notification."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/notifications/action",
        req.dict(exclude_none=True))


@router.post("/notifications/respond")
async def notification_respond(req: Request):
    """Slice 9.6 CP1: log Tory's response to a custom action button
    on a notification (free_text / yes_no / dispatch_sibling / etc).
    Body: {notification_id, action_kind, action_label?, response_payload?, also_archive?}"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    return await pi_client.plugin_call(
        "overseer", "POST", "/notifications/respond", body or {})


@router.get("/budget")
async def budget():
    return await pi_client.plugin_call("overseer", "GET", "/budget")


# ── Squeeze (IA overhaul 2026-07-10): graded-dispatch report card ──
# The Pi route already exists (it feeds the Lemon export connector);
# this proxy lets the Squeeze tab render the same rows directly.

@router.get("/dispatch-export")
async def dispatch_export(since: int = 0, limit: int = 1000):
    return await pi_client.plugin_call(
        "overseer", "GET", "/dispatch-export",
        {"since": since, "limit": limit})



# ── Slice 3f.5: overseer journal (the thinking layer) ──────────

@router.get("/journal")
async def journal(limit: int = 30):
    return await pi_client.plugin_call(
        "overseer", "GET", "/journal", {"limit": limit})


# ── Slice 3f.5 #2: question-centered ────────────────────────────

@router.get("/questions/get")
async def question_detail(id: int, recent_n: int = 20):
    return await pi_client.plugin_call(
        "overseer", "GET", "/questions/get",
        {"id": id, "recent_n": recent_n})


class QuestionLifecycleRequest(BaseModel):
    id: int
    lifecycle: str   # dormant | active | partially_answered | resolved | abandoned


@router.post("/questions/lifecycle")
async def question_lifecycle(req: QuestionLifecycleRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/questions/lifecycle", req.model_dump())


class QuestionUpsertRequest(BaseModel):
    id: int | None = None
    question: str = ""
    body: str = ""
    confidence: str = "med"
    tags: list[str] = []


@router.post("/questions/upsert")
async def question_upsert(req: QuestionUpsertRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/questions/upsert",
        req.dict(exclude_none=True))


class RouteExistingRequest(BaseModel):
    limit: int = 100
    max_cost_usd: float = 0.50


@router.post("/questions/route-existing")
async def route_existing(req: RouteExistingRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/questions/route-existing", req.model_dump(),
        timeout=600.0,
    )


# ── Slice 3f.5 #4: known blindspots ─────────────────────────────

@router.get("/blindspots")
async def list_blindspots(active_only: int = 1, model: str = "",
                           topic: str = ""):
    payload: dict = {"active_only": active_only}
    if model:
        payload["model"] = model
    if topic:
        payload["topic"] = topic
    return await pi_client.plugin_call(
        "overseer", "GET", "/blindspots", payload)


class BlindspotUpsertRequest(BaseModel):
    id: int | None = None
    model_pattern: str
    topic_pattern: str = ""
    direction: str = "general"
    confidence_adjustment: int = 0
    body: str
    rationale: str = ""
    confidence: str = "med"
    source: str = "user"
    is_active: bool = True


@router.post("/blindspots/upsert")
async def upsert_blindspot(req: BlindspotUpsertRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/blindspots/upsert",
        req.dict(exclude_none=True))


class BlindspotActiveRequest(BaseModel):
    id: int
    is_active: bool = True


@router.post("/blindspots/active")
async def set_blindspot_active(req: BlindspotActiveRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/blindspots/active", req.model_dump())


class CorrectionRequest(BaseModel):
    what_was_wrong: str
    user_correction: str = ""
    model: str = ""
    artifact_table: str = ""
    artifact_id: int | None = None
    topic: str = ""
    severity: str = "med"
    source: str = "manual"


@router.post("/corrections")
async def log_correction(req: CorrectionRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/corrections",
        req.dict(exclude_none=True))


@router.get("/corrections")
async def list_corrections(limit: int = 100, undistilled_only: int = 0):
    return await pi_client.plugin_call(
        "overseer", "GET", "/corrections",
        {"limit": limit, "undistilled_only": undistilled_only})


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
    on Pi during import), size_bytes, mtime, AND already_imported (a
    server-computed flag based on hash equality with rows in the Pi's
    imported_sessions table — this is authoritative regardless of how
    many rows the Hub Imports panel currently has loaded).

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

    # Pull the FULL set of already-imported hashes on the Pi so we can
    # mark each scanned file authoritatively. Without this the UI was
    # only matching against the first 200 imported rows it had loaded;
    # users with 200+ imports saw real duplicates marked as "new" and
    # then got "skipped" on import. Polish slice CP1.
    try:
        known_hashes = await _already_imported_hashes("claude-code")
    except Exception:
        known_hashes = set()  # degrade gracefully — UI still functional

    found: list[dict] = []
    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            try:
                stat = jsonl.stat()
            except OSError:
                continue
            digest = ""
            try:
                digest = _file_sha256(jsonl)
            except Exception:
                pass  # keep the row visible even if we can't hash it
            found.append({
                "path": str(jsonl),
                "session_id": jsonl.stem,
                "project_folder": project_dir.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "mtime_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.gmtime(stat.st_mtime)) + "Z",
                "file_hash": digest,
                "already_imported": bool(digest and digest in known_hashes),
            })

    found.sort(key=lambda x: x["mtime"], reverse=True)
    already = sum(1 for f in found if f.get("already_imported"))
    return {
        "ok": True,
        "scanned_dir": str(base),
        "total": len(found),
        "already_imported_count": already,
        "new_count": len(found) - already,
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


# ── Slice 3g checkpoint 2: drill-down ──────────────────────────


@router.get("/intro")
async def overseer_intro(format: str = "markdown"):
    """Curated 'who is Tory + what does he care about' brief for
    new AI sessions. Replaces the working_memory dump as the
    boot-context surface."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/intro", {"format": format},
        timeout=15.0,
    )


@router.get("/vector/status")
async def vector_status():
    """Vector index coverage: model, dim, embedded/total gists."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/vector/status", timeout=10.0)


@router.get("/vector/search")
async def vector_search(q: str, k: int = 10):
    """Semantic (meaning) search over the gist corpus. Local
    embeddings on the Pi; nothing leaves the host."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/vector/search", {"q": q, "k": k},
        timeout=30.0)


@router.get("/detail")
async def overseer_detail(token: str):
    """Resolve a working-memory token (e.g. 'q:42', 'p:5') to its full
    row + tags + type-specific context + suggested next-step tokens."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/detail", {"token": token})


# ── Phase 1 (2026-05-27): corpus search + pull events ──────────


@router.get("/search")
async def overseer_search(
    q: str,
    kinds: str = "",
    limit_per_kind: int = 5,
    limit_total: int = 40,
    days: int = 0,
    caller_id: str = "",
):
    """Substring search across the interpretive corpus. Mirror of the
    cortex_search MCP tool so the Hub can drive the same surface."""
    payload = {
        "q": q,
        "limit_per_kind": limit_per_kind,
        "limit_total": limit_total,
    }
    if kinds:
        payload["kinds"] = kinds
    if days:
        payload["days"] = days
    if caller_id:
        payload["caller_id"] = caller_id
    return await pi_client.plugin_call(
        "overseer", "GET", "/search", payload, timeout=20.0,
    )


@router.get("/pull-events")
async def overseer_pull_events(
    limit: int = 50,
    surface: str = "",
    artifact_table: str = "",
    days: int = 0,
):
    """Recent corpus-drill events. Surfaces what external AIs are
    bouncing off of."""
    payload = {"limit": limit}
    if surface:
        payload["surface"] = surface
    if artifact_table:
        payload["artifact_table"] = artifact_table
    if days:
        payload["days"] = days
    return await pi_client.plugin_call(
        "overseer", "GET", "/pull-events", payload, timeout=10.0,
    )


@router.get("/pull-events/stats")
async def overseer_pull_event_stats(days: int = 7):
    """Aggregate pull-event stats for a time window."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/pull-events/stats", {"days": days},
        timeout=10.0,
    )


# ── Sub-agent tier management (2026-05-27) ──────────────────────


@router.get("/sub-agents")
async def overseer_sub_agents():
    """List B/C sub-agents with their current tier, default, and
    per-agent invocation history."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/sub-agents", None, timeout=10.0,
    )


class SubAgentTierUpdate(BaseModel):
    agent_type: str
    agent_name: str
    tier: str
    notes: str = ""


@router.post("/sub-agents/set-tier")
async def overseer_set_sub_agent_tier(req: SubAgentTierUpdate):
    """Change a sub-agent's tier (flash | sonnet | opus). Persists."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/sub-agents/set-tier", req.model_dump(),
        timeout=10.0,
    )


@router.get("/sub-agents/performance")
async def overseer_sub_agent_performance(
    agent_type: str,
    agent_name: str,
    last_n: int = 10,
):
    """Quality-rating signal for one sub-agent over the last N
    completed dispatches."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/sub-agents/performance",
        {"agent_type": agent_type, "agent_name": agent_name,
         "last_n": last_n},
        timeout=10.0,
    )


# ── Slice 3h: insight generation queue ─────────────────────────


class InsightScanRequest(BaseModel):
    project: str
    days: int = 7


@router.post("/insight/scan-now")
async def insight_scan_now(req: InsightScanRequest):
    """Trigger a Sonnet scan of one project's recent gist arc.
    Proposes theme/pattern/drift candidates to the pending queue."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/insight/scan-now", req.model_dump(),
        timeout=60.0,
    )


@router.get("/insight/pending")
async def insight_pending(
    status: str | None = None,
    kind: str | None = None,
    project: str | None = None,
    limit: int = 200,
):
    payload: dict = {"limit": limit}
    if status:
        payload["status"] = status
    if kind:
        payload["kind"] = kind
    if project:
        payload["project"] = project
    return await pi_client.plugin_call(
        "overseer", "GET", "/insight/pending", payload)


class InsightDecideRequest(BaseModel):
    id: int
    decision: str               # confirm | reject | edit-and-confirm
    edit_title: str | None = None
    edit_body: str | None = None
    review_note: str | None = None
    reviewed_by: str = "user"


@router.post("/insight/decide")
async def insight_decide(req: InsightDecideRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/insight/decide", req.dict(exclude_none=True))


@router.get("/insight/scans")
async def insight_scans(project: str | None = None, limit: int = 20):
    payload: dict = {"limit": limit}
    if project is not None:
        payload["project"] = project
    return await pi_client.plugin_call(
        "overseer", "GET", "/insight/scans", payload)


@router.post("/insight/distill-corrections")
async def insight_distill_corrections():
    """3i CP2: trigger a Sonnet pass over uncondidated corrections,
    proposing blindspot candidates into pending_interpretations."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/insight/distill-corrections", {},
        timeout=60.0,
    )


# ── Polish slice: Data Explorer graph ───────────────────────────


@router.get("/explorer/graph")
async def explorer_graph():
    """Return the Explorer graph data: nodes (questions, projects,
    patterns, drift, themes, filed gists, episodes) + edges
    (evidence, derived_from, in_project)."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/explorer/graph", {})


# ── Slice 4 CP1a/CP1b: project rollups + narrative ──────────────


@router.get("/projects/summary")
async def projects_summary(
    order_by: str = "last_active_at",
    descending: int = 1,
):
    """Proxy: GET /plugins/overseer/projects/summary

    Returns the full list of project_summaries rows (stats + top
    files + models + narrative). order_by whitelisted server-side
    to: last_active_at | session_count | cost_usd_estimate |
    total_minutes | total_messages | first_active_at |
    stats_updated_at | project. Defaults: last_active_at desc.
    """
    return await pi_client.plugin_call(
        "overseer", "GET", "/projects/summary",
        {"order_by": order_by, "descending": int(descending)},
    )


@router.get("/projects/summary/get")
async def project_summary_get(project: str):
    """Proxy: GET /plugins/overseer/projects/summary/get?project=<name>"""
    return await pi_client.plugin_call(
        "overseer", "GET", "/projects/summary/get",
        {"project": project},
    )


class ProjectRefreshRequest(BaseModel):
    project: str


@router.post("/projects/summary/refresh")
async def project_summary_refresh(req: ProjectRefreshRequest):
    """Proxy: POST /plugins/overseer/projects/summary/refresh —
    recompute one project's deterministic stats from
    imported_sessions + each row's metadata_json."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/projects/summary/refresh",
        req.model_dump(),
    )


@router.post("/projects/summary/refresh-all")
async def project_summary_refresh_all():
    """Proxy: POST /plugins/overseer/projects/summary/refresh-all
    — recompute every project. Cheap (no LLM), but scales with
    imported_sessions row count."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/projects/summary/refresh-all",
        {}, timeout=120.0,
    )


class ProjectNarrativeRequest(BaseModel):
    project: str
    force: bool | None = True
    max_cost_usd: float | None = None


@router.post("/narrative/generate")
async def narrative_generate(req: ProjectNarrativeRequest):
    """Proxy: POST /plugins/overseer/narrative/generate — manual
    Sonnet/Opus narrative regen for one project. Bypasses the loop's
    24h + ≥3-sessions gate by default."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/narrative/generate",
        req.dict(exclude_none=True),
        timeout=120.0,
    )


# ── Slice 5: temporal cadence + human journal ───────────────────


@router.get("/temporal")
async def list_temporal(kind: str | None = None, limit: int = 50):
    """Proxy: GET /plugins/overseer/temporal — list daily/weekly/
    monthly narratives, newest first."""
    payload: dict = {"limit": limit}
    if kind:
        payload["kind"] = kind
    return await pi_client.plugin_call(
        "overseer", "GET", "/temporal", payload)


@router.get("/temporal/get")
async def get_temporal(kind: str, period_label: str):
    """Proxy: GET /plugins/overseer/temporal/get?kind=&period_label="""
    return await pi_client.plugin_call(
        "overseer", "GET", "/temporal/get",
        {"kind": kind, "period_label": period_label})


class TemporalGenerateRequest(BaseModel):
    kind: str   # daily | weekly | monthly
    period_label: str | None = None
    force: bool | None = False


@router.post("/temporal/generate")
async def temporal_generate(req: TemporalGenerateRequest):
    """Proxy: POST /plugins/overseer/temporal/generate — manual
    Sonnet narrative for one temporal period. Bypasses the loop's
    22:00-local trigger; honors UNIQUE(kind, period_label) unless
    force=True."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/temporal/generate",
        req.dict(exclude_none=True),
        timeout=120.0,
    )


@router.get("/human-journal")
async def list_human_journal(limit: int = 100, offset: int = 0):
    """Proxy: GET /plugins/overseer/human-journal — newest first."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/human-journal",
        {"limit": limit, "offset": offset})


class HumanJournalRequest(BaseModel):
    text: str
    entry_type: str | None = "free"


@router.post("/human-journal")
async def add_human_journal(req: HumanJournalRequest):
    """Proxy: POST /plugins/overseer/human-journal — append a free-form
    user-written journal entry. Captured in local TZ; auto-included
    in temporal narrative prompts for the period it falls in."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/human-journal",
        req.dict(exclude_none=True))


class HumanJournalDeleteRequest(BaseModel):
    id: int


@router.post("/human-journal/delete")
async def delete_human_journal(req: HumanJournalDeleteRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/human-journal/delete",
        req.model_dump())


# ── Slice 6: people ─────────────────────────────────────────────


@router.get("/people")
async def list_people(limit: int = 200, offset: int = 0,
                       order_by: str = "last_interacted_at"):
    return await pi_client.plugin_call(
        "overseer", "GET", "/people",
        {"limit": limit, "offset": offset, "order_by": order_by})


@router.get("/people/get")
async def get_person(id: int):
    return await pi_client.plugin_call(
        "overseer", "GET", "/people/get", {"id": id})


@router.get("/people/search")
async def search_people(q: str = "", limit: int = 50):
    return await pi_client.plugin_call(
        "overseer", "GET", "/people/search",
        {"q": q, "limit": limit})


class PersonAddRequest(BaseModel):
    name: str
    display_name: str | None = None
    online_handles: list[str] | str | None = None
    social_links: list[str] | str | None = None
    areas_of_expertise: list[str] | str | None = None
    notes: str | None = None
    tags: list[str] | str | None = None
    aliases: list[str] | str | None = None
    last_interacted_at: str | None = None
    created_by_agent: str | None = None
    created_by_session_id: str | None = None


@router.post("/people/add")
async def add_person(req: PersonAddRequest):
    """Add a person. Idempotent on case-insensitive name — if a
    person with the same name already exists, returns the existing
    row with `created: false` (the caller can then call
    /people/update if they want to merge in new data)."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/add",
        req.dict(exclude_none=True))


class PersonUpdateRequest(BaseModel):
    id: int
    display_name: str | None = None
    online_handles: list[str] | str | None = None
    social_links: list[str] | str | None = None
    areas_of_expertise: list[str] | str | None = None
    tags: list[str] | str | None = None
    aliases: list[str] | str | None = None
    notes_append: str | None = None    # append-mode (default for agents)
    notes_replace: str | None = None   # replace-mode (manual UI edits)
    last_interacted_at: str | None = None


@router.post("/people/update")
async def update_person(req: PersonUpdateRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/update",
        req.dict(exclude_none=True))


class PersonDeleteRequest(BaseModel):
    id: int


@router.post("/people/delete")
async def delete_person(req: PersonDeleteRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/delete", req.model_dump())


class LinkProjectPersonRequest(BaseModel):
    project: str
    person_id: int
    role: str | None = None
    created_by_agent: str | None = None


@router.post("/people/link-project")
async def link_project_person(req: LinkProjectPersonRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/link-project",
        req.dict(exclude_none=True))


class UnlinkProjectPersonRequest(BaseModel):
    project: str
    person_id: int


@router.post("/people/unlink-project")
async def unlink_project_person(req: UnlinkProjectPersonRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/unlink-project", req.model_dump())


@router.get("/people/for-project")
async def people_for_project(project: str):
    return await pi_client.plugin_call(
        "overseer", "GET", "/people/for-project", {"project": project})


@router.get("/people/stats")
async def people_stats():
    """Proxy: GET /plugins/overseer/people/stats — cross-cutting
    counts (totals, recent additions, orphans, multi-project
    connectors, top expertise tags, top projects by linked people).
    Used by both MCP agents and the upcoming Hub UI Network section."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/people/stats", None)


# ── 2026-06-13 taxonomy build: structured person_notes ──────────────


@router.get("/people/notes")
async def list_person_notes(person_id: int, limit: int = 200,
                            include_superseded: int = 0):
    return await pi_client.plugin_call(
        "overseer", "GET", "/people/notes",
        {"person_id": person_id, "limit": limit,
         "include_superseded": include_superseded})


class PersonNoteAddRequest(BaseModel):
    person_id: int
    body: str
    note_kind: str | None = None
    provenance: str | None = None
    modality: str | None = None
    created_by_agent: str | None = None
    created_by_session_id: str | None = None


@router.post("/people/notes/add")
async def add_person_note(req: PersonNoteAddRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/notes/add",
        req.dict(exclude_none=True))


class PersonNoteDeleteRequest(BaseModel):
    note_id: int


@router.post("/people/notes/delete")
async def delete_person_note(req: PersonNoteDeleteRequest):
    return await pi_client.plugin_call(
        "overseer", "POST", "/people/notes/delete", req.model_dump())


# ── Slice 9.3: sibling task dispatch proxies ────────────────────
# The MCP tools talk to the Pi directly, but exposing the same
# surface here lets the Hub UI build a "Tasks" sub-tab later
# (pending queue, completed-but-unrated, dispatch-quality histogram)
# without touching the Pi endpoint layer again.

@router.post("/sibling/dispatch")
async def sibling_dispatch(body: dict):
    """Proxy: POST /plugins/overseer/sibling/dispatch.
    Body: {prompt, created_by?, target?, task_type?,
           preferred_model_tier?, cost_budget_usd?, context?}"""
    return await pi_client.plugin_call(
        "overseer", "POST", "/sibling/dispatch", body)


@router.get("/sibling/pending")
async def sibling_pending(target: str = "claude-code", limit: int = 50):
    """Proxy: GET /plugins/overseer/sibling/pending.
    Default target='claude-code' matches the MCP tool default."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/sibling/pending",
        {"target": target, "limit": limit})


@router.post("/sibling/claim")
async def sibling_claim(body: dict):
    """Proxy: POST /plugins/overseer/sibling/claim.
    Body: {id, claimed_by}"""
    return await pi_client.plugin_call(
        "overseer", "POST", "/sibling/claim", body)


@router.post("/sibling/complete")
async def sibling_complete(body: dict):
    """Proxy: POST /plugins/overseer/sibling/complete.
    Body: {id, result_text, actual_model_used?, result_cost_usd?,
           dispatch_quality_rating?, dispatch_quality_notes?}"""
    return await pi_client.plugin_call(
        "overseer", "POST", "/sibling/complete", body)


@router.post("/sibling/reject")
async def sibling_reject(body: dict):
    """Proxy: POST /plugins/overseer/sibling/reject.
    Body: {id, reason}"""
    return await pi_client.plugin_call(
        "overseer", "POST", "/sibling/reject", body)


@router.get("/sibling/recent")
async def sibling_recent(limit: int = 20, unread: int = 0):
    """Proxy: GET /plugins/overseer/sibling/recent.
    Pass unread=1 to filter to tasks the overseer hasn't rated yet."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/sibling/recent",
        {"limit": limit, "unread": unread})


@router.get("/sibling/stats")
async def sibling_stats():
    """Proxy: GET /plugins/overseer/sibling/stats — headline counts
    + daily dispatch budget posture."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/sibling/stats", None)


# ── Slice 10.4 (2026-05-20): ecosystem visualizer ──────────────────


@router.get("/ecosystem")
async def ecosystem():
    """Proxy: GET /plugins/overseer/ecosystem — static map of tools,
    tick steps, hooks, B-agents, C-agents for the Hub's ecosystem
    visualizer (the "Map" sub-tab under Overseer)."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/ecosystem", None)


# ── Slice 10.4 Phase 2 (2026-05-20): runs / Activity tab ──────────


@router.get("/runs/recent")
async def runs_recent(hours: int = 24, limit: int = 200,
                       kinds: str = ""):
    """Proxy: GET /plugins/overseer/runs/recent — unified timeline
    of recent runs (B/C agents, A siblings, chat turns, journal
    steps)."""
    payload = {"hours": hours, "limit": limit}
    if kinds:
        payload["kinds"] = kinds
    return await pi_client.plugin_call(
        "overseer", "GET", "/runs/recent", payload)


@router.get("/runs/detail")
async def runs_detail(kind: str, id: str):
    """Proxy: GET /plugins/overseer/runs/detail — full detail of one
    run including nodes/edges for the flow graph + full prompt +
    full output."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/runs/detail", {"kind": kind, "id": id})


@router.get("/runs/export")
async def runs_export(hours: int = 24):
    """Proxy: GET /plugins/overseer/runs/export — full bundle of
    all runs in the past N hours. Frontend triggers a file
    download from this response."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/runs/export", {"hours": hours})


class _RunRateBody(BaseModel):
    sibling_task_id: int
    rating: int
    notes: str = ""
    dataset_candidate: bool = False


@router.post("/runs/rate")
async def runs_rate(body: _RunRateBody):
    """Proxy: POST /plugins/overseer/runs/rate — rate a run that has
    a sibling_task_id (B/C/A dispatches)."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/runs/rate", body.model_dump())
