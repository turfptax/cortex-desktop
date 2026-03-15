"""Step 5: Automated hyperparameter research with monotonic ratchet.

Inspired by Karpathy's autoresearch — loops through train→eval cycles
with different hyperparameter configs, keeping only the best adapter.

Each iteration:
  1. Generate a hyperparameter config (grid / random / smart strategy)
  2. Run 03_train_pet.py as a subprocess (VRAM isolation)
  3. Run 04_eval.py as a subprocess (perplexity measurement)
  4. Compare to best — if improved, promote adapter (ratchet)
  5. Append results to research_log.jsonl

Subprocess isolation ensures VRAM is fully released between iterations.

Usage:
    python 05_autoresearch.py                               # random, 10 iters
    python 05_autoresearch.py --strategy grid --budget 20   # grid search
    python 05_autoresearch.py --strategy smart --budget 50  # smart mutations
    python 05_autoresearch.py --resume                      # continue previous

Outputs:
    ../models/pet-lora-best/      - Best adapter (ratchet target)
    ../models/pet-lora-iter/      - Scratch space (overwritten each iter)
    ../models/research_log.jsonl  - Append-only experiment log
"""

import argparse
import itertools
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
MODELS_DIR = PROJECT_DIR / "models"
ITER_DIR = MODELS_DIR / "pet-lora-iter"
BEST_DIR = MODELS_DIR / "pet-lora-best"
LOG_PATH = MODELS_DIR / "research_log.jsonl"
STOP_SENTINEL = MODELS_DIR / ".stop_autoresearch"

# Default search space
SEARCH_SPACE = {
    "learning_rate": {
        "type": "log_uniform",
        "low": 5e-5,
        "high": 1e-3,
    },
    "lora_rank": {
        "type": "categorical",
        "values": [8, 16, 32, 64],
    },
    "lora_alpha": {
        "type": "categorical",
        "values": [16, 32, 64, 128],
    },
    "epochs": {
        "type": "categorical",
        "values": [2, 3, 5],
    },
    "batch_size": {
        "type": "categorical",
        "values": [2, 4, 8],
    },
}

# Grid search — smaller set of values for tractable enumeration
GRID_VALUES = {
    "learning_rate": [1e-4, 2e-4, 5e-4],
    "lora_rank": [8, 16, 32],
    "lora_alpha": [16, 32, 64],
    "epochs": [3, 5],
    "batch_size": [4],
}


def log(msg):
    """Print with timestamp prefix."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_research_log():
    """Load existing research log entries."""
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def append_log_entry(entry):
    """Append a single entry to the research log."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_best_perplexity(entries):
    """Find the best perplexity from existing log entries."""
    best = float("inf")
    for e in entries:
        if e.get("status") == "ok" and "metrics" in e:
            ppl = e["metrics"].get("perplexity", float("inf"))
            if ppl < best:
                best = ppl
    return best


def check_stop_requested():
    """Check if graceful stop was requested via sentinel file."""
    if STOP_SENTINEL.exists():
        try:
            STOP_SENTINEL.unlink()
        except OSError:
            pass
        return True
    return False


def clear_stop_sentinel():
    """Remove stop sentinel if it exists (fresh start)."""
    if STOP_SENTINEL.exists():
        try:
            STOP_SENTINEL.unlink()
        except OSError:
            pass


# ---- Search Strategies ----


def strategy_random(search_space, budget, existing_configs):
    """Generate random hyperparameter configs."""
    configs = []
    for _ in range(budget):
        config = {}
        for param, spec in search_space.items():
            if spec["type"] == "log_uniform":
                log_low = math.log(spec["low"])
                log_high = math.log(spec["high"])
                config[param] = round(math.exp(random.uniform(log_low, log_high)), 6)
            elif spec["type"] == "categorical":
                config[param] = random.choice(spec["values"])
        configs.append(config)
    return configs


def strategy_grid(grid_values, budget, existing_configs):
    """Generate grid search configs (all combinations)."""
    keys = list(grid_values.keys())
    values = [grid_values[k] for k in keys]
    all_combos = list(itertools.product(*values))

    # Skip already-tested configs
    existing_tuples = set()
    for ec in existing_configs:
        tup = tuple(ec.get(k) for k in keys)
        existing_tuples.add(tup)

    configs = []
    for combo in all_combos:
        if combo not in existing_tuples:
            config = dict(zip(keys, combo))
            configs.append(config)
        if len(configs) >= budget:
            break

    if not configs:
        log("Grid search exhausted — all combinations tested.")

    return configs


