"""Cortex Agent watcher (CP1, v0.22.0): pushes local Claude Code
sessions to the cloud corpus.

Spec of record: docs/CORTEX_AGENT_PLAN.md section 3. Two-step push
(the proven Hub protocol): POST {base}/files/uploads with X-Filename,
then POST {base}/plugins/overseer/imports/from-path. Whole-file
re-push of a grown idle session is the incremental model; the server
dedups by hash and re-queues grown sessions for gisting (the CP0
cloud fix). The client never tails byte offsets.

Transport: stdlib urllib with cortex_mcp/wifi_bridge.py's config
loader + Basic-auth helper. Config is read fresh every cycle; the
base URL comes from pi_host (a full https URL on cloud installs).

Enable/disable: config key ingest_enabled (default true). Env
CORTEX_LOCAL_INGEST=0 is an emergency override that disables the
watcher regardless of config. (The old opt-IN =1 semantics from the
scaffold era are retired.)

Usage:
    python -m cortex_local.ingester             one real cycle
    python -m cortex_local.ingester --dry-run   scan + report only
    python -m cortex_local.ingester --loop      run forever (dev)
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from cortex_mcp.wifi_bridge import (
    _load_user_config,
    _make_basic_auth_header,
    get_pi_credentials,
    get_pi_host,
    get_pi_port,
)
from cortex_local.logging_setup import setup_logging

log = logging.getLogger("cortex.agent.ingest")

TIMEOUT_S = 110              # gateway /core proxy read timeout is 120s
MAX_FILE_BYTES = 50 * 1024 * 1024
POISON_STRIKES = 3           # consecutive failures before cooldown
POISON_COOLDOWN_SCANS = 6    # scans skipped while cooling down

DEFAULT_SETTINGS = {
    "ingest_enabled": True,
    "ingest_idle_minutes": 30,
    "ingest_scan_interval_seconds": 300,
    "ingest_max_per_cycle": 20,
    "ingest_upload_delay_seconds": 3,
}


# == Config ========================================================


def load_settings() -> dict:
    cfg = _load_user_config()
    out = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in cfg:
            out[key] = cfg[key]
    return out


def ingest_disabled_reason(settings: dict) -> str:
    """Empty string when the watcher should run, else a human reason."""
    if os.environ.get("CORTEX_LOCAL_INGEST", "").strip() == "0":
        return "env override CORTEX_LOCAL_INGEST=0"
    if not settings.get("ingest_enabled", True):
        return "config ingest_enabled=false"
    return ""


def base_url() -> str:
    host = get_pi_host()
    if "://" in host:
        return host.rstrip("/")
    return "http://{}:{}".format(host, get_pi_port())


def _auth_header() -> str:
    user, password = get_pi_credentials()
    return _make_basic_auth_header(user, password)


# == HTTP (stdlib, 110s, fresh config per call) ====================


class PushError(Exception):
    """A push step failed. step is 1 or 2; status is the HTTP code
    when the server answered, else None (network-class failure)."""

    def __init__(self, step: int, message: str, status=None):
        super().__init__(message)
        self.step = step
        self.status = status


def _request_json(method: str, path: str, body: bytes | None = None,
                  headers: dict | None = None) -> dict:
    url = base_url() + path
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", _auth_header())
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    resp = urllib.request.urlopen(req, timeout=TIMEOUT_S)
    return json.loads(resp.read().decode("utf-8", errors="replace"))


def upload_session_file(path: Path) -> dict:
    """Step 1: POST the raw bytes to {base}/files/uploads."""
    data = path.read_bytes()
    return _request_json("POST", "/files/uploads", body=data, headers={
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(data)),
        "X-Filename": path.name,
        "X-Description": "Claude Code session import",
        "X-Tags": "claude-code,overseer-import",
    })


def push_session_file(path: Path) -> dict:
    """The two-step push. Returns the step-2 response on confirmed
    success (imported_id OR skipped). Raises PushError otherwise."""
    try:
        upload = upload_session_file(path)
    except urllib.error.HTTPError as e:
        raise PushError(1, "upload HTTP {}".format(e.code), status=e.code)
    except Exception as e:
        raise PushError(1, "upload failed: {}".format(e))

    remote_path = upload.get("path")
    if not remote_path:
        raise PushError(1, "upload returned no path: {}".format(upload))

    payload = json.dumps(
        {"path": remote_path, "source": "claude-code"}).encode("utf-8")
    try:
        ingest = _request_json(
            "POST", "/plugins/overseer/imports/from-path",
            body=payload, headers={"Content-Type": "application/json"})
    except urllib.error.HTTPError as e:
        raise PushError(2, "from-path HTTP {}".format(e.code), status=e.code)
    except Exception as e:
        raise PushError(2, "from-path failed: {}".format(e))

    if ingest.get("imported_id") or ingest.get("skipped"):
        return ingest
    raise PushError(2, "from-path rejected: {}".format(
        str(ingest.get("error") or ingest)[:300]))


def fetch_server_hashes() -> set[str]:
    """Dedupe bootstrap: page the cloud's imported_sessions hashes.
    Raises on transport failure; the caller degrades gracefully."""
    out: set[str] = set()
    offset = 0
    while True:
        page = _request_json(
            "GET",
            "/plugins/overseer/imports?source=claude-code"
            "&limit=500&offset={}".format(offset))
        if not page.get("ok"):
            raise PushError(0, "imports page not ok: {}".format(
                str(page)[:200]))
        rows = page.get("imports") or []
        for row in rows:
            h = row.get("file_hash") or ""
            if h:
                out.add(h)
        if len(rows) < 500:
            break
        offset += 500
    return out


# == Local scan ====================================================


def claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_idle_sessions(idle_minutes: int) -> list[dict]:
    """All .jsonl session files idle past the gate, newest first.
    No hashing here; the cycle decides that with the state pre-filter."""
    base = claude_projects_dir()
    if not base.is_dir():
        return []
    cutoff = time.time() - idle_minutes * 60
    found: list[dict] = []
    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                stat = jsonl.stat()
            except OSError:
                continue
            if stat.st_mtime > cutoff:
                continue
            found.append({
                "path": jsonl,
                "session_uuid": jsonl.stem,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


# == State (schema v2, CORTEX_AGENT_PLAN section 2.4) ==============


def state_path() -> Path:
    base = Path(os.environ.get(
        "APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "Cortex" / "local_ingest_state.json"


def _empty_state() -> dict:
    return {
        "version": 2,
        "bootstrap_done_at": None,
        "server_hashes_seen": [],
        "sessions": {},
        "scan_count": 0,
        "last_scan_at": None,
    }


def load_state() -> dict:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if raw.get("version") == 2:
        for key, value in _empty_state().items():
            raw.setdefault(key, value)
        return raw
    # v1 scaffold migration: fold pushed_hashes into server_hashes_seen
    state = _empty_state()
    state["server_hashes_seen"] = list(raw.get("pushed_hashes", []))
    return state


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z"


# == The cycle =====================================================


def _record_success(state: dict, entry: dict, digest: str,
                    result: dict) -> None:
    sessions = state["sessions"]
    rec = sessions.get(entry["session_uuid"], {})
    rec.update({
        "path": str(entry["path"]),
        "last_pushed_sha256": digest,
        "last_pushed_size": entry["size"],
        "last_pushed_mtime": entry["mtime"],
        "pushed_at": _now_iso(),
        "imported_id": result.get("imported_id") or rec.get("imported_id"),
        "attempts": 0,
        "last_error": None,
        "last_failed_hash": None,
        "pending_confirm_hash": None,
        "cooldown_until_scan": 0,
    })
    sessions[entry["session_uuid"]] = rec


def _record_failure(state: dict, entry: dict, digest: str,
                    err: PushError) -> None:
    """Poison bookkeeping. A step-2 failure after a successful step-1
    does not strike immediately: the from-path parse can exceed the
    proxy window and 502 to us while completing server-side, and the
    next scan's re-push self-heals via skipped. It strikes only when
    the re-push of the same hash fails too (spec section 3 item 5)."""
    sessions = state["sessions"]
    rec = sessions.get(entry["session_uuid"], {})
    rec.setdefault("path", str(entry["path"]))
    rec["last_error"] = str(err)

    if rec.get("last_failed_hash") != digest:
        # New content since the last failure: strikes start over.
        rec["attempts"] = 0
        rec["cooldown_until_scan"] = 0
    rec["last_failed_hash"] = digest

    soft = (err.step == 2
            and rec.get("pending_confirm_hash") != digest)
    if soft:
        rec["pending_confirm_hash"] = digest
    else:
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        if rec["attempts"] >= POISON_STRIKES:
            rec["cooldown_until_scan"] = (
                state["scan_count"] + POISON_COOLDOWN_SCANS)
    sessions[entry["session_uuid"]] = rec


def run_cycle(dry_run: bool = False) -> dict:
    """One scan + push cycle. Returns a summary dict."""
    settings = load_settings()
    reason = ingest_disabled_reason(settings)
    if reason:
        log.info("ingest disabled (%s)", reason)
        return {"enabled": False, "reason": reason}

    state = load_state()
    state["scan_count"] = int(state.get("scan_count", 0)) + 1
    scan_no = state["scan_count"]

    # Fresh-machine dedupe bootstrap. On failure proceed anyway
    # (server hash-dedupe makes duplicates harmless) and retry next
    # cycle because bootstrap_done_at stays null.
    if not state.get("bootstrap_done_at") and not dry_run:
        try:
            hashes = fetch_server_hashes()
            state["server_hashes_seen"] = sorted(hashes)
            state["bootstrap_done_at"] = _now_iso()
            log.info("dedupe bootstrap: %d hashes known server-side",
                     len(hashes))
        except Exception as e:
            log.warning("dedupe bootstrap failed (%s); proceeding, "
                        "will retry next cycle", e)

    server_hashes = set(state.get("server_hashes_seen", []))
    sessions = state["sessions"]

    candidates: list[tuple[dict, str]] = []
    skipped_oversize = 0
    for entry in list_idle_sessions(int(settings["ingest_idle_minutes"])):
        rec = sessions.get(entry["session_uuid"])
        if (rec and rec.get("last_pushed_size") == entry["size"]
                and rec.get("last_pushed_mtime") == entry["mtime"]):
            continue  # unchanged since last push; skip without hashing
        if entry["size"] > MAX_FILE_BYTES:
            skipped_oversize += 1
            log.warning("skipping %s: %d bytes exceeds the 50MB proxy "
                        "line", entry["path"].name, entry["size"])
            continue
        try:
            digest = file_sha256(entry["path"])
        except OSError as e:
            log.warning("cannot hash %s: %s", entry["path"], e)
            continue
        if rec and digest == rec.get("last_pushed_sha256"):
            rec["last_pushed_size"] = entry["size"]
            rec["last_pushed_mtime"] = entry["mtime"]
            continue  # content identical; refresh the cheap pre-filter
        if digest in server_hashes:
            _record_success(state, entry, digest, {})
            continue  # server already has these bytes
        if (rec and rec.get("cooldown_until_scan", 0) > scan_no
                and rec.get("last_failed_hash") == digest):
            continue  # poison cooldown
        candidates.append((entry, digest))

    pushed = failed = 0
    stopped_early = False
    cap = int(settings["ingest_max_per_cycle"])
    delay = float(settings["ingest_upload_delay_seconds"])

    for entry, digest in candidates[:cap]:
        if dry_run:
            log.info("DRY RUN would push %s (%d bytes)",
                     entry["path"].name, entry["size"])
            continue
        try:
            result = push_session_file(entry["path"])
            _record_success(state, entry, digest, result)
            pushed += 1
            outcome = result.get("imported_id") or "skipped"
            log.info("pushed %s -> %s (%d bytes)",
                     entry["path"].name, outcome, entry["size"])
        except PushError as e:
            if e.status in (429, 503):
                log.warning("cloud says back off (HTTP %s); stopping "
                            "this cycle", e.status)
                stopped_early = True
                break
            failed += 1
            _record_failure(state, entry, digest, e)
            log.warning("push failed for %s (step %d): %s",
                        entry["path"].name, e.step, e)
        save_state(state)  # persist after every outcome (crash safety)
        if delay > 0:
            time.sleep(delay)

    state["last_scan_at"] = _now_iso()
    if not dry_run:
        save_state(state)

    summary = {
        "enabled": True,
        "scan": scan_no,
        "candidates": len(candidates),
        "pushed": pushed,
        "failed": failed,
        "oversize_skipped": skipped_oversize,
        "stopped_early": stopped_early,
        "remaining": len(candidates) - pushed - failed,
    }
    log.info("scan %d done: %d candidates, %d pushed, %d failed%s",
             scan_no, len(candidates), pushed, failed,
             " (dry run)" if dry_run else "")
    return summary


def run_loop(shutdown_event) -> None:
    """Background watcher loop for the tray app (cortex_desktop/app.py).
    Never raises; a broken cycle logs and the loop continues."""
    setup_logging()
    log.info("ingest watcher started (target %s)", base_url())
    while not shutdown_event.is_set():
        try:
            run_cycle()
        except Exception:
            log.exception("ingest cycle crashed; continuing")
        interval = float(
            load_settings()["ingest_scan_interval_seconds"])
        shutdown_event.wait(interval)
    log.info("ingest watcher stopped")


# == CLI ===========================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cortex Agent session watcher (CP1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="scan and report; push nothing")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (dev)")
    args = parser.parse_args()

    setup_logging(console=True)
    if args.loop:
        import threading
        run_loop(threading.Event())
        return 0
    summary = run_cycle(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("enabled") else 2


if __name__ == "__main__":
    sys.exit(main())
