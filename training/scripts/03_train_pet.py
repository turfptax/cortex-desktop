"""Step 2: Fine-tune a base model with LoRA.

Uses PEFT (Parameter-Efficient Fine-Tuning) with LoRA adapters so you
only train ~1-5% of the model parameters. Fast, low VRAM, and the
adapter is tiny (<10MB) so you can keep multiple versions.

Supports any HuggingFace causal LM model (SmolLM2, Qwen, etc.).
Base model and LoRA settings are read from config/settings.json,
with model-specific defaults from config/model_presets.json.

Tries Unsloth first for 2x faster training / 70% less VRAM.
Falls back to standard PEFT + TRL if Unsloth isn't available.

Usage:
    python 02_train_pet.py                        # train with defaults
    python 02_train_pet.py --epochs 5             # more epochs
    python 02_train_pet.py --batch-size 2         # reduce if OOM
    python 02_train_pet.py --base-model <name>    # different base model

Requirements:
    pip install torch transformers datasets peft trl accelerate
    Optional: pip install unsloth (for faster training)

Outputs:
    ../models/pet-lora/  - PEFT LoRA adapter weights
    ../models/pet-lora/training_log.json - Training metrics
"""
import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DATASET_DIR = PROJECT_DIR / "dataset"
MODELS_DIR = PROJECT_DIR / "models"
CONFIG_PATH = PROJECT_DIR / "config" / "settings.json"


def _next_bloom(models_dir):
    """Auto-detect next bloom number from existing pet-lora-bloom-* directories."""
    existing = []
    for p in models_dir.glob("pet-lora-bloom-*"):
        m = re.match(r"pet-lora-bloom-(\d+)$", p.name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _archive_bloom(output_dir, models_dir, log_data):
    """Copy the trained adapter to a bloom-numbered archive directory."""
    bloom = _next_bloom(models_dir)
    bloom_dir = models_dir / f"pet-lora-bloom-{bloom}"
    shutil.copytree(output_dir, bloom_dir)
    # Add bloom number to the log inside the archived copy
    bloom_log = bloom_dir / "training_log.json"
    log_data["bloom"] = bloom
    with open(bloom_log, "w") as f:
        json.dump(log_data, f, indent=2)
    return bloom, bloom_dir


def check_deps():
    """Check required packages are installed."""
    missing = []
    for pkg in ["torch", "transformers", "datasets", "peft", "trl", "accelerate"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print(f"Install: pip install {' '.join(missing)}")
        sys.exit(1)


check_deps()

import torch
from datasets import load_from_disk, Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Config not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_model_presets():
    """Load model presets for architecture-specific defaults."""
    presets_path = CONFIG_PATH.parent / "model_presets.json"
    if presets_path.exists():
        with open(presets_path) as f:
            data = json.load(f)
        return data.get("presets", {})
    return {}


def try_unsloth(base_model, max_seq_length, dtype):
    """Try loading model with Unsloth for faster training.

    Returns (model, tokenizer) or (None, None) if Unsloth isn't available.
    """
    try:
        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=base_model,
            max_seq_length=max_seq_length,
            dtype=dtype,
            load_in_4bit=True,
        )
        print("  Using Unsloth (2x faster training)")
        return model, tokenizer, True
    except ImportError:
        print("  Unsloth not installed — using standard PEFT")
        return None, None, False
    except Exception as e:
        print(f"  Unsloth failed ({e}) — falling back to standard PEFT")
        return None, None, False


def load_standard(base_model, dtype):
    """Load model with standard transformers + PEFT."""
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=dtype,
        device_map="auto",
    )

    # Ensure pad token exists
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        model.config.pad_token_id = model.config.eos_token_id

    return model, tokenizer


def load_dataset_from_disk_or_jsonl():
    """Load the prepared dataset (HF Dataset or JSONL fallback)."""
    # Try HuggingFace Dataset format first
    try:
        ds = load_from_disk(str(DATASET_DIR))
        return ds
    except Exception:
        pass

    # Fallback: load from JSONL files
    train_path = DATASET_DIR / "train.jsonl"
    test_path = DATASET_DIR / "test.jsonl"

    if not train_path.exists():
        print(f"Dataset not found at {DATASET_DIR}")
        print("Run 01_prepare_dataset.py first.")
        sys.exit(1)

    def load_jsonl(path):
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    from datasets import DatasetDict

    train_data = load_jsonl(train_path)
    test_data = load_jsonl(test_path) if test_path.exists() else []

    ds = DatasetDict({
        "train": Dataset.from_list(train_data),
        "test": Dataset.from_list(test_data) if test_data else Dataset.from_list(train_data[:1]),
    })
    return ds


