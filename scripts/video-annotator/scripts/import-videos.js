#!/usr/bin/env node
/**
 * import-videos.js — populate the annotator DB from a selected_paths.txt
 * and the mining run's state-index.json.
 *
 * Usage:
 *   node scripts/import-videos.js                        # use defaults
 *   node scripts/import-videos.js --paths <file>
 *                                  --state-index <file>
 *                                  --screenshots <dir>
 *                                  --transcripts <dir>
 *
 * Defaults are tuned for Tory's local layout:
 *   --paths        C:\Users\User\Downloads\selected_paths (1).txt
 *   --state-index  D:\video_mining_output\state-index.json
 *                  (falls back to F:\video_mining_output\state-index.json)
 *   --screenshots  D:\video_mining_output\screenshots
 *                  (falls back to F:\video_mining_output\screenshots)
 *   --transcripts  D:\video_mining_output\transcripts
 *                  (falls back to F:\video_mining_output\transcripts)
 *
 * Re-running is safe: rows are upserted by src_path, annotation fields
 * (title/description/tags/decision/notes) are preserved across re-imports.
 * Mining-state fields (transcribe_done, transcript_chars, etc.) are
 * always refreshed from the latest state-index.json.
 */
const fs = require("fs");
const path = require("path");
const { getDb } = require("../db");

