const express = require("express");
const path = require("path");
const fs = require("fs");
const { exec } = require("child_process");
const { getDb } = require("./db");

const app = express();
const PORT = 3334;

app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(__dirname, "public")));

// ── Helpers ─────────────────────────────────────────────────────
function safeReadVideo(id) {
  const db = getDb();
  const v = db.prepare("SELECT * FROM videos WHERE id = ?").get(id);
  db.close();
  return v;
}

// ── GET /api/stats ──────────────────────────────────────────────
app.get("/api/stats", (req, res) => {
  const db = getDb();
  const total = db.prepare("SELECT COUNT(*) as n FROM videos").get().n;
  const byDecision = db
    .prepare("SELECT decision, COUNT(*) as n FROM videos GROUP BY decision")
    .all();
  const byYear = db
    .prepare(
      "SELECT substr(filmed_date, 1, 4) as year, COUNT(*) as n " +
        "FROM videos GROUP BY year ORDER BY year"
    )
    .all();
  const byParent = db
    .prepare(
      "SELECT parent_dir, COUNT(*) as n FROM videos GROUP BY parent_dir " +
        "ORDER BY n DESC LIMIT 30"
    )
    .all();
  const transcribed = db
    .prepare("SELECT COUNT(*) as n FROM videos WHERE transcribe_done = 1")
    .get().n;
  const partial = db
    .prepare("SELECT COUNT(*) as n FROM videos WHERE transcribe_partial = 1")
    .get().n;
  const errored = db
    .prepare("SELECT COUNT(*) as n FROM videos WHERE transcribe_error IS NOT NULL")
    .get().n;
  db.close();
  res.json({ total, byDecision, byYear, byParent, transcribed, partial, errored });
});

// ── GET /api/videos ─────────────────────────────────────────────
app.get("/api/videos", (req, res) => {
  const db = getDb();
  const { decision, year, parent, search, transcribed, sort } = req.query;
  const where = [];
  const params = {};
  if (decision) {
    where.push("decision = @decision");
    params.decision = decision;
  }
  if (year) {
    where.push("substr(filmed_date, 1, 4) = @year");
    params.year = year;
  }
  if (parent) {
    where.push("parent_dir = @parent");
    params.parent = parent;
  }
  if (search) {
    where.push(
      "(filename LIKE @s OR rel_path LIKE @s OR title LIKE @s OR description LIKE @s OR tags LIKE @s OR notes LIKE @s)"
    );
    params.s = `%${search}%`;
  }
  if (transcribed === "1") where.push("transcribe_done = 1");
  if (transcribed === "0") where.push("transcribe_done = 0");
  if (transcribed === "partial") where.push("transcribe_partial = 1");
  if (transcribed === "error") where.push("transcribe_error IS NOT NULL");

  const orderBy =
    sort === "size_desc"
      ? "file_size DESC"
      : sort === "size_asc"
      ? "file_size ASC"
      : sort === "filename"
      ? "filename"
      : "COALESCE(filmed_date, '9999'), filename";

  const sql =
    "SELECT id, src_path, rel_path, filename, parent_dir, file_size, " +
    "filmed_date, filmed_date_source, duration_s, " +
    "title, decision, tags, transcribe_done, transcribe_partial, " +
    "transcript_chars, transcribe_error " +
    "FROM videos " +
    (where.length ? "WHERE " + where.join(" AND ") : "") +
    " ORDER BY " +
    orderBy;

  const rows = db.prepare(sql).all(params);
  db.close();
  res.json(rows);
});

// ── GET /api/videos/:id ─────────────────────────────────────────
app.get("/api/videos/:id", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).json({ error: "Video not found" });
  res.json(v);
});

// ── PUT /api/videos/:id ─────────────────────────────────────────
app.put("/api/videos/:id", (req, res) => {
  const db = getDb();
  const cur = db.prepare("SELECT * FROM videos WHERE id = ?").get(req.params.id);
  if (!cur) {
    db.close();
    return res.status(404).json({ error: "Video not found" });
  }

  const editable = ["title", "description", "tags", "decision", "notes"];
  const updates = [];
  const params = { id: req.params.id };
  for (const k of editable) {
    if (k in req.body) {
      updates.push(`${k} = @${k}`);
      params[k] = req.body[k];
    }
  }
  updates.push("updated_at = datetime('now')");

  if (updates.length > 1) {
    db.prepare(`UPDATE videos SET ${updates.join(", ")} WHERE id = @id`).run(params);
  }
  const updated = db.prepare("SELECT * FROM videos WHERE id = ?").get(req.params.id);
  db.close();
  res.json(updated);
});

