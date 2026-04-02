"""Cortex Train CLI — unified training pipeline commands.

Usage:
    cortex-train sync [--export-only]
    cortex-train synthesize [--max-notes N] [--force] [--dry-run]
    cortex-train prepare [--no-synthetic] [--no-personality] [--no-curated]
    cortex-train status
"""

import json
import sys

try:
    import click
except ImportError:
    print("ERROR: 'click' library required. Install with: pip install click")
    sys.exit(1)

from cortex_train import __version__
from cortex_train.config import load_settings
from cortex_train.paths import TrainPaths
from cortex_train.progress import cli_progress


@click.group()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="Override path to settings.json")
@click.option("--training-dir", type=click.Path(), default=None,
              help="Override training data directory")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.version_option(__version__, prog_name="cortex-train")
@click.pass_context
def main(ctx, config_path, training_dir, verbose):
    """Cortex Train — unified training pipeline for the Cortex AI pet."""
    ctx.ensure_object(dict)

    # Set up paths
    if training_dir:
        from pathlib import Path
        paths = TrainPaths(training_dir=Path(training_dir))
    else:
        paths = TrainPaths()

    # Override config path if specified
    if config_path:
        from pathlib import Path
        settings_path = Path(config_path)
    else:
        settings_path = paths.settings_path

    # Load settings
    settings = load_settings(settings_path)

    ctx.obj["paths"] = paths
    ctx.obj["settings"] = settings
    ctx.obj["verbose"] = verbose


@main.command()
@click.option("--export-only", is_flag=True, help="Skip SCP, use existing local cortex.db")
@click.pass_context
def sync(ctx, export_only):
    """Sync training data from the Pi (SCP + export to JSONL)."""
    from cortex_train.steps.sync import run_sync

    result = run_sync(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        export_only=export_only,
    )
    if result["ok"]:
        click.echo(f"\nSync complete: {result['interactions']} interactions, "
                    f"{result['notes']} notes, {result['sessions']} sessions")


@main.command()
@click.option("--max-notes", type=int, default=None, help="Max notes to process")
@click.option("--force", is_flag=True, help="Regenerate all, ignoring tracker")
@click.option("--dry-run", is_flag=True, help="Preview without calling LLM")
@click.pass_context
def synthesize(ctx, max_notes, force, dry_run):
    """Synthesize training data from Pi notes using LM Studio."""
    from cortex_train.steps.synthesize import run_synthesize

    result = run_synthesize(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        max_notes=max_notes,
        force=force,
        dry_run=dry_run,
    )
    if result["ok"]:
        click.echo(f"\nSynthesis: {result['examples_generated']} examples "
                    f"from {result['notes_processed']} notes")


@main.command()
@click.option("--no-notes", is_flag=True, help="Skip note-based knowledge injection")
@click.option("--no-personality", is_flag=True, help="Skip personality shaping examples")
@click.option("--no-curated", is_flag=True, help="Skip curated Hub examples")
@click.option("--no-synthetic", is_flag=True, help="Skip LM Studio synthesized examples")
@click.option("--no-heartbeat", is_flag=True, help="Skip heartbeat examples")
@click.option("--seed", type=int, default=42, help="Random seed for split")
@click.pass_context
def prepare(ctx, no_notes, no_personality, no_curated, no_synthetic, no_heartbeat, seed):
    """Prepare training dataset by merging all data sources."""
    from cortex_train.steps.prepare import run_prepare

    result = run_prepare(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        include_notes=not no_notes,
        include_personality=not no_personality,
        include_curated=not no_curated,
        include_synthetic=not no_synthetic,
        include_heartbeat=not no_heartbeat,
        seed=seed,
    )
    if result["ok"]:
        click.echo(f"\nDataset: {result['train_size']} train, {result['test_size']} test")
        click.echo(f"Sources: {json.dumps(result['sources'], indent=2)}")


@main.command()
@click.option("--epochs", type=int, default=None, help="Override training epochs")
@click.option("--batch-size", type=int, default=None, help="Override batch size")
@click.option("--lr", type=float, default=None, help="Override learning rate")
@click.option("--lora-rank", type=int, default=None, help="Override LoRA rank")
@click.option("--lora-alpha", type=int, default=None, help="Override LoRA alpha")
@click.option("--no-unsloth", is_flag=True, help="Skip Unsloth, use standard PEFT")
@click.pass_context
def train(ctx, epochs, batch_size, lr, lora_rank, lora_alpha, no_unsloth):
    """Fine-tune base model with LoRA adapter (GPU required)."""
    from cortex_train.steps.train import run_train

    result = run_train(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=lr,
        lora_rank=lora_rank,
        lora_alpha=lora_alpha,
        no_unsloth=no_unsloth,
    )
    if result["ok"]:
        click.echo(f"\nTraining complete: loss={result['final_loss']:.4f}, "
                    f"bloom={result['bloom_number']}, {result['training_time_s']}s")


