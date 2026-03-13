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
