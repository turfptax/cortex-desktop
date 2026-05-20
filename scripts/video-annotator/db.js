const Database = require("better-sqlite3");
const path = require("path");

const DB_PATH = path.join(__dirname, "annotator.db");

function getDb() {
  const db = new Database(DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  // Videos table — one row per video file in the working set.
  // src_path is the canonical key (UNIQUE) so re-imports are idempotent
  // and existing annotations survive a re-run of the import script.
  db.exec(`
    CREATE TABLE IF NOT EXISTS videos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      src_path TEXT NOT NULL UNIQUE,
      rel_path TEXT,
      filename TEXT,
      parent_dir TEXT,
      file_size INTEGER,
      filmed_date TEXT,
      filmed_date_source TEXT,
      duration_s REAL,

      -- Annotation fields (filled in via the UI)
      title TEXT,
      description TEXT,
      tags TEXT,
      decision TEXT DEFAULT 'pending',
      notes TEXT,

      -- Thumbnails (absolute paths to .jpg, ~3 per video)
      thumb_start TEXT,
      thumb_middle TEXT,
      thumb_end TEXT,

      -- Mining state mirrored from state-index.json
      transcribe_done INTEGER DEFAULT 0,
      transcribe_partial INTEGER DEFAULT 0,
      transcript_chars INTEGER,
      transcribe_error TEXT,
      transcript_path TEXT,

      -- Audit
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_videos_decision ON videos(decision);
    CREATE INDEX IF NOT EXISTS idx_videos_filmed_date ON videos(filmed_date);
    CREATE INDEX IF NOT EXISTS idx_videos_transcribe ON videos(transcribe_done);
  `);

  return db;
}

module.exports = { getDb, DB_PATH };
