"""mine_video_journals.py — walk a directory of video files, extract
1-frame-per-minute screenshots, and POST each video to the local
Cortex Hub for transcription via whisper.cpp on Vulkan.

Designed to run for hours on a folder full of unedited journal
videos. Resumable, error-tolerant, periodic state backups.

Usage:

    # Phase A — walk + screenshots + transcribe everything
    python mine_video_journals.py "F:/Video Backups"

    # Custom output
    python mine_video_journals.py "F:/Video Backups" --output D:/mined

    # Just scan, don't process
    python mine_video_journals.py "F:/Video Backups" --scan-only

    # Re-do anything that errored last time
    python mine_video_journals.py "F:/Video Backups" --retry-errors

    # Re-process specific files (matches substring against src_path)
    python mine_video_journals.py "F:/Video Backups" --reprocess 2023

Defaults:
  output       =  <input>/../video_mining_output
  screenshots  =  every 60s, 640px wide, mid-quality JPEG
  hub          =  http://127.0.0.1:8003
  video exts   =  mp4 mov mkv avi m4v webm wmv mpg mpeg 3gp m2ts ts
  min size     =  1 MB (skip thumbnails / placeholder files)
  state backup =  every 25 entries OR 30 minutes, keep last 10

Stop with Ctrl+C anytime. Re-run to resume — state file knows what's
already done.

Output structure:

  <output>/
    transcripts/
      <relative-path>/<name>.txt        # plain transcript (concatenated)
      <relative-path>/<name>.json       # chunk plan + GPU stats
      <relative-path>/<name>_chunks/    # per-chunk audit trail
        chunk_001.txt
        chunk_002.txt
    audio/
      <relative-path>/<name>.mp3        # source mp3 (deleted on success)
      <relative-path>/<name>_chunks/    # only when split is needed
        chunk_001.mp3                   # deleted on success, kept on failure
        chunk_002.mp3
    screenshots/
      <relative-path>/<name>/
        00_00_00.jpg
        00_01_00.jpg
        ...
    state.jsonl                         # append-only log
    state-index.json                    # current per-file status (derived)
    backups/
      state-YYYYMMDD-HHMMSS.jsonl
    errors.log

Chunked transcribe pipeline (added 2026-05-07): each video's mp3 is
split into <=30-min segments before submitting to the Hub. Whisper.cpp
can hang on long audio with hard-to-decode stretches; capping at 30 min
bounds the damage so a single bad chunk fails alone, the rest of the
video transcribes, and resume only retries the failed chunks. mp3s are
deleted on full success and kept on any failure for next-pass retry.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ── Defaults ────────────────────────────────────────────────────

DEFAULT_HUB = "http://127.0.0.1:8003"
DEFAULT_VIDEO_EXTS = {
    "mp4", "mov", "mkv", "avi", "m4v", "webm",
    "wmv", "mpg", "mpeg", "3gp", "m2ts", "ts",
}
DEFAULT_MIN_SIZE_MB = 1
DEFAULT_SCREENSHOT_INTERVAL_S = 60
DEFAULT_SCREENSHOT_WIDTH = 640
DEFAULT_SCREENSHOT_QUALITY = 5  # ffmpeg -q:v scale 1 (best) – 31 (worst)
DEFAULT_BACKUP_EVERY_N = 25
DEFAULT_BACKUP_EVERY_S = 30 * 60
DEFAULT_BACKUP_KEEP_LAST = 10
POLL_INTERVAL_S = 3.0
TRANSCRIBE_MAX_WAIT_S = 6 * 3600  # 6 hours per file (very long videos)
# Stuck-progress watchdog: if progress_pct doesn't advance for this
# many seconds, treat as hung. Whisper.cpp with temperature fallback
# can iterate on hard stretches without emitting progress updates,
# but typically not for more than a couple of minutes. 5 min is a
# safe upper bound that won't false-trip on legitimate slow chunks.
STUCK_PROGRESS_TIMEOUT_S = 300
# Hard cap per video: max(15 min, 4x audio duration). A 35-min audio
# file gets up to 2h20m before we give up entirely. At expected ~11x
# realtime the typical run is ~3 min for a 35-min file; 4x gives
# generous slack for occasional slow stretches without being
# unbounded.
PER_VIDEO_TIMEOUT_FLOOR_S = 15 * 60
PER_VIDEO_TIMEOUT_DURATION_MULT = 4.0
# Chunk audio into ≤N-minute pieces before transcribing. Whisper.cpp's
# temperature-fallback decoder can hang on long audio with hard
# stretches (Tory observed repeated hangs at 66% on a 1.5 GB OBS file
# even with the 5-min stuck-progress watchdog). Capping the input
# bounds the damage from any one bad segment and lets us resume
# mid-video if a chunk fails — the good chunks stay transcribed, only
# the failed one needs re-running.
#
# Default 15 min: lowered from 30 min after Tory's first chunked test
# failed at minute 24 of a 30-min chunk (whisper.cpp got stuck at 80%).
# A 15-min chunk that hangs at minute 12 only burns 12 min of audio
# before the watchdog kills it; the next 15-min chunk is fresh ground
# for whisper, no compounded fallback state.
CHUNK_MAX_DURATION_S = 15 * 60


# ── Terminal helpers ─────────────────────────────────────────────

# Force UTF-8 stdout on Windows so box-drawing/CJK don't crash. With
# errors='replace' a single weird filename never kills a multi-hour run.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ANSI_CLEAR_LINE = "\r\033[K"
USE_ANSI = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
# Detect whether stdout can render Unicode box drawing — fall back to
# ASCII if the encoding can't handle them.
try:
    "═".encode(sys.stdout.encoding or "utf-8")
    USE_UNICODE_BOX = True
except (UnicodeEncodeError, LookupError):
    USE_UNICODE_BOX = False

# Box characters with ASCII fallback
if USE_UNICODE_BOX:
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "╔", "╗", "╚", "╝"
    BOX_H, BOX_V = "═", "║"
    BULLET = "·"
else:
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "+", "+", "+", "+"
    BOX_H, BOX_V = "=", "|"
    BULLET = "-"

# Color codes (no-op when not a TTY)
def _c(code: str) -> str:
    return f"\033[{code}m" if USE_ANSI else ""

C_RESET = _c("0")
C_DIM = _c("2")
C_BOLD = _c("1")
C_GREEN = _c("32")
C_YELLOW = _c("33")
C_RED = _c("31")
C_CYAN = _c("36")
C_BLUE = _c("34")

ICON_OK = "[ok]" if not USE_ANSI else f"{C_GREEN}✓{C_RESET}"
ICON_ERR = "[!]" if not USE_ANSI else f"{C_RED}✗{C_RESET}"
ICON_WARN = "[?]" if not USE_ANSI else f"{C_YELLOW}!{C_RESET}"
ICON_RUN = "[*]" if not USE_ANSI else f"{C_CYAN}…{C_RESET}"


def line(s: str = "", *, replace: bool = False) -> None:
    """Print a line. If replace, overwrite the current line in place."""
    if replace and USE_ANSI:
        sys.stdout.write(ANSI_CLEAR_LINE + s)
    elif replace:
        sys.stdout.write("\r" + s + " " * 10)
    else:
        sys.stdout.write(s + "\n")
    sys.stdout.flush()


def fmt_size(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{int(s)//60}m{int(s)%60:02d}s"
    return f"{int(s)//3600}h{(int(s)%3600)//60:02d}m"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Run logger ───────────────────────────────────────────────────

_run_log: logging.Logger | None = None


def setup_run_logger(output: Path) -> logging.Logger:
    """File logger for unattended-run audit trail. Writes to
    <output>/run.log with timestamped events (each video start, each
    stage outcome, errors, summaries)."""
    global _run_log
    log = logging.getLogger("video_mining")
    log.setLevel(logging.INFO)
    # Clear any prior handlers (re-runs in same process)
    log.handlers.clear()
    fh = logging.FileHandler(output / "run.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(fh)
    log.propagate = False
    _run_log = log
    return log


def rlog(msg: str, level: str = "INFO") -> None:
    if _run_log:
        getattr(_run_log, level.lower())(msg)


# ── Snapshot / status ───────────────────────────────────────────

def compute_snapshot(state_index: dict[str, dict],
                     wanted: set[str] | None = None) -> dict:
    """Derive run statistics from state.jsonl-rebuilt index. If wanted
    is given (from --from-list), restrict to that set."""
    keys = wanted if wanted is not None else set(state_index.keys())
    stats = {
        "total": len(keys),
        "transcribe_done": 0,
        "audio_extracted": 0,
        "screenshots_done": 0,
        "errors": 0,
        "pending": 0,
        "transcript_chars_total": 0,
        "audio_bytes_total": 0,
        "transcribe_ms_total": 0,
        "errors_by_stage": Counter(),
    }
    for k in keys:
        rec = state_index.get(k, {})
        if rec.get("transcribe_done"):
            stats["transcribe_done"] += 1
            stats["transcript_chars_total"] += rec.get("transcript_chars", 0) or 0
            stats["transcribe_ms_total"] += rec.get("transcribe_latency_ms", 0) or 0
        elif rec.get("phase") == "error":
            stats["errors"] += 1
            stats["errors_by_stage"][rec.get("stage", "?")] += 1
        else:
            stats["pending"] += 1
        if rec.get("audio_extracted"):
            stats["audio_extracted"] += 1
            stats["audio_bytes_total"] += rec.get("audio_size", 0) or 0
        if rec.get("screenshots_done"):
            stats["screenshots_done"] += 1
    return stats


def print_status(state_index: dict[str, dict],
                 wanted: set[str] | None = None,
                 title: str = "STATUS SNAPSHOT") -> None:
    s = compute_snapshot(state_index, wanted)
    bar = "─" * 60
    line(f"\n{C_BOLD}{title}{C_RESET}")
    line(bar)
    line(f"  Total tracked   : {s['total']}")
    line(
        f"  {ICON_OK} done            : {C_GREEN}{s['transcribe_done']}{C_RESET}"
        f"  ({fmt_size(s['audio_bytes_total'])} audio, "
        f"{s['transcript_chars_total']:,} chars, "
        f"{fmt_dur(s['transcribe_ms_total']/1000)} GPU)"
    )
    line(f"  {ICON_ERR} errors          : {C_RED}{s['errors']}{C_RESET}")
    if s["errors_by_stage"]:
        for stg, n in s["errors_by_stage"].most_common():
            line(f"      {stg:20s}: {n}")
    line(f"  {ICON_RUN} pending         : {C_YELLOW}{s['pending']}{C_RESET}")
    if s["total"]:
        pct = 100 * s["transcribe_done"] / s["total"]
        line(f"  Progress        : {pct:.1f}%")
    line(bar)


# ── ffmpeg helpers ───────────────────────────────────────────────

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def video_duration_s(path: Path) -> float | None:
    """Use ffprobe to get duration in seconds; None on failure."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return float(proc.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