def format_chat(example, tokenizer):
    """Apply the chat template to convert messages list to a single string."""
    text = tokenizer.apply_chat_template(
        example["messages"],
        tokenize=False,
        add_generation_prompt=False,
    )
    return {"text": text}


def main():
    parser = argparse.ArgumentParser(description="Fine-tune a model with LoRA")
    parser.add_argument("--base-model", default=None,
                        help="Base model (default: from settings.json)")
    parser.add_argument("--epochs", type=int, default=None,
                        help="Training epochs (default: from settings.json)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Per-device batch size (default: from settings.json)")
    parser.add_argument("--learning-rate", type=float, default=None,
                        help="Learning rate (default: from settings.json)")
    parser.add_argument("--lora-rank", type=int, default=None,
                        help="LoRA rank (default: from settings.json)")
    parser.add_argument("--lora-alpha", type=int, default=None,
                        help="LoRA alpha (default: from settings.json)")
    parser.add_argument("--no-unsloth", action="store_true",
                        help="Skip Unsloth, use standard PEFT")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: ../models/pet-lora)")
    args = parser.parse_args()

    config = load_config()
    model_cfg = config.get("model", {})
    lora_cfg = config.get("lora", {})
    train_cfg = config.get("training", {})
    presets = load_model_presets()

    # Resolve parameters (CLI overrides config, config overrides presets)
    base_model = args.base_model or model_cfg.get("base_model", "HuggingFaceTB/SmolLM2-135M-Instruct")
    preset = presets.get(base_model, {})
    max_seq_length = model_cfg.get("max_seq_length", preset.get("max_seq_length", 2048))
    epochs = args.epochs or train_cfg.get("epochs", 3)
    batch_size = args.batch_size or train_cfg.get("batch_size", 4)
    lr = args.learning_rate or train_cfg.get("learning_rate", 2e-4)
    lora_r = args.lora_rank or lora_cfg.get("r", preset.get("recommended_lora_r", 16))
    lora_alpha = args.lora_alpha or lora_cfg.get("alpha", preset.get("recommended_lora_alpha", 32))
    lora_target = lora_cfg.get("target_modules", preset.get("lora_target_modules", ["q_proj", "v_proj"]))
    lora_dropout = lora_cfg.get("dropout", 0.05)
    grad_accum = train_cfg.get("gradient_accumulation_steps", 4)
    warmup_steps = train_cfg.get("warmup_steps", 10)
    logging_steps = train_cfg.get("logging_steps", 5)
    fp16 = train_cfg.get("fp16", True)

    output_dir = Path(args.output_dir) if args.output_dir else MODELS_DIR / "pet-lora"

    # Determine dtype
    dtype_str = model_cfg.get("dtype", "float16")
    dtype = torch.float16 if dtype_str == "float16" else torch.bfloat16

    print("=== Cortex Pet Training — LoRA Fine-Tune ===")
    print(f"  Base model:     {base_model}")
    print(f"  Max seq length: {max_seq_length}")
    print(f"  LoRA rank:      {lora_r}")
    print(f"  LoRA alpha:     {lora_alpha}")
    print(f"  LoRA targets:   {lora_target}")
    print(f"  Epochs:         {epochs}")
    print(f"  Batch size:     {batch_size}")
    print(f"  Grad accum:     {grad_accum}")
    print(f"  Learning rate:  {lr}")
    print(f"  Output:         {output_dir}")

    # Check GPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"\n  GPU: {gpu_name} ({gpu_mem:.1f} GB)")
    else:
        print("\n  WARNING: No GPU detected. Training will be slow.")
        fp16 = False
        dtype = torch.float32  # float16 not well supported on CPU

    # Load dataset
    print(f"\nLoading dataset from {DATASET_DIR}")
    ds = load_dataset_from_disk_or_jsonl()
    print(f"  Train: {len(ds['train'])} samples")
    print(f"  Test:  {len(ds['test'])} samples")

    # Load model
    print(f"\nLoading model: {base_model}")
    using_unsloth = False

    if not args.no_unsloth:
        model, tokenizer, using_unsloth = try_unsloth(base_model, max_seq_length, dtype)

    if not using_unsloth:
        model, tokenizer = load_standard(base_model, dtype)

    # Apply chat template to dataset
    print("\nApplying chat template...")
    ds_formatted = ds.map(
        lambda ex: format_chat(ex, tokenizer),
        remove_columns=ds["train"].column_names,
    )

    # Apply LoRA
    if using_unsloth:
        from unsloth import FastLanguageModel
        model = FastLanguageModel.get_peft_model(
            model,
            r=lora_r,
            lora_alpha=lora_alpha,
            target_modules=lora_target,
            lora_dropout=lora_dropout,
            bias="none",
            use_gradient_checkpointing="unsloth",
            max_seq_length=max_seq_length,
        )
    else:
        print(f"\nApplying LoRA (rank={lora_r}, alpha={lora_alpha})")
        lora_config = LoraConfig(
            r=lora_r,
            lora_alpha=lora_alpha,
            target_modules=lora_target,
            lora_dropout=lora_dropout,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(model, lora_config)

    model.print_trainable_parameters()

    # Output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Training arguments — build kwargs, then filter to what SFTConfig accepts
    sft_kwargs = dict(
        output_dir=str(output_dir),
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        learning_rate=lr,
        warmup_steps=warmup_steps,
        num_train_epochs=epochs,
        fp16=fp16 and device == "cuda",
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_steps=logging_steps,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        report_to="none",
        push_to_hub=False,
        weight_decay=train_cfg.get("weight_decay", 0.01),
    )

    # CPU-only training needs use_cpu flag
    if device != "cuda":
        sft_kwargs["use_cpu"] = True

    # Detect which params SFTConfig / SFTTrainer accept (API changed across TRL versions)
    import inspect
    sft_config_params = set(inspect.signature(SFTConfig.__init__).parameters.keys())

    for optional_key, optional_val in [
        ("max_seq_length", max_seq_length),
        ("dataset_text_field", "text"),
    ]:
        if optional_key in sft_config_params:
            sft_kwargs[optional_key] = optional_val

    training_args = SFTConfig(**sft_kwargs)

    # Trainer — TRL >=0.28 renamed 'tokenizer' to 'processing_class'
    trainer_kwargs = dict(
        model=model,
        args=training_args,
        train_dataset=ds_formatted["train"],
        eval_dataset=ds_formatted["test"],
    )

    sft_trainer_params = set(inspect.signature(SFTTrainer.__init__).parameters.keys())
    if "processing_class" in sft_trainer_params:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer

    trainer = SFTTrainer(**trainer_kwargs)

    # Train
    print(f"\nStarting training: {epochs} epochs, batch={batch_size}, "
          f"grad_accum={grad_accum}")
    print(f"Effective batch size: {batch_size * grad_accum}")
    print(f"Output: {output_dir}\n")

    start_time = time.time()
    result = trainer.train()
    elapsed = time.time() - start_time

    # Save final adapter
    print("\nSaving LoRA adapter...")
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # Save training log
    log_data = {
        "base_model": base_model,
        "epochs": epochs,
        "batch_size": batch_size,
        "gradient_accumulation_steps": grad_accum,
        "effective_batch_size": batch_size * grad_accum,
        "learning_rate": lr,
        "lora_rank": lora_r,
        "lora_alpha": lora_alpha,
        "lora_targets": lora_target,
        "lora_dropout": lora_dropout,
        "max_seq_length": max_seq_length,
        "train_samples": len(ds["train"]),
        "test_samples": len(ds["test"]),
        "device": device,
        "gpu": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "using_unsloth": using_unsloth,
        "fp16": fp16,
        "final_loss": result.training_loss,
        "total_steps": result.global_step,
        "training_time_s": round(elapsed, 1),
    }

    log_path = output_dir / "training_log.json"
    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)

    print(f"\nTraining complete!")
    print(f"  Final loss:    {result.training_loss:.4f}")
    print(f"  Total steps:   {result.global_step}")
    print(f"  Training time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Adapter saved: {output_dir}")
    print(f"  Log saved:     {log_path}")

    # Archive adapter to bloom directory (pet-lora-bloom-1, bloom-2, ...)
    bloom, bloom_dir = _archive_bloom(output_dir, MODELS_DIR, log_data)
    print(f"  Bloom:         {bloom} ({bloom_dir.name})")
    print(f"\nNext: python 03_eval.py")


if __name__ == "__main__":
    main()
