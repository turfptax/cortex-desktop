"""Slice 7: local Whisper transcription for the human journal.

Audio + video files upload here, Whisper transcribes locally
(default model: large-v3), the transcript is returned to the
caller. The caller (Hub UI Journal tab) pre-fills its textarea
with the transcript so the user can edit before committing via
the existing /api/overseer/human-journal endpoint.

DESIGN

  - Whisper runs LOCALLY on the desktop (Hub backend's host).
    File never leaves the user's machine. Matches the privacy
    framing of the rest of the Cortex memory layer.
  - whisper module is lazy-imported. If it (or ffmpeg) isn't
    installed, this router returns a clear setup-instructions
    error; the rest of the Hub keeps working.
  - Models cached under %APPDATA%/Cortex/whisper-models/ so
    the first transcription pays the download cost once and
    subsequent ones are instant.
  - Default model: large-v3 (best accuracy Whisper offers).
    Configurable via Cortex config.json: whisper_model.

NOT IN CP1

  - MCP tool wrapper (cortex_human_journal_transcribe) — CP2
  - Auto-install of openai-whisper on first use — CP2 if friction
  - Speaker diarization (different stack)
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from config import settings


log = logging.getLogger("hub.transcribe")

router = APIRouter()


# ── Constants ────────────────────────────────────────────────────


# Hard cap on uploads. Anything bigger should be chunked outside.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

# Whisper handles all of these directly OR via its internal ffmpeg
# call. We accept the union; format detection determines whether
# we pre-extract or pass straight through.
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
              ".opus", ".wma"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".mpg",
              ".mpeg", ".m4v", ".3gp"}

# Default model. Tory's pick: "the more powerful one" — large-v3
# is the best accuracy Whisper offers (~3GB cached). Override via
# Cortex config.json: whisper_model.
DEFAULT_WHISPER_MODEL = "large-v3"


# ── Lazy module load with clear setup error ──────────────────────


_whisper_cached = None
_whisper_load_error: str | None = None


def _get_whisper():
    """Import whisper lazily so the rest of the Hub still runs if it
    isn't installed. Returns the module or raises HTTPException with
    setup instructions."""
    global _whisper_cached, _whisper_load_error
    if _whisper_cached is not None:
        return _whisper_cached
    try:
        import whisper  # type: ignore
        _whisper_cached = whisper
        return whisper
    except ImportError as e:
        _whisper_load_error = str(e)
        raise HTTPException(
            status_code=501,
            detail={
                "error": "whisper_not_installed",
                "message": (
                    "Voice transcription needs the openai-whisper "
                    "package. Install with:\n\n"
                    "    pip install openai-whisper\n\n"
                    "(This is a one-time ~2GB install due to PyTorch.)"
                ),
                "import_error": str(e),
            },
        )


def _check_ffmpeg() -> bool:
    """Return True if ffmpeg is on PATH. Whisper uses it internally
    for everything except WAV input; we use it explicitly for video
    audio extraction. Cached for the process lifetime."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, check=True, timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return False


# ── Model cache ─────────────────────────────────────────────────


_loaded_model = None
_loaded_model_name: str | None = None


def _model_cache_dir() -> Path:
    """%APPDATA%/Cortex/whisper-models on Windows, ~/.cortex/whisper-
    models elsewhere. Whisper handles the actual download + caching
    once we point it at this dir via the download_root arg."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Cortex"
    else:
        base = Path.home() / ".cortex"
    cache = base / "whisper-models"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _get_model(model_name: str):
    """Load (and cache) a Whisper model. First call for a given model
    triggers a download into the cache dir. Subsequent calls return
    the in-memory model immediately."""
    global _loaded_model, _loaded_model_name
    if _loaded_model is not None and _loaded_model_name == model_name:
        return _loaded_model
    whisper = _get_whisper()
    log.info("loading Whisper model %s (this may take a while on "
             "first use; downloading to %s)",
             model_name, _model_cache_dir())
    _loaded_model = whisper.load_model(
        model_name, download_root=str(_model_cache_dir()),
    )
    _loaded_model_name = model_name
    log.info("Whisper model %s loaded", model_name)
    return _loaded_model


# ── Audio extraction (video → audio) ────────────────────────────


def _extract_audio_from_video(video_path: Path,
                                output_path: Path) -> None:
    """Strip audio from a video file via ffmpeg. Output is wav so
    Whisper doesn't need to re-decode."""
    cmd = [
        "ffmpeg",
        "-y",                     # overwrite output if exists
        "-i", str(video_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # 16-bit PCM (whisper-friendly)
        "-ar", "16000",           # 16kHz (whisper's native rate)
        "-ac", "1",               # mono
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, check=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail={"error": "ffmpeg_timeout",
                    "message": "Audio extraction took longer than "
                               "10 minutes. The video may be too long "
                               "or corrupted."})
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "ffmpeg_failed",
                    "message": "ffmpeg couldn't extract audio.",
                    "stderr": (e.stderr or b"").decode(
                        "utf-8", errors="replace")[:1000]})