// ── CLI arg parsing ──────────────────────────────────────────────
const args = process.argv.slice(2);
function arg(name, fallback) {
  const i = args.indexOf("--" + name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : fallback;
}

const PATHS_FILE = arg("paths",
  "C:\\Users\\User\\Downloads\\selected_paths (1).txt");
const STATE_INDEX = arg("state-index", null);
const SCREENSHOTS = arg("screenshots", null);
const TRANSCRIPTS = arg("transcripts", null);

const STATE_INDEX_CANDIDATES = [
  STATE_INDEX,
  "D:\\video_mining_output\\state-index.json",
  "F:\\video_mining_output\\state-index.json",
].filter(Boolean);

const SCREENSHOTS_CANDIDATES = [
  SCREENSHOTS,
  "D:\\video_mining_output\\screenshots",
  "F:\\video_mining_output\\screenshots",
].filter(Boolean);

const TRANSCRIPTS_CANDIDATES = [
  TRANSCRIPTS,
  "D:\\video_mining_output\\transcripts",
  "F:\\video_mining_output\\transcripts",
].filter(Boolean);

const VIDEO_BACKUPS_ROOT = "F:\\Video Backups";

// ── Helpers ─────────────────────────────────────────────────────
function firstExisting(candidates) {
  return candidates.find((p) => p && fs.existsSync(p)) || null;
}

function loadStateIndex() {
  const merged = {};
  for (const p of STATE_INDEX_CANDIDATES) {
    if (!p || !fs.existsSync(p)) continue;
    try {
      const data = JSON.parse(fs.readFileSync(p, "utf-8"));
      // Both indexes are keyed by src_path (Windows-escaped). Merge by
      // letting later candidates win — matches "D: is current, F: is
      // historical" priority.
      Object.assign(merged, data);
      console.log(`  loaded state-index: ${p} (${Object.keys(data).length} rows)`);
    } catch (e) {
      console.warn(`  WARN: failed to parse ${p}: ${e.message}`);
    }
  }
  return merged;
}

function findThumbnails(relPathNoExt) {
  // Look for screenshot dir under any candidate root. The
  // index_videos.py / mine_video_journals.py output puts frames
  // at <screenshots>/<rel-path-without-ext>/frame_NNNNN.jpg.
  for (const root of SCREENSHOTS_CANDIDATES) {
    if (!root) continue;
    const dir = path.join(root, relPathNoExt);
    if (!fs.existsSync(dir)) continue;
    let frames;
    try {
      frames = fs
        .readdirSync(dir)
        .filter((f) => /^frame_\d+\.jpg$/i.test(f))
        .sort();
    } catch (e) {
      continue;
    }
    if (frames.length === 0) continue;
    const start = path.join(dir, frames[0]);
    const middle = path.join(dir, frames[Math.floor(frames.length / 2)]);
    const end = path.join(dir, frames[frames.length - 1]);
    return { start, middle, end, count: frames.length };
  }
  return { start: null, middle: null, end: null, count: 0 };
}

function findTranscript(relPathNoExt) {
  for (const root of TRANSCRIPTS_CANDIDATES) {
    if (!root) continue;
    const txt = path.join(root, relPathNoExt + ".txt");
    if (fs.existsSync(txt)) return txt;
  }
  return null;
}

// Filename-pattern date extractor — same idea as index_videos.py.
// Returns { date: "YYYY-MM-DD", source: "filename"|"unknown" }.
function inferFilmedDate(filename) {
  const patterns = [
    // OBS / Sony ZV-E10 / generic ISO: 2024-05-24 13-15-10.mp4
    /(\d{4})[-_](\d{2})[-_](\d{2})/,
    // Samsung Galaxy / DJI: 20240524_131510 / IMG_20240524_...
    /(?:^|[^\d])(\d{4})(\d{2})(\d{2})(?:[^\d]|$)/,
  ];
  for (const re of patterns) {
    const m = filename.match(re);
    if (m) {
      const y = parseInt(m[1], 10);
      const mo = parseInt(m[2], 10);
      const d = parseInt(m[3], 10);
      if (y >= 2010 && y <= 2030 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
        return {
          date: `${m[1]}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
          source: "filename",
        };
      }
    }
  }
  return { date: null, source: "unknown" };
}

// ── Main ────────────────────────────────────────────────────────
function main() {
  if (!fs.existsSync(PATHS_FILE)) {
    console.error(`ERR: paths file not found: ${PATHS_FILE}`);
    process.exit(1);
  }
  console.log(`Importing from: ${PATHS_FILE}`);
  console.log(`State-index roots: ${STATE_INDEX_CANDIDATES.join(", ")}`);
  console.log(`Screenshots roots: ${SCREENSHOTS_CANDIDATES.join(", ")}`);

  const stateIndex = loadStateIndex();
  console.log(`  total state rows: ${Object.keys(stateIndex).length}`);

  const lines = fs
    .readFileSync(PATHS_FILE, "utf-8")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));
  console.log(`  paths to import: ${lines.length}`);

  const db = getDb();
  const upsert = db.prepare(`
    INSERT INTO videos (
      src_path, rel_path, filename, parent_dir, file_size,
      filmed_date, filmed_date_source, duration_s,
      thumb_start, thumb_middle, thumb_end,
      transcribe_done, transcribe_partial, transcript_chars,
      transcribe_error, transcript_path,
      updated_at
    ) VALUES (
      @src_path, @rel_path, @filename, @parent_dir, @file_size,
      @filmed_date, @filmed_date_source, @duration_s,
      @thumb_start, @thumb_middle, @thumb_end,
      @transcribe_done, @transcribe_partial, @transcript_chars,
      @transcribe_error, @transcript_path,
      datetime('now')
    )
    ON CONFLICT(src_path) DO UPDATE SET
      rel_path           = excluded.rel_path,
      filename           = excluded.filename,
      parent_dir         = excluded.parent_dir,
      file_size          = COALESCE(excluded.file_size, videos.file_size),
      filmed_date        = COALESCE(excluded.filmed_date, videos.filmed_date),
      filmed_date_source = COALESCE(excluded.filmed_date_source, videos.filmed_date_source),
      duration_s         = COALESCE(excluded.duration_s, videos.duration_s),
      thumb_start        = COALESCE(excluded.thumb_start, videos.thumb_start),
      thumb_middle       = COALESCE(excluded.thumb_middle, videos.thumb_middle),
      thumb_end          = COALESCE(excluded.thumb_end, videos.thumb_end),
      transcribe_done    = excluded.transcribe_done,
      transcribe_partial = excluded.transcribe_partial,
      transcript_chars   = excluded.transcript_chars,
      transcribe_error   = excluded.transcribe_error,
      transcript_path    = COALESCE(excluded.transcript_path, videos.transcript_path),
      updated_at         = datetime('now')
  `);

  const tx = db.transaction((rows) => {
    for (const r of rows) upsert.run(r);
  });

  let n_done = 0, n_partial = 0, n_pending = 0, n_errored = 0;
  const rows = lines.map((src) => {
    let rel = src;
    if (rel.toLowerCase().startsWith(VIDEO_BACKUPS_ROOT.toLowerCase())) {
      rel = rel.substring(VIDEO_BACKUPS_ROOT.length).replace(/^[\\/]+/, "");
    }
    const filename = path.basename(src);
    const parent_dir = path.dirname(src);
    const ext = path.extname(filename);
    const stem = filename.substring(0, filename.length - ext.length);
    const relNoExt = rel.substring(0, rel.length - ext.length);

    let file_size = null;
    try {
      file_size = fs.statSync(src).size;
    } catch (e) {
      // File missing on disk — keep import going, leave size null
    }

    const { date: filmed_date, source: filmed_date_source } = inferFilmedDate(filename);
    const thumbs = findThumbnails(relNoExt);
    const transcript_path = findTranscript(relNoExt);

    // Pull mining state. The state-index keys use back-slash Windows
    // paths exactly as the script wrote them; selected_paths.txt may
    // use either, so check both forms.
    const stateKey =
      stateIndex[src] ||
      stateIndex[src.replace(/\//g, "\\")] ||
      stateIndex[src.replace(/\\/g, "/")] ||
      {};

    let transcribe_done = stateKey.transcribe_done ? 1 : 0;
    let transcribe_partial = stateKey.transcript_partial ||
                              stateKey.phase === "transcribe_partial" ? 1 : 0;
    let transcript_chars = stateKey.transcript_chars || null;
    let transcribe_error = null;
    if (stateKey.phase === "error") transcribe_error = stateKey.error || "unknown";
    let duration_s = stateKey.duration_s || null;

    if (transcribe_done) n_done++;
    else if (transcribe_partial) n_partial++;
    else if (stateKey.phase === "error") n_errored++;
    else n_pending++;

    return {
      src_path: src,
      rel_path: rel,
      filename,
      parent_dir,
      file_size,
      filmed_date,
      filmed_date_source,
      duration_s,
      thumb_start: thumbs.start,
      thumb_middle: thumbs.middle,
      thumb_end: thumbs.end,
      transcribe_done,
      transcribe_partial,
      transcript_chars,
      transcribe_error,
      transcript_path,
    };
  });

  tx(rows);
  db.close();

  console.log(`\nImport complete: ${rows.length} rows`);
  console.log(`  transcribe_done    : ${n_done}`);
  console.log(`  transcribe_partial : ${n_partial}`);
  console.log(`  transcribe_error   : ${n_errored}`);
  console.log(`  pending            : ${n_pending}`);
}

main();
