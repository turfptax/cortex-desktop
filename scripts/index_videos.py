"""index_videos.py — fast preview pass over a video library.

For every video, extracts 3 thumbnails (start / middle / end), captures
metadata (path, size, duration, modified), and emits a browseable
HTML index so the user can skim and pick the high-value ones to
fully transcribe later.

Usage:
    python index_videos.py "F:/Video Backups"
    python index_videos.py "F:/Video Backups" --output F:/video_index

Defaults:
  output       = <input>/../video_index
  thumb width  = 320 px
  thumb quality= 6  (good enough for skimming, small files ~30KB each)
  fast-seek    = on (uses -ss before -i for instant seek; ~3s per video)

Output structure:

  <output>/
    thumbnails/
      <relative-path>/<video-name>/
        start.jpg
        middle.jpg
        end.jpg
    index.csv                 # raw manifest, openable in Excel
    index.html                # browseable in any web browser
    state.jsonl               # resumable progress log

Estimated runtime: ~3 sec per video. 1298 videos → ~65 min.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

DEFAULT_VIDEO_EXTS = {
    "mp4", "mov", "mkv", "avi", "m4v", "webm",
    "wmv", "mpg", "mpeg", "3gp", "m2ts", "ts",
}
DEFAULT_MIN_SIZE_MB = 1
DEFAULT_THUMB_WIDTH = 320
DEFAULT_THUMB_QUALITY = 6
PAGE_SIZE = 100  # cards per HTML section


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def fmt_size(b: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def fmt_dur(s: float | None) -> str:
    if not s:
        return "?"
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{int(s)//60}m{int(s)%60:02d}s"
    return f"{int(s)//3600}h{(int(s)%3600)//60:02d}m"


def video_duration_s(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return float(proc.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


# Filename patterns where the date is encoded in the filename. Order
# matters — first match wins. Each tuple: (compiled regex, factory).
import re as _re_for_dates
FILENAME_DATE_PATTERNS = [
    # OBS recordings: "2024-05-24 13-59-00.mp4" / "2024-05-24 13.59.00"
    (_re_for_dates.compile(r"(\d{4})-(\d{2})-(\d{2})[ _-](\d{2})[-.](\d{2})[-.](\d{2})"),
     lambda m: f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"),
    # Sony ZV-E10: "ZV-E10-TTX-20241103_0282.MP4"
    (_re_for_dates.compile(r"(?:ZV-E10|ZV-?\w*?)-?\w*-(\d{8})_\d+", _re_for_dates.IGNORECASE),
     lambda m: f"{m[1][:4]}-{m[1][4:6]}-{m[1][6:8]}"),
    # Samsung Galaxy / Android: "20211211_173328.MOV" / "VID_20211211_173328.mp4"
    (_re_for_dates.compile(r"(?:VID_)?(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})"),
     lambda m: f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"),
    # DJI drone: "DJI_20260123105637_0014_D.MP4"
    (_re_for_dates.compile(r"DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", _re_for_dates.IGNORECASE),
     lambda m: f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"),
    # iPhone screen-record / similar: "IMG_YYYYMMDD_HHMMSS"
    (_re_for_dates.compile(r"IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})"),
     lambda m: f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}"),
    # Bare YYYY-MM-DD anywhere in name: last-resort
    (_re_for_dates.compile(r"(\d{4})-(\d{2})-(\d{2})"),
     lambda m: f"{m[1]}-{m[2]}-{m[3]}"),
]


def filmed_date_from_filename(name: str) -> tuple[str, str] | None:
    """Try filename-pattern matching. Returns (iso_date, source_label)
    or None."""
    for rx, fmt in FILENAME_DATE_PATTERNS:
        m = rx.search(name)
        if m:
            try:
                return fmt(m), "filename"
            except (IndexError, ValueError):
                pass
    return None


def filmed_date_from_ffprobe(path: Path) -> tuple[str, str] | None:
    """Read camera-recorded creation_time metadata. Most modern
    cameras embed this in MP4/MOV headers as ISO 8601."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format_tags=creation_time:stream_tags=creation_time",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        for ln in (proc.stdout or "").splitlines():
            ln = ln.strip()
            if ln and ln != "N/A":
                # Normalize trailing Z and microseconds
                ln = ln.replace("Z", "").rstrip()
                # Validate by parsing
                try:
                    datetime.strptime(ln[:19], "%Y-%m-%dT%H:%M:%S")
                    return ln, "ffprobe"
                except ValueError:
                    try:
                        datetime.strptime(ln[:19], "%Y-%m-%d %H:%M:%S")
                        return ln.replace(" ", "T"), "ffprobe"
                    except ValueError:
                        continue
    except subprocess.SubprocessError:
        pass
    return None


