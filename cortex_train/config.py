"""Unified configuration for the training pipeline.

Loads from settings.json + model_presets.json, with optional CLI overrides.
Uses plain dataclasses (no pydantic dependency) so the package stays lightweight.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PiConfig:
    """Pi Zero connection settings."""
    user: str = "turfptax"
    host: str = "10.0.0.25"
    db_remote_path: str = "/home/turfptax/cortex.db"
    model_deploy_dir: str = "/home/turfptax/models"
    config_path: str = "/home/turfptax/cortex-core/src/config.py"
    service_name: str = "cortex-core"
    llama_service_name: str = "llama-server"
    llama_service_path: str = "/etc/systemd/system/llama-server.service"


@dataclass
class ModelConfig:
    """Base model settings."""
    base_model: str = "Qwen/Qwen3.5-0.8B"
    max_seq_length: int = 2048
    dtype: str = "float16"
    gguf_quantization: str = "q8_0"


@dataclass
class LoraConfig:
    """LoRA adapter hyperparameters."""
    r: int = 16
    alpha: int = 32
    target_modules: List[str] = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj"])
    dropout: float = 0.05
    bias: str = "none"


@dataclass
class TrainingConfig:
    """Training hyperparameters."""
    epochs: int = 3
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_steps: int = 10
    weight_decay: float = 0.01
    logging_steps: int = 5
    save_strategy: str = "epoch"
    fp16: bool = True


@dataclass
class DataConfig:
    """Dataset preparation options."""
    min_response_tokens: int = 1
    test_split: float = 0.1
    max_interactions: Optional[int] = None
    include_notes: bool = True
    include_personality: bool = True
    include_synthetic: bool = True
    include_curated: bool = True
    include_heartbeat: bool = True
    curated_min_quality: int = 3


@dataclass
class LMStudioConfig:
    """LM Studio teacher model settings."""
    url: str = "http://10.0.0.102:1234/v1"
    examples_per_note: int = 3
    temperature: float = 0.8
    max_tokens: int = 2048


@dataclass
class TrainSettings:
    """Complete training pipeline configuration."""
    pi: PiConfig = field(default_factory=PiConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    lora: LoraConfig = field(default_factory=LoraConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    lmstudio: LMStudioConfig = field(default_factory=LMStudioConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for saving back to settings.json)."""
        import dataclasses
        return {
            "pi": dataclasses.asdict(self.pi),
            "model": dataclasses.asdict(self.model),
            "lora": dataclasses.asdict(self.lora),
            "training": dataclasses.asdict(self.training),
            "data": dataclasses.asdict(self.data),
            "lmstudio": dataclasses.asdict(self.lmstudio),
        }


def _merge_dataclass(dc, overrides: Dict[str, Any]):
    """Update a dataclass instance from a dict, ignoring unknown keys."""
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(dc)}
    for key, value in overrides.items():
        if key in valid_fields and value is not None:
            setattr(dc, key, value)
    return dc


def load_settings(settings_path: Path, overrides: Optional[Dict[str, Any]] = None) -> TrainSettings:
    """Load TrainSettings from settings.json with optional overrides.

    Args:
        settings_path: Path to settings.json
        overrides: Dict of section->values to override, e.g.
                   {"training": {"epochs": 5}, "model": {"base_model": "..."}}
    """
    settings = TrainSettings()

    if settings_path.exists():
        with open(settings_path) as f:
            raw = json.load(f)

        section_map = {
            "pi": settings.pi,
            "model": settings.model,
            "lora": settings.lora,
            "training": settings.training,
            "data": settings.data,
            "lmstudio": settings.lmstudio,
        }

        for section_name, dc in section_map.items():
            if section_name in raw and isinstance(raw[section_name], dict):
                _merge_dataclass(dc, raw[section_name])

    # Apply CLI overrides on top
    if overrides:
        section_map = {
            "pi": settings.pi,
            "model": settings.model,
            "lora": settings.lora,
            "training": settings.training,
            "data": settings.data,
            "lmstudio": settings.lmstudio,
        }
        for section_name, values in overrides.items():
            if section_name in section_map and isinstance(values, dict):
                _merge_dataclass(section_map[section_name], values)

    return settings


def save_settings(settings: TrainSettings, settings_path: Path) -> None:
    """Save TrainSettings back to settings.json."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings.to_dict(), f, indent=2)


def load_model_presets(presets_path: Path) -> Dict[str, Any]:
    """Load model_presets.json — per-model LoRA/quant defaults."""
    if not presets_path.exists():
        return {}
    with open(presets_path) as f:
        return json.load(f)


def apply_model_preset(settings: TrainSettings, presets: Dict[str, Any]) -> TrainSettings:
    """Apply model-specific preset values (LoRA targets, quantization) if available."""
    model_name = settings.model.base_model
    if model_name in presets:
        preset = presets[model_name]
        if "lora" in preset:
            _merge_dataclass(settings.lora, preset["lora"])
        if "model" in preset:
            _merge_dataclass(settings.model, preset["model"])
    return settings
