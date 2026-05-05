"""Slice 7 CP2: voice journal transcription via whisper.cpp.

The dev.7 version used openai-whisper (Python + PyTorch ~2GB).
That broke for users on the installed exe path because
PyInstaller-bundled binaries can't pip-install at runtime. dev.9
switches to whisper.cpp — a single ~5MB native binary that's
bundled into the exe, plus a separately downloaded GGML model
file (~3GB for large-v3). No Python deps for transcription.

LAYOUT

  Binary lookup order:
    1. _MEIPASS/backend/bin/whisper-cli{.exe}      (PyInstaller bundle)
    2. <repo>/hub/backend/bin/whisper-cli{.exe}    (source dev — the
                                                    scripts/build_whisper_cpp.py
                                                    output)
    3. %APPDATA%/Cortex/whisper-cpp/whisper-cli{.exe}
                                                   (auto-downloaded fallback —
                                                    not implemented in CP2;
                                                    we assume one of the
                                                    above two is present)

  Model file:
    %APPDATA%/Cortex/whisper-models/ggml-large-v3.bin
    Downloaded from HuggingFace on first transcription. ~3GB.
    Single download; cached forever.

PRIVACY

  All transcription is local. The binary runs on the user's
  machine, the model file lives in their AppData. Audio never
  leaves the host.

  The model is downloaded once from HuggingFace — that's the
  ONLY network call related to voice. If the user objects, they
  can manually drop a model file in the AppData path and the
  download skips.

CONCURRENCY

  Model download runs in a daemon thread on first call. While it's
  in-flight, /api/transcribe returns 202 with a status hint; the
  UI polls /api/transcribe/status to track progress and retries
  once the model is ready.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from config import settings


log = logging.getLogger("hub.transcribe")

router = APIRouter()


# ── Constants ────────────────────────────────────────────────────


MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
              ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".mpg",
              ".mpeg", ".m4v", ".3gp"}

# Default model. Tory: "the more powerful one." large-v3 is the
# best Whisper offers. ~3GB GGML file.
DEFAULT_WHISPER_MODEL = "large-v3"

# Download URL template. HuggingFace hosts the official GGML
# conversions of every Whisper checkpoint at this canonical
# location, maintained by the whisper.cpp project.
HF_MODEL_URL_TEMPLATE = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
    "main/ggml-{model}.bin"
)

# Map model names users might pass to the HF download identifier.
# Keep both with-and-without -v variants so "large", "large-v3",
# and "large-v3-turbo" all do the right thing.
MODEL_ALIASES = {
    "tiny": "tiny",
    "tiny.en": "tiny.en",
    "base": "base",
    "base.en": "base.en",
    "small": "small",
    "small.en": "small.en",
    "medium": "medium",
    "medium.en": "medium.en",
    "large": "large-v3",
    "large-v1": "large-v1",
    "large-v2": "large-v2",
    "large-v3": "large-v3",
    "large-v3-turbo": "large-v3-turbo",
    "turbo": "large-v3-turbo",
}


# ── Paths ───────────────────────────────────────────────────────


def _appdata_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Cortex"
    else:
        base = Path.home() / ".cortex"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _model_dir() -> Path:
    d = _appdata_dir() / "whisper-models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _model_path(model: str) -> Path:
    return _model_dir() / "ggml-{}.bin".format(MODEL_ALIASES.get(
        model, model))


def _binary_name() -> str:
    return "whisper-cli.exe" if os.name == "nt" else "whisper-cli"


def _find_binary() -> Path | None:
    """Lookup order: PyInstaller bundle → repo dev path → AppData
    fallback. Returns None if none of them have the binary."""
    name = _binary_name()
    candidates: list[Path] = []
    # PyInstaller bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "backend" / "bin" / name)
    # Source dev: cortex-desktop/hub/backend/bin/<name>
    repo_bin = Path(__file__).resolve().parent.parent / "bin" / name
    candidates.append(repo_bin)
    # User AppData fallback (manually-installed binary)
    candidates.append(_appdata_dir() / "whisper-cpp" / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


# ── ffmpeg sanity check ─────────────────────────────────────────


def _check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"],
                       capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return False


# ── Audio normalization (always WAV 16kHz mono for whisper.cpp) ──


def _normalize_to_wav(input_path: Path, output_path: Path) -> None:
    """Convert any audio/video input to WAV 16kHz mono — the format
    whisper.cpp wants. ffmpeg handles every format we accept; this
    also cleanly strips video tracks from .mp4/.mov inputs."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail={
            "error": "ffmpeg_timeout",
            "message": "Audio normalization took longer than 15 "
                       "minutes. The input may be too long or "
                       "corrupted."})
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail={
            "error": "ffmpeg_failed",
            "message": "ffmpeg couldn't decode the input.",
            "stderr": (e.stderr or b"").decode(
                "utf-8", errors="replace")[:1000],
        })


# ── Model download (background, with progress tracking) ─────────


