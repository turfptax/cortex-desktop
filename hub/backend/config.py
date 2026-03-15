"""Cortex Hub configuration."""

import json
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LM Studio
    lmstudio_url: str = "http://10.0.0.102:1234/v1"
    lmstudio_default_model: str = "smollm2-135m-instruct"

    # Pi connection
    pi_host: str = "10.0.0.25"
    pi_port: int = 8420
    pi_username: str = "cortex"
    pi_password: str = "cortex"

    # Training pipeline paths
    # Priority: env var CORTEX_HUB_TRAINING_DIR > embedded training/ > sibling cortex-pet-training/
    training_dir: str = ""
    scripts_dir: str = ""
    training_config_path: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8003

    class Config:
        env_prefix = "CORTEX_HUB_"

    def model_post_init(self, __context):
        if not self.training_dir:
            self.training_dir = str(self._find_training_dir())
        training = Path(self.training_dir)
        if not self.scripts_dir:
            self.scripts_dir = str(training / "scripts")
        if not self.training_config_path:
            self.training_config_path = str(training / "config" / "settings.json")

    @staticmethod
    def _find_training_dir() -> Path:
        """Find training dir: embedded training/ > sibling cortex-pet-training/."""
        import sys
        # In PyInstaller bundle, check next to the exe
        if getattr(sys, '_MEIPASS', None):
            bundle_dir = Path(sys._MEIPASS) / "training"
            if bundle_dir.exists() and any(bundle_dir.glob("scripts/*.py")):
                return bundle_dir
        # Embedded in repo: cortex-desktop/training/
        embedded = Path(__file__).resolve().parent.parent.parent / "training"
        if embedded.exists() and any(embedded.glob("scripts/*.py")):
            return embedded
        # Legacy: sibling cortex-pet-training/ repo
        sibling = Path(__file__).resolve().parent.parent.parent / "cortex-pet-training"
        return sibling

    @property
    def pi_base_url(self) -> str:
        return f"http://{self.pi_host}:{self.pi_port}"

    def load_training_config(self) -> dict:
        path = Path(self.training_config_path)
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def save_training_config(self, config: dict):
        path = Path(self.training_config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(config, f, indent=2)


settings = Settings()