def extract_filmed_date(path: Path) -> tuple[str, str]:
    """Returns (iso_date_or_empty, source). Source is one of:
    'filename', 'ffprobe', 'mtime', 'unknown'.
    Filename takes priority because it's the source the user named the
    file with — if they renamed, that's the date they meant. ffprobe
    creation_time is next (camera metadata). mtime is last resort."""
    fn = filmed_date_from_filename(path.name)
    if fn:
        return fn
    pr = filmed_date_from_ffprobe(path)
    if pr:
        return pr
    try:
        return (
            datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "mtime",
        )
    except OSError:
        return ("", "unknown")


def extract_thumbnail(src: Path, dst: Path, ts: float,
                      width: int, quality: int) -> bool:
    """Extract one frame at the given timestamp. Fast-seek mode (less
    accurate but instant — fine for preview thumbs)."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(ts, 0):.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", str(quality),
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        return proc.returncode == 0 and dst.is_file()
    except subprocess.TimeoutExpired:
        return False


def walk_videos(root: Path, exts: set[str], min_b: int) -> list[Path]:
    out = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lstrip(".").lower() not in exts:
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        if sz < min_b:
            continue
        out.append(f)
    return sorted(out)


def load_state(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    idx: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                src = r.get("src_path")
                if src:
                    idx.setdefault(src, {}).update(r)
            except json.JSONDecodeError:
                continue
    return idx


def append_state(path: Path, rec: dict) -> None:
    rec.setdefault("ts", datetime.utcnow().isoformat() + "Z")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def index_one(src: Path, root: Path, output: Path,
              width: int, quality: int) -> dict:
    rel = src.relative_to(root)
    thumb_dir = output / "thumbnails" / rel.parent / src.stem
    thumb_dir.mkdir(parents=True, exist_ok=True)
    duration = video_duration_s(src)

    targets = [
        ("start.jpg", 1.0),
        ("middle.jpg", (duration / 2) if duration and duration > 4 else 2.0),
        ("end.jpg", max((duration - 2) if duration else 3.0, 2.0)),
    ]
    extracted = []
    for name, ts in targets:
        ok = extract_thumbnail(src, thumb_dir / name, ts, width, quality)
        extracted.append(ok)

    try:
        st = src.stat()
        size = st.st_size
        modified = datetime.fromtimestamp(st.st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except OSError:
        size = 0
        modified = ""

    filmed_date, filmed_source = extract_filmed_date(src)

    return {
        "src_path": str(src),
        "rel_path": str(rel).replace("\\", "/"),
        "name": src.name,
        "stem": src.stem,
        "size_bytes": size,
        "size_human": fmt_size(size),
        "duration_s": duration,
        "duration_human": fmt_dur(duration),
        "modified_at": modified,
        "filmed_date": filmed_date,        # ISO 8601, may include time
        "filmed_source": filmed_source,    # filename | ffprobe | mtime | unknown
        "thumbs_extracted": sum(extracted),
        "thumbs_dir": str(thumb_dir),
    }


# ── HTML index ────────────────────────────────────────────────────

HTML_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Video Index — {root}</title>
<style>
  body { font-family: -apple-system,Segoe UI,Helvetica,sans-serif;
         background: #111; color: #ddd; margin: 0; padding: 16px; }
  h1 { font-size: 18px; color: #fff; margin: 0 0 16px; }
  h2 { font-size: 16px; color: #fff; margin: 24px 0 8px;
       border-bottom: 1px solid #333; padding-bottom: 4px; }
  .toolbar { position: sticky; top: 0; background: #111; padding: 8px 0;
             z-index: 10; border-bottom: 1px solid #222; margin-bottom: 8px;
             display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .toolbar input { background: #222; color: #ddd; border: 1px solid #444;
                   padding: 6px 10px; width: 320px; border-radius: 4px; }
  .toolbar button { background: #2a2a2a; color: #ddd; border: 1px solid #444;
                    padding: 6px 12px; border-radius: 4px; cursor: pointer;
                    font-size: 12px; }
  .toolbar button:hover { background: #3a3a3a; border-color: #666; }
  .toolbar button.primary { background: #2d5a87; border-color: #4a7ab0; color: #fff; }
  .toolbar button.primary:hover { background: #3d6a97; }
  .stats { font-size: 12px; color: #888; }
  .stats b { color: #fff; }
  .stats .mine { color: #6c6; }
  .stats .skip { color: #c66; }
  .stats .unsure { color: #cc6; }
  .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a;
          border-radius: 6px; padding: 8px; transition: border-color 0.15s; }
  .card.state-mine { border-color: #6c6; background: #182a18; }
  .card.state-skip { border-color: #c66; background: #2a1818; opacity: 0.6; }
  .card.state-unsure { border-color: #cc6; background: #2a2a18; }
  .card .meta { font-size: 11px; color: #888; margin-bottom: 6px;
                font-family: monospace; word-break: break-all; }
  .card .name { font-size: 13px; color: #fff; margin-bottom: 4px;
                font-weight: 500; }
  .card .imgs { display: grid; grid-template-columns: repeat(3, 1fr);
                gap: 4px; margin: 6px 0; }
  .card .imgs img { width: 100%; height: 110px; object-fit: cover;
                    background: #000; border-radius: 3px; cursor: zoom-in; }
  .card .imgs .label { font-size: 10px; color: #666; text-align: center; }
  .stats-row { font-size: 11px; color: #999; margin-top: 4px;
               display: flex; justify-content: space-between; align-items: center; }
  .stats-row b { color: #ddd; }
  .actions { display: flex; gap: 4px; }
  .actions button { background: #2a2a2a; color: #aaa; border: 1px solid #3a3a3a;
                    padding: 3px 10px; border-radius: 4px; cursor: pointer;
                    font-size: 11px; }
  .actions button.active.mine   { background: #285028; border-color: #4a7; color: #cfc; }
  .actions button.active.skip   { background: #502828; border-color: #a47; color: #fcc; }
  .actions button.active.unsure { background: #4f4f28; border-color: #aa7; color: #ffc; }
  /* Lightbox overlay for clicked thumbnails */
  #lightbox { display: none; position: fixed; top: 0; left: 0; width: 100%;
              height: 100%; background: rgba(0,0,0,0.92); z-index: 100;
              justify-content: center; align-items: center; cursor: zoom-out; }
  #lightbox.show { display: flex; }
  #lightbox img { max-width: 95%; max-height: 95%; object-fit: contain; }
</style>
<script>
const STATE_KEY = 'vidx_v2';

function loadState() {
  try { return JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveState(s) {
  localStorage.setItem(STATE_KEY, JSON.stringify(s));
}
let cardState = loadState();

function applyState(card) {
  const id = card.dataset.id;
  const s = cardState[id] || '';
  card.classList.remove('state-mine', 'state-skip', 'state-unsure');
  if (s) card.classList.add('state-' + s);
  card.querySelectorAll('.actions button').forEach(b => {
    b.classList.toggle('active', b.dataset.state === s);
  });
}

function setState(id, newState) {
  const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  const cur = cardState[id] || '';
  cardState[id] = (cur === newState) ? '' : newState;
  if (!cardState[id]) delete cardState[id];
  saveState(cardState);
  applyState(card);
  updateStats();
}

function filt() {
  const q = document.getElementById('q').value.toLowerCase();
  const showState = document.getElementById('show-state').value;
  document.querySelectorAll('.card').forEach(c => {
    const matchQ = c.dataset.search.includes(q);
    const s = cardState[c.dataset.id] || '';
    const matchState = (showState === 'all') || (s === showState)
                       || (showState === 'unmarked' && !s);
    c.style.display = (matchQ && matchState) ? '' : 'none';
  });
  updateStats();
}

function bulkSetVisible(state) {
  document.querySelectorAll('.card').forEach(c => {
    if (c.style.display !== 'none') {
      cardState[c.dataset.id] = state;
      applyState(c);
    }
  });
  saveState(cardState);
  updateStats();
}

function bulkClearVisible() {
  document.querySelectorAll('.card').forEach(c => {
    if (c.style.display !== 'none') {
      delete cardState[c.dataset.id];
      applyState(c);
    }
  });
  saveState(cardState);
  updateStats();
}

function exportSelected() {
  const lines = [];
  Object.entries(cardState).forEach(([id, s]) => {
    if (s === 'mine') lines.push(id);
  });
  if (!lines.length) {
    alert('Nothing marked "mine" yet. Mark some cards first.');
    return;
  }
  const txt = lines.join('\\n') + '\\n';
  const blob = new Blob([txt], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'selected_paths.txt';
  a.click();
  URL.revokeObjectURL(url);
}

function updateStats() {
  let mine = 0, skip = 0, unsure = 0, total = 0, visible = 0;
  document.querySelectorAll('.card').forEach(c => {
    total++;
    if (c.style.display !== 'none') visible++;
    const s = cardState[c.dataset.id] || '';
    if (s === 'mine') mine++;
    else if (s === 'skip') skip++;
    else if (s === 'unsure') unsure++;
  });
  document.getElementById('stat-counts').innerHTML =
    `<b>${visible}</b>/${total} visible · ` +
    `<span class="mine">${mine} mine</span> · ` +
    `<span class="skip">${skip} skip</span> · ` +
    `<span class="unsure">${unsure} unsure</span>`;
}

function showLightbox(src) {
  const lb = document.getElementById('lightbox');
  document.getElementById('lightbox-img').src = src;
  lb.classList.add('show');
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.card').forEach(c => applyState(c));
  document.querySelectorAll('.card .imgs img').forEach(img => {
    img.addEventListener('click', () => showLightbox(img.src));
  });
  document.getElementById('lightbox').addEventListener('click', () => {
    document.getElementById('lightbox').classList.remove('show');
  });
  updateStats();
});
</script>
</head><body>
<h1>Video Index — {root}</h1>
<div class="toolbar">
  <input id="q" placeholder="Filter (path/filename)..." oninput="filt()" />
  <select id="show-state" onchange="filt()" style="background:#222;color:#ddd;border:1px solid #444;padding:6px;border-radius:4px">
    <option value="all">all states</option>
    <option value="unmarked">unmarked only</option>
    <option value="mine">mine only</option>
    <option value="skip">skip only</option>
    <option value="unsure">unsure only</option>
  </select>
  <button onclick="bulkSetVisible('mine')">✓ Mine all visible</button>
  <button onclick="bulkSetVisible('skip')">✗ Skip all visible</button>
  <button onclick="bulkClearVisible()">Clear visible</button>
  <button class="primary" onclick="exportSelected()">⬇ Export "mine" list</button>
  <span id="stat-counts" class="stats"></span>
</div>
<div id="lightbox"><img id="lightbox-img" /></div>
"""