# ── Helper: configured model name ───────────────────────────────


def _configured_model() -> str:
    # Hub config (Pydantic settings) — falls back to default if not set.
    return getattr(settings, "whisper_model", DEFAULT_WHISPER_MODEL)


# ── Routes ──────────────────────────────────────────────────────


@router.post("")
async def transcribe(
    file: UploadFile = File(...),
    model: str | None = Form(None),
):
    """POST /api/transcribe

    multipart/form-data:
      file:  audio or video file (audio: mp3/wav/m4a/ogg/flac/...;
             video: mp4/mov/webm/mkv/...)
      model: optional override; defaults to config.whisper_model
             or 'large-v3'

    Response shape:
      {
        ok: true,
        transcript: "...",
        language: "en",
        model: "large-v3",
        duration_s: 42.3,
        latency_ms: 8400,
        audio_extracted: false,
        source_format: ".mp3",
        bytes: 1234567,
      }

    File is processed entirely on the local machine — no network
    calls during transcription. Temp files are deleted after the
    response is built.
    """
    if not file.filename:
        raise HTTPException(status_code=400,
                            detail={"error": "no_filename"})

    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS and ext not in VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_format",
                "extension": ext,
                "supported_audio": sorted(AUDIO_EXTS),
                "supported_video": sorted(VIDEO_EXTS),
            },
        )

    is_video = ext in VIDEO_EXTS

    # If we'll need ffmpeg for video extraction, sanity-check now.
    if is_video and not _check_ffmpeg():
        raise HTTPException(
            status_code=501,
            detail={
                "error": "ffmpeg_not_installed",
                "message": (
                    "Video transcription needs ffmpeg on PATH. "
                    "Install:\n\n"
                    "  Windows:  winget install ffmpeg\n"
                    "  macOS:    brew install ffmpeg\n"
                    "  Linux:    apt install ffmpeg"
                ),
            },
        )

    # Read upload to a temp file. Bound the size before we commit
    # disk I/O.
    tmp_dir = Path(tempfile.mkdtemp(prefix="cortex-transcribe-"))
    upload_path = tmp_dir / ("upload" + ext)
    audio_path = upload_path

    total = 0
    try:
        with open(upload_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "file_too_large",
                            "max_bytes": MAX_UPLOAD_BYTES,
                            "received_bytes": total,
                        },
                    )
                out.write(chunk)

        # Extract audio if video
        audio_extracted = False
        if is_video:
            audio_path = tmp_dir / "extracted.wav"
            log.info("extracting audio from video (%d bytes)", total)
            _extract_audio_from_video(upload_path, audio_path)
            audio_extracted = True

        # Transcribe
        model_name = (model or "").strip() or _configured_model()
        loaded = _get_model(model_name)

        log.info("transcribing %s (model=%s, audio_extracted=%s)",
                 upload_path.name, model_name, audio_extracted)
        t0 = time.monotonic()
        result = loaded.transcribe(
            str(audio_path),
            verbose=False,
            # fp16 is auto-detected (CUDA only) — safe default.
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        transcript = (result.get("text") or "").strip()
        language = result.get("language") or ""
        # Whisper returns segments[] each with end timestamp; the
        # last one's `end` is the audio duration.
        segments = result.get("segments") or []
        duration_s = round(float(segments[-1]["end"]), 1) \
            if segments else 0.0

        return {
            "ok": True,
            "transcript": transcript,
            "language": language,
            "model": model_name,
            "duration_s": duration_s,
            "latency_ms": latency_ms,
            "audio_extracted": audio_extracted,
            "source_format": ext,
            "bytes": total,
        }
    finally:
        # Best-effort cleanup of temp files
        try:
            if upload_path.exists():
                upload_path.unlink()
            if audio_path != upload_path and audio_path.exists():
                audio_path.unlink()
            tmp_dir.rmdir()
        except Exception as e:
            log.warning("temp cleanup failed: %s", e)


@router.get("/status")
async def transcribe_status():
    """Quick health probe for the UI. Returns whether Whisper +
    ffmpeg are installed AND the configured model name. UI uses
    this to decide whether to show the 🎤 button or a setup hint."""
    whisper_ok = False
    whisper_error: str | None = None
    try:
        _get_whisper()
        whisper_ok = True
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {}
        whisper_error = d.get("message") or "whisper not installed"

    ffmpeg_ok = _check_ffmpeg()
    return {
        "ok": whisper_ok and ffmpeg_ok,
        "whisper_installed": whisper_ok,
        "ffmpeg_installed": ffmpeg_ok,
        "whisper_error": whisper_error,
        "model": _configured_model(),
        "model_loaded": _loaded_model_name,
        "supported_audio_exts": sorted(AUDIO_EXTS),
        "supported_video_exts": sorted(VIDEO_EXTS),
    }