// ── GET /api/videos/:id/thumb/:n ────────────────────────────────
// n = 0 (start), 1 (middle), 2 (end)
app.get("/api/videos/:id/thumb/:n", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).end();
  const which = parseInt(req.params.n, 10);
  const thumb =
    which === 0 ? v.thumb_start : which === 1 ? v.thumb_middle : v.thumb_end;
  if (!thumb || !fs.existsSync(thumb)) return res.status(404).end();
  res.type("image/jpeg").sendFile(thumb);
});

// ── GET /api/videos/:id/stream ──────────────────────────────────
// Range-aware streaming so HTML5 <video> can scrub. Without Range
// support, the browser would download the whole file before playing.
app.get("/api/videos/:id/stream", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).end();
  if (!fs.existsSync(v.src_path))
    return res.status(404).json({ error: "File not found on disk" });

  const stat = fs.statSync(v.src_path);
  const fileSize = stat.size;
  const range = req.headers.range;
  const ext = path.extname(v.src_path).toLowerCase().replace(".", "");
  const mimeMap = {
    mp4: "video/mp4",
    mov: "video/quicktime",
    mkv: "video/x-matroska",
    webm: "video/webm",
    avi: "video/x-msvideo",
    m4v: "video/mp4",
  };
  const contentType = mimeMap[ext] || "application/octet-stream";

  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
    const chunksize = end - start + 1;
    const file = fs.createReadStream(v.src_path, { start, end });
    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": chunksize,
      "Content-Type": contentType,
    });
    file.pipe(res);
  } else {
    res.writeHead(200, {
      "Content-Length": fileSize,
      "Content-Type": contentType,
    });
    fs.createReadStream(v.src_path).pipe(res);
  }
});

// ── GET /api/videos/:id/transcript ──────────────────────────────
app.get("/api/videos/:id/transcript", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).end();
  if (!v.transcript_path || !fs.existsSync(v.transcript_path))
    return res.status(404).json({ error: "No transcript yet" });
  try {
    const text = fs.readFileSync(v.transcript_path, "utf-8");
    res.type("text/plain").send(text);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── POST /api/videos/:id/open ───────────────────────────────────
// Opens an Explorer window with the file selected. Windows-only path.
app.post("/api/videos/:id/open", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).end();
  if (!fs.existsSync(v.src_path))
    return res.status(404).json({ error: "File not found on disk" });
  // /select, opens Explorer at parent dir with the file highlighted.
  // Quote the path defensively — names contain spaces, dashes, etc.
  exec(`explorer.exe /select,"${v.src_path.replace(/"/g, '\\"')}"`, (err) => {
    // explorer.exe usually returns exit 1 even on success. Don't
    // surface that as an error to the UI.
    res.json({ ok: true });
  });
});

// ── POST /api/videos/:id/play ───────────────────────────────────
// Opens the file in the user's default video player. Useful when the
// embedded HTML5 player can't decode the format (e.g., raw .mts).
app.post("/api/videos/:id/play", (req, res) => {
  const v = safeReadVideo(req.params.id);
  if (!v) return res.status(404).end();
  if (!fs.existsSync(v.src_path))
    return res.status(404).json({ error: "File not found on disk" });
  // start "" "<path>" launches via shell association.
  exec(`start "" "${v.src_path.replace(/"/g, '\\"')}"`, (err) => {
    res.json({ ok: true });
  });
});

// ── GET /api/export ─────────────────────────────────────────────
// Returns the current "mine" set as a plain-text file (one path per
// line) — same format mine_video_journals.py consumes via --from-list.
// Use ?include=mine,unsure to include other decision values.
app.get("/api/export", (req, res) => {
  const db = getDb();
  const include = (req.query.include || "mine").split(",");
  const placeholders = include.map(() => "?").join(",");
  const rows = db
    .prepare(
      `SELECT src_path FROM videos WHERE decision IN (${placeholders}) ORDER BY src_path`
    )
    .all(...include);
  db.close();
  const text = rows.map((r) => r.src_path).join("\r\n") + "\r\n";
  res.type("text/plain").set(
    "Content-Disposition",
    'attachment; filename="selected_paths.txt"'
  );
  res.send(text);
});

// ── POST /api/import ────────────────────────────────────────────
// Re-runs the import script as a child process. Useful after the
// mining run logs new transcript completions, so the UI reflects them.
app.post("/api/import", (req, res) => {
  const { execSync } = require("child_process");
  try {
    execSync("node scripts/import-videos.js", { cwd: __dirname });
    res.json({ ok: true });
  } catch (e) {
    res
      .status(500)
      .json({ error: e.message, stderr: (e.stderr || "").toString().slice(0, 1000) });
  }
});

// ── Start ───────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`Cortex Video Annotator running at http://localhost:${PORT}`);
  console.log("Endpoints: /api/stats, /api/videos, /api/videos/:id, /api/export");
});