HTML_TAIL = "</body></html>"


def write_html(output: Path, root: Path, manifest: list[dict]) -> Path:
    """Generate the browseable index.html. Sectioned by top-level dir."""
    out = output / "index.html"

    # Group by top-level subdir of the root
    sections: dict[str, list[dict]] = {}
    for m in manifest:
        rel = m["rel_path"]
        top = rel.split("/", 1)[0] if "/" in rel else "(root)"
        sections.setdefault(top, []).append(m)

    total_size = fmt_size(sum(m["size_bytes"] for m in manifest))
    head = (HTML_HEAD
            .replace("{root}", html.escape(str(root)))
            .replace("{n}", str(len(manifest)))
            .replace("{total_size}", total_size))

    parts = [head]
    for section_name in sorted(sections):
        items = sections[section_name]
        sec_size = fmt_size(sum(m["size_bytes"] for m in items))
        parts.append(
            f'<h2>{html.escape(section_name)} '
            f'<span class="stats">— {len(items)} files, {sec_size}</span></h2>'
        )
        parts.append('<div class="grid">')
        for m in items:
            thumb_rel = "thumbnails/" + m["rel_path"].rsplit(".", 1)[0]
            search = (m["rel_path"] + " " + m["name"]).lower()
            # Use src_path as the stable id (gets stored in localStorage
            # AND written verbatim into selected_paths.txt for downstream
            # mining via --from-list)
            id_attr = html.escape(m["src_path"], quote=True)
            parts.append(
                f'<div class="card" '
                f'data-id="{id_attr}" '
                f'data-search="{html.escape(search)}">'
            )
            parts.append(
                f'<div class="name">{html.escape(m["name"])}</div>'
            )
            parts.append(
                f'<div class="meta">{html.escape(m["rel_path"])}</div>'
            )
            parts.append('<div class="imgs">')
            for label, name in (("start", "start.jpg"),
                                ("middle", "middle.jpg"),
                                ("end", "end.jpg")):
                src = quote(thumb_rel + "/" + name)
                parts.append(
                    f'<div><img src="{src}" loading="lazy" '
                    f'alt="{label}"/>'
                    f'<div class="label">{label}</div></div>'
                )
            parts.append('</div>')
            # Stats row + action buttons
            esc_id = m["src_path"].replace("\\", "\\\\").replace("'", "\\'")
            filmed = (m.get("filmed_date") or "")[:19]  # trim subseconds
            filmed_src = m.get("filmed_source") or "unknown"
            filmed_label = (
                f'filmed: {html.escape(filmed)} <span style="color:#666">'
                f'({filmed_src})</span>'
                if filmed
                else '<span style="color:#666">filmed: unknown</span>'
            )
            parts.append(
                f'<div class="stats-row">'
                f'<span><b>{html.escape(m["size_human"])}</b> · '
                f'{html.escape(m["duration_human"])} · '
                f'{filmed_label}</span>'
                f'<span class="actions">'
                f'<button data-state="mine" class="mine" '
                f'  onclick="setState(\'{esc_id}\',\'mine\')">✓ mine</button>'
                f'<button data-state="unsure" class="unsure" '
                f'  onclick="setState(\'{esc_id}\',\'unsure\')">?</button>'
                f'<button data-state="skip" class="skip" '
                f'  onclick="setState(\'{esc_id}\',\'skip\')">✗ skip</button>'
                f'</span>'
                f'</div>'
            )
            parts.append('</div>')  # card
        parts.append('</div>')  # grid

    parts.append(HTML_TAIL)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# ── Main ──────────────────────────────────────────────────────────