def extract_screenshots(
    src: Path, dst_dir: Path, interval_s: int,
    width: int, quality: int,
) -> tuple[int, str | None]:
    """Run ffmpeg -vf fps=1/N to dump JPEGs. Returns (frame_count, error)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"fps=1/{interval_s},scale={width}:-2",
        "-q:v", str(quality),
        str(dst_dir / "frame_%05d.jpg"),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
    except subprocess.TimeoutExpired:
        return 0, "ffmpeg timed out (>1h)"
    if proc.returncode != 0:
        return 0, (proc.stderr or "")[-500:].strip()
    n = sum(1 for _ in dst_dir.glob("frame_*.jpg"))
    return n, None


def extract_audio(src: Path, dst: Path) -> tuple[int, str | None]:
    """Extract audio as 32 kbps mono mp3 — small enough to upload to
    the Hub even for hour-long videos (the Hub has a 500 MB cap; a
    36-min video → ~1 MB mp3). Whisper.cpp downsamples to 16 kHz
    internally; encoding at 32 kbps mono is plenty for transcription
    quality. Returns (output_size_bytes, error)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vn",                 # drop video
        "-ac", "1",            # mono
        "-ar", "16000",        # 16 kHz (whisper's native rate)
        "-c:a", "libmp3lame",
        "-b:a", "32k",         # 32 kbps mono — voice-friendly
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=3600)
    except subprocess.TimeoutExpired:
        return 0, "ffmpeg audio extract timed out (>1h)"
    if proc.returncode != 0:
        return 0, (proc.stderr or "")[-500:].strip()
    if not dst.is_file():
        return 0, "ffmpeg succeeded but output mp3 missing"
    return dst.stat().st_size, None


def split_audio_into_chunks(
    audio_path: Path, chunk_dir: Path,
    max_chunk_s: int = CHUNK_MAX_DURATION_S,
) -> tuple[list[Path], float, str | None]:
    """Split an mp3 into ≤max_chunk_s segments using ffmpeg `-f segment
    -c copy`. mp3 has no keyframes per se (every frame is independently
    decodable) so segments split cleanly at the requested boundary, with
    only ~26 ms (one mp3 frame) of imprecision per cut — irrelevant for
    transcription.

    Returns (chunk_paths_sorted, total_duration_s, error). When the
    audio is shorter than max_chunk_s + 60s slop, returns
    ([audio_path], duration, None) — no split needed; the source file
    is its own (only) chunk and the caller should treat it as such.

    Idempotent: wipes any prior chunk_*.mp3 files in chunk_dir before
    running, so a partial split from a previous failed run doesn't
    pollute the result."""
    duration = video_duration_s(audio_path)
    if duration is None:
        return [], 0.0, "could not determine audio duration via ffprobe"
    # Don't split a 30:30 file into a 30-min + 30-sec chunk pair.
    if duration <= max_chunk_s + 60:
        return [audio_path], duration, None
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for old in chunk_dir.glob("chunk_*.mp3"):
        try:
            old.unlink()
        except OSError:
            pass
    out_pattern = chunk_dir / "chunk_%03d.mp3"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", str(max_chunk_s),
        "-c", "copy",
        "-reset_timestamps", "1",
        str(out_pattern),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=1800)
    except subprocess.TimeoutExpired:
        return [], duration, "ffmpeg chunk split timed out (>30m)"
    if proc.returncode != 0:
        return [], duration, (proc.stderr or "")[-500:].strip()
    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    if not chunks:
        return [], duration, "ffmpeg ran clean but no chunk files appeared"
    return chunks, duration, None