# Singleton state — one download at a time.
_download_lock = threading.Lock()
_download_state: dict = {
    "in_progress": False,
    "model": "",
    "bytes_downloaded": 0,
    "bytes_total": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def _download_model_blocking(model: str) -> None:
    """Download the GGML model file to disk. Runs in a daemon
    thread; updates _download_state for status polling."""
    canonical = MODEL_ALIASES.get(model, model)
    url = HF_MODEL_URL_TEMPLATE.format(model=canonical)
    target = _model_path(model)
    tmp = target.with_suffix(target.suffix + ".part")

    log.info("downloading whisper model %s from %s", canonical, url)
    _download_state["in_progress"] = True
    _download_state["model"] = canonical
    _download_state["bytes_downloaded"] = 0
    _download_state["bytes_total"] = 0
    _download_state["error"] = None
    _download_state["started_at"] = time.time()
    _download_state["finished_at"] = None

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "cortex-desktop/0.17"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            _download_state["bytes_total"] = total
            written = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    _download_state["bytes_downloaded"] = written
        # Atomic rename so a partial download never looks complete.
        if target.exists():
            target.unlink()
        tmp.rename(target)
        _download_state["finished_at"] = time.time()
        log.info("downloaded %s (%d bytes)", canonical, written)
    except (urllib.error.URLError, OSError) as e:
        _download_state["error"] = str(e)
        log.exception("model download failed: %s", e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    finally:
        _download_state["in_progress"] = False


def _start_model_download(model: str) -> bool:
    """Start a download in a daemon thread. Returns True if a new
    download was kicked off, False if one is already in-flight."""
    with _download_lock:
        if _download_state["in_progress"]:
            return False
        t = threading.Thread(
            target=_download_model_blocking, args=(model,),
            daemon=True, name="whisper-model-download")
        t.start()
        return True


# ── whisper.cpp invocation ──────────────────────────────────────


def _run_whisper_cli(*, binary: Path, model_path: Path,
                     audio_path: Path) -> dict:
    """Run whisper-cli on a normalized WAV; return parsed JSON.

    whisper-cli's -oj flag writes <input>.json next to the input,
    with the structured transcription. We pass an explicit -of to
    control the basename so we know exactly where to read.

    Slice 7 dev.11: explicit -t <cpu_count>. whisper-cli's default
    is min(cpu_count, 4) — wastes most of a modern CPU. Tory's
    Task Manager showed 14% CPU utilization on his 16-thread box
    during a real run; bumping to all available threads typically
    delivers 2-3× speedup on CPU-only builds (memory bandwidth
    becomes the limit past ~8 threads, but extra threads don't
    hurt — whisper.cpp's scheduler tops out gracefully).
    """
    out_base = audio_path.with_suffix("")
    threads = max(1, (os.cpu_count() or 4))
    cmd = [
        str(binary),
        "-m", str(model_path),
        "-f", str(audio_path),
        "-t", str(threads),        # all CPU threads (default was 4)
        "-oj",                     # output JSON
        "-of", str(out_base),      # output file base (no extension)
        "-l", "auto",              # language autodetect
        "-pp",                     # print progress in stderr (visible in logs)
        "-nt",                     # no timestamps in stdout (cleaner)
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True,
                       timeout=3600)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail={
            "error": "whisper_timeout",
            "message": "Transcription took longer than an hour. "
                       "The input may be too long for this model "
                       "to handle in one pass."})
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail={
            "error": "whisper_failed",
            "message": "whisper-cli returned a non-zero exit code.",
            "stderr": (e.stderr or b"").decode(
                "utf-8", errors="replace")[-2000:],
        })

    json_path = out_base.with_suffix(".json")
    if not json_path.is_file():
        raise HTTPException(status_code=500, detail={
            "error": "whisper_no_output",
            "message": "whisper-cli ran but didn't write a JSON file."})
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            json_path.unlink()
        except OSError:
            pass


def _flatten_transcription(payload: dict) -> tuple[str, float]:
    """Pull the text + last-segment end-time out of whisper-cli's
    JSON output."""
    segs = payload.get("transcription") or []
    if not segs:
        return "", 0.0
    parts = []
    last_end_s = 0.0
    for s in segs:
        t = (s.get("text") or "").strip()
        if t:
            parts.append(t)
        offsets = s.get("offsets") or {}
        end_ms = offsets.get("to")
        if isinstance(end_ms, (int, float)):
            last_end_s = max(last_end_s, end_ms / 1000.0)
    return " ".join(parts), round(last_end_s, 2)


# ── Routes ──────────────────────────────────────────────────────


def _configured_model() -> str:
    return getattr(settings, "whisper_model", DEFAULT_WHISPER_MODEL)


