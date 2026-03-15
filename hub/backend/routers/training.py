"""Training pipeline router — run scripts, stream logs, manage config."""

import base64
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings


def _pi_auth_header() -> str:
    """Build HTTP Basic Auth header from configured credentials."""
    creds = f"{settings.pi_username}:{settings.pi_password}"
    return "Basic " + base64.b64encode(creds.encode()).decode()
from services import process_manager
from services import dataset_manager

router = APIRouter()

# ── Dream cycle state (polled by frontend) ─────────────────────────
_dream_state = {
    "active": False,
    "current_step": None,
    "current_step_name": None,
    "steps_completed": [],
    "steps_total": 6,
    "errors": [],
    "started_at": None,
    "completed_at": None,
    "metrics": None,
    "trigger": None,
    "progress": None,  # {epoch, total_epochs, loss, step, total_steps, pct, elapsed_s}
}


@router.get("/availability")
async def check_availability():
    """Check if training scripts and tools are available on this machine."""
    scripts_dir = Path(settings.scripts_dir)
    training_dir = Path(settings.training_dir)
    scripts_exist = scripts_dir.exists() and any(scripts_dir.glob("*.py"))
    # Check if this is a PyInstaller bundle
    import sys, shutil
    is_bundled = getattr(sys, '_MEIPASS', None) is not None
    # In bundled mode, we need a system Python on PATH to run scripts
    has_system_python = (
        shutil.which("python") is not None or shutil.which("python3") is not None
    ) if is_bundled else True

    available_steps = {}
    for step_id, step_info in process_manager.STEPS.items():
        script_path = scripts_dir / step_info["script"]
        available_steps[step_id] = {
            "exists": script_path.exists(),
            "path": str(script_path),
        }

    # Check for synthetic examples from learning
    learned_examples = 0
    appdata = Path.home() / "AppData" / "Roaming" / "Cortex" / "learning"
    examples_path = appdata / "synthetic_examples.jsonl"
    if examples_path.exists():
        learned_examples = sum(1 for _ in examples_path.open(encoding="utf-8", errors="ignore"))

    # Count other data sources
    synthetic_count = 0
    synth_path = training_dir / "raw_data" / "synthetic_examples.jsonl"
    if synth_path.exists():
        synthetic_count = sum(1 for _ in synth_path.open(encoding="utf-8", errors="ignore"))

    curated_count = 0
    curated_path = training_dir / "raw_data" / "curated_examples.jsonl"
    if curated_path.exists():
        curated_count = sum(1 for _ in curated_path.open(encoding="utf-8", errors="ignore"))

    # Scripts are available if they exist AND we can run them
    scripts_available = scripts_exist and has_system_python

    return {
        "ok": True,
        "scripts_dir": str(scripts_dir),
        "scripts_dir_exists": scripts_dir.exists(),
        "scripts_available": scripts_available,
        "is_bundled": is_bundled,
        "has_system_python": has_system_python,
        "training_dir": str(training_dir),
        "training_dir_exists": training_dir.exists(),
        "available_steps": available_steps,
        "learned_examples": learned_examples,
        "synthetic_examples": synthetic_count,
        "curated_examples": curated_count,
    }


class RunStepRequest(BaseModel):
    extra_args: list[str] | None = None


class AutoResearchRequest(BaseModel):
    strategy: str = "random"
    budget: int = 10
    resume: bool = False


class DatasetMessage(BaseModel):
    role: str
    content: str


class SaveExampleRequest(BaseModel):
    messages: list[DatasetMessage]
    source: str = "manual"
    mood: str = ""
    stage: int = 4
    topic: str = ""
    quality: int = 5
    original_response: str = ""


class UpdateExampleRequest(BaseModel):
    messages: list[DatasetMessage] | None = None
    metadata: dict | None = None


@router.get("/steps")
async def get_steps():
    """List pipeline steps with their current status."""
    return {"steps": process_manager.get_steps()}


