"""Dream cycle: full training pipeline orchestrator.

Runs the complete training pipeline end-to-end:
sync -> synthesize -> prepare -> train -> evaluate -> deploy

Used by the Hub backend when the pet is tucked in, and by the CLI
via `cortex-train dream`.
"""

import json
import time
from typing import Dict, List, Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import CortexTrainError
from cortex_train.paths import TrainPaths
from cortex_train.progress import (
    ProgressCallback, ProgressEvent, make_step_progress, null_progress,
)

# Steps that can fail without stopping the pipeline
NON_FATAL_STEPS = {"sync", "synthesize"}


def run_dream(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    skip_sync: bool = False,
    skip_synthesize: bool = False,
    export_only: bool = False,
    pi_ip: Optional[str] = None,
) -> dict:
    """Run the full dream training cycle.

    Args:
        skip_sync: Skip the data sync step
        skip_synthesize: Skip the LM Studio synthesis step
        export_only: Don't deploy to Pi (just export GGUF)
        pi_ip: Override Pi IP for deployment

    Returns:
        {ok, metrics, steps_completed, steps_failed, errors, elapsed_s}
    """
    emit = make_step_progress("dream", on_progress)

    # Override Pi IP if provided
    if pi_ip:
        settings.pi.host = pi_ip

    steps_completed = []
    steps_failed = []
    errors = []
    metrics = {}
    start_time = time.time()

    # Define pipeline steps
    pipeline = []
    if not skip_sync:
        pipeline.append(("sync", _run_sync))
    if not skip_synthesize:
        pipeline.append(("synthesize", _run_synthesize))
    pipeline.append(("prepare", _run_prepare))
    pipeline.append(("train", _run_train))
    pipeline.append(("evaluate", _run_evaluate))
    pipeline.append(("deploy", lambda s, p, cb, **kw: _run_deploy(s, p, cb, export_only=export_only)))

    total_steps = len(pipeline)

    for i, (step_name, step_fn) in enumerate(pipeline):
        step_pct = (i / total_steps) * 100
        emit(f"Starting step: {step_name} ({i+1}/{total_steps})", pct=step_pct)

        try:
            result = step_fn(settings, paths, on_progress)
            steps_completed.append(step_name)

            # Collect key metrics
            if step_name == "train":
                metrics["final_loss"] = result.get("final_loss")
                metrics["training_time_s"] = result.get("training_time_s")
                metrics["dataset_size"] = result.get("dataset_size")
                metrics["bloom_number"] = result.get("bloom_number")
            elif step_name == "evaluate":
                metrics["base_perplexity"] = result.get("base_perplexity")
                metrics["finetuned_perplexity"] = result.get("finetuned_perplexity")
            elif step_name == "deploy":
                metrics["gguf_path"] = result.get("gguf_path")
                metrics["deployed"] = result.get("deployed")
                metrics["bloom"] = result.get("bloom")

            emit(f"Step {step_name} completed", pct=step_pct + (100 / total_steps))

        except CortexTrainError as e:
            error_msg = f"{step_name}: {e}"
            errors.append(error_msg)

            if step_name in NON_FATAL_STEPS:
                emit(f"Step {step_name} failed (non-fatal): {e}")
                steps_failed.append(step_name)
                continue
            else:
                emit(f"Step {step_name} FAILED: {e}")
                steps_failed.append(step_name)
                break

        except Exception as e:
            error_msg = f"{step_name}: {type(e).__name__}: {e}"
            errors.append(error_msg)
            emit(f"Step {step_name} FAILED unexpectedly: {e}")
            steps_failed.append(step_name)

            if step_name in NON_FATAL_STEPS:
                continue
            else:
                break

    elapsed = time.time() - start_time
    ok = "train" in steps_completed  # Dream is ok if training completed

    if ok:
        # Format lora_version for the Pi
        bloom = metrics.get("bloom_number") or metrics.get("bloom")
        if bloom:
            metrics["lora_version"] = f"bloom-{bloom}"

    emit(f"Dream {'complete' if ok else 'FAILED'}: "
         f"{len(steps_completed)}/{total_steps} steps, {elapsed:.0f}s",
         pct=100 if ok else None,
         metrics=metrics)

    return {
        "ok": ok,
        "metrics": metrics,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed,
        "errors": errors,
        "elapsed_s": round(elapsed, 1),
    }


# Thin wrappers that import step functions lazily
def _run_sync(settings, paths, on_progress):
    from cortex_train.steps.sync import run_sync
    return run_sync(settings, paths, on_progress, export_only=False)

def _run_synthesize(settings, paths, on_progress):
    from cortex_train.steps.synthesize import run_synthesize
    return run_synthesize(settings, paths, on_progress)

def _run_prepare(settings, paths, on_progress):
    from cortex_train.steps.prepare import run_prepare
    return run_prepare(settings, paths, on_progress)

def _run_train(settings, paths, on_progress):
    from cortex_train.steps.train import run_train
    return run_train(settings, paths, on_progress)

def _run_evaluate(settings, paths, on_progress):
    from cortex_train.steps.evaluate import run_evaluate
    return run_evaluate(settings, paths, on_progress, save=True)

def _run_deploy(settings, paths, on_progress, export_only=False):
    from cortex_train.steps.deploy import run_deploy
    return run_deploy(settings, paths, on_progress, merge=True, export_only=export_only)