def concat_chunk_transcripts(
    chunk_txts: list[Path], chunk_durations_s: list[float],
    chunk_results: list[dict] | None = None,
) -> str:
    """Glue per-chunk transcripts into one document with boundary
    markers. The marker tells humans (and downstream synthesis) that
    a transition happened and at roughly what point in the source
    audio — useful when context spans a cut. When chunk_results is
    provided and a chunk's `ok` is False, insert an inline failure
    marker rather than the (missing) transcript text."""
    parts: list[str] = []
    elapsed = 0.0
    for i, (txt, dur) in enumerate(zip(chunk_txts, chunk_durations_s), 1):
        if i > 1:
            hh = int(elapsed) // 3600
            mm = (int(elapsed) % 3600) // 60
            parts.append(
                f"\n\n--- chunk {i:02d} (start ~{hh:02d}:{mm:02d}) ---\n\n"
            )
        if chunk_results and i <= len(chunk_results) and not chunk_results[i - 1].get("ok"):
            err = (chunk_results[i - 1].get("error") or "unknown")[:200]
            parts.append(f"[chunk {i} FAILED: {err}]")
            elapsed += dur
            continue
        try:
            parts.append(txt.read_text(encoding="utf-8").strip())
        except OSError:
            parts.append(f"[chunk {i} unreadable]")
        elapsed += dur
    return "".join(parts).strip()


# ── Hub interaction ──────────────────────────────────────────────

def post_transcribe(hub: str, audio_path: Path) -> dict:
    """POST an audio file to /api/transcribe (multipart). Returns the
    parsed JSON response. Raises if the response indicates an error
    or doesn't show 'transcribing' status."""
    proc = subprocess.run(
        [
            "curl", "-sS", "-X", "POST",
            f"{hub}/api/transcribe",
            "-F", f"file=@{audio_path};type=audio/mpeg",
        ],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"curl failed (exit {proc.returncode}): "
            f"{(proc.stderr or '')[:500]}"
        )
    try:
        resp = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"unparseable Hub response: {proc.stdout[:500]}"
        )
    # Validate: Hub returns {"ok": true, "status": "transcribing", ...}
    # on accepted submission, OR an error structure if rejected.
    if resp.get("status") != "transcribing":
        # 4xx/5xx returns {"detail": {"error": "...", ...}}
        detail = resp.get("detail") or resp
        raise RuntimeError(
            f"Hub rejected submission: {json.dumps(detail)[:300]}"
        )
    return resp


def fetch_status(hub: str) -> dict:
    with urllib.request.urlopen(f"{hub}/api/transcribe/status",
                                timeout=15) as r:
        return json.loads(r.read())


def kill_whisper_cli() -> None:
    """Best-effort kill of any running whisper-cli.exe processes.
    Used when we detect a stuck job — Windows-only path, harmless on
    other OSes."""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "whisper-cli.exe"],
            capture_output=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass


def wait_for_hub_idle(hub: str, max_wait_s: float = 1800) -> tuple[bool, str]:
    """Block until the Hub's transcribe_state.in_progress is False.
    Used before every submit so a busy Hub (from a leftover whisper-cli
    that outlived a previous script run, or a still-decoding job from
    an earlier video) no longer cascade-fails the next batch with
    'transcribe_already_running' rejections.

    Includes the same stuck-progress watchdog as poll_until_done: if
    the running job stops advancing for STUCK_PROGRESS_TIMEOUT_S, kill
    whisper-cli to free the Hub. The Hub will mark the killed job as
    error and we proceed to submit the next.

    Returns (is_idle, status_msg). If max_wait_s elapses without
    idleness, returns (False, ...) and the caller should treat as a
    submit failure for that chunk."""
    deadline = time.time() + max_wait_s
    last_filename = ""
    last_pct = -1
    last_change_at = time.time()
    while time.time() < deadline:
        try:
            ts = fetch_status(hub).get("transcribe_state") or {}
        except Exception as e:
            line(f"      Hub status poll error: {e}", replace=True)
            time.sleep(POLL_INTERVAL_S)
            continue
        if not ts.get("in_progress"):
            return True, "idle"
        fname = ts.get("filename") or ""
        pct = ts.get("progress_pct") or 0
        if fname != last_filename or pct != last_pct:
            last_filename, last_pct = fname, pct
            last_change_at = time.time()
            line(
                f"      Hub busy on {fname} {pct}% — waiting...",
                replace=True,
            )
        elif (time.time() - last_change_at) > STUCK_PROGRESS_TIMEOUT_S:
            line(
                f"      Hub stuck on {fname} at {pct}% for "
                f">{STUCK_PROGRESS_TIMEOUT_S // 60}m — "
                f"killing whisper-cli",
                replace=True,
            )
            line()
            kill_whisper_cli()
            time.sleep(3)  # let Hub poll subprocess + update state
            last_change_at = time.time()
        time.sleep(POLL_INTERVAL_S)
    return False, f"Hub still busy after {int(max_wait_s)}s"


def poll_until_done(hub: str, expected_filename: str,
                    audio_duration_s: float = 0) -> dict:
    """Poll /api/transcribe/status until the Hub's transcribe_state
    matches our submission AND reaches ready/done/error.

    Critical: the Hub's transcribe_state reflects whatever the most
    recent job was. Without filename validation, we can mistake a
    PRIOR job's 'ready' state for ours and return immediately with
    stale data. Always verify filename matches our expected_filename
    before treating any stage as authoritative.

    Two watchdogs (added after Tory observed a 9+ minute stall on a
    hard-to-decode OBS recording):
      - per-video deadline = max(floor, mult * duration)
      - stuck-progress detector = no pct advance for STUCK_PROGRESS_TIMEOUT_S
    Either trip → kill whisper-cli, return error, batch continues."""
    per_video_deadline_s = max(
        PER_VIDEO_TIMEOUT_FLOOR_S,
        PER_VIDEO_TIMEOUT_DURATION_MULT * (audio_duration_s or 0),
    )
    hard_deadline = time.time() + min(per_video_deadline_s, TRANSCRIBE_MAX_WAIT_S)

    last_pct = -1
    last_pct_change_at = time.time()
    started = time.time()
    saw_our_file = False
    while time.time() < hard_deadline:
        time.sleep(POLL_INTERVAL_S)
        try:
            ts = fetch_status(hub).get("transcribe_state") or {}
        except Exception as e:
            line(f"      poll error: {e}", replace=True)
            continue
        stage = ts.get("stage") or "?"
        polled_filename = ts.get("filename") or ""
        pct = ts.get("progress_pct") or 0
        elapsed = int(time.time() - started)

        # Have we seen the Hub start processing OUR file?
        if polled_filename == expected_filename:
            saw_our_file = True

        if not saw_our_file:
            line(
                f"      waiting for Hub to pick up "
                f"({fmt_dur(elapsed)} elapsed)",
                replace=True,
            )
            continue

        # Stuck-progress detector
        if pct != last_pct:
            last_pct = pct
            last_pct_change_at = time.time()
            line(
                f"      transcribing {pct:>3}%  "
                f"({fmt_dur(elapsed)} elapsed)",
                replace=True,
            )
        elif (time.time() - last_pct_change_at) > STUCK_PROGRESS_TIMEOUT_S:
            line(
                f"      stuck at {pct}% for "
                f"{fmt_dur(time.time() - last_pct_change_at)} — "
                f"killing whisper-cli, marking error",
                replace=True,
            )
            line()
            kill_whisper_cli()
            return {
                "stage": "error",
                "error": (
                    f"stuck at {pct}% for over "
                    f"{STUCK_PROGRESS_TIMEOUT_S // 60} min "
                    f"(watchdog killed whisper-cli)"
                ),
            }

        if stage in ("ready", "done"):
            return ts
        if stage == "error":
            return ts
    # Per-video hard deadline tripped
    line(
        f"      hit per-video hard timeout of "
        f"{fmt_dur(per_video_deadline_s)}, killing whisper-cli",
        replace=True,
    )
    line()
    kill_whisper_cli()
    return {
        "stage": "timeout",
        "error": (
            f"per-video timeout exceeded "
            f"{int(per_video_deadline_s)}s "
            f"(audio_duration_s={audio_duration_s:.0f})"
        ),
    }


