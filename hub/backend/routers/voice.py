"""Slice 14 (2026-05-21): voice mode for the Overseer Chat tab.

Two endpoints:
  POST /api/voice/stt  — synchronous short-clip transcription. Reuses
                         the bundled whisper-cli (on-device, private)
                         via the transcribe.py helpers. Built for
                         conversational turns (a few seconds of
                         speech) — no polling, no singleton machinery,
                         just transcribe-and-return.
  POST /api/voice/tts  — ElevenLabs TTS proxy (cloud, opt-in). The
                         on-device default is the browser's built-in
                         speechSynthesis API, which needs no backend
                         at all — this endpoint only exists for the
                         ElevenLabs upgrade path.
  GET  /api/voice/config — reports which backends are available
                         (whisper present? elevenlabs/groq keys set?)
                         so the UI can render the right options.

Privacy posture (ties into Slice 13 sensitivity tiers): on-device
STT (whisper-cli) + on-device TTS (browser speechSynthesis) keep all
audio on the machine. The Groq STT and ElevenLabs TTS paths are
explicit opt-in — when enabled, audio/text leaves the host.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

# Reuse the whisper.cpp plumbing built in Slice 7.
from routers.transcribe import (
    _find_binary,
    _model_path,
    _normalize_to_wav,
    _build_whisper_cmd,
    _run_whisper_proc,
    _flatten_transcription,
    _configured_model,
    _check_ffmpeg,
)

from services import voice_agent_manager

log = logging.getLogger("hub.voice")
router = APIRouter()


# ── Hub config (%APPDATA%/Cortex/config.json) ───────────────────
# The Pydantic Settings class is env-var-only; voice API keys are
# user-managed, so they live in the desktop app's config.json. The
# Settings tab (CP3) writes them there.


def _hub_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Cortex"
    else:
        base = Path.home() / ".cortex"
    return base / "config.json"


def _load_hub_config() -> dict:
    p = _hub_config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("could not read hub config.json: %s", e)
        return {}


# ── STT — synchronous short-clip transcription ──────────────────

VOICE_AUDIO_EXTS = {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac"}
MAX_VOICE_CLIP_BYTES = 25 * 1024 * 1024  # 25 MB — a clip, not a file


def _blocking_stt(upload_path: Path, audio_path: Path,
                  binary: Path, model_path: Path) -> dict:
    """Normalize → whisper-cli → flatten. Runs in a threadpool so it
    doesn't block the event loop. Returns {text, duration_s}."""
    _normalize_to_wav(upload_path, audio_path)
    out_base = audio_path.with_suffix("")
    threads = max(1, (os.cpu_count() or 4))
    cmd = _build_whisper_cmd(
        binary=binary, model_path=model_path,
        audio_path=audio_path, out_base=out_base,
        threads=threads, force_cpu=False,
    )
    returncode, stderr_tail = _run_whisper_proc(cmd)
    if returncode != 0:
        raise RuntimeError(
            f"whisper-cli exit {returncode}: {stderr_tail[-400:]}")
    json_path = out_base.with_suffix(".json")
    if not json_path.is_file():
        raise RuntimeError("whisper-cli produced no JSON output")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    finally:
        try:
            json_path.unlink()
        except OSError:
            pass
    transcript, duration_s = _flatten_transcription(payload)
    return {"text": transcript, "duration_s": duration_s}


