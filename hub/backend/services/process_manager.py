"""Subprocess manager for training pipeline scripts.

Uses threading + subprocess.Popen instead of asyncio subprocess
because uvicorn on Windows uses SelectorEventLoop which doesn't
support asyncio.create_subprocess_exec.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from config import settings


@dataclass
class Job:
    job_id: str
    step: str
    script: str
    status: str = "pending"  # pending, running, completed, failed
    start_time: float = 0.0
    end_time: float = 0.0
    return_code: int | None = None
    log_lines: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict:
        # Check if process died without us noticing
        if self.status == "running" and self.process:
            rc = self.process.poll()
            if rc is not None:
                self.return_code = rc
                self.status = "completed" if rc == 0 else "failed"
                self.end_time = self.end_time or time.time()

        return {
            "job_id": self.job_id,
            "step": self.step,
            "script": self.script,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "return_code": self.return_code,
            "log_line_count": len(self.log_lines),
            "elapsed_s": round(self.end_time - self.start_time, 1)
            if self.end_time
            else round(time.time() - self.start_time, 1)
            if self.start_time
            else 0,
        }


# Pipeline step definitions
STEPS = {
    "00": {
        "name": "Sync Data",
        "script": "00_sync_data.py",
        "description": "Pull cortex.db from Pi via SCP and export notes/sessions to JSONL files for training",
        "args": ["--export-only"],
    },
    "01": {
        "name": "Synthesize Notes",
        "script": "01_synthesize_notes.py",
        "description": "Send Pi notes to LM Studio teacher model to generate Q&A training examples (legacy — use Learning tab instead)",
    },
    "02": {
        "name": "Prepare Dataset",
        "script": "02_prepare_dataset.py",
        "description": "Combine learned examples, curated examples, and personality data into a ChatML dataset for PyTorch training",
    },
    "03": {
        "name": "Train LoRA",
        "script": "03_train_pet.py",
        "description": "Fine-tune the base model (SmolLM2/Qwen) with a lightweight LoRA adapter using PyTorch — requires GPU with CUDA",
    },
    "04": {
        "name": "Evaluate",
        "script": "04_eval.py",
        "description": "Compare base model vs fine-tuned model on test prompts — measures perplexity and response quality improvement",
        "args": ["--save"],
    },
    "05": {
        "name": "Auto-Research",
        "script": "05_autoresearch.py",
        "description": "Automated hyperparameter search — tries different LoRA ranks, learning rates, etc. to find the best config",
    },
    "07": {
        "name": "Learn Cycle",
        "script": "07_learn_cycle.py",
        "description": "Pull new data from Pi and synthesize training examples via LM Studio (legacy script — use Learning tab instead)",
    },
    "06": {
        "name": "Export & Deploy",
        "script": "06_export_deploy.py",
        "description": "Merge LoRA adapter into base model, convert to GGUF format, and deploy the updated model to the Pi via SCP",
        "args": ["--merge"],
    },
    "pong_train": {
        "name": "Train Pong AI",
        "script": "pong_train.py",
        "description": "Q-learning training for Pong AI opponent",
    },
}

# Active jobs
_jobs: dict[str, Job] = {}
# SSE subscribers per job — asyncio queues that background threads push into
_subscribers: dict[str, list[asyncio.Queue]] = {}
# Reference to the main event loop for cross-thread queue puts
_loop: asyncio.AbstractEventLoop | None = None


def get_steps() -> list[dict]:
    """Return pipeline step definitions with current status."""
    result = []
    for step_id, step_info in STEPS.items():
        entry = {
            "id": step_id,
            "name": step_info["name"],
            "script": step_info["script"],
            "description": step_info["description"],
        }
        # Find latest job for this step
        latest = None
        for job in _jobs.values():
            if job.step == step_id:
                if latest is None or job.start_time > latest.start_time:
                    latest = job
        if latest:
            entry["latest_job"] = latest.to_dict()
        result.append(entry)
    return result


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def reset_all_jobs():
    """Clear all jobs — use when stuck zombie jobs need cleanup."""
    global _jobs, _subscribers
    for job in _jobs.values():
        if job.process and job.status == "running":
            try:
                job.process.kill()
            except Exception:
                pass
    _jobs = {}
    _subscribers = {}


def _notify_subscribers(job_id: str, message: str | None):
    """Thread-safe: push a message to all SSE subscriber queues."""
    loop = _loop
    if not loop:
        return
    for queue in _subscribers.get(job_id, []):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except Exception:
            pass


def _read_output_thread(job: Job):
    """Read subprocess stdout in a background thread and broadcast lines."""
    try:
        for raw_line in job.process.stdout:
            try:
                text = raw_line.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                text = raw_line.decode("utf-8", errors="replace").rstrip()

            with job._lock:
                job.log_lines.append(text)

            _notify_subscribers(job.job_id, text)

        # Wait for process to finish
        job.process.wait()
        job.return_code = job.process.returncode
        job.status = "completed" if job.return_code == 0 else "failed"
        job.end_time = time.time()

        status_msg = f"[JOB {job.status.upper()}] exit code {job.return_code}"
        with job._lock:
            job.log_lines.append(status_msg)

        _notify_subscribers(job.job_id, status_msg)
        _notify_subscribers(job.job_id, None)  # Signal end

    except Exception as e:
        job.status = "failed"
        job.end_time = time.time()
        error_msg = f"[ERROR] {e}"
        with job._lock:
            job.log_lines.append(error_msg)

        _notify_subscribers(job.job_id, error_msg)
        _notify_subscribers(job.job_id, None)


def _try_cortex_train_import():
    """Try to import cortex_train for direct function calls."""
    try:
        from cortex_train.config import load_settings
        from cortex_train.paths import TrainPaths
        from cortex_train.progress import ProgressEvent
        return True
    except ImportError:
        return False


# Map step IDs to cortex_train step functions and their kwargs
_DIRECT_STEP_MAP = {
    "00": ("cortex_train.steps.sync", "run_sync", {"export_only": True}),
    "02": ("cortex_train.steps.prepare", "run_prepare", {}),
    "03": ("cortex_train.steps.train", "run_train", {}),
    "04": ("cortex_train.steps.evaluate", "run_evaluate", {"save": True}),
    "06": ("cortex_train.steps.deploy", "run_deploy", {"merge": True}),
}


def _run_direct_thread(job: Job, step: str):
    """Run a cortex_train step function directly in a thread."""
    try:
        from cortex_train.config import load_settings
        from cortex_train.paths import TrainPaths
        from cortex_train.progress import ProgressEvent
        import importlib

        step_module, step_func, default_kwargs = _DIRECT_STEP_MAP[step]
        mod = importlib.import_module(step_module)
        fn = getattr(mod, step_func)

        paths = TrainPaths()
        train_settings = load_settings(paths.settings_path)

        # Progress callback that feeds into the Job/SSE system
        def on_progress(event: ProgressEvent):
            pct_str = f" ({event.pct:.0f}%)" if event.pct is not None else ""
            line = f"[{event.timestamp}] [{event.step}]{pct_str} {event.message}"
            with job._lock:
                job.log_lines.append(line)
            _notify_subscribers(job.job_id, line)

        result = fn(
            settings=train_settings,
            paths=paths,
            on_progress=on_progress,
            **default_kwargs,
        )

        # Report result
        ok = result.get("ok", False)
        summary = json.dumps(result, indent=2, default=str)
        with job._lock:
            job.log_lines.append(summary)
        _notify_subscribers(job.job_id, summary)

        job.return_code = 0 if ok else 1
        job.status = "completed" if ok else "failed"
    except Exception as e:
        error_msg = f"[ERROR] {type(e).__name__}: {e}"
        with job._lock:
            job.log_lines.append(error_msg)
        _notify_subscribers(job.job_id, error_msg)
        job.return_code = 1
        job.status = "failed"
    finally:
        job.end_time = time.time()
        status_msg = f"[JOB {job.status.upper()}] exit code {job.return_code}"
        with job._lock:
            job.log_lines.append(status_msg)
        _notify_subscribers(job.job_id, status_msg)
        _notify_subscribers(job.job_id, None)  # Signal end


async def start_job(step: str, extra_args: list[str] | None = None) -> Job:
    """Start a training pipeline step.

    Tries cortex_train direct import first for supported steps,
    falls back to subprocess for all steps."""
    global _loop

    if step not in STEPS:
        raise ValueError(f"Unknown step: {step}")

    # Capture the event loop for cross-thread communication
    _loop = asyncio.get_running_loop()

    step_info = STEPS[step]
    job_id = str(uuid.uuid4())[:8]
    script_path = Path(settings.scripts_dir) / step_info["script"]

    job = Job(
        job_id=job_id,
        step=step,
        script=str(script_path),
    )
    _jobs[job_id] = job
    _subscribers[job_id] = []

    # Try cortex_train direct import for supported steps (no subprocess overhead)
    if step in _DIRECT_STEP_MAP and not extra_args and _try_cortex_train_import():
        job.status = "running"
        job.start_time = time.time()

        with job._lock:
            job.log_lines.append(f"[cortex_train] Running step '{step}' directly (no subprocess)")

        thread = threading.Thread(
            target=_run_direct_thread,
            args=(job, step),
            daemon=True,
            name=f"job-{job_id}-direct",
        )
        thread.start()
        return job

    # Fallback: subprocess execution
    # Build command — find a real Python interpreter
    # In PyInstaller bundle, sys.executable is the exe, not Python
    if getattr(sys, '_MEIPASS', None):
        # Try to find system Python
        import shutil
        python_exe = shutil.which("python") or shutil.which("python3")
        if not python_exe:
            raise RuntimeError(
                "Cannot find Python interpreter. Install Python and ensure it's on PATH."
            )
    else:
        python_exe = sys.executable
    cmd = [python_exe, "-u", str(script_path)]
    default_args = step_info.get("args", [])
    cmd.extend(default_args)
    if extra_args:
        cmd.extend(extra_args)

    # Force UTF-8 for subprocess to handle emoji/unicode output
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    job.status = "running"
    job.start_time = time.time()

    # Launch subprocess with Popen (works on all Windows event loops)
    # stdin=DEVNULL so scripts detect non-interactive mode (sys.stdin.isatty() == False)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=settings.scripts_dir,
        env=env,
    )
    job.process = process

    # Read output in a background thread
    thread = threading.Thread(
        target=_read_output_thread,
        args=(job,),
        daemon=True,
        name=f"job-{job_id}-reader",
    )
    thread.start()

    return job


async def subscribe_logs(job_id: str) -> AsyncGenerator[str, None]:
    """Subscribe to live log output for a job."""
    job = _jobs.get(job_id)
    if not job:
        yield f"data: {json.dumps({'error': f'Job not found: {job_id}'})}\n\n"
        return

    queue: asyncio.Queue = asyncio.Queue()

    # Send existing lines first (thread-safe read)
    with job._lock:
        existing = list(job.log_lines)
    for line in existing:
        yield f"data: {json.dumps({'line': line}, ensure_ascii=True)}\n\n"

    # If job already finished, stop here
    if job.status in ("completed", "failed"):
        yield f"data: {json.dumps({'status': job.status, 'return_code': job.return_code})}\n\n"
        return

    # Subscribe for new lines
    _subscribers.setdefault(job_id, []).append(queue)
    try:
        while True:
            line = await queue.get()
            if line is None:
                yield f"data: {json.dumps({'status': job.status, 'return_code': job.return_code})}\n\n"
                break
            yield f"data: {json.dumps({'line': line}, ensure_ascii=True)}\n\n"
    finally:
        if queue in _subscribers.get(job_id, []):
            _subscribers[job_id].remove(queue)


async def stop_job(job_id: str) -> bool:
    """Kill a running job."""
    job = _jobs.get(job_id)
    if not job or not job.process:
        return False
    if job.status != "running":
        return False
    try:
        job.process.kill()
        job.process.wait(timeout=5)
        job.status = "failed"
        job.end_time = time.time()
        job.return_code = -9
        return True
    except Exception:
        return False