# ── State management (append-only JSONL + derived index) ─────────

class State:
    def __init__(self, output_root: Path):
        self.root = output_root
        self.log_path = output_root / "state.jsonl"
        self.index_path = output_root / "state-index.json"
        self.backups_dir = output_root / "backups"
        self.errors_log = output_root / "errors.log"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        self._index: dict[str, dict] = self._load_index_or_rebuild()
        self._appends_since_backup = 0
        self._last_backup_at = time.time()

    def _load_index_or_rebuild(self) -> dict[str, dict]:
        # Always replay state.jsonl when present (it is the source of
        # truth, append-only, written on every event). The previous
        # version preferred state-index.json as a fast path, but that
        # file only updates on backup() calls (every 25 entries or
        # 30 minutes); a run that exited before its first backup would
        # leave a stale index, causing the next run to mistakenly
        # redo videos. Replay is fast (~ms per thousand entries) so
        # the fast-path optimization is not worth the correctness risk.
        idx: dict[str, dict] = {}
        if self.log_path.is_file():
            with open(self.log_path, encoding="utf-8") as f:
                for ln in f:
                    try:
                        rec = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    src = rec.get("src_path")
                    if src:
                        idx.setdefault(src, {}).update(rec)
            return idx
        # Final fallback: index_path exists but state.jsonl doesn't
        # (would only happen if state.jsonl was manually deleted).
        if self.index_path.is_file():
            try:
                with open(self.index_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return idx

    def append(self, rec: dict) -> None:
        rec.setdefault("ts", now_iso())
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        src = rec.get("src_path")
        if src:
            self._index.setdefault(src, {}).update(rec)
        self._appends_since_backup += 1
        self._maybe_backup()

    def _maybe_backup(self) -> None:
        if (
            self._appends_since_backup >= DEFAULT_BACKUP_EVERY_N
            or (time.time() - self._last_backup_at) >= DEFAULT_BACKUP_EVERY_S
        ):
            self.backup()

    def backup(self) -> None:
        if not self.log_path.is_file():
            return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = self.backups_dir / f"state-{ts}.jsonl"
        shutil.copy2(self.log_path, dst)
        # Also write the index snapshot for fast reload
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f)
        # Prune old backups
        backups = sorted(self.backups_dir.glob("state-*.jsonl"))
        while len(backups) > DEFAULT_BACKUP_KEEP_LAST:
            try:
                backups.pop(0).unlink()
            except OSError:
                break
        self._appends_since_backup = 0
        self._last_backup_at = time.time()

    def status_for(self, src_path: str) -> dict:
        return self._index.get(src_path, {})

    def append_error(self, src_path: str, stage: str, error: str) -> None:
        with open(self.errors_log, "a", encoding="utf-8") as f:
            f.write(f"[{now_iso()}] {stage} | {src_path} | {error}\n")
        self.append({
            "phase": "error",
            "src_path": src_path,
            "stage": stage,
            "error": error,
        })


# ── Walking + processing ────────────────────────────────────────

def walk_videos(
    root: Path, exts: set[str], min_size_b: int,
) -> list[Path]:
    found: list[Path] = []
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lstrip(".").lower()
        if ext not in exts:
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        if sz < min_size_b:
            continue
        found.append(f)
    return sorted(found)