@main.command("eval")
@click.option("--save/--no-save", default=True, help="Save results to eval_results.json")
@click.option("--max-tokens", type=int, default=128, help="Max tokens per response")
@click.pass_context
def evaluate(ctx, save, max_tokens):
    """Evaluate fine-tuned model vs base (perplexity + responses)."""
    from cortex_train.steps.evaluate import run_evaluate

    result = run_evaluate(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        save=save,
        max_tokens=max_tokens,
    )
    if result["ok"]:
        base = result.get("base_perplexity") or {}
        ft = result.get("finetuned_perplexity") or {}
        click.echo(f"\nBase perplexity: {base.get('perplexity', '?')}")
        click.echo(f"Fine-tuned perplexity: {ft.get('perplexity', '?')}")


@main.command()
@click.option("--merge/--no-merge", default=True, help="Merge adapter into base (recommended)")
@click.option("--export-only", is_flag=True, help="Export GGUF only, skip deploy")
@click.option("--deploy-only", is_flag=True, help="Deploy existing GGUF only")
@click.option("--quantization", type=str, default=None, help="GGUF quantization (q8_0, q4_k_m)")
@click.pass_context
def deploy(ctx, merge, export_only, deploy_only, quantization):
    """Export LoRA to GGUF and deploy to Pi."""
    from cortex_train.steps.deploy import run_deploy

    result = run_deploy(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        merge=merge,
        export_only=export_only,
        deploy_only=deploy_only,
        quantization=quantization,
    )
    if result["ok"]:
        click.echo(f"\nExported: {result['gguf_path']} ({result['gguf_size_mb']} MB)")
        click.echo(f"Deployed: {result['deployed']}")


@main.command()
@click.option("--skip-sync", is_flag=True, help="Skip data sync from Pi")
@click.option("--skip-learn", is_flag=True, help="Skip LM Studio synthesis")
@click.option("--export-only", is_flag=True, help="Don't deploy to Pi")
@click.option("--pi-ip", type=str, default=None, help="Override Pi IP address")
@click.pass_context
def dream(ctx, skip_sync, skip_learn, export_only, pi_ip):
    """Run the full dream training cycle (sync -> train -> deploy)."""
    from cortex_train.steps.dream import run_dream

    result = run_dream(
        settings=ctx.obj["settings"],
        paths=ctx.obj["paths"],
        on_progress=cli_progress,
        skip_sync=skip_sync,
        skip_synthesize=skip_learn,
        export_only=export_only,
        pi_ip=pi_ip,
    )
    if result["ok"]:
        m = result.get("metrics", {})
        click.echo(f"\nDream complete!")
        click.echo(f"  Steps: {', '.join(result['steps_completed'])}")
        click.echo(f"  Loss: {m.get('final_loss', '?')}")
        click.echo(f"  Bloom: {m.get('bloom_number', '?')}")
        click.echo(f"  Time: {result['elapsed_s']}s")
    else:
        click.echo(f"\nDream FAILED")
        for err in result.get("errors", []):
            click.echo(f"  ERROR: {err}")


@main.command()
@click.pass_context
def status(ctx):
    """Show training status: bloom, last training, dataset size, Pi connection."""
    paths = ctx.obj["paths"]
    settings = ctx.obj["settings"]

    click.echo(f"cortex-train v{__version__}")
    click.echo(f"Training dir: {paths.training_dir}")

    # Warnings
    warnings = paths.validate()
    if warnings:
        for w in warnings:
            click.echo(f"  WARNING: {w}")
        return

    click.echo(f"Model: {settings.model.base_model}")
    click.echo(f"Pi: {settings.pi.user}@{settings.pi.host}")
    click.echo(f"LoRA: r={settings.lora.r}, alpha={settings.lora.alpha}")
    click.echo(f"LM Studio: {settings.lmstudio.url}")

    # Training log
    if paths.training_log_path.exists():
        try:
            with open(paths.training_log_path) as f:
                log = json.load(f)
            click.echo(f"\nLast training:")
            click.echo(f"  Loss: {log.get('final_loss', '?')}")
            click.echo(f"  Time: {log.get('training_time_s', '?')}s")
            click.echo(f"  Dataset: {log.get('dataset_size', '?')} examples")
            click.echo(f"  Bloom: {log.get('bloom_number', '?')}")
        except (json.JSONDecodeError, OSError):
            click.echo("\nTraining log: could not read")
    else:
        click.echo("\nNo training log found (no training runs yet)")

    # Dataset size
    from cortex_train.formats import load_jsonl
    synthetic = load_jsonl(paths.synthetic_path)
    curated = load_jsonl(paths.curated_path)
    interactions = load_jsonl(paths.interactions_path)
    notes = load_jsonl(paths.notes_path)

    click.echo(f"\nData files:")
    click.echo(f"  Interactions: {len(interactions)}")
    click.echo(f"  Notes: {len(notes)}")
    click.echo(f"  Synthetic: {len(synthetic)}")
    click.echo(f"  Curated: {len(curated)}")

    # Pi connectivity
    click.echo(f"\nPi connectivity:")
    try:
        from cortex_train.pi_client import pi_http_command
        result = pi_http_command(settings.pi.host, 8420, "ping")
        click.echo(f"  Pi: ONLINE (pong)")
    except Exception as e:
        click.echo(f"  Pi: OFFLINE ({e})")


if __name__ == "__main__":
    main()