def strategy_smart(search_space, budget, existing_entries):
    """Mutate the best-so-far config with small random perturbations."""
    # Find best entry
    best_entry = None
    best_ppl = float("inf")
    for e in existing_entries:
        if e.get("status") == "ok" and "metrics" in e:
            ppl = e["metrics"].get("perplexity", float("inf"))
            if ppl < best_ppl:
                best_ppl = ppl
                best_entry = e

    configs = []
    for _ in range(budget):
        if best_entry and "config" in best_entry:
            # Mutate from best
            base = best_entry["config"].copy()
            config = mutate_config(base, search_space)
        else:
            # No best yet — random start
            config = strategy_random(search_space, 1, [])[0]
        configs.append(config)

    return configs


def mutate_config(base, search_space):
    """Apply small random perturbations to a config."""
    config = base.copy()

    # Pick 1-2 params to mutate
    params = list(search_space.keys())
    n_mutations = random.randint(1, min(2, len(params)))
    mutate_params = random.sample(params, n_mutations)

    for param in mutate_params:
        spec = search_space[param]
        if spec["type"] == "log_uniform":
            # Perturb by ±50% in log space
            current = config.get(param, math.exp((math.log(spec["low"]) + math.log(spec["high"])) / 2))
            log_val = math.log(current)
            log_range = math.log(spec["high"]) - math.log(spec["low"])
            perturbation = random.gauss(0, log_range * 0.2)
            new_log = max(math.log(spec["low"]), min(math.log(spec["high"]), log_val + perturbation))
            config[param] = round(math.exp(new_log), 6)
        elif spec["type"] == "categorical":
            # Pick a neighbor or random value
            values = spec["values"]
            current = config.get(param, values[0])
            if current in values:
                idx = values.index(current)
                # Move ±1 position or stay
                new_idx = max(0, min(len(values) - 1, idx + random.choice([-1, 0, 1])))
                config[param] = values[new_idx]
            else:
                config[param] = random.choice(values)

    return config


# ---- Core Orchestrator ----