def process_one(
    src: Path, root: Path, output: Path, hub: str,
    state: State, do_screenshots: bool, screenshot_interval_s: int,
    screenshot_width: int, screenshot_quality: int,
    idx: int, total: int, run_stats: dict,
) -> None:
    rel = src.relative_to(root)
    transcripts_dir = output / "transcripts" / rel.parent
    screenshots_dir = output / "screenshots" / rel.parent / src.stem
    audio_dir = output / "audio" / rel.parent
    txt_path = transcripts_dir / (src.stem + ".txt")
    json_path = transcripts_dir / (src.stem + ".json")
    audio_path = audio_dir / (src.stem + ".mp3")
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Compute running ETA from videos done so far in THIS run (not
    # historical). Average wall time per completed video extrapolated
    # against remaining count.
    elapsed_run = time.time() - run_stats["run_started_at"]
    done_this_run = run_stats["done"] + run_stats["errors"]
    if done_this_run >= 1:
        avg = elapsed_run / done_this_run
        eta_s = avg * (total - idx + 1)
        eta_str = f"ETA {fmt_dur(eta_s)}"
    else:
        eta_str = "ETA --"

    pct_overall = 100 * (idx - 1) / total if total else 0
    src_size = src.stat().st_size

    # Try to extract filmed_date from src or fall back
    filmed = run_stats.get("filmed_dates", {}).get(str(src), "")
    filmed_str = f"filmed {filmed[:10]}" if filmed else ""

    # Header block — gets printed once per video
    header = (
        f"{C_BOLD}[{idx}/{total}{C_RESET} · "
        f"{C_GREEN}done {run_stats['done']}{C_RESET} · "
        f"{C_RED}err {run_stats['errors']}{C_RESET} · "
        f"{pct_overall:>5.1f}% · {eta_str}{C_BOLD}]{C_RESET} "
        f"{rel}  {C_DIM}({fmt_size(src_size)}"
        + (f" · {filmed_str}" if filmed_str else "") +
        f"){C_RESET}"
    )
    line(header)
    rlog(f"START [{idx}/{total}] {rel} ({fmt_size(src_size)})")
    video_started = time.time()

    # ── Screenshots ──
    prior = state.status_for(str(src))
    if do_screenshots and prior.get("screenshots_done") != True:
        line("      extracting frames...", replace=True)
        n, err = extract_screenshots(
            src, screenshots_dir, screenshot_interval_s,
            screenshot_width, screenshot_quality,
        )
        if err:
            state.append_error(str(src), "screenshots", err)
            line(f"      screenshots FAILED: {err[:200]}", replace=True)
            line()
        else:
            state.append({
                "phase": "screenshots",
                "src_path": str(src),
                "screenshots_done": True,
                "n_frames": n,
                "screenshots_dir": str(screenshots_dir),
            })
            line(f"      screenshots: {n} frames", replace=True)
            line()
    elif prior.get("screenshots_done"):
        line(
            f"      screenshots: skip ({prior.get('n_frames','?')} "
            f"frames already extracted)", replace=True,
        )
        line()

    # ── Transcribe (early skip if already done) ──
    if prior.get("transcribe_done") and txt_path.is_file():
        chars = prior.get("transcript_chars", 0)
        line(f"  {ICON_OK} transcript already saved ({chars}c) — skipping")
        rlog(f"SKIP {rel}: already done ({chars}c)")
        run_stats["done"] += 1
        return

    # ── Audio extraction (compress to mp3 before upload) ──
    # The Hub's /api/transcribe has a 500 MB upload cap. Raw video is
    # often 1-50 GB. Pre-extract to 32 kbps mono mp3 — a 1-hour video
    # becomes ~14 MB, fitting easily under the cap, and the Hub's
    # internal ffmpeg normalize step has nothing to do.
    if not audio_path.is_file() or prior.get("audio_extracted") != True:
        line(f"  {ICON_RUN} extracting audio...", replace=True)
        t_audio = time.time()
        sz, err = extract_audio(src, audio_path)
        if err:
            state.append_error(str(src), "audio_extract", err)
            line(f"  {ICON_ERR} audio extract FAILED: {err[:120]}")
            rlog(f"FAIL {rel}: audio_extract — {err[:200]}", "ERROR")
            run_stats["errors"] += 1
            return
        line(
            f"  {ICON_OK} audio: {fmt_size(sz)} "
            f"{C_DIM}({fmt_dur(time.time() - t_audio)}){C_RESET}",
            replace=True,
        )
        line()
        state.append({
            "phase": "audio_extracted",
            "src_path": str(src),
            "audio_extracted": True,
            "audio_path": str(audio_path),
            "audio_size": sz,
        })
        rlog(f"AUDIO {rel}: {sz} bytes in {time.time()-t_audio:.1f}s")
    else:
        prior_sz = prior.get("audio_size", 0)
        line(
            f"  {ICON_OK} audio: {fmt_size(prior_sz)} "
            f"{C_DIM}(cached){C_RESET}"
        )

    # ── Chunk audio into ≤30-min pieces ──
    # Whisper.cpp's temperature-fallback decoder can hang on long audio
    # with hard stretches (observed: 1.5 GB OBS recordings stuck at 66%
    # past the 5-min watchdog). Splitting bounds blast radius — a bad
    # 30-min chunk fails alone and the rest of the video still
    # transcribes; on resume only the failed chunk re-runs.
    chunks_dir = audio_dir / (src.stem + "_chunks")
    chunk_max_s = run_stats.get("chunk_max_s", CHUNK_MAX_DURATION_S)
    prior_chunk_paths = prior.get("chunk_paths") or []
    # Re-split when we have no prior plan, OR when the recorded chunks
    # aren't on disk (e.g., user moved the dir, or a partial run was
    # interrupted before chunks landed). The split helper is idempotent
    # — it wipes chunk_dir before writing.
    need_split = (
        not prior_chunk_paths
        or any(not Path(p).is_file() for p in prior_chunk_paths)
    )
    if need_split:
        line(
            f"  {ICON_RUN} chunking audio "
            f"(<={chunk_max_s // 60}m segments)...",
            replace=True,
        )
        chunk_paths, audio_total_dur, err = split_audio_into_chunks(
            audio_path, chunks_dir, max_chunk_s=chunk_max_s,
        )
        if err:
            state.append_error(str(src), "audio_chunk", err)
            line(f"  {ICON_ERR} chunk split FAILED: {err[:120]}")
            rlog(f"FAIL {rel}: audio_chunk — {err[:200]}", "ERROR")
            run_stats["errors"] += 1
            return
        line(
            f"  {ICON_OK} chunks: {len(chunk_paths)} "
            f"({fmt_dur(audio_total_dur)} total)",
            replace=True,
        )
        line()
        state.append({
            "phase": "audio_chunked",
            "src_path": str(src),
            "n_chunks": len(chunk_paths),
            "chunk_paths": [str(p) for p in chunk_paths],
            "audio_total_duration_s": audio_total_dur,
        })
    else:
        chunk_paths = [Path(p) for p in prior_chunk_paths]
        audio_total_dur = prior.get("audio_total_duration_s") or 0
        line(
            f"  {ICON_OK} chunks: {len(chunk_paths)} "
            f"{C_DIM}(cached){C_RESET}"
        )

    # ── Transcribe each chunk ──
    chunk_txt_dir = transcripts_dir / (src.stem + "_chunks")
    chunk_txt_dir.mkdir(parents=True, exist_ok=True)

    chunk_txt_paths: list[Path] = []
    chunk_durations: list[float] = []
    chunk_results: list[dict] = []  # per-chunk: {ok, chars, latency_ms, error}
    total_gpu_ms = 0
    total_chars = 0
    abort_reason: str | None = None  # systemic failure -> stop the whole video

    for ci, chunk_path in enumerate(chunk_paths, 1):
        chunk_txt = chunk_txt_dir / (chunk_path.stem + ".txt")
        chunk_txt_paths.append(chunk_txt)
        chunk_dur = video_duration_s(chunk_path) or 0
        chunk_durations.append(chunk_dur)

        # Resume: skip any chunk whose transcript txt is already on
        # disk and non-empty. The txt file is the durable signal —
        # state.jsonl backs it up but the file is the truth.
        if chunk_txt.is_file() and chunk_txt.stat().st_size > 0:
            cached_chars = len(chunk_txt.read_text(encoding="utf-8"))
            total_chars += cached_chars
            chunk_results.append({"ok": True, "chars": cached_chars,
                                  "cached": True})
            line(
                f"  {ICON_OK} chunk {ci}/{len(chunk_paths)}: "
                f"{C_DIM}{cached_chars}c (cached){C_RESET}"
            )
            continue

        # Wait for Hub idle before submit. Without this, a still-busy
        # Hub (from a previous run's orphaned whisper-cli, or just a
        # job that hasn't released its slot yet) cascades into
        # 'transcribe_already_running' rejections. wait_for_hub_idle
        # also runs the stuck-progress watchdog, so a hung pre-existing
        # job gets cleaned up here before we try to submit.
        line(
            f"  {ICON_RUN} chunk {ci}/{len(chunk_paths)}: "
            f"waiting for Hub idle...",
            replace=True,
        )
        idle, msg = wait_for_hub_idle(hub, max_wait_s=1800)
        if not idle:
            # Systemic — Hub itself is wedged. No point trying later
            # chunks; abort this whole video.
            abort_reason = f"Hub never idle: {msg}"
            chunk_results.append({"ok": False, "error": msg})
            state.append_error(
                str(src), f"transcribe_submit_chunk_{ci}", msg,
            )
            break

        line(
            f"  {ICON_RUN} chunk {ci}/{len(chunk_paths)}: "
            f"submitting ({fmt_dur(chunk_dur)})...",
            replace=True,
        )
        try:
            post_transcribe(hub, chunk_path)
        except Exception as e:
            # Submit failed (network / Hub busy / etc.) — also systemic.
            abort_reason = f"Hub submit failed: {e}"
            chunk_results.append({"ok": False, "error": str(e)})
            state.append_error(
                str(src), f"transcribe_submit_chunk_{ci}", str(e),
            )
            break

        state.append({
            "phase": "chunk_submit",
            "src_path": str(src),
            "chunk_idx": ci,
            "chunk_path": str(chunk_path),
            "submitted_at": now_iso(),
            "submitted_filename": chunk_path.name,
        })

        ts = poll_until_done(hub, chunk_path.name, chunk_dur)
        stage = ts.get("stage")
        if stage in ("error", "timeout"):
            # Chunk-specific decode failure — whisper hung on this
            # particular slice of audio, watchdog killed it. Each
            # chunk is independent; carry on to the next chunk so
            # any later good chunks still land. Concat will mark
            # this chunk's place with a [chunk N: <error>] inline
            # so the partial transcript stays time-aligned.
            err = ts.get("error") or "unknown"
            chunk_results.append({"ok": False, "error": err})
            state.append_error(
                str(src), f"transcribe_chunk_{ci}", err,
            )
            line(
                f"  {ICON_ERR} chunk {ci}/{len(chunk_paths)}: "
                f"FAILED — {err[:80]}"
            )
            rlog(
                f"CHUNK_FAIL {rel} {ci}/{len(chunk_paths)}: {err[:200]}",
                "ERROR",
            )
            continue

        chunk_text = (ts.get("transcript") or "").strip()
        chunk_chars = len(chunk_text)
        chunk_latency_ms = ts.get("latency_ms") or 0
        total_chars += chunk_chars
        total_gpu_ms += chunk_latency_ms
        chunk_txt.write_text(chunk_text, encoding="utf-8")
        chunk_results.append({"ok": True, "chars": chunk_chars,
                              "latency_ms": chunk_latency_ms})
        state.append({
            "phase": "chunk_done",
            "src_path": str(src),
            "chunk_idx": ci,
            "chunk_path": str(chunk_path),
            "chunk_chars": chunk_chars,
            "chunk_latency_ms": chunk_latency_ms,
            "chunk_txt_path": str(chunk_txt),
        })
        line(
            f"  {ICON_OK} chunk {ci}/{len(chunk_paths)}: "
            f"{chunk_chars}c "
            f"{C_DIM}({fmt_dur(chunk_latency_ms / 1000)} GPU){C_RESET}"
        )
        rlog(
            f"CHUNK {rel} {ci}/{len(chunk_paths)}: "
            f"{chunk_chars}c GPU {chunk_latency_ms}ms"
        )

    n_ok = sum(1 for r in chunk_results if r.get("ok"))
    n_failed = len(chunk_results) - n_ok

    if abort_reason:
        line(f"  {ICON_ERR} transcribe ABORTED: {abort_reason[:150]}")
        rlog(f"FAIL {rel}: aborted — {abort_reason}", "ERROR")
        run_stats["errors"] += 1
        return

    if n_ok == 0:
        # Every chunk failed — count as full error so retry-errors
        # picks the whole video back up. State.jsonl already has
        # individual chunk error entries from the loop.
        line(
            f"  {ICON_ERR} transcribe FAILED: all "
            f"{len(chunk_paths)} chunks failed"
        )
        rlog(
            f"FAIL {rel}: all {len(chunk_paths)} chunks failed",
            "ERROR",
        )
        run_stats["errors"] += 1
        return

    # ── Concat chunk transcripts → final txt (with failure markers) ──
    final_text = concat_chunk_transcripts(
        chunk_txt_paths, chunk_durations, chunk_results,
    )
    txt_path.write_text(final_text, encoding="utf-8")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "src_path": str(src),
            "n_chunks": len(chunk_paths),
            "n_chunks_ok": n_ok,
            "n_chunks_failed": n_failed,
            "chunk_paths": [str(p) for p in chunk_paths],
            "chunk_txts": [str(p) for p in chunk_txt_paths],
            "chunk_durations_s": chunk_durations,
            "chunk_results": chunk_results,
            "total_chars": total_chars,
            "total_gpu_ms": total_gpu_ms,
            "audio_total_duration_s": audio_total_dur,
        }, f, indent=2)

    # ── Cleanup mp3s ──
    # Full success (n_failed == 0): delete chunk mp3s + source mp3.
    # Partial success: keep failed-chunk mp3s for retry; delete only
    # the successful chunks' mp3s. Keep source mp3 too — re-running
    # under --retry-errors should re-attempt failed chunks against the
    # same split, not start over from scratch.
    deleted_bytes = 0
    multi_chunk = (
        len(chunk_paths) > 1
        or (len(chunk_paths) == 1 and chunk_paths[0] != audio_path)
    )
    if multi_chunk:
        for cp, r in zip(chunk_paths, chunk_results):
            if not r.get("ok"):
                continue  # keep failed-chunk mp3 for retry
            try:
                deleted_bytes += cp.stat().st_size
                cp.unlink()
            except OSError:
                pass
        # Only remove the chunks dir if every chunk succeeded.
        if n_failed == 0:
            try:
                chunks_dir.rmdir()
            except OSError:
                pass
    if n_failed == 0:
        try:
            if audio_path.is_file():
                deleted_bytes += audio_path.stat().st_size
                audio_path.unlink()
        except OSError:
            pass

    state.append({
        "phase": "transcribe_done" if n_failed == 0 else "transcribe_partial",
        "src_path": str(src),
        # transcribe_done flag controls the early-skip on resume; only
        # set True on full success so partial videos retry their failed
        # chunks on the next --retry-errors pass.
        "transcribe_done": n_failed == 0,
        "transcript_partial": n_failed > 0,
        "n_chunks_ok": n_ok,
        "n_chunks_failed": n_failed,
        "transcript_chars": total_chars,
        "transcribe_latency_ms": total_gpu_ms,
        "transcript_path": str(txt_path),
        "n_chunks": len(chunk_paths),
        "audio_total_duration_s": audio_total_dur,
        "deleted_audio_bytes": deleted_bytes,
    })

    wall = time.time() - video_started
    if n_failed == 0:
        line(
            f"  {ICON_OK} transcript: {C_BOLD}{total_chars}c{C_RESET} "
            f"{C_DIM}· {len(chunk_paths)} chunks "
            f"· {fmt_dur(total_gpu_ms / 1000)} GPU "
            f"· {fmt_dur(wall)} wall "
            f"· freed {fmt_size(deleted_bytes)}{C_RESET}"
        )
        rlog(
            f"DONE {rel}: {total_chars}c, {len(chunk_paths)} chunks, "
            f"GPU {total_gpu_ms}ms, wall {wall:.1f}s, "
            f"freed {deleted_bytes} bytes"
        )
        run_stats["done"] += 1
    else:
        line(
            f"  {ICON_WARN} transcript PARTIAL: "
            f"{C_BOLD}{total_chars}c{C_RESET} "
            f"{C_DIM}· {n_ok}/{len(chunk_paths)} chunks ok "
            f"· {fmt_dur(total_gpu_ms / 1000)} GPU "
            f"· {fmt_dur(wall)} wall{C_RESET}"
        )
        rlog(
            f"PARTIAL {rel}: {total_chars}c, "
            f"{n_ok}/{len(chunk_paths)} chunks ok, "
            f"{n_failed} failed, GPU {total_gpu_ms}ms, "
            f"wall {wall:.1f}s",
            "WARNING",
        )
        # Count partials as errors for the run-summary tally so it's
        # obvious from the snapshot that the video isn't fully done.
        run_stats["errors"] += 1


