"""Training pipeline router — run scripts, stream logs, manage config."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import settings
from services import process_manager
from services import dataset_manager

router = APIRouter()


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
        learned_examples = sum(1 for _ in examples_path.open())

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
        from pathlib import Path

        scripts_dir = Path(settings.scripts_dir)
        training_dir = Path(settings.training_dir)
        results = {"steps_completed": [], "errors": []}

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
                    "Authorization": "Basic REDACTED_AUTH_TOKEN==",
                },
            )
            with urllib.request.urlopen(http_req, timeout=10) as resp:
                data = json.loads(resp.read())
                # Parse RSP:pet_intelligence:{json}
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

        for step_id, step_name, extra_args in dream_steps:
            step_info = process_manager.STEPS.get(step_id)
            if not step_info:
                results["errors"].append(f"Unknown step: {step_id}")
                continue

            script_path = scripts_dir / step_info["script"]
            if not script_path.exists():
                results["errors"].append(
                    f"Script not found: {step_info['script']}")
                continue

            args = [sys.executable, str(script_path)]
            args.extend(step_info.get("args", []))
            args.extend(extra_args)

            try:
                result = subprocess.run(
                    args,
                    cwd=str(training_dir),
                    capture_output=True,
                    text=True,
                    timeout=3600,  # 1 hour max per step
                )
                if result.returncode == 0:
                    results["steps_completed"].append(step_id)
                else:
                    results["errors"].append(
                        f"Step {step_id} ({step_name}) failed: "
                        f"{result.stderr[:500]}"
                    )
                    break  # Stop pipeline on failure
            except subprocess.TimeoutExpired:
                results["errors"].append(
                    f"Step {step_id} ({step_name}) timed out")
                break
            except Exception as e:
                results["errors"].append(
                    f"Step {step_id} ({step_name}) error: {e}")
                break

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

        # ── Deploy LoRA adapter to Pi ─────────────────────────────────
        PI_SSH = f"turfptax@{req.pi_ip}"
        PI_LORA_DIR = "/home/turfptax/models/pet-lora"
        lora_dir = training_dir / "models" / "pet-lora"

        if lora_dir.exists() and not results["errors"]:
            try:
                subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no",
                     "-o", "ConnectTimeout=10",
                     PI_SSH, f"mkdir -p {PI_LORA_DIR}"],
                    timeout=15, capture_output=True,
                )

                lora_files = list(lora_dir.glob("adapter_*")) + \
                    list(lora_dir.glob("*.json"))
                for fpath in lora_files:
                    scp_result = subprocess.run(
                        ["scp", "-o", "StrictHostKeyChecking=no",
                         str(fpath), f"{PI_SSH}:{PI_LORA_DIR}/"],
                        timeout=120, capture_output=True, text=True,
                    )
                    if scp_result.returncode != 0:
                        results["errors"].append(
                            f"SCP {fpath.name} failed: {scp_result.stderr[:200]}"
                        )
                        break

                if not results["errors"]:
                    results["steps_completed"].append("lora_deploy")
                    training_metrics["lora_deployed"] = True

                    restart_cmd = (
                        "sudo systemctl stop llama-server && "
                        "sudo bash -c '"
                        "/usr/local/bin/llama-server "
                        "-m /home/turfptax/models/qwen3.5-0.8b-q4_k_m.gguf "
                        f"--lora {PI_LORA_DIR}/adapter_model.safetensors "
                        "--host 127.0.0.1 --port 8081 "
                        "-ngl 0 -c 2048 --temp 0.7 "
                        "> /tmp/llama-server.log 2>&1 &'"
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
                            f"{restart_result.stderr[:200]}"
                        )
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
                "payload": json.dumps(training_metrics),
            }).encode()
            http_req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Basic REDACTED_AUTH_TOKEN==",
                },
            )
            with urllib.request.urlopen(http_req, timeout=30) as resp:
                resp.read()
        except Exception as e:
            results["errors"].append(f"Failed to notify Pi: {e}")

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
