"""Local Claude-file ingester scaffold (PREP ONLY, gated on core P3).

Scans ~/.claude/projects/ for Claude Code session .jsonl files and
tracks which content hashes have been pushed. The push itself is a
stub: the cloud gateway's session-file import endpoint does not exist
yet (see docs/CLOUD_MIGRATION_DESKTOP_PREP.md section 3), and this
module must not be wired to a cloud that is not deployed.

Runs nothing unless CORTEX_LOCAL_INGEST=1. Even then, the CLI is a
dry-run scan that prints what WOULD be pushed. Stdlib only; no Hub
backend or Pi imports.

Usage:
    set CORTEX_LOCAL_INGEST=1
    python -m cortex_local.ingester
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

# A session file is only a push candidate once it has been idle this
# long; Claude appends to live sessions and dedup is by content hash,
# so pushing early just wastes an upload per append burst.
IDLE_MINUTES_DEFAULT = 30


def claude_projects_dir() -> Path:
    """Where Claude Code (and Claude Desktop code mode) write .jsonl."""
    return Path.home() / ".claude" / "projects"


def state_path() -> Path:
    base = Path(os.environ.get(
        "APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "Cortex" / "local_ingest_state.json"


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"pushed_hashes": [], "last_scan_at": None}


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(idle_minutes: int = IDLE_MINUTES_DEFAULT) -> list[dict]:
    """Walk the projects dir; return candidate session files.

    A candidate is a .jsonl idle for at least idle_minutes. Each entry:
    path, session_id (filename stem, Claude's session uuid),
    project_folder, size_bytes, mtime, file_hash.
    """
    base = claude_projects_dir()
    if not base.is_dir():
        return []
    cutoff = time.time() - idle_minutes * 60
    found: list[dict] = []
    for project_dir in sorted(base.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            try:
                stat = jsonl.stat()
            except OSError:
                continue
            if stat.st_mtime > cutoff:
                continue  # still being written; next pass picks it up
            found.append({
                "path": str(jsonl),
                "session_id": jsonl.stem,
                "project_folder": project_dir.name,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "file_hash": file_sha256(jsonl),
            })
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


def push_session_file(entry: dict, gateway_url: str, token: str) -> dict:
    """Push one session file to the cloud gateway.

    STUB: the gateway endpoint (POST /v1/imports/session-file) is not
    built yet, and OAuth scope/refresh questions are open. See
    docs/CLOUD_MIGRATION_DESKTOP_PREP.md section 3.
    """
    raise NotImplementedError(
        "gateway session-file import endpoint does not exist yet; "
        "gated on core P3 (docs/CLOUD_MIGRATION_DESKTOP_PREP.md)")


def main() -> int:
    if os.environ.get("CORTEX_LOCAL_INGEST", "").strip() != "1":
        print("cortex_local.ingester is gated: set CORTEX_LOCAL_INGEST=1 "
              "to run the dry-run scan. (Design: "
              "docs/CLOUD_MIGRATION_DESKTOP_PREP.md)")
        return 2

    state = load_state()
    pushed = set(state.get("pushed_hashes", []))
    candidates = scan()
    new = [c for c in candidates if c["file_hash"] not in pushed]

    print("scanned: {}  candidates (idle >= {} min): {}  "
          "already pushed: {}  would push: {}".format(
              claude_projects_dir(), IDLE_MINUTES_DEFAULT,
              len(candidates), len(candidates) - len(new), len(new)))
    for c in new[:20]:
        print("  WOULD PUSH {}  ({} bytes, {})".format(
            c["session_id"], c["size_bytes"], c["project_folder"]))
    if len(new) > 20:
        print("  ... and {} more".format(len(new) - 20))
    print("dry run only: push is stubbed until the cloud app exists.")

    state["last_scan_at"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.gmtime()) + "Z"
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