# ── CLI ──────────────────────────────────────────────────────────

def print_banner(input_dir: Path, output: Path, hub: str,
                 from_list: str | None, n_videos: int,
                 total_size: int, state_index: dict) -> None:
    """Print a clean startup snapshot."""
    bar = BOX_H * 68
    title = "Cortex Video Journal Miner"
    pad = 68 - len(title) - 2
    line(f"\n{C_BOLD}{BOX_TL}{bar}{BOX_TR}{C_RESET}")
    line(f"{C_BOLD}{BOX_V}  {title}{' ' * pad}{BOX_V}{C_RESET}")
    line(f"{C_BOLD}{BOX_BL}{bar}{BOX_BR}{C_RESET}")
    line(f"  {C_DIM}Input  :{C_RESET} {input_dir}")
    line(f"  {C_DIM}        :{C_RESET} {n_videos} videos · {fmt_size(total_size)}")
    line(f"  {C_DIM}Output :{C_RESET} {output}")
    line(f"  {C_DIM}Hub    :{C_RESET} {hub}")
    if from_list:
        line(f"  {C_DIM}List   :{C_RESET} {from_list}")
    s = compute_snapshot(state_index)
    if s["transcribe_done"] or s["errors"]:
        line(f"  {C_DIM}Prior  :{C_RESET} "
             f"{C_GREEN}{s['transcribe_done']} done{C_RESET} · "
             f"{C_RED}{s['errors']} errs{C_RESET} · "
             f"{C_DIM}{fmt_size(s['audio_bytes_total'])} audio · "
             f"{s['transcript_chars_total']:,}c · "
             f"{fmt_dur(s['transcribe_ms_total']/1000)} GPU{C_RESET}")
    line(f"  {C_DIM}Logs   :{C_RESET} {output / 'run.log'}")
    line()


