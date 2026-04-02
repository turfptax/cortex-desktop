"""Step 3: Fine-tune a base model with LoRA.

Uses PEFT with LoRA adapters. Tries Unsloth first for 2x faster training,
falls back to standard PEFT + TRL. All heavy deps are lazy-imported.
"""

import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from cortex_train.config import TrainSettings
from cortex_train.errors import TrainingError
from cortex_train.paths import TrainPaths
from cortex_train.progress import ProgressCallback, make_step_progress, null_progress


def _next_bloom(models_dir: Path) -> int:
    """Auto-detect next bloom number from existing pet-lora-bloom-* directories."""
    existing = []
    for p in models_dir.glob("pet-lora-bloom-*"):
        m = re.match(r"pet-lora-bloom-(\d+)$", p.name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _archive_bloom(output_dir: Path, models_dir: Path, log_data: dict) -> tuple:
    """Copy the trained adapter to a bloom-numbered archive directory."""
    bloom = _next_bloom(models_dir)
    bloom_dir = models_dir / f"pet-lora-bloom-{bloom}"
    shutil.copytree(output_dir, bloom_dir)
    log_data["bloom_number"] = bloom
    with open(bloom_dir / "training_log.json", "w") as f:
        json.dump(log_data, f, indent=2)
    return bloom, bloom_dir


def _check_deps():
    """Check required ML packages. Raises TrainingError if missing."""
    missing = []
    for pkg in ["torch", "transformers", "datasets", "peft", "trl", "accelerate"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise TrainingError(f"Missing packages: {', '.join(missing)}. "
                            f"Install: pip install {' '.join(missing)}")


def run_train(
    settings: TrainSettings,
    paths: TrainPaths,
    on_progress: ProgressCallback = null_progress,
    epochs: Optional[int] = None,
    batch_size: Optional[int] = None,
    learning_rate: Optional[float] = None,
    lora_rank: Optional[int] = None,
    lora_alpha: Optional[int] = None,
    no_unsloth: bool = False,
) -> dict:
    """Fine-tune base model with LoRA adapter.

    All torch/transformers/peft imports happen inside this function.

    Returns:
        {ok, final_loss, training_time_s, dataset_size, bloom_number, total_steps}
    """
    emit = make_step_progress("train", on_progress)

    # Check dependencies
    emit("Checking ML dependencies...")
    _check_deps()

    # Lazy imports
    import torch
    from datasets import load_from_disk, Dataset, DatasetDict
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer, SFTConfig
    import inspect

    # Resolve parameters (CLI overrides > settings)
    base_model = settings.model.base_model
    max_seq_length = settings.model.max_seq_length
    _epochs = epochs or settings.training.epochs
    _batch_size = batch_size or settings.training.batch_size
    _lr = learning_rate or settings.training.learning_rate
    _lora_r = lora_rank or settings.lora.r
    _lora_alpha = lora_alpha or settings.lora.alpha
    _lora_target = settings.lora.target_modules
    _lora_dropout = settings.lora.dropout
    _grad_accum = settings.training.gradient_accumulation_steps
    _warmup_steps = settings.training.warmup_steps
    _logging_steps = settings.training.logging_steps
    _fp16 = settings.training.fp16

    output_dir = paths.adapter_dir

    # Determine dtype
    dtype = torch.float16 if settings.model.dtype == "float16" else torch.bfloat16

    emit(f"Model: {base_model}, LoRA r={_lora_r} alpha={_lora_alpha}, {_epochs} epochs")

    # Check GPU
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        emit(f"GPU: {gpu_name} ({gpu_mem:.1f} GB)")
    else:
        emit("WARNING: No GPU detected. Training will be slow.")
        _fp16 = False
        dtype = torch.float32

    # Load dataset
    emit("Loading dataset...")
    try:
        ds = load_from_disk(str(paths.dataset))
    except Exception:
        train_path = paths.dataset / "train.jsonl"
        if not train_path.exists():
            raise TrainingError(f"Dataset not found at {paths.dataset}. Run prepare step first.")
        from cortex_train.formats import load_jsonl
        train_data = load_jsonl(train_path)
        test_path = paths.dataset / "test.jsonl"
        test_data = load_jsonl(test_path) if test_path.exists() else train_data[:1]
        ds = DatasetDict({
            "train": Dataset.from_list(train_data),
            "test": Dataset.from_list(test_data),
        })

    emit(f"Dataset: {len(ds['train'])} train, {len(ds['test'])} test", pct=10)

    # Load model
    emit(f"Loading model: {base_model}...", pct=15)
    using_unsloth = False

    if not no_unsloth:
        try:
            from unsloth import FastLanguageModel
            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=base_model, max_seq_length=max_seq_length,
                dtype=dtype, load_in_4bit=True,
            )
            using_unsloth = True
            emit("Using Unsloth (2x faster training)")
        except (ImportError, Exception):
            pass

    if not using_unsloth:
        tokenizer = AutoTokenizer.from_pretrained(base_model)
        model = AutoModelForCausalLM.from_pretrained(
            base_model, torch_dtype=dtype, device_map="auto",
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
            model.config.pad_token_id = model.config.eos_token_id

    emit("Applying chat template...", pct=25)

    def format_chat(example):
        text = tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False,
        )
        return {"text": text}

    ds_formatted = ds.map(format_chat, remove_columns=ds["train"].column_names)

    # Apply LoRA
    emit(f"Applying LoRA (rank={_lora_r}, alpha={_lora_alpha})...", pct=30)
    if using_unsloth:
        from unsloth import FastLanguageModel
        model = FastLanguageModel.get_peft_model(
            model, r=_lora_r, lora_alpha=_lora_alpha,
            target_modules=_lora_target, lora_dropout=_lora_dropout,
            bias="none", use_gradient_checkpointing="unsloth",
            max_seq_length=max_seq_length,
        )
    else:
        lora_config = LoraConfig(
            r=_lora_r, lora_alpha=_lora_alpha,
            target_modules=_lora_target, lora_dropout=_lora_dropout,
            bias="none", task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(model, lora_config)

    # Training arguments
    output_dir.mkdir(parents=True, exist_ok=True)
    sft_kwargs = dict(
        output_dir=str(output_dir),
        per_device_train_batch_size=_batch_size,
        gradient_accumulation_steps=_grad_accum,
        learning_rate=_lr, warmup_steps=_warmup_steps,
        num_train_epochs=_epochs,
        fp16=_fp16 and device == "cuda",
        eval_strategy="epoch", save_strategy="epoch",
        logging_steps=_logging_steps,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss", greater_is_better=False,
        report_to="none", push_to_hub=False,
        weight_decay=settings.training.weight_decay,
    )
    if device != "cuda":
        sft_kwargs["use_cpu"] = True

    sft_config_params = set(inspect.signature(SFTConfig.__init__).parameters.keys())
    for key, val in [("max_seq_length", max_seq_length), ("dataset_text_field", "text")]:
        if key in sft_config_params:
            sft_kwargs[key] = val

    training_args = SFTConfig(**sft_kwargs)

    # Custom callback for progress reporting
    from transformers import TrainerCallback

    class ProgressReporter(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs and "loss" in logs:
                epoch = logs.get("epoch", 0)
                pct = 30 + (epoch / _epochs) * 60  # 30-90%
                emit(f"Epoch {epoch:.1f}/{_epochs}, loss={logs['loss']:.4f}",
                     pct=min(pct, 90),
                     metrics={"loss": logs["loss"], "epoch": epoch, "total_epochs": _epochs})

    # Build trainer
    trainer_kwargs = dict(
        model=model, args=training_args,
        train_dataset=ds_formatted["train"],
        eval_dataset=ds_formatted["test"],
        callbacks=[ProgressReporter()],
    )
    sft_trainer_params = set(inspect.signature(SFTTrainer.__init__).parameters.keys())
    if "processing_class" in sft_trainer_params:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer

    trainer = SFTTrainer(**trainer_kwargs)

    # Train
    emit(f"Training: {_epochs} epochs, batch={_batch_size}, effective={_batch_size * _grad_accum}", pct=30)
    start_time = time.time()
    result = trainer.train()
    elapsed = time.time() - start_time

    # Save adapter
    emit("Saving LoRA adapter...", pct=92)
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    # Save training log
    log_data = {
        "base_model": base_model, "epochs": _epochs,
        "batch_size": _batch_size, "gradient_accumulation_steps": _grad_accum,
        "effective_batch_size": _batch_size * _grad_accum,
        "learning_rate": _lr, "lora_rank": _lora_r, "lora_alpha": _lora_alpha,
        "lora_targets": _lora_target, "lora_dropout": _lora_dropout,
        "max_seq_length": max_seq_length,
        "train_samples": len(ds["train"]), "test_samples": len(ds["test"]),
        "dataset_size": len(ds["train"]) + len(ds["test"]),
        "device": device,
        "gpu": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "using_unsloth": using_unsloth, "fp16": _fp16,
        "final_loss": result.training_loss,
        "total_steps": result.global_step,
        "training_time_s": round(elapsed, 1),
    }

    with open(output_dir / "training_log.json", "w") as f:
        json.dump(log_data, f, indent=2)

    # Archive to bloom directory
    bloom, bloom_dir = _archive_bloom(output_dir, paths.models, log_data)
    emit(f"Training complete: loss={result.training_loss:.4f}, bloom={bloom}, {elapsed:.0f}s", pct=100)

    return {
        "ok": True,
        "final_loss": result.training_loss,
        "training_time_s": round(elapsed, 1),
        "dataset_size": len(ds["train"]) + len(ds["test"]),
        "bloom_number": bloom,
        "total_steps": result.global_step,
    }
