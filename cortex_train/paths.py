"""Path management for the training pipeline.

Auto-detects the training data directory using this priority:
  1. CORTEX_TRAINING_DIR env var
  2. Embedded in cortex-desktop repo (../cortex-pet-training relative to this package)
  3. Sibling directory (cortex-pet-training next to cortex-desktop)
  4. Explicit path passed to TrainPaths()
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _find_training_dir() -> Optional[Path]:
    """Auto-detect the training data directory."""
    # 1. Environment variable
    env = os.environ.get("CORTEX_TRAINING_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    # 2. Sibling to this package's repo (cortex-desktop/../cortex-pet-training)
    package_dir = Path(__file__).parent  # cortex_train/
    repo_dir = package_dir.parent        # cortex-desktop/
    sibling = repo_dir.parent / "cortex-pet-training"
    if sibling.is_dir() and (sibling / "config").is_dir():
        return sibling

    # 3. Embedded training/ dir inside cortex-desktop
    embedded = repo_dir / "training"
    if embedded.is_dir() and (embedded / "config").is_dir():
        return embedded

    return None


@dataclass
class TrainPaths:
    """All paths used by the training pipeline.

    Initialize with an explicit training_dir or let it auto-detect.
    Creates directories as needed on first access.
    """
    training_dir: Path = field(default_factory=lambda: _find_training_dir() or Path.cwd())

    @property
    def config_dir(self) -> Path:
        return self.training_dir / "config"

    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def model_presets_path(self) -> Path:
        return self.config_dir / "model_presets.json"

    @property
    def raw_data(self) -> Path:
        p = self.training_dir / "raw_data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def dataset(self) -> Path:
        p = self.training_dir / "dataset"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def models(self) -> Path:
        p = self.training_dir / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def exports(self) -> Path:
        p = self.training_dir / "exports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def adapter_dir(self) -> Path:
        """Current LoRA adapter output directory."""
        return self.models / "pet-lora"

    @property
    def training_log_path(self) -> Path:
        return self.adapter_dir / "training_log.json"

    @property
    def eval_results_path(self) -> Path:
        return self.adapter_dir / "eval_results.json"

    # Raw data files
    @property
    def db_path(self) -> Path:
        return self.raw_data / "cortex.db"

    @property
    def interactions_path(self) -> Path:
        return self.raw_data / "interactions.jsonl"

    @property
    def notes_path(self) -> Path:
        return self.raw_data / "notes.jsonl"

    @property
    def sessions_path(self) -> Path:
        return self.raw_data / "sessions.jsonl"

    @property
    def synthetic_path(self) -> Path:
        return self.raw_data / "synthetic_examples.jsonl"

    @property
    def curated_path(self) -> Path:
        return self.raw_data / "curated_examples.jsonl"

    @property
    def heartbeat_path(self) -> Path:
        return self.raw_data / "heartbeat_examples.jsonl"

    @property
    def synthesis_tracker_path(self) -> Path:
        return self.raw_data / ".synthesis_tracker.json"

    def adapter_archive(self, bloom: int) -> Path:
        """Versioned LoRA adapter archive directory."""
        return self.models / f"pet-lora-bloom-{bloom}"

    def gguf_path(self, bloom: Optional[int] = None, quantization: str = "q8_0") -> Path:
        """GGUF export path."""
        if bloom is not None:
            return self.exports / f"pet-lora-bloom-{bloom}-{quantization}.gguf"
        return self.exports / "pet-lora.gguf"

    def validate(self) -> list:
        """Check that required directories/files exist. Returns list of warnings."""
        warnings = []
        if not self.training_dir.is_dir():
            warnings.append(f"Training dir not found: {self.training_dir}")
        if not self.settings_path.exists():
            warnings.append(f"Settings not found: {self.settings_path}")
        return warnings
