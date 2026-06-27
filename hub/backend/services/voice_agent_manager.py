"""Launch + supervise the voice_agent sidecar.

The voice agent is a separate pipecat process (its own venv with WebRTC + STT/TTS
deps), NOT bundled into the Hub exe. The Hub starts it on demand and the browser
connects to it directly over local WebRTC.

Single-instance: there is one voice agent. Uses subprocess.Popen + a tail thread
(uvicorn on Windows can't use asyncio subprocesses, same as process_manager.py).
"""
from __future__ import annotations

import collections
import os
import subprocess
import threading
import time
from pathlib import Path

# .../cortex-desktop/hub/backend/services/voice_agent_manager.py
#   parents[3] == cortex-desktop (the dir voice_agent/ lives in + the cwd for -m)
_REPO_ROOT = Path(__file__).resolve().parents[3]

AGENT_PORT = int(os.environ.get("VOICE_AGENT_PORT", "7860"))
MONITOR_PORT = int(os.environ.get("VOICE_MONITOR_PORT", "7861"))

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_log = collections.deque(maxlen=60)
_ready = False  # set when the agent's web server reports it is up


def _find_python() -> str | None:
    """The interpreter for the sidecar's venv (has pipecat + STT/TTS deps)."""
    candidates = [os.environ.get("VOICE_AGENT_PYTHON")]
    candidates.append(str(_REPO_ROOT / "voice_agent" / ".venv" / "Scripts" / "python.exe"))
    # Dev fallback: the prototype venv that already has every dependency.
    candidates.append(str(_REPO_ROOT.parent / "_spikes" / ".venv-kokoro" / "Scripts" / "python.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _tail(proc: subprocess.Popen) -> None:
    global _ready
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", errors="replace").rstrip()
        _log.append(line)
        if "Uvicorn running" in line or "Application startup complete" in line:
            _ready = True


def _status_locked() -> dict:
    running = _proc is not None and _proc.poll() is None
    return {
        "running": running,
        "ready": running and _ready,
        "pid": _proc.pid if running and _proc else None,
        "agent_url": f"http://localhost:{AGENT_PORT}/",
        "monitor_url": f"http://localhost:{MONITOR_PORT}/",
        "python": _find_python(),
    }


def status() -> dict:
    with _lock:
        return _status_locked()


def start() -> dict:
    global _proc, _ready
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return {"ok": True, "already_running": True, **_status_locked()}
        python = _find_python()
        if not python:
            return {"ok": False, "error": "voice_agent venv not found; create "
                    "cortex-desktop/voice_agent/.venv or set VOICE_AGENT_PYTHON"}
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("VOICE_MONITOR_PORT", str(MONITOR_PORT))
        _ready = False
        _log.clear()
        _proc = subprocess.Popen(
            [python, "-m", "voice_agent.bot", "-t", "webrtc"],
            cwd=str(_REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        threading.Thread(target=_tail, args=(_proc,), daemon=True).start()
        time.sleep(0.4)  # surface an immediate crash before returning
        return {"ok": True, "started": True, **_status_locked()}


def stop() -> dict:
    global _proc
    with _lock:
        if _proc is None or _proc.poll() is not None:
            _proc = None
            return {"ok": True, "running": False}
        try:
            _proc.kill()
            _proc.wait(timeout=5)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        _proc = None
        return {"ok": True, "running": False}


def log_tail() -> list[str]:
    return list(_log)