@router.post("")
async def transcribe(
    file: UploadFile = File(...),
    model: str | None = Form(None),
):
    """POST /api/transcribe

    multipart/form-data with `file` (audio or video) and optional
    `model` override. Returns:
      { ok, transcript, language, model, duration_s, latency_ms,
        audio_extracted, source_format, bytes }

    On first call when the GGML model isn't downloaded yet, returns
    202 with status:
      { ok: false, status: "model_downloading", model, bytes_total,
        bytes_downloaded }
    The UI polls /api/transcribe/status until the download completes,
    then retries.
    """
    if not file.filename:
        raise HTTPException(status_code=400,
                            detail={"error": "no_filename"})

    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail={
            "error": "unsupported_format",
            "extension": ext,
            "supported_audio": sorted(AUDIO_EXTS),
            "supported_video": sorted(VIDEO_EXTS),
        })

    binary = _find_binary()
    if binary is None:
        raise HTTPException(status_code=501, detail={
            "error": "whisper_cli_not_found",
            "message": (
                "whisper-cli binary isn't bundled with this Hub "
                "install. Re-install the Cortex Hub release "
                "(v0.17.0-dev.9 or later) — the binary ships "
                "inside the installer. For source dev, run "
                "`python scripts/build_whisper_cpp.py` from the "
                "cortex-desktop repo to build it locally."),
        })

    if not _check_ffmpeg():
        raise HTTPException(status_code=501, detail={
            "error": "ffmpeg_not_installed",
            "message": (
                "ffmpeg is required for audio normalization. Install:\n"
                "  Windows:  winget install ffmpeg\n"
                "  macOS:    brew install ffmpeg\n"
                "  Linux:    apt install ffmpeg"),
        })

    model_name = (model or "").strip() or _configured_model()
    model_file = _model_path(model_name)
    if not model_file.is_file():
        # Kick off download in the background; tell UI to poll.
        started = _start_model_download(model_name)
        return {
            "ok": False,
            "status": "model_downloading"
                     if started else "model_already_downloading",
            "message": (
                "Downloading the {} GGML model (~3GB for large-v3). "
                "This is a one-time setup; subsequent transcriptions "
                "are instant. Poll /api/transcribe/status for "
                "progress, then retry your upload."
                .format(model_name)),
            "model": model_name,
            "bytes_total": _download_state.get("bytes_total", 0),
            "bytes_downloaded": _download_state.get(
                "bytes_downloaded", 0),
        }

    # Stream upload to a temp file
    tmp_dir = Path(tempfile.mkdtemp(prefix="cortex-transcribe-"))
    upload_path = tmp_dir / ("upload" + ext)
    audio_path = tmp_dir / "normalized.wav"
    is_video = ext in VIDEO_EXTS
    total = 0
    try:
        with open(upload_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail={
                        "error": "file_too_large",
                        "max_bytes": MAX_UPLOAD_BYTES,
                        "received_bytes": total})
                out.write(chunk)

        # Always normalize to 16kHz mono WAV — whisper.cpp wants
        # exactly that, and ffmpeg handles every format we accept
        # plus video → audio extraction in one pass.
        log.info("normalizing %s (%d bytes) -> WAV 16kHz mono",
                 upload_path.name, total)
        _normalize_to_wav(upload_path, audio_path)

        # Run whisper.cpp
        log.info("transcribing %s with model %s",
                 audio_path.name, model_name)
        t0 = time.monotonic()
        payload = _run_whisper_cli(
            binary=binary, model_path=model_file,
            audio_path=audio_path,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        transcript, duration_s = _flatten_transcription(payload)
        language = ((payload.get("result") or {})
                    .get("language") or "")

        return {
            "ok": True,
            "transcript": transcript,
            "language": language,
            "model": model_name,
            "duration_s": duration_s,
            "latency_ms": latency_ms,
            "audio_extracted": is_video,
            "source_format": ext,
            "bytes": total,
        }
    finally:
        # Best-effort cleanup
        try:
            for p in (upload_path, audio_path):
                if p.exists():
                    p.unlink()
            tmp_dir.rmdir()
        except OSError as e:
            log.warning("temp cleanup failed: %s", e)


@router.get("/status")
async def transcribe_status():
    """GET /api/transcribe/status — installation + download state.

    UI uses this to:
      - know whether to enable the 🎤 button
      - poll model download progress on first run
    """
    binary = _find_binary()
    binary_path = str(binary) if binary else None
    model_name = _configured_model()
    model_file = _model_path(model_name)
    return {
        "ok": (binary is not None
               and _check_ffmpeg()
               and model_file.is_file()),
        "binary_present": binary is not None,
        "binary_path": binary_path,
        "ffmpeg_installed": _check_ffmpeg(),
        "model": model_name,
        "model_present": model_file.is_file(),
        "model_path": str(model_file),
        "model_download": dict(_download_state),
        "supported_audio_exts": sorted(AUDIO_EXTS),
        "supported_video_exts": sorted(VIDEO_EXTS),
    }


@router.post("/setup")
async def transcribe_setup(model: str | None = Form(None)):
    """POST /api/transcribe/setup — kick off the model download
    explicitly (without uploading a file). Useful for the UI's
    "Set up voice transcription" button on a fresh install.
    """
    model_name = (model or "").strip() or _configured_model()
    if _model_path(model_name).is_file():
        return {"ok": True, "already_present": True,
                "model": model_name}
    started = _start_model_download(model_name)
    return {
        "ok": True,
        "started": started,
        "already_in_flight": not started,
        "model": model_name,
        "model_download": dict(_download_state),
    }
