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
    # dev.18: bumped from 800 -> 8000. The 800-token cap was truncating
    # mid-paragraph on essay-length responses (Tory's catalog of things
    # he wanted tracked got cut off mid-list, structured insight blocks
    # got cut off mid-JSON). 8000 is generous default; Pi ceiling is
    # 32000 for callers that explicitly need more.
    max_tokens: int = 8000
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
        "overseer", "POST", "/questions/lifecycle", req.dict())


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
        "overseer", "POST", "/questions/route-existing", req.dict(),
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
        "overseer", "POST", "/blindspots/active", req.dict())


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


@router.get("/detail")
async def overseer_detail(token: str):
    """Resolve a working-memory token (e.g. 'q:42', 'p:5') to its full
    row + tags + type-specific context + suggested next-step tokens."""
    return await pi_client.plugin_call(
        "overseer", "GET", "/detail", {"token": token})


# ── Slice 3h: insight generation queue ─────────────────────────


class InsightScanRequest(BaseModel):
    project: str
    days: int = 7


@router.post("/insight/scan-now")
async def insight_scan_now(req: InsightScanRequest):
    """Trigger a Sonnet scan of one project's recent gist arc.
    Proposes theme/pattern/drift candidates to the pending queue."""
    return await pi_client.plugin_call(
        "overseer", "POST", "/insight/scan-now", req.dict(),
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
        req.dict(),
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
        req.dict())


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
        "overseer", "POST", "/people/delete", req.dict())


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
        "overseer", "POST", "/people/unlink-project", req.dict())


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
