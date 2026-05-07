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

  CP4 (dev.13): transcription itself ALSO runs async in a daemon
  thread. POST /api/transcribe stages the upload, kicks off the
  background runner, returns 202 immediately. Frontend polls
  /api/transcribe/status — the response now carries
  transcribe_state with stage + progress_pct + transcript-when-
  ready. Two side benefits:
    - whisper-cli progress (parsed from -pp stderr) shows live as
      a percentage in the UI textarea/button.
    - Browser refresh during transcription no longer kills the
      job: the state lives server-side, the UI rebinds on next
      load.
  Singleton design — one transcription at a time. Concurrent POST
  while a run is in-flight returns 409 / "transcribe_already_running".
"""

from __future__ import annotations

import json
import logging
import re
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


def _binary_marker(binary_path: Path) -> dict:
    """Read whisper-cli.version next to the binary. Format is
    "<tag>+<backend1>+<backend2>" (e.g. "v1.7.4+vulkan+cpu" or
    "v1.7.4+cpu"). Returns:
      {
        version: "v1.7.4",
        backends: ["vulkan", "cpu"],
        gpu_capable: True,    # vulkan/cuda/metal in backends
        raw: "v1.7.4+vulkan+cpu",
      }
    Best-effort — returns empty dict if marker missing/unreadable."""
    marker = binary_path.with_suffix(".version")
    if not marker.is_file():
        return {}
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    parts = raw.split("+")
    version = parts[0] if parts else ""
    backends = [p for p in parts[1:] if p]
    gpu_backends = {"vulkan", "cuda", "metal", "rocm", "opencl"}
    return {
        "version": version,
        "backends": backends,
        "gpu_capable": any(b in gpu_backends for b in backends),
        "raw": raw,
    }


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


# ── Async transcription state (CP4) ──────────────────────────────


# whisper-cli with -pp prints lines like
#   "whisper_print_progress_callback: progress = 5%"
# to stderr. We grep these as we stream the output.
_PROGRESS_RX = re.compile(r"progress\s*=\s*(\d+)\s*%")


# Native Windows fastfail exit codes that mean "the binary itself
# tripped over an instruction the CPU/runtime won't execute"
# rather than "the inputs were bad" or "transcription failed
# normally." If we see one of these we surface a much clearer
# error than "non-zero exit code."
#   0xC000001D STATUS_ILLEGAL_INSTRUCTION — usually AVX-512 in a
#              binary running on a CPU without AVX-512 (e.g.
#              every Intel hybrid SKU since Alder Lake, most
#              consumer Ryzens).
#   0xC0000409 STATUS_STACK_BUFFER_OVERRUN — fastfail / __chkstk
#              violation. Often a corrupt binary, a bad runtime
#              DLL, or a Vulkan driver crashing back into the
#              process. Also seen with some illegal AVX-512
#              variants depending on Windows/CPU combo.
#   0xC0000005 STATUS_ACCESS_VIOLATION — null deref / bad pointer.
#              On Windows subprocess.Popen these come back as
#              negative integers (Python signs the DWORD).
_HARD_CRASH_CODES = {
    0xC000001D: "ILLEGAL_INSTRUCTION",
    0xC0000409: "STACK_BUFFER_OVERRUN",
    0xC0000005: "ACCESS_VIOLATION",
}


def _classify_exit_code(code: int) -> str | None:
    """Return a short tag for the native-fault exit codes we care
    about, or None for normal non-zero exits. Handles Windows'
    signed/unsigned DWORD ambiguity (Python returns negative ints
    for codes ≥ 0x80000000)."""
    if code is None:
        return None
    # Normalize negative-signed DWORD -> unsigned
    unsigned = code & 0xFFFFFFFF
    return _HARD_CRASH_CODES.get(unsigned)


# Sticky flag: once we've seen a hard native crash on this binary,
# we stop attempting GPU and run -ng on every subsequent transcription
# until the Hub restarts. The crash signature is also surfaced via
# /api/transcribe/status so the UI can show a clear remediation
# banner. Reset on successful run.
_runtime_flags_lock = threading.Lock()
_runtime_flags: dict = {
    "force_cpu": False,           # set when a crash demoted us to -ng
    "last_hard_crash": None,      # tag from _classify_exit_code, or None
    "last_hard_crash_at": None,   # epoch seconds
    "fallback_succeeded": False,  # True if -ng finished after a GPU crash
}


# Singleton state. Personal-use Hub runs one transcription at a
# time; concurrent POSTs return 409. Polled by /api/transcribe/status.
_transcribe_lock = threading.Lock()
_transcribe_state: dict = {
    "in_progress": False,
    # 'queued' | 'normalizing' | 'transcribing' | 'ready' | 'error'
    "stage": "idle",
    "progress_pct": 0,
    "started_at": None,
    "finished_at": None,
    "model": "",
    "filename": "",
    "bytes": 0,
    "source_format": "",
    "audio_extracted": False,
    "duration_s": 0.0,
    "language": "",
    "latency_ms": 0,
    "transcript": None,
    "error": None,
}


def _reset_transcribe_state(*, model: str, filename: str,
                              bytes_: int, source_format: str,
                              audio_extracted: bool) -> None:
    """Caller has the lock. Resets state for a fresh run."""
    _transcribe_state.update({
        "in_progress": True,
        "stage": "queued",
        "progress_pct": 0,
        "started_at": time.time(),
        "finished_at": None,
        "model": model,
        "filename": filename,
        "bytes": bytes_,
        "source_format": source_format,
        "audio_extracted": audio_extracted,
        "duration_s": 0.0,
        "language": "",
        "latency_ms": 0,
        "transcript": None,
        "error": None,
    })


def _build_whisper_cmd(*, binary: Path, model_path: Path,
                        audio_path: Path, out_base: Path,
                        threads: int, force_cpu: bool) -> list[str]:
    cmd = [
        str(binary),
        "-m", str(model_path),
        "-f", str(audio_path),
        "-t", str(threads),
        "-oj",
        "-of", str(out_base),
        "-l", "auto",
        "-pp",                  # print progress (we stream it)
        "-nt",
    ]
    if force_cpu:
        cmd.append("-ng")       # whisper-cli: disable GPU backend
    return cmd


def _run_whisper_proc(cmd: list[str]) -> tuple[int, str]:
    """Launch whisper-cli, drain stderr while updating progress,
    return (returncode, stderr_tail). Reads stderr from a pipe in
    the same thread, blocking until the child exits. Tail capped
    at 2000 chars; line buffer trimmed to last 50 lines so a
    chatty subprocess can't balloon memory."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    tail_buf: list[str] = []
    if proc.stderr is not None:
        for raw in iter(proc.stderr.readline, ""):
            if not raw:
                break
            tail_buf.append(raw)
            if len(tail_buf) > 50:
                tail_buf = tail_buf[-50:]
            m = _PROGRESS_RX.search(raw)
            if m:
                try:
                    _transcribe_state["progress_pct"] = int(m.group(1))
                except (ValueError, KeyError):
                    pass
    try:
        proc.wait(timeout=3600)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("whisper-cli timeout after 1 hour")
    return proc.returncode, "".join(tail_buf)[-2000:]


