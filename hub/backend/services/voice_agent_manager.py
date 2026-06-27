"""Launch + supervise the voice_agent sidecar.

The voice agent is a separate pipecat process (its own venv with WebRTC + STT/TTS
deps), NOT bundled into the Hub exe. The Hub starts it on demand and the browser
connects to it directly over local WebRTC.

Single-instance: there is one voice agent. Uses subprocess.Popen + a tail thread
(uvicorn on Windows can't use asyncio subprocesses, same as process_manager.py).
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import threading
import time
from pathlib import Path

# .../cortex-desktop/hub/backend/services/voice_agent_manager.py
#   parents[3] == cortex-desktop in a DEV/source checkout (the dir voice_agent/
#   lives in). In a PACKAGED install this points inside the install tree, which
#   has no voice_agent/ next to it, so the sidecar location comes from config/env.
_REPO_ROOT = Path(__file__).resolve().parents[3]

AGENT_PORT = int(os.environ.get("VOICE_AGENT_PORT", "7860"))
MONITOR_PORT = int(os.environ.get("VOICE_MONITOR_PORT", "7861"))

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_log = collections.deque(maxlen=60)
_ready = False  # set when the agent's web server reports it is up


def _hub_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Cortex"
    else:
        base = Path.home() / ".cortex"
    return base / "config.json"


def _hub_config() -> dict:
    """Read the desktop app's %APPDATA%/Cortex/config.json. Packaged installs
    have no voice_agent/ checkout of their own, so the sidecar's python + dir
    are taken from here (the keys voice_agent_python / voice_agent_dir)."""
    p = _hub_config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_python() -> str | None:
    """The interpreter for the sidecar's venv (has pipecat + STT/TTS deps).

    Priority: VOICE_AGENT_PYTHON env > config.json voice_agent_python > a .venv
    beside a voice_agent/ checkout (dev runs) > the prototype venv.
    """
    cfg = _hub_config()
    candidates = [
        os.environ.get("VOICE_AGENT_PYTHON"),
        cfg.get("voice_agent_python"),
        str(_REPO_ROOT / "voice_agent" / ".venv" / "Scripts" / "python.exe"),
        # Dev fallback: the prototype venv that already has every dependency.
        str(_REPO_ROOT.parent / "_spikes" / ".venv-kokoro" / "Scripts" / "python.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _workdir(python: str | None) -> Path:
    """Directory to run `python -m voice_agent.bot` from. It must CONTAIN the
    voice_agent package. Priority: VOICE_AGENT_DIR env > config.json
    voice_agent_dir > derived from a voice_agent/.venv python > _REPO_ROOT.
    """
    explicit = (os.environ.get("VOICE_AGENT_DIR")
                or _hub_config().get("voice_agent_dir") or "").strip()
    if explicit:
        return Path(explicit)
    # When python is <root>/voice_agent/.venv/Scripts/python.exe, the checkout
    # root (which holds the voice_agent package) is parents[3] — so pointing
    # only voice_agent_python at a dev venv is enough to locate the package too.
    if python:
        p = Path(python)
        try:
            if (p.parent.name.lower() == "scripts"
                    and p.parents[1].name == ".venv"
                    and p.parents[2].name == "voice_agent"):
                return p.parents[3]
        except IndexError:
            pass
    return _REPO_ROOT


def _bot_present(workdir: Path) -> bool:
    return (workdir / "voice_agent" / "bot.py").is_file()


def _tail(proc: subprocess.Popen) -> None:
    global _ready
    for raw in proc.stdout:  # type: ignore[union-attr]
        line = raw.decode("utf-8", errors="replace").rstrip()
        _log.append(line)
        if "Uvicorn running" in line or "Application startup complete" in line:
            _ready = True


def _status_locked() -> dict:
    running = _proc is not None and _proc.poll() is None
    python = _find_python()
    workdir = _workdir(python)
    return {
        "running": running,
        "ready": running and _ready,
        "pid": _proc.pid if running and _proc else None,
        "agent_url": f"http://localhost:{AGENT_PORT}/",
        "monitor_url": f"http://localhost:{MONITOR_PORT}/",
        "python": python,
        "workdir": str(workdir),
        "package_found": _bot_present(workdir),
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
            return {"ok": False, "error": "Sidecar python not found. Set "
                    "voice_agent_python in %APPDATA%/Cortex/config.json (or the "
                    "VOICE_AGENT_PYTHON env var) to the sidecar venv's python.exe, "
                    "e.g. <cortex-desktop>/voice_agent/.venv/Scripts/python.exe."}
        workdir = _workdir(python)
        if not _bot_present(workdir):
            return {"ok": False, "error": f"voice_agent package not found under "
                    f"{workdir}. Set voice_agent_dir in %APPDATA%/Cortex/config.json "
                    "to a cortex-desktop checkout that contains voice_agent/."}
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("VOICE_MONITOR_PORT", str(MONITOR_PORT))
        _ready = False
        _log.clear()
        _proc = subprocess.Popen(
            [python, "-m", "voice_agent.bot", "-t", "webrtc"],
            cwd=str(workdir),
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