def main() -> int:
    # Windows cp1252 default can't encode non-ASCII filenames (CJK,
    # fullwidth, etc.). Force UTF-8 with errors='replace' so a single
    # weird filename doesn't kill the run mid-corpus.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("input")
    p.add_argument("--output")
    p.add_argument("--video-exts",
                   default=",".join(sorted(DEFAULT_VIDEO_EXTS)))
    p.add_argument("--min-size-mb", type=int, default=DEFAULT_MIN_SIZE_MB)
    p.add_argument("--thumb-width", type=int, default=DEFAULT_THUMB_WIDTH)
    p.add_argument("--thumb-quality", type=int, default=DEFAULT_THUMB_QUALITY)
    p.add_argument("--limit", type=int, help="Process at most N videos")
    p.add_argument("--rebuild-html-only", action="store_true",
                   help="Skip thumbnail extraction; just regenerate index.html "
                        "from existing state.jsonl")
    p.add_argument("--enrich-only", action="store_true",
                   help="Skip thumbnails; for entries in state.jsonl that "
                        "lack filmed_date, re-extract metadata only and "
                        "rebuild index.html. Preserves localStorage marks.")
    args = p.parse_args()

    root = Path(args.input).resolve()
    if not root.is_dir():
        print(f"ERR: {root} not found", file=sys.stderr)
        return 1

    output = Path(args.output).resolve() if args.output else (
        root.parent / "video_index"
    )
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.jsonl"

    if not have("ffmpeg"):
        print("ERR: ffmpeg not on PATH", file=sys.stderr)
        return 2
    if not have("ffprobe"):
        print("WARN: ffprobe not on PATH; durations will be missing")

    exts = {e.strip().lower().lstrip(".")
            for e in args.video_exts.split(",") if e.strip()}
    min_b = args.min_size_mb * 1024 * 1024

    print(f"Scanning {root}...")
    videos = walk_videos(root, exts, min_b)
    print(f"Found {len(videos)} videos (>={args.min_size_mb} MB)")
    print(f"Output: {output}")
    print()

    if args.rebuild_html_only:
        idx = load_state(state_path)
        manifest = list(idx.values())
        if not manifest:
            print("ERR: no state.jsonl yet; run without --rebuild-html-only first")
            return 1
        out = write_html(output, root, manifest)
        print(f"Wrote {out} ({len(manifest)} videos)")
        return 0

    if args.enrich_only:
        idx = load_state(state_path)
        if not idx:
            print("ERR: no state.jsonl yet; run a normal pass first")
            return 1
        n_total = len(idx)
        need_filmed = [k for k, v in idx.items()
                       if not v.get("filmed_date")]
        print(f"{n_total} entries; {len(need_filmed)} need filmed_date")
        for i, src_str in enumerate(need_filmed, 1):
            v = Path(src_str)
            if not v.is_file():
                # Stale entry (file moved/deleted) — write empty filmed_date
                idx[src_str]["filmed_date"] = ""
                idx[src_str]["filmed_source"] = "unknown"
                continue
            d, src = extract_filmed_date(v)
            idx[src_str]["filmed_date"] = d
            idx[src_str]["filmed_source"] = src
            # Append to state.jsonl as a new event (resumable)
            append_state(state_path, {
                "src_path": src_str,
                "filmed_date": d,
                "filmed_source": src,
                "phase": "metadata_enriched",
            })
            sys.stdout.write(
                f"\r\033[K[{i:>4}/{len(need_filmed)}] {src} "
                f"{d[:19] if d else '(unknown)':>19}  "
                f"{Path(src_str).name[:60]}"
            )
            sys.stdout.flush()
        sys.stdout.write("\n\n")
        # Rewrite CSV with new columns
        csv_path = output / "index.csv"
        cols = ["rel_path", "name", "size_bytes", "size_human",
                "duration_s", "duration_human", "filmed_date",
                "filmed_source", "modified_at",
                "thumbs_extracted", "src_path"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for rec in idx.values():
                w.writerow(rec)
        print(f"CSV manifest: {csv_path}")
        out = write_html(output, root, list(idx.values()))
        print(f"HTML index:  {out}")
        # Summary by source
        from collections import Counter
        sources = Counter(v.get("filmed_source", "unknown")
                          for v in idx.values())
        print("\n--- filmed_date source breakdown ---")
        for src, n in sources.most_common():
            print(f"  {src}: {n}")
        return 0

    # Resume from prior state
    state = load_state(state_path)

    to_process: list[Path] = []
    skipped = 0
    for v in videos:
        if str(v) in state and state[str(v)].get("thumbs_extracted", 0) >= 1:
            skipped += 1
            continue
        to_process.append(v)
    print(f"To process: {len(to_process)}  (skipping {skipped} already done)")
    if args.limit:
        to_process = to_process[:args.limit]
        print(f"--limit {args.limit} -> first {len(to_process)} only")
    print()

    started = time.time()
    try:
        for i, v in enumerate(to_process, 1):
            t0 = time.time()
            try:
                rec = index_one(v, root, output,
                                args.thumb_width, args.thumb_quality)
            except Exception as e:
                rec = {
                    "src_path": str(v),
                    "rel_path": str(v.relative_to(root)).replace("\\", "/"),
                    "name": v.name,
                    "error": str(e),
                    "thumbs_extracted": 0,
                }
            append_state(state_path, rec)
            state[str(v)] = rec
            elapsed = time.time() - t0
            rel = rec.get("rel_path", v.name)
            sz = rec.get("size_human", "?")
            dur = rec.get("duration_human", "?")
            n_thumbs = rec.get("thumbs_extracted", 0)
            sys.stdout.write(
                f"\r\033[K[{i:>4}/{len(to_process)}] {n_thumbs}/3 thumbs "
                f"{elapsed:>4.1f}s  {sz:>9}  {dur:>7}  {rel[:80]}"
            )
            sys.stdout.flush()
            if i % 50 == 0:
                # Periodic HTML rebuild so user can browse partial results
                write_html(output, root, list(state.values()))
    except KeyboardInterrupt:
        sys.stdout.write("\n  interrupted — state saved, re-run to resume\n")
    sys.stdout.write("\n\n")

    elapsed = time.time() - started
    print(f"DONE  total: {elapsed:.0f}s  ({elapsed/60:.1f} min)")

    # CSV manifest
    csv_path = output / "index.csv"
    cols = ["rel_path", "name", "size_bytes", "size_human",
            "duration_s", "duration_human", "filmed_date",
            "filmed_source", "modified_at",
            "thumbs_extracted", "src_path"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for rec in state.values():
            w.writerow(rec)
    print(f"CSV manifest: {csv_path}")

    # Final HTML
    html_path = write_html(output, root, list(state.values()))
    print(f"HTML index:  {html_path}")
    print()
    print("Open the HTML in your browser to skim. Use the filter box at top.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