def _transcribe_background(*, upload_path: Path, audio_path: Path,
                            tmp_dir: Path,
                            binary: Path, model_path: Path,
                            model_name: str, ext: str,
                            is_video: bool) -> None:
    """Daemon thread entry point. Owns its temp dir and cleans up
    in finally. Mutates _transcribe_state as it progresses."""
    t_start = time.monotonic()
    try:
        # ── normalize ────────────────────────────────────────────
        _transcribe_state["stage"] = "normalizing"
        _normalize_to_wav(upload_path, audio_path)

        # ── transcribe ───────────────────────────────────────────
        _transcribe_state["stage"] = "transcribing"
        _transcribe_state["progress_pct"] = 0

        out_base = audio_path.with_suffix("")
        threads = max(1, (os.cpu_count() or 4))

        # Decide whether to force CPU on this run. Two paths in:
        # (a) explicit setting `whisper_force_cpu=True` in config,
        # (b) sticky runtime flag set by a prior hard crash on the
        # GPU path during this process lifetime.
        cfg_force_cpu = bool(getattr(settings, "whisper_force_cpu", False))
        with _runtime_flags_lock:
            sticky_force_cpu = _runtime_flags["force_cpu"]
        force_cpu = cfg_force_cpu or sticky_force_cpu

        cmd = _build_whisper_cmd(
            binary=binary, model_path=model_path,
            audio_path=audio_path, out_base=out_base,
            threads=threads, force_cpu=force_cpu,
        )
        log.info("transcribing %s with model %s (background)%s",
                 audio_path.name, model_name,
                 " [-ng forced]" if force_cpu else "")
        returncode, stderr_tail = _run_whisper_proc(cmd)

        # Hard-crash retry path: if the GPU run died with a native
        # fastfail exit code, demote to -ng and try once more. This
        # rescues the common cases of transient Vulkan-driver
        # crashes (NVIDIA / AMD post-driver-update).
        # NOTE: this does NOT rescue an AVX-512-in-binary crash —
        # CPU mode crashes the same way, since the crash is in
        # ggml's CPU kernels too. In that case the second run also
        # fails and we fall through to the clearer error message.
        if (returncode != 0 and not force_cpu
                and _classify_exit_code(returncode) is not None):
            crash_tag = _classify_exit_code(returncode)
            log.warning(
                "whisper-cli hard crash (%s, code %d) on GPU path; "
                "retrying with -ng",
                crash_tag, returncode)
            with _runtime_flags_lock:
                _runtime_flags["last_hard_crash"] = crash_tag
                _runtime_flags["last_hard_crash_at"] = time.time()
            cmd = _build_whisper_cmd(
                binary=binary, model_path=model_path,
                audio_path=audio_path, out_base=out_base,
                threads=threads, force_cpu=True,
            )
            _transcribe_state["progress_pct"] = 0
            returncode, stderr_tail = _run_whisper_proc(cmd)
            if returncode == 0:
                # CPU fallback worked — pin force_cpu sticky for
                # the rest of this process so future runs go
                # straight to CPU.
                with _runtime_flags_lock:
                    _runtime_flags["force_cpu"] = True
                    _runtime_flags["fallback_succeeded"] = True
                log.info(
                    "whisper-cli -ng fallback succeeded; pinning "
                    "force_cpu for remainder of session")

        if returncode != 0:
            crash_tag = _classify_exit_code(returncode)
            if crash_tag is not None:
                with _runtime_flags_lock:
                    _runtime_flags["last_hard_crash"] = crash_tag
                    _runtime_flags["last_hard_crash_at"] = time.time()
                # Both GPU and CPU paths crashed natively — almost
                # certainly an AVX-512-in-binary issue, a corrupt
                # whisper-cli, or a missing C++ runtime. Surface a
                # clear remediation message rather than dumping
                # stderr.
                raise RuntimeError(
                    "whisper-cli crashed with " + crash_tag
                    + " (exit 0x{:08X}). This usually means the "
                    "bundled binary uses CPU instructions your "
                    "machine doesn't have (e.g. AVX-512 on a "
                    "modern Intel/AMD desktop). Fix: update the "
                    "Cortex Hub to v0.18.0-dev.14 or later — that "
                    "release ships an AVX2-baseline binary that "
                    "runs on every x86_64 CPU since 2013. "
                    "Alternatively, reinstall the current Hub to "
                    "rule out a corrupted binary copy.".format(
                        returncode & 0xFFFFFFFF))
            raise RuntimeError(
                "whisper-cli exit {}: {}".format(
                    returncode, stderr_tail))

        json_path = out_base.with_suffix(".json")
        if not json_path.is_file():
            raise RuntimeError(
                "whisper-cli ran but didn't write JSON output")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        finally:
            try:
                json_path.unlink()
            except OSError:
                pass

        transcript, duration_s = _flatten_transcription(payload)
        language = ((payload.get("result") or {})
                    .get("language") or "")

        _transcribe_state.update({
            "transcript": transcript,
            "language": language,
            "duration_s": duration_s,
            "progress_pct": 100,
            "stage": "ready",
            "latency_ms": int((time.monotonic() - t_start) * 1000),
            "finished_at": time.time(),
        })
        log.info("transcription ready (%d chars, %.1fs audio, %dms wall)",
                 len(transcript), duration_s,
                 _transcribe_state["latency_ms"])
    except Exception as e:
        _transcribe_state.update({
            "stage": "error",
            "error": str(e),
            "finished_at": time.time(),
            "latency_ms": int((time.monotonic() - t_start) * 1000),
        })
        log.exception("background transcription failed: %s", e)
    finally:
        _transcribe_state["in_progress"] = False
        # Best-effort temp cleanup
        try:
            for p in (upload_path, audio_path):
                if p.exists():
                    p.unlink()
            tmp_dir.rmdir()
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

    # CP4: refuse a second concurrent transcription. Returns 409
    # so the UI can show "already running, polling for current."
    with _transcribe_lock:
        if _transcribe_state["in_progress"]:
            return {
                "ok": False,
                "status": "transcribe_already_running",
                "message": "A transcription is already in progress. "
                           "Poll /api/transcribe/status for its state.",
                "transcribe_state": dict(_transcribe_state),
            }

    # Stream upload to a temp file. We do the read in the request
    # handler (FastAPI's UploadFile is async) before handing off to
    # the background thread.
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
    except Exception:
        # Upload failed before we even started — clean up.
        for p in (upload_path,):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        raise

    # Hand off to the background runner. Acquire the lock again
    # to publish the initial state atomically with `in_progress`.
    with _transcribe_lock:
        if _transcribe_state["in_progress"]:
            # Race: another POST snuck in between our checks.
            for p in (upload_path,):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass
            return {
                "ok": False,
                "status": "transcribe_already_running",
                "message": "A transcription is already in progress.",
                "transcribe_state": dict(_transcribe_state),
            }
        _reset_transcribe_state(
            model=model_name,
            filename=file.filename or "",
            bytes_=total,
            source_format=ext,
            audio_extracted=is_video,
        )

    t = threading.Thread(
        target=_transcribe_background,
        kwargs={
            "upload_path": upload_path,
            "audio_path": audio_path,
            "tmp_dir": tmp_dir,
            "binary": binary,
            "model_path": model_file,
            "model_name": model_name,
            "ext": ext,
            "is_video": is_video,
        },
        daemon=True,
        name="whisper-transcribe",
    )
    t.start()

    # 202 Accepted shape — UI polls /api/transcribe/status from here.
    return {
        "ok": False,
        "status": "transcribing",
        "message": "Transcription started. Poll "
                   "/api/transcribe/status for progress.",
        "transcribe_state": dict(_transcribe_state),
    }