@router.post("/stt")
async def voice_stt(file: UploadFile = File(...)):
    """POST /api/voice/stt — transcribe one short spoken clip.

    multipart/form-data with `file`. Returns
      { ok, text, duration_s, latency_ms }
    Synchronous: built for conversational turns, blocks until the
    transcript is ready (a few seconds of audio → ~1-3s on GPU).
    """
    if not file.filename:
        raise HTTPException(status_code=400,
                            detail={"error": "no_filename"})
    ext = Path(file.filename).suffix.lower()
    if ext not in VOICE_AUDIO_EXTS:
        # Browsers usually send .webm from MediaRecorder; be lenient
        # and let ffmpeg sort it out, but reject obviously-wrong types.
        ext = ext or ".webm"

    binary = _find_binary()
    if binary is None:
        raise HTTPException(status_code=501, detail={
            "error": "whisper_cli_not_found",
            "message": "whisper-cli isn't bundled with this Hub "
                       "install — voice STT unavailable."})
    if not _check_ffmpeg():
        raise HTTPException(status_code=501, detail={
            "error": "ffmpeg_not_installed",
            "message": "ffmpeg is required for voice STT."})
    model_file = _model_path(_configured_model())
    if not model_file.is_file():
        raise HTTPException(status_code=409, detail={
            "error": "model_not_downloaded",
            "message": "The Whisper model isn't downloaded yet. "
                       "Open the Journal tab and run a transcription "
                       "once, or hit /api/transcribe/setup."})

    tmp_dir = Path(tempfile.mkdtemp(prefix="cortex-voice-"))
    upload_path = tmp_dir / ("clip" + ext)
    audio_path = tmp_dir / "clip.wav"
    total = 0
    t0 = time.monotonic()
    try:
        with open(upload_path, "wb") as out:
            while True:
                chunk = await file.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VOICE_CLIP_BYTES:
                    raise HTTPException(status_code=413, detail={
                        "error": "clip_too_large",
                        "max_bytes": MAX_VOICE_CLIP_BYTES})
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400,
                                detail={"error": "empty_clip"})
        result = await asyncio.to_thread(
            _blocking_stt, upload_path, audio_path, binary, model_file)
        return {
            "ok": True,
            "text": (result["text"] or "").strip(),
            "duration_s": result["duration_s"],
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("voice STT failed")
        raise HTTPException(status_code=500, detail={
            "error": "stt_failed", "message": str(e)[:400]})
    finally:
        for p in (upload_path, audio_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


# ── TTS — ElevenLabs proxy (cloud, opt-in) ──────────────────────


class TtsRequest(BaseModel):
    text: str
    voice_id: str | None = None


ELEVENLABS_TTS_URL = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}")
# A widely-used default ElevenLabs voice id ("Rachel"). The user can
# override per-request or in config.json.
DEFAULT_ELEVENLABS_VOICE = "21m00Tcm4TlvDq8ikWAM"


@router.post("/tts")
async def voice_tts(body: TtsRequest):
    """POST /api/voice/tts — synthesize speech via ElevenLabs.

    Returns audio/mpeg bytes on success. If no ElevenLabs key is
    configured, returns 200 with {ok: false, reason} so the frontend
    can fall back to the browser's on-device speechSynthesis.
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400,
                            detail={"error": "empty_text"})
    cfg = _load_hub_config()
    api_key = (cfg.get("elevenlabs_api_key") or "").strip()
    if not api_key:
        # Not an error — the on-device path is the default.
        return {
            "ok": False,
            "reason": "elevenlabs_not_configured",
            "message": "No ElevenLabs key set — use on-device "
                       "(browser) TTS, or add a key in Settings.",
        }
    voice_id = (body.voice_id
                or cfg.get("elevenlabs_voice_id")
                or DEFAULT_ELEVENLABS_VOICE)
    model_id = cfg.get("elevenlabs_model") or "eleven_turbo_v2_5"
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    req_body = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5,
                            "similarity_boost": 0.75},
    }).encode("utf-8")

    def _call_elevenlabs() -> bytes:
        req = urllib.request.Request(
            url, data=req_body, method="POST",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    try:
        audio = await asyncio.to_thread(_call_elevenlabs)
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode("utf-8", "replace")[:400]
        log.warning("ElevenLabs TTS HTTP %s: %s", e.code, detail)
        raise HTTPException(status_code=502, detail={
            "error": "elevenlabs_http_error",
            "status": e.code, "message": detail})
    except Exception as e:
        log.exception("ElevenLabs TTS failed")
        raise HTTPException(status_code=502, detail={
            "error": "elevenlabs_failed", "message": str(e)[:400]})
    return Response(content=audio, media_type="audio/mpeg")


# ── Config probe ────────────────────────────────────────────────


@router.get("/config")
async def voice_config():
    """GET /api/voice/config — which voice backends are available.

    The UI uses this to decide what to offer: on-device STT/TTS are
    always available (whisper bundled + browser speechSynthesis);
    Groq/ElevenLabs appear only when keys are set."""
    cfg = _load_hub_config()
    binary = _find_binary()
    model_file = _model_path(_configured_model())
    return {
        "ok": True,
        "stt": {
            "on_device_available": (binary is not None
                                    and model_file.is_file()
                                    and _check_ffmpeg()),
            "whisper_model": _configured_model(),
            "groq_configured": bool(
                (cfg.get("groq_api_key") or "").strip()),
        },
        "tts": {
            # Browser speechSynthesis is always available client-side.
            "on_device_available": True,
            "elevenlabs_configured": bool(
                (cfg.get("elevenlabs_api_key") or "").strip()),
            "elevenlabs_voice_id": cfg.get("elevenlabs_voice_id") or "",
        },
        # Persisted user preference (CP3 settings UI writes these).
        "preferred_stt": cfg.get("voice_stt_backend") or "on-device",
        "preferred_tts": cfg.get("voice_tts_backend") or "on-device",
    }


# ── Voice agent (pipecat sidecar) ───────────────────────────────────
# The real-time two-tier voice agent runs as a separate process. The Hub
# launches/supervises it; the browser connects to it directly over WebRTC.


@router.get("/agent/status")
async def voice_agent_status():
    """Is the voice agent sidecar running, and where to reach it."""
    return voice_agent_manager.status()


@router.post("/agent/start")
async def voice_agent_start():
    """Launch the voice agent sidecar (idempotent)."""
    return voice_agent_manager.start()


@router.post("/agent/stop")
async def voice_agent_stop():
    """Stop the voice agent sidecar."""
    return voice_agent_manager.stop()


@router.get("/agent/logs")
async def voice_agent_logs():
    """Recent sidecar stdout (startup + errors), for the Hub UI."""
    return {"lines": voice_agent_manager.log_tail()}
