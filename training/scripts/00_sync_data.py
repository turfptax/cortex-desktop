"""Step 0: Sync training data from the Pi Zero.

Copies the Cortex database from the Pi and exports pet_interactions,
notes, and sessions as JSONL files for the training pipeline.

Usage:
    python 00_sync_data.py                # full sync (copy DB + export)
    python 00_sync_data.py --export-only  # skip SCP, use existing local DB

Requirements:
    SSH access to the Pi (key-based auth recommended).

Outputs:
    ../raw_data/cortex.db              - Full database copy
    ../raw_data/interactions.jsonl     - Pet interactions
    ../raw_data/notes.jsonl            - User notes
    ../raw_data/sessions.jsonl         - Session summaries
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RAW_DATA_DIR = PROJECT_DIR / "raw_data"
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"


def load_config():
    """Load Pi connection settings from config/settings.json."""
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def sync_db(config):
    """SCP the cortex.db from the Pi to raw_data/."""
    pi = config["pi"]
    remote = f"{pi['user']}@{pi['host']}:{pi['db_remote_path']}"
    local = str(RAW_DATA_DIR / "cortex.db")

    print(f"\n=== Syncing database ===")
    print(f"  From: {remote}")
    print(f"  To:   {local}")

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["scp", remote, local],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  SCP failed: {result.stderr.strip()}")
            sys.exit(1)
        print("  Database synced successfully.")
    except FileNotFoundError:
        print("  ERROR: 'scp' not found. Make sure OpenSSH is installed.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("  ERROR: SCP timed out. Is the Pi reachable?")
        sys.exit(1)


def table_exists(conn, table_name):
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] > 0


def export_interactions(conn, output_path):
    """Export pet_interactions table to JSONL."""
    print(f"\n--- Exporting interactions ---")

    if not table_exists(conn, "pet_interactions"):
        print("  Table 'pet_interactions' not found — skipping.")
        print("  (The pet engine creates this table on first run.)")
        # Write empty file so downstream scripts don't break
        with open(output_path, "w") as f:
            pass
        return 0

    rows = conn.execute(
        "SELECT id, prompt, response, sentiment_score, inference_time_ms, "
        "tokens_generated, stage, mood, session_id, created_at "
        "FROM pet_interactions ORDER BY created_at ASC"
    ).fetchall()

    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            record = {
                "id": row[0],
                "prompt": row[1],
                "response": row[2],
                "sentiment_score": row[3],
                "inference_time_ms": row[4],
                "tokens_generated": row[5],
                "stage": row[6],
                "mood": row[7],
                "session_id": row[8],
                "created_at": row[9],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    # Stats
    valid = sum(1 for r in rows if r[2] and r[5] and r[5] > 0)
    print(f"  Total interactions: {count}")
    print(f"  With valid responses: {valid}")
    if rows:
        print(f"  Date range: {rows[0][9]} -> {rows[-1][9]}")

    return count


def export_notes(conn, output_path):
    """Export notes table to JSONL."""
    print(f"\n--- Exporting notes ---")

    if not table_exists(conn, "notes"):
        print("  Table 'notes' not found — skipping.")
        with open(output_path, "w") as f:
            pass
        return 0

    rows = conn.execute(
        "SELECT id, content, tags, project, note_type, source, "
        "session_id, created_at "
        "FROM notes ORDER BY created_at ASC"
    ).fetchall()

    count = 0
    projects = set()
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            record = {
                "id": row[0],
                "content": row[1],
                "tags": row[2],
                "project": row[3],
                "note_type": row[4],
                "source": row[5],
                "session_id": row[6],
                "created_at": row[7],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            if row[3]:
                projects.add(row[3])

    print(f"  Total notes: {count}")
    if projects:
        print(f"  Projects: {', '.join(sorted(projects))}")
    if rows:
        print(f"  Date range: {rows[0][7]} -> {rows[-1][7]}")

    return count


def export_sessions(conn, output_path):
    """Export sessions table to JSONL."""
    print(f"\n--- Exporting sessions ---")

    if not table_exists(conn, "sessions"):
        print("  Table 'sessions' not found — skipping.")
        with open(output_path, "w") as f:
            pass
        return 0

    rows = conn.execute(
        "SELECT id, ai_platform, hostname, os_info, started_at, "
        "ended_at, summary, projects "
        "FROM sessions ORDER BY started_at ASC"
    ).fetchall()

    count = 0
    with_summary = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            record = {
                "id": row[0],
                "ai_platform": row[1],
                "hostname": row[2],
                "os_info": row[3],
                "started_at": row[4],
                "ended_at": row[5],
                "summary": row[6],
                "projects": row[7],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            if row[6]:
                with_summary += 1

    print(f"  Total sessions: {count}")
    print(f"  With summaries: {with_summary}")

    return count


def main():
    parser = argparse.ArgumentParser(description="Sync training data from Pi")
    parser.add_argument("--export-only", action="store_true",
                        help="Skip SCP, use existing local cortex.db")
    args = parser.parse_args()

    config = load_config()

    print("=== Cortex Pet Training — Data Sync ===")

    # Step 1: Copy DB from Pi
    if not args.export_only:
        sync_db(config)
    else:
        print("\n  (Skipping SCP — using existing local DB)")

    # Step 2: Open local DB
    db_path = RAW_DATA_DIR / "cortex.db"
    if not db_path.exists():
        print(f"\nERROR: Database not found at {db_path}")
        print("Run without --export-only to sync from Pi first.")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))

    # Show available tables
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [t[0] for t in tables]
    print(f"\n  Tables in DB: {', '.join(table_names)}")

    # Step 3: Export tables
    n_interactions = export_interactions(
        conn, RAW_DATA_DIR / "interactions.jsonl"
    )
    n_notes = export_notes(conn, RAW_DATA_DIR / "notes.jsonl")
    n_sessions = export_sessions(conn, RAW_DATA_DIR / "sessions.jsonl")

    conn.close()

    # Summary
    print(f"\n=== Sync Complete ===")
    print(f"  Interactions: {n_interactions}")
    print(f"  Notes:        {n_notes}")
    print(f"  Sessions:     {n_sessions}")
    print(f"  Output:       {RAW_DATA_DIR}")
    print(f"\nNext: python 01_prepare_dataset.py")


if __name__ == "__main__":
    main()