@router.get("/status")
async def transcribe_status():
    """GET /api/transcribe/status — installation + download state.

    UI uses this to:
      - know whether to enable the 🎤 button
      - poll model download progress on first run
    """
    binary = _find_binary()
    binary_path = str(binary) if binary else None
    binary_info = _binary_marker(binary) if binary else {}
    model_name = _configured_model()
    model_file = _model_path(model_name)
    with _runtime_flags_lock:
        runtime_flags = dict(_runtime_flags)
    return {
        "ok": (binary is not None
               and _check_ffmpeg()
               and model_file.is_file()),
        "binary_present": binary is not None,
        "binary_path": binary_path,
        "binary_version": binary_info.get("version", ""),
        "binary_backends": binary_info.get("backends", []),
        "gpu_capable": binary_info.get("gpu_capable", False),
        "ffmpeg_installed": _check_ffmpeg(),
        "model": model_name,
        "model_present": model_file.is_file(),
        "model_path": str(model_file),
        "model_download": dict(_download_state),
        # CP4: live transcription state for UI polling. Stage values:
        #   'idle' | 'queued' | 'normalizing' | 'transcribing'
        #   | 'ready' | 'error'
        "transcribe_state": dict(_transcribe_state),
        # dev.14: native-fault state. UI surfaces a remediation
        # banner when last_hard_crash is set.
        "runtime_flags": runtime_flags,
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
