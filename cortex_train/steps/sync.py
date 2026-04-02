"""Step 0: Sync training data from the Pi Zero.

Copies cortex.db via SCP and exports interactions, notes, and sessions as JSONL.
"""

import json
import sqlite3
from typing import Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import SyncError
from cortex_train.paths import TrainPaths
from cortex_train.pi_client import scp_from_pi
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row[0] > 0


def _export_table(conn, table, columns, output_path, emit, order_by="created_at") -> int:
    """Generic JSONL exporter for a database table."""
    if not _table_exists(conn, table):
        emit(f"Table '{table}' not found — skipping")
        output_path.write_text("")
        return 0

    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by} ASC"
    ).fetchall()

    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            record = dict(zip(columns, row))
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    emit(f"Exported {table}: {count} rows")
    return count


def run_sync(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    export_only: bool = False,
) -> dict:
    """Sync data from Pi and export to JSONL.

    Args:
        export_only: Skip SCP, use existing local cortex.db

    Returns:
        {ok, interactions, notes, sessions}
    """
    emit = make_step_progress("sync", on_progress)

    # Step 1: SCP database from Pi
    if not export_only:
        emit(f"Syncing database from {settings.pi.user}@{settings.pi.host}...")
        try:
            scp_from_pi(
                user=settings.pi.user,
                host=settings.pi.host,
                remote_path=settings.pi.db_remote_path,
                local_path=paths.db_path,
            )
            emit("Database synced successfully")
        except SyncError as e:
            emit(f"SCP failed: {e}")
            raise
    else:
        emit("Skipping SCP — using existing local DB")

    # Step 2: Verify DB exists
    if not paths.db_path.exists():
        raise SyncError(f"Database not found at {paths.db_path}")

    conn = sqlite3.connect(str(paths.db_path))

    # Show tables
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    emit(f"Tables in DB: {', '.join(tables)}")

    # Step 3: Export tables
    n_interactions = _export_table(
        conn, "pet_interactions",
        ["id", "prompt", "response", "sentiment_score", "inference_time_ms",
         "tokens_generated", "stage", "mood", "session_id", "created_at"],
        paths.interactions_path, emit,
    )

    n_notes = _export_table(
        conn, "notes",
        ["id", "content", "tags", "project", "note_type", "source",
         "session_id", "created_at"],
        paths.notes_path, emit,
    )

    n_sessions = _export_table(
        conn, "sessions",
        ["id", "ai_platform", "hostname", "os_info", "started_at",
         "ended_at", "summary", "projects"],
        paths.sessions_path, emit, order_by="started_at",
    )

    conn.close()

    emit(f"Sync complete: {n_interactions} interactions, {n_notes} notes, {n_sessions} sessions", pct=100.0)

    return {
        "ok": True,
        "interactions": n_interactions,
        "notes": n_notes,
        "sessions": n_sessions,
    }