def run_iteration(iteration, config, no_unsloth=False):
    """Run one train→eval cycle. Returns result dict.

    Each step runs as a separate subprocess to ensure full VRAM cleanup.
    """
    result = {
        "iteration": iteration,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "status": "pending",
        "metrics": {},
    }

    # Ensure clean scratch directory
    if ITER_DIR.exists():
        shutil.rmtree(ITER_DIR, ignore_errors=True)
    ITER_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Step 1: Train ----
    train_args = [
        sys.executable, "-u", "03_train_pet.py",
        "--output-dir", str(ITER_DIR),
        "--epochs", str(config["epochs"]),
        "--learning-rate", str(config["learning_rate"]),
        "--lora-rank", str(config["lora_rank"]),
        "--lora-alpha", str(config["lora_alpha"]),
        "--batch-size", str(config["batch_size"]),
    ]
    if no_unsloth:
        train_args.append("--no-unsloth")

    log(f"  Training: lr={config['learning_rate']}, rank={config['lora_rank']}, "
        f"alpha={config['lora_alpha']}, epochs={config['epochs']}, batch={config['batch_size']}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    train_start = time.time()
    train_proc = subprocess.run(
        train_args,
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
        env=env,
    )
    train_elapsed = time.time() - train_start

    if train_proc.returncode != 0:
        log(f"  Training FAILED (exit {train_proc.returncode})")
        # Print last few lines of stderr/stdout for debugging
        output = train_proc.stdout + "\n" + train_proc.stderr
        for line in output.strip().splitlines()[-5:]:
            log(f"    {line}")
        result["status"] = "train_failed"
        result["error"] = output.strip()[-500:]
        return result

    # Read training log for loss
    train_log_path = ITER_DIR / "training_log.json"
    train_loss = None
    if train_log_path.exists():
        try:
            train_log = json.loads(train_log_path.read_text())
            train_loss = train_log.get("final_loss")
            log(f"  Train done in {train_elapsed:.1f}s — loss: {train_loss:.4f}")
        except (json.JSONDecodeError, KeyError):
            log(f"  Train done in {train_elapsed:.1f}s (couldn't parse log)")

    result["metrics"]["train_loss"] = train_loss
    result["metrics"]["train_time_s"] = round(train_elapsed, 1)

    # ---- Step 2: Eval ----
    eval_args = [
        sys.executable, "-u", "04_eval.py",
        "--adapter-dir", str(ITER_DIR),
        "--finetuned-only",
        "--save",
    ]

    log("  Evaluating...")
    eval_start = time.time()
    eval_proc = subprocess.run(
        eval_args,
        capture_output=True,
        text=True,
        cwd=str(SCRIPT_DIR),
        env=env,
    )
    eval_elapsed = time.time() - eval_start

    if eval_proc.returncode != 0:
        log(f"  Eval FAILED (exit {eval_proc.returncode})")
        output = eval_proc.stdout + "\n" + eval_proc.stderr
        for line in output.strip().splitlines()[-5:]:
            log(f"    {line}")
        result["status"] = "eval_failed"
        result["error"] = output.strip()[-500:]
        return result

    # Read eval results
    eval_path = ITER_DIR / "eval_results.json"
    if not eval_path.exists():
        log("  Eval completed but no eval_results.json found")
        result["status"] = "eval_no_results"
        return result

    try:
        eval_data = json.loads(eval_path.read_text())
        ppl_data = eval_data.get("finetuned_perplexity", {})
        perplexity = ppl_data.get("perplexity", float("inf"))
        avg_loss = ppl_data.get("avg_loss")
    except (json.JSONDecodeError, KeyError) as e:
        log(f"  Failed to parse eval results: {e}")
        result["status"] = "eval_parse_error"
        return result

    result["metrics"]["perplexity"] = perplexity
    result["metrics"]["eval_loss"] = avg_loss
    result["metrics"]["eval_time_s"] = round(eval_elapsed, 1)
    result["metrics"]["total_time_s"] = round(train_elapsed + eval_elapsed, 1)
    result["status"] = "ok"

    log(f"  Eval done in {eval_elapsed:.1f}s — perplexity: {perplexity:.2f}")

    return result


def maybe_promote(perplexity, best_perplexity):
    """Copy iter adapter to best if improved. Returns new best perplexity."""
    if perplexity < best_perplexity:
        if BEST_DIR.exists():
            shutil.rmtree(BEST_DIR, ignore_errors=True)
        shutil.copytree(ITER_DIR, BEST_DIR)
        log(f"  NEW BEST! {perplexity:.2f} < {best_perplexity:.2f} — promoted to pet-lora-best")
        return perplexity
    else:
        log(f"  No improvement ({perplexity:.2f} >= {best_perplexity:.2f})")
        return best_perplexity


def main():
    parser = argparse.ArgumentParser(
        description="Automated hyperparameter search for Cortex Pet training"
    )
    parser.add_argument(
        "--strategy", choices=["grid", "random", "smart"], default="random",
        help="Search strategy (default: random)"
    )
    parser.add_argument(
        "--budget", type=int, default=10,
        help="Number of iterations to run (default: 10)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from existing research_log.jsonl"
    )
    parser.add_argument(
        "--no-unsloth", action="store_true",
        help="Disable Unsloth (passed through to training script)"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility"
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print("=" * 60)
    print("  CORTEX PET — AUTO-RESEARCH")
    print("=" * 60)
    print(f"  Strategy:  {args.strategy}")
    print(f"  Budget:    {args.budget} iterations")
    print(f"  Resume:    {args.resume}")
    print(f"  Log:       {LOG_PATH}")
    print(f"  Iter dir:  {ITER_DIR}")
    print(f"  Best dir:  {BEST_DIR}")
    print()

    # Clear stop sentinel
    clear_stop_sentinel()

    # Load existing log
    existing_entries = load_research_log() if args.resume else []
    existing_configs = [e["config"] for e in existing_entries if "config" in e]

    if args.resume and existing_entries:
        log(f"Resuming from {len(existing_entries)} existing iterations")
    elif not args.resume and LOG_PATH.exists():
        # Fresh start — archive old log
        archive_path = LOG_PATH.with_suffix(f".{int(time.time())}.jsonl")
        LOG_PATH.rename(archive_path)
        log(f"Archived old log to {archive_path.name}")
        existing_entries = []
        existing_configs = []

    best_perplexity = get_best_perplexity(existing_entries)
    if best_perplexity < float("inf"):
        log(f"Current best perplexity: {best_perplexity:.2f}")
    else:
        log("No previous best — starting fresh")

    # Generate configs based on strategy
    if args.strategy == "grid":
        configs = strategy_grid(GRID_VALUES, args.budget, existing_configs)
    elif args.strategy == "random":
        configs = strategy_random(SEARCH_SPACE, args.budget, existing_configs)
    elif args.strategy == "smart":
        configs = strategy_smart(SEARCH_SPACE, args.budget, existing_entries)
    else:
        configs = strategy_random(SEARCH_SPACE, args.budget, existing_configs)

    if not configs:
        log("No configs to test. Done.")
        return

    actual_budget = min(len(configs), args.budget)
    log(f"Generated {len(configs)} configs, running {actual_budget}")
    print()

    # Track stats
    start_iteration = len(existing_entries) + 1
    completed = 0
    failed = 0
    improved = 0
    total_start = time.time()

    for i, config in enumerate(configs[:actual_budget]):
        iteration = start_iteration + i

        # Check for stop request
        if check_stop_requested():
            log("Stop requested — finishing gracefully")
            break

        print("-" * 60)
        log(f"Iteration {iteration} / {start_iteration + actual_budget - 1}")

        # Run the iteration
        result = run_iteration(iteration, config, no_unsloth=args.no_unsloth)

        # Ratchet logic
        if result["status"] == "ok":
            completed += 1
            ppl = result["metrics"]["perplexity"]
            old_best = best_perplexity
            best_perplexity = maybe_promote(ppl, best_perplexity)
            result["is_best"] = ppl < old_best
            if result["is_best"]:
                improved += 1
        else:
            failed += 1
            result["is_best"] = False

        result["best_perplexity"] = best_perplexity

        # Append to log
        append_log_entry(result)

        # For smart strategy: update best entry for next mutation
        if args.strategy == "smart" and result.get("is_best"):
            # Regenerate remaining configs from new best
            remaining = actual_budget - (i + 1)
            if remaining > 0:
                new_entries = load_research_log()
                new_configs = strategy_smart(SEARCH_SPACE, remaining, new_entries)
                configs[i + 1:i + 1 + remaining] = new_configs

        print()

    # Summary
    total_time = time.time() - total_start
    print("=" * 60)
    log("AUTO-RESEARCH COMPLETE")
    print(f"  Iterations: {completed + failed} ({completed} ok, {failed} failed)")
    print(f"  Improvements: {improved}")
    print(f"  Best perplexity: {best_perplexity:.2f}" if best_perplexity < float("inf") else "  Best perplexity: N/A")
    print(f"  Total time: {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"  Log: {LOG_PATH}")
    if BEST_DIR.exists():
        print(f"  Best adapter: {BEST_DIR}")
    print()

    # Print top 5 results
    all_entries = load_research_log()
    ok_entries = [e for e in all_entries if e.get("status") == "ok"]
    ok_entries.sort(key=lambda e: e["metrics"].get("perplexity", float("inf")))

    if ok_entries:
        print("  Top 5 configs:")
        print(f"  {'#':>4}  {'PPL':>8}  {'Loss':>8}  {'LR':>10}  {'Rank':>4}  {'Alpha':>5}  {'Ep':>2}  {'Batch':>5}  {'Time':>6}")
        print(f"  {'----':>4}  {'--------':>8}  {'--------':>8}  {'----------':>10}  {'----':>4}  {'-----':>5}  {'--':>2}  {'-----':>5}  {'------':>6}")
        for e in ok_entries[:5]:
            m = e["metrics"]
            c = e["config"]
            print(f"  {e['iteration']:4d}  {m['perplexity']:8.2f}  {m.get('train_loss', 0):8.4f}  "
                  f"{c['learning_rate']:10.6f}  {c['lora_rank']:4d}  {c['lora_alpha']:5d}  "
                  f"{c['epochs']:2d}  {c['batch_size']:5d}  {m.get('total_time_s', 0):5.0f}s")


if __name__ == "__main__":
    main()