@router.post("/run/{step}")
async def run_step(step: str, req: RunStepRequest | None = None):
    """Start a pipeline step."""
    try:
        extra = req.extra_args if req else None
        job = await process_manager.start_job(step, extra)
        return {"job": job.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/logs/{job_id}")
async def stream_logs(job_id: str):
    """SSE stream of job log output."""
    job = process_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        process_manager.subscribe_logs(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop/{job_id}")
async def stop_job(job_id: str):
    """Kill a running job."""
    success = await process_manager.stop_job(job_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot stop job")
    return {"stopped": True}


@router.post("/reset")
async def reset_jobs():
    """Clear all jobs — useful for cleaning up zombie processes."""
    process_manager.reset_all_jobs()
    return {"reset": True}


@router.get("/job/{job_id}")
async def get_job(job_id: str):
    """Get job status."""
    job = process_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job.to_dict()}


@router.get("/model-presets")
async def get_model_presets():
    """Return available model presets for the training pipeline."""
    presets_path = Path(settings.training_dir) / "config" / "model_presets.json"
    if not presets_path.exists():
        return {"presets": {}}
    with open(presets_path) as f:
        data = json.load(f)
    return data


@router.get("/config")
async def get_config():
    """Read training configuration (settings.json)."""
    config = settings.load_training_config()
    return {"config": config}


@router.put("/config")
async def update_config(updates: dict):
    """Update training configuration values."""
    config = settings.load_training_config()

    # Deep merge updates
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value

    settings.save_training_config(config)
    return {"config": config}


@router.get("/results")
async def get_results():
    """Read training results (training_log.json + eval_results.json)."""
    training_dir = Path(settings.training_dir)
    results = {}

    # Training log
    log_path = training_dir / "models" / "pet-lora" / "training_log.json"
    if log_path.exists():
        with open(log_path) as f:
            results["training_log"] = json.load(f)

    # Eval results
    eval_path = training_dir / "models" / "pet-lora" / "eval_results.json"
    if eval_path.exists():
        with open(eval_path) as f:
            results["eval_results"] = json.load(f)

    # Dataset info
    dataset_dir = training_dir / "dataset"
    if dataset_dir.exists():
        try:
            # Check for dataset_info.json (HuggingFace datasets)
            info_path = dataset_dir / "dataset_info.json"
            if info_path.exists():
                with open(info_path) as f:
                    results["dataset_info"] = json.load(f)
        except Exception:
            pass

    return results


# --- Dataset management endpoints ---


@router.get("/dataset")
async def list_examples(source: str | None = None):
    """List training examples. Use ?source=synthetic for synthetic data."""
    if source == "synthetic":
        examples = dataset_manager.load_synthetic()
        stats = dataset_manager.get_synthetic_stats()
    else:
        examples = dataset_manager.load_all()
        stats = dataset_manager.get_stats()
    return {"examples": examples, "stats": stats}


@router.post("/dataset")
async def save_example(req: SaveExampleRequest):
    """Save a new curated training example."""
    messages = [m.model_dump() for m in req.messages]
    example = dataset_manager.add_example(
        messages=messages,
        source=req.source,
        mood=req.mood,
        stage=req.stage,
        topic=req.topic,
        quality=req.quality,
        original_response=req.original_response,
    )
    return {"example": example}


@router.put("/dataset/{example_id}")
async def update_example(example_id: str, req: UpdateExampleRequest):
    """Update an existing curated example."""
    updates = {}
    if req.messages is not None:
        updates["messages"] = [m.model_dump() for m in req.messages]
    if req.metadata is not None:
        updates["metadata"] = req.metadata
    result = dataset_manager.update_example(example_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="Example not found")
    return {"example": result}


@router.delete("/dataset/{example_id}")
async def delete_example(example_id: str):
    """Delete a curated example."""
    success = dataset_manager.delete_example(example_id)
    if not success:
        raise HTTPException(status_code=404, detail="Example not found")
    return {"deleted": True}


@router.get("/dataset/stats")
async def dataset_stats():
    """Get curated dataset statistics."""
    return dataset_manager.get_stats()


# --- Auto-research endpoints ---


@router.post("/autoresearch")
async def start_autoresearch(req: AutoResearchRequest):
    """Start automated hyperparameter search."""
    extra_args = ["--strategy", req.strategy, "--budget", str(req.budget)]
    if req.resume:
        extra_args.append("--resume")
    # Clear stop sentinel before starting
    sentinel = Path(settings.training_dir) / "models" / ".stop_autoresearch"
    if sentinel.exists():
        sentinel.unlink()
    try:
        job = await process_manager.start_job("05", extra_args)
        return {"job": job.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.post("/autoresearch/stop")
async def stop_autoresearch():
    """Gracefully stop autoresearch after current iteration finishes."""
    sentinel = Path(settings.training_dir) / "models" / ".stop_autoresearch"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("stop")
    return {"stopping": True}


@router.get("/research-log")
async def get_research_log():
    """Read the autoresearch experiment log."""
    log_path = Path(settings.training_dir) / "models" / "research_log.jsonl"
    if not log_path.exists():
        return {"entries": [], "best": None}

    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Find best entry
    ok_entries = [e for e in entries if e.get("status") == "ok" and "metrics" in e]
    best = None
    if ok_entries:
        best = min(ok_entries, key=lambda e: e["metrics"].get("perplexity", float("inf")))

    return {"entries": entries, "best": best}


@router.delete("/research-log")
async def clear_research_log():
    """Clear the research log to start fresh."""
    log_path = Path(settings.training_dir) / "models" / "research_log.jsonl"
    if log_path.exists():
        log_path.unlink()
    return {"cleared": True}


# ── Dream Cycle (Sleep-Triggered Auto-Training) ──────────────────────

class DreamCycleRequest(BaseModel):
    pi_ip: str = "10.0.0.25"
    pi_port: int = 8420
    trigger: str = "sleep_dream"
    # Data source options
    include_learned: bool = True
    include_synthetic: bool = True
    include_curated: bool = True
    run_learn_cycle: bool = True


@router.get("/dream-status")
async def dream_status():
    """Return current dream cycle status for frontend polling."""
    return _dream_state


@router.post("/dream-reset")
async def dream_reset():
    """Reset stuck dream state (e.g. after a crash)."""
    _dream_state["active"] = False
    _dream_state["current_step"] = None
    _dream_state["current_step_name"] = None
    _dream_state["completed_at"] = None
    _dream_state["errors"] = []
    return {"ok": True}


@router.post("/dream-cycle")
async def start_dream_cycle(req: DreamCycleRequest):
    """Orchestrated dream training cycle triggered by Pi during sleep.

    Runs the full pipeline: sync → learn-cycle → train → eval → export.
    Each step runs sequentially. When complete, notifies the Pi that
    the dream is done with the training metrics.
    """
    import threading
    from services import pi_client

    def _run_dream():
        """Background thread that orchestrates the dream pipeline."""
        import time
        import subprocess
        import sys
        import shutil
        import os
        from pathlib import Path
        from datetime import datetime, timezone

        scripts_dir = Path(settings.scripts_dir)
        training_dir = Path(settings.training_dir)
        results = {"steps_completed": [], "errors": []}

        # Find a real Python interpreter (PyInstaller bundle uses the exe)
        if getattr(sys, '_MEIPASS', None):
            python_exe = shutil.which("python") or shutil.which("python3")
            if not python_exe:
                _dream_state["active"] = False
                _dream_state["errors"] = ["Cannot find Python interpreter on PATH"]
                _dream_state["completed_at"] = datetime.now(timezone.utc).isoformat()
                return
        else:
            python_exe = sys.executable

        # Initialize dream state
        _dream_state["active"] = True
        _dream_state["started_at"] = datetime.now(timezone.utc).isoformat()
        _dream_state["completed_at"] = None
        _dream_state["steps_completed"] = []
        _dream_state["errors"] = []
        _dream_state["metrics"] = None
        _dream_state["trigger"] = req.trigger
        _dream_state["current_step"] = None
        _dream_state["current_step_name"] = None

        try:
            # Record old intelligence for delta reporting
            old_intelligence = 0
            try:
                import urllib.request
                url = f"http://{req.pi_ip}:{req.pi_port}/api/cmd"
                payload = json.dumps({
                    "command": "pet_intelligence",
                    "payload": "",
                }).encode()
                http_req = urllib.request.Request(
                    url, data=payload, method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": _pi_auth_header(),
                    },
                )
                with urllib.request.urlopen(http_req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    if isinstance(data, dict):
                        old_intelligence = data.get("score", 0)
            except Exception:
                pass

            # Step sequence for dream training
            dream_steps = [
                ("00", "Sync Data", ["--export-only"]),
                ("07", "Learn Cycle", []),
                ("02", "Prepare Dataset", []),
                ("03", "Train LoRA", []),
                ("04", "Evaluate", ["--save"]),
                ("06", "Export & Deploy", ["--merge"]),
            ]

            # Skip learn cycle if not requested
            if not req.run_learn_cycle:
                dream_steps = [
                    s for s in dream_steps if s[0] != "07"
                ]

            _dream_state["steps_total"] = len(dream_steps)

            # Read training epochs for progress calculation
            train_cfg_epochs = None
            try:
                tcfg = settings.load_training_config()
                train_cfg_epochs = tcfg.get("training", {}).get("epochs", 3)
            except Exception:
                train_cfg_epochs = 3

            for step_id, step_name, extra_args in dream_steps:
                _dream_state["current_step"] = step_id
                _dream_state["current_step_name"] = step_name

                step_info = process_manager.STEPS.get(step_id)
                if not step_info:
                    results["errors"].append(f"Unknown step: {step_id}")
                    _dream_state["errors"].append(f"Unknown step: {step_id}")
                    continue

                script_path = scripts_dir / step_info["script"]
                if not script_path.exists():
                    results["errors"].append(
                        f"Script not found: {step_info['script']}")
                    _dream_state["errors"].append(
                        f"Script not found: {step_info['script']}")
                    continue

                cmd_args = [python_exe, "-u", str(script_path)]
                cmd_args.extend(step_info.get("args", []))
                cmd_args.extend(extra_args)

                # Force UTF-8 for subprocess output
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUTF8"] = "1"

                # Reset progress for this step
                _dream_state["progress"] = None

                try:
                    proc = subprocess.Popen(
                        cmd_args,
                        cwd=str(training_dir),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                    )
                    stderr_lines = []
                    step_start = time.time()

                    # Stream output line-by-line, parse training progress
                    for line in proc.stdout:
                        line = line.rstrip()
                        stderr_lines.append(line)
                        # Keep last 100 lines for error reporting
                        if len(stderr_lines) > 100:
                            stderr_lines.pop(0)

                        # Parse HuggingFace Trainer log lines:
                        #   {'loss': 0.42, 'grad_norm': 1.2, 'learning_rate': 0.0001, 'epoch': 1.5}
                        if step_id == "03" and "'loss'" in line:
                            try:
                                import ast
                                # Find the dict in the line
                                start = line.index("{")
                                end = line.index("}") + 1
                                log_dict = ast.literal_eval(line[start:end])
                                progress = {
                                    "loss": round(log_dict.get("loss", 0), 4),
                                    "epoch": round(log_dict.get("epoch", 0), 2),
                                    "learning_rate": log_dict.get("learning_rate"),
                                    "elapsed_s": round(time.time() - step_start, 1),
                                }
                                # Parse total epochs from config
                                total_epochs = train_cfg_epochs
                                if total_epochs:
                                    progress["total_epochs"] = total_epochs
                                    progress["pct"] = round(
                                        log_dict.get("epoch", 0) / total_epochs * 100, 1
                                    )
                                _dream_state["progress"] = progress
                            except Exception:
                                pass

                        # Parse tqdm progress bars (fallback):
                        #   30%|███       | 30/100 [05:00<10:00]
                        elif step_id == "03" and "%" in line and "|" in line:
                            try:
                                import re as _re
                                m = _re.search(r"(\d+)%\|", line)
                                if m:
                                    pct = int(m.group(1))
                                    cur_progress = _dream_state.get("progress") or {}
                                    cur_progress["pct"] = pct
                                    cur_progress["elapsed_s"] = round(
                                        time.time() - step_start, 1
                                    )
                                    _dream_state["progress"] = cur_progress
                            except Exception:
                                pass

                    proc.wait(timeout=3600)

                    if proc.returncode == 0:
                        results["steps_completed"].append(step_id)
                        _dream_state["steps_completed"].append(step_id)
                    else:
                        last_output = "\n".join(stderr_lines[-10:])
                        err_msg = (
                            f"Step {step_id} ({step_name}) failed: "
                            f"{last_output[:500]}"
                        )
                        results["errors"].append(err_msg)
                        _dream_state["errors"].append(err_msg)
                        break  # Stop pipeline on failure
                except subprocess.TimeoutExpired:
                    proc.kill()
                    err_msg = f"Step {step_id} ({step_name}) timed out"
                    results["errors"].append(err_msg)
                    _dream_state["errors"].append(err_msg)
                    break
                except Exception as e:
                    err_msg = f"Step {step_id} ({step_name}) error: {e}"
                    results["errors"].append(err_msg)
                    _dream_state["errors"].append(err_msg)
                    break
                finally:
                    _dream_state["progress"] = None

            # Read training results
            training_metrics = {"old_intelligence": old_intelligence}
            try:
                log_path = training_dir / "models" / "pet-lora" / "training_log.json"
                if log_path.exists():
                    with open(log_path) as f:
                        tlog = json.load(f)
                    training_metrics["final_loss"] = tlog.get("final_loss")
                    training_metrics["training_time_s"] = tlog.get(
                        "training_time_s")
                    training_metrics["dataset_size"] = tlog.get("train_samples")
                    training_metrics["lora_version"] = tlog.get(
                        "bloom_number", "dream")
            except Exception:
                pass

            try:
                eval_path = (training_dir / "models" / "pet-lora"
                             / "eval_results.json")
                if eval_path.exists():
                    with open(eval_path) as f:
                        elog = json.load(f)
                    training_metrics["perplexity_base"] = (
                        elog.get("base_perplexity", {}).get("perplexity"))
                    training_metrics["perplexity_finetuned"] = (
                        elog.get("finetuned_perplexity", {}).get("perplexity"))
            except Exception:
                pass

            # ── Deploy: merge LoRA → GGUF → SCP → restart ─────────────
            PI_SSH = f"turfptax@{req.pi_ip}"
            PI_MODEL_DIR = "/home/turfptax/models"
            PI_GGUF = f"{PI_MODEL_DIR}/cortex-pet-q8_0.gguf"
            lora_dir = training_dir / "models" / "pet-lora"
            merged_dir = training_dir / "models" / "pet-merged"
            gguf_path = merged_dir / "cortex-pet-q8_0.gguf"
            convert_script = training_dir / "llama.cpp" / "convert_hf_to_gguf.py"

            if lora_dir.exists() and not results["errors"]:
                _dream_state["current_step_name"] = "Deploy (merging LoRA)"
                try:
                    # Step 1: Merge LoRA into base model
                    tcfg = settings.load_training_config()
                    base_model = tcfg.get("model", {}).get(
                        "base_model", "Qwen/Qwen3.5-0.8B")

                    merge_code = (
                        "import torch; "
                        "from peft import PeftModel; "
                        "from transformers import "
                        "AutoModelForCausalLM, AutoTokenizer; "
                        f"base = AutoModelForCausalLM.from_pretrained("
                        f"'{base_model}', dtype=torch.float16); "
                        f"tok = AutoTokenizer.from_pretrained("
                        f"'{base_model}'); "
                        f"model = PeftModel.from_pretrained("
                        f"base, '{lora_dir}'); "
                        "merged = model.merge_and_unload(); "
                        f"merged.save_pretrained('{merged_dir}'); "
                        f"tok.save_pretrained('{merged_dir}'); "
                        "print('MERGE_OK')"
                    )
                    merge_result = subprocess.run(
                        [python_exe, "-c", merge_code],
                        cwd=str(training_dir),
                        capture_output=True, text=True,
                        timeout=300, env=env,
                    )
                    if "MERGE_OK" not in (merge_result.stdout or ""):
                        err = merge_result.stderr[:500] if merge_result.stderr else "unknown"
                        results["errors"].append(
                            f"LoRA merge failed: {err}")
                    else:
                        results["steps_completed"].append("lora_merge")

                    # Step 2: Convert merged model to GGUF
                    if not results["errors"] and convert_script.exists():
                        _dream_state["current_step_name"] = (
                            "Deploy (converting to GGUF)")
                        convert_result = subprocess.run(
                            [python_exe, str(convert_script),
                             str(merged_dir),
                             "--outfile", str(gguf_path),
                             "--outtype", "q8_0"],
                            cwd=str(training_dir),
                            capture_output=True, text=True,
                            timeout=600, env=env,
                        )
                        if convert_result.returncode != 0:
                            results["errors"].append(
                                f"GGUF conversion failed: "
                                f"{convert_result.stderr[:500]}")
                        else:
                            results["steps_completed"].append("gguf_convert")
                    elif not convert_script.exists():
                        results["errors"].append(
                            f"convert_hf_to_gguf.py not found at "
                            f"{convert_script}")

                    # Step 3: SCP GGUF to Pi
                    if not results["errors"] and gguf_path.exists():
                        _dream_state["current_step_name"] = (
                            "Deploy (uploading to Pi)")
                        scp_result = subprocess.run(
                            ["scp", "-o", "StrictHostKeyChecking=no",
                             str(gguf_path),
                             f"{PI_SSH}:{PI_GGUF}"],
                            timeout=600, capture_output=True, text=True,
                        )
                        if scp_result.returncode != 0:
                            results["errors"].append(
                                f"SCP GGUF failed: "
                                f"{scp_result.stderr[:200]}")
                        else:
                            results["steps_completed"].append("lora_deploy")
                            training_metrics["lora_deployed"] = True

                    # Step 4: Update llama-server service to use new model
                    #         and restart
                    if not results["errors"]:
                        _dream_state["current_step_name"] = (
                            "Deploy (restarting llama-server)")
                        restart_cmd = (
                            "sudo systemctl stop llama-server && "
                            f"sudo sed -i "
                            f"'s|--model [^ ]*|"
                            f"--model {PI_GGUF}|' "
                            "/etc/systemd/system/llama-server.service && "
                            "sudo systemctl daemon-reload && "
                            "sudo systemctl start llama-server"
                        )
                        restart_result = subprocess.run(
                            ["ssh", "-o", "StrictHostKeyChecking=no",
                             "-o", "ConnectTimeout=10",
                             PI_SSH, restart_cmd],
                            timeout=30, capture_output=True, text=True,
                        )
                        if restart_result.returncode != 0:
                            results["errors"].append(
                                f"llama-server restart failed: "
                                f"{restart_result.stderr[:200]}")
                        else:
                            results["steps_completed"].append("llama_restart")
                            training_metrics["llama_restarted"] = True

                except subprocess.TimeoutExpired:
                    results["errors"].append("LoRA deploy timed out")
                except Exception as e:
                    results["errors"].append(f"LoRA deploy error: {e}")

            # Notify Pi that dream is complete
            try:
                import urllib.request
                url = f"http://{req.pi_ip}:{req.pi_port}/api/cmd"
                payload = json.dumps({
                    "command": "dream_complete",
                    "payload": training_metrics,
                }).encode()
                http_req = urllib.request.Request(
                    url, data=payload, method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": _pi_auth_header(),
                    },
                )
                with urllib.request.urlopen(http_req, timeout=30) as resp:
                    resp.read()
            except Exception as e:
                results["errors"].append(f"Failed to notify Pi: {e}")

            # Store metrics in dream state
            _dream_state["metrics"] = training_metrics
        except Exception as exc:
            _dream_state["errors"].append(f"Dream thread crashed: {exc}")
        finally:
            _dream_state["active"] = False
            _dream_state["completed_at"] = datetime.now(timezone.utc).isoformat()
            _dream_state["current_step"] = None
            _dream_state["current_step_name"] = None

    # Launch dream in background thread
    thread = threading.Thread(target=_run_dream, daemon=True,
                              name="dream-cycle")
    thread.start()

    return {
        "status": "started",
        "trigger": req.trigger,
        "pi_ip": req.pi_ip,
        "message": "Dream cycle started in background",
    }