def cmd_main(args) -> int:
    root = Path(args.input).resolve()
    if not root.is_dir():
        line(f"ERR: {root} not found")
        return 1

    output = Path(args.output).resolve() if args.output else (
        root.parent / "video_mining_output"
    )
    output.mkdir(parents=True, exist_ok=True)
    setup_run_logger(output)

    if not have("ffmpeg"):
        line("ERR: ffmpeg not on PATH. Install: winget install ffmpeg")
        return 2
    if not have("ffprobe"):
        line("WARN: ffprobe not on PATH; duration estimates may fail")
    if not have("curl"):
        line("ERR: curl not on PATH (Win 10+ has it built-in)")
        return 2

    exts = {e.strip().lower().lstrip(".") for e in (args.video_exts or "").split(",") if e.strip()}
    if not exts:
        exts = DEFAULT_VIDEO_EXTS
    min_size_b = int(args.min_size_mb * 1024 * 1024)

    line(f"Scanning {root}...")
    videos = walk_videos(root, exts, min_size_b)
    line(f"Found {len(videos)} video files (>={args.min_size_mb} MB)")

    if videos:
        total_size = sum(v.stat().st_size for v in videos)
        line(f"Total source size: {fmt_size(total_size)}")
        if args.scan_only:
            line()
            line("--- per-extension breakdown ---")
            from collections import Counter
            c = Counter(v.suffix.lower() for v in videos)
            for ext, n in c.most_common():
                line(f"  {ext}: {n}")
            line()
            line("--- 10 largest ---")
            for v in sorted(videos, key=lambda x: -x.stat().st_size)[:10]:
                line(f"  {fmt_size(v.stat().st_size):>10}  {v.relative_to(root)}")
            return 0

    state = State(output)

    # --status: snapshot of current state, no processing
    if args.status:
        wanted = None
        if args.from_list:
            list_path = Path(args.from_list).resolve()
            if list_path.is_file():
                with open(list_path, encoding="utf-8") as f:
                    wanted = {ln.strip() for ln in f
                              if ln.strip() and not ln.startswith("#")}
        print_banner(root, output, args.hub, args.from_list,
                     len(videos), total_size, state._index)
        print_status(state._index, wanted, title="STATUS SNAPSHOT")
        return 0

    print_banner(root, output, args.hub, args.from_list,
                 len(videos), total_size, state._index)

    # Filter for the run
    to_process: list[Path] = []
    skipped = 0
    if args.from_list:
        # --from-list is exclusive: read paths from file, only process
        # videos whose absolute path matches an entry in the list.
        # Pre-filter all_done items so the displayed count is accurate
        # AND already-done entries don't trigger per-stage redo even
        # if state-index.json is stale (process_one's per-stage skip
        # is the second line of defense; this is the first).
        list_path = Path(args.from_list).resolve()
        if not list_path.is_file():
            line(f"ERR: --from-list file not found: {list_path}")
            return 1
        wanted: set[str] = set()
        with open(list_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                wanted.add(ln)
        line(f"--from-list: {len(wanted)} paths requested")
        videos_by_str = {str(v): v for v in videos}
        n_already_done = 0
        n_not_found = 0
        for w in sorted(wanted):
            v = videos_by_str.get(w)
            if v is None:
                # Try case-insensitive / forward-slash match for
                # cross-platform robustness
                w_norm = w.replace("/", "\\").lower()
                hit = next(
                    (v for s, v in videos_by_str.items()
                     if s.lower() == w_norm),
                    None,
                )
                if hit is None:
                    line(f"  WARN: not in walk results: {w}")
                    n_not_found += 1
                    continue
                v = hit
            # Pre-filter all_done unless --retry-errors / --reprocess
            prior = state.status_for(str(v))
            had_error = prior.get("phase") == "error"
            all_done = prior.get("transcribe_done") and (
                prior.get("screenshots_done")
                or not args.screenshots_every
            )
            if args.retry_errors and had_error:
                to_process.append(v)
                continue
            if all_done:
                n_already_done += 1
                continue
            to_process.append(v)
        skipped = len(videos) - len(to_process)
        if n_already_done:
            line(f"  {n_already_done} already done; skipping")
        if n_not_found:
            line(f"  {n_not_found} not found in walk; skipping")
    elif args.only:
        # --only is exclusive: restrict to files whose path contains
        # the substring, regardless of done/error state.
        for v in videos:
            if args.only in str(v):
                to_process.append(v)
        skipped = len(videos) - len(to_process)
    else:
        for v in videos:
            prior = state.status_for(str(v))
            had_error = prior.get("phase") == "error"
            all_done = prior.get("transcribe_done") and (
                prior.get("screenshots_done")
                or not args.screenshots_every
            )
            if args.retry_errors and had_error:
                to_process.append(v)
                continue
            if args.reprocess and args.reprocess in str(v):
                to_process.append(v)
                continue
            if all_done:
                skipped += 1
                continue
            to_process.append(v)

    line(f"  {C_BOLD}To process this run:{C_RESET} "
         f"{C_CYAN}{len(to_process)}{C_RESET} videos "
         f"{C_DIM}(skipping {skipped}){C_RESET}")
    if args.limit:
        to_process = to_process[:args.limit]
        line(f"  --limit {args.limit} -> first {len(to_process)} only")
    rlog(f"RUN_START to_process={len(to_process)} skipped={skipped}")
    line()

    # Build a {src_path: filmed_date} lookup from the index_videos
    # output if present, so the per-video header can show filmed date.
    filmed_dates: dict[str, str] = {}
    candidate_indexes = [
        output.parent / "video_index" / "state-index.json",
        Path(str(output).replace("video_mining_output",
                                  "video_index")) / "state-index.json",
    ]
    for cand in candidate_indexes:
        if cand.is_file():
            try:
                with open(cand, encoding="utf-8") as f:
                    idx = json.load(f)
                for src, rec in idx.items():
                    if rec.get("filmed_date"):
                        filmed_dates[src] = rec["filmed_date"]
                break
            except Exception:
                pass

    do_screenshots = args.screenshots_every is not None and args.screenshots_every > 0
    interval_s = args.screenshots_every or DEFAULT_SCREENSHOT_INTERVAL_S

    started = time.time()
    run_stats = {
        "run_started_at": started,
        "done": 0,
        "errors": 0,
        "filmed_dates": filmed_dates,
        "chunk_max_s": max(60, args.chunk_minutes * 60),
    }

    try:
        for i, v in enumerate(to_process, 1):
            process_one(
                v, root, output, args.hub, state,
                do_screenshots, interval_s,
                args.screenshot_width, args.screenshot_quality,
                i, len(to_process), run_stats,
            )
            # Periodic compact snapshot every 10 videos
            if i % 10 == 0 and i < len(to_process):
                elapsed = time.time() - started
                avg = elapsed / i
                eta = avg * (len(to_process) - i)
                bytes_audio_run = run_stats.get("done", 0)  # counter
                line(
                    f"  {C_DIM}--- snapshot @ {i}/{len(to_process)}: "
                    f"{run_stats['done']} done, {run_stats['errors']} err, "
                    f"avg {fmt_dur(avg)}/video, ETA {fmt_dur(eta)} ---"
                    f"{C_RESET}"
                )
                rlog(
                    f"SNAPSHOT i={i} done={run_stats['done']} "
                    f"err={run_stats['errors']} elapsed={elapsed:.0f}s "
                    f"avg_per_video={avg:.1f}s eta={eta:.0f}s"
                )
    except KeyboardInterrupt:
        line()
        line(f"  {ICON_WARN} interrupted — state saved, re-run to resume")
        rlog(
            f"INTERRUPTED done={run_stats['done']} "
            f"err={run_stats['errors']}",
            "WARNING",
        )
        state.backup()
        return 130

    elapsed = time.time() - started
    line()
    line(
        f"{C_BOLD}DONE{C_RESET}  total wall: {C_BOLD}{fmt_dur(elapsed)}{C_RESET}  "
        f"· {C_GREEN}{run_stats['done']} done{C_RESET}"
        f" · {C_RED}{run_stats['errors']} errors{C_RESET}"
    )
    rlog(
        f"RUN_END elapsed={elapsed:.0f}s done={run_stats['done']} "
        f"err={run_stats['errors']}"
    )
    print_status(state._index, title="FINAL SNAPSHOT")
    state.backup()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", help="Directory of video files (recursive)")
    p.add_argument("--output", help="Output dir (default: <input>/../video_mining_output)")
    p.add_argument("--hub", default=DEFAULT_HUB,
                   help=f"Cortex Hub base URL (default: {DEFAULT_HUB})")
    p.add_argument("--video-exts",
                   default=",".join(sorted(DEFAULT_VIDEO_EXTS)),
                   help="Comma-separated video extensions")
    p.add_argument("--min-size-mb", type=int, default=DEFAULT_MIN_SIZE_MB,
                   help=f"Skip videos smaller than N MB (default: {DEFAULT_MIN_SIZE_MB})")
    p.add_argument("--screenshots-every", type=int,
                   default=DEFAULT_SCREENSHOT_INTERVAL_S,
                   help=f"Frame extraction interval in seconds "
                        f"(default: {DEFAULT_SCREENSHOT_INTERVAL_S}; 0 to disable)")
    p.add_argument("--screenshot-width", type=int,
                   default=DEFAULT_SCREENSHOT_WIDTH,
                   help=f"Frame max width in pixels (default: {DEFAULT_SCREENSHOT_WIDTH})")
    p.add_argument("--screenshot-quality", type=int,
                   default=DEFAULT_SCREENSHOT_QUALITY,
                   help=f"ffmpeg -q:v JPEG quality (1=best, 31=worst; default: {DEFAULT_SCREENSHOT_QUALITY})")
    p.add_argument("--scan-only", action="store_true",
                   help="Just walk and report; don't process anything")
    p.add_argument("--retry-errors", action="store_true",
                   help="Re-process entries with phase='error'")
    p.add_argument("--reprocess",
                   help="Re-process files whose path contains this substring")
    p.add_argument("--only",
                   help="Process ONLY files whose path contains this "
                        "substring (replaces the default walk)")
    p.add_argument("--from-list",
                   help="Read absolute video paths from a text file "
                        "(one per line, # comments OK) and process "
                        "ONLY those. Designed to consume the export "
                        "from index_videos.py's HTML interface.")
    p.add_argument("--limit", type=int,
                   help="Process at most N files (for testing)")
    p.add_argument("--status", action="store_true",
                   help="Print current state snapshot and exit; don't "
                        "process anything. Combine with --from-list to "
                        "scope the snapshot to your selected paths.")
    p.add_argument("--chunk-minutes", type=int,
                   default=CHUNK_MAX_DURATION_S // 60,
                   help=f"Split audio into <=N-minute chunks before "
                        f"transcribing (default: "
                        f"{CHUNK_MAX_DURATION_S // 60}). Bounds the "
                        f"damage from whisper.cpp hangs on long audio.")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(cmd_main(args))
