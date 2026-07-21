"""Cortex Hub configuration.

Single source of truth is the user config file at
%APPDATA%/Cortex/config.json (written by cortex_desktop and the
Settings UI). Resolution order for every field:

    1. CORTEX_HUB_* environment variable
    2. config.json value
    3. hardcoded default below

Before this, the backend only honored config.json when launched by
the tray app (which copied it into env vars). Run standalone via
uvicorn, it silently fell back to hardcoded IPs.
"""

import json
import os
import platform
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict,
)


def user_config_path() -> Path:
    """Path to the shared user config file (same logic as
    cortex_desktop.config; duplicated so the backend stays importable
    without the cortex_desktop package, e.g. bare uvicorn in CI)."""
    if platform.system() == "Windows":
        base = Path(os.environ.get(
            "APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get(
            "XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Cortex" / "config.json"


# config.json key -> Settings field name (keys identical unless noted)
_CONFIG_KEY_MAP = {
    "pi_host": "pi_host",
    "pi_port": "pi_port",
    "pi_username": "pi_username",
    "pi_password": "pi_password",
    "lmstudio_url": "lmstudio_url",
    "lmstudio_model": "lmstudio_default_model",
    "hub_host": "host",
    "hub_port": "port",
    "whisper_model": "whisper_model",
    "whisper_force_cpu": "whisper_force_cpu",
    "lemon_url": "lemon_url",
    "lemon_export_enabled": "lemon_export_enabled",
    "lemon_export_interval_s": "lemon_export_interval_s",
}


class UserConfigSource(PydanticBaseSettingsSource):
    """pydantic-settings source backed by config.json. Sits below env
    vars in precedence, above the field defaults."""

    def __init__(self, settings_cls):
        super().__init__(settings_cls)
        self._values: dict[str, Any] = {}
        try:
            raw = json.loads(user_config_path().read_text())
            for key, field in _CONFIG_KEY_MAP.items():
                if key in raw:
                    self._values[field] = raw[key]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def get_field_value(self, field: FieldInfo, field_name: str):
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._values.items() if v is not None}


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

    # Slice 7: local Whisper transcription. Default model is the
    # most accurate Whisper offers; ~3GB cached on first use.
    # Override via CORTEX_HUB_WHISPER_MODEL env var or config file.
    # Valid: tiny | base | small | medium | large | large-v2 |
    # large-v3 | turbo (also accepts language-specific variants
    # like "small.en" — see Whisper docs).
    whisper_model: str = "large-v3"

    # dev.14: bypass the GPU backend entirely. Lets the user opt
    # out of Vulkan even when a capable GPU is present — useful
    # when a driver update is fighting the bundled binary, or
    # the user has a known-bad GPU/driver combo. The transcribe
    # router also flips an in-memory sticky flag automatically
    # when a hard native crash is observed, so this setting is
    # the persistent escape hatch (the runtime flag resets on
    # Hub restart).
    whisper_force_cpu: bool = False

    # Lemon Squeezer dispatch export (2026-06-13). Desktop is the egress:
    # it pulls graded dispatches from the Pi and POSTs them to Lemon
    # Squeezer's ingest endpoint. Disabled by default — opt in once
    # `lemon serve` is running. See services/lemon_export.py.
    lemon_url: str = "http://localhost:8080"
    lemon_export_enabled: bool = False
    lemon_export_interval_s: int = 900

    model_config = SettingsConfigDict(env_prefix="CORTEX_HUB_")

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings,
        dotenv_settings, file_secret_settings,
    ):
        # Precedence: init kwargs > env vars > config.json > defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            UserConfigSource(settings_cls),
            file_secret_settings,
        )

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
        # Cloud P5: pi_host may carry a FULL base URL (e.g.
        # https://<cortex-solo-fqdn>/core, the gateway's authenticated
        # proxy to the cloud core). Used verbatim, port ignored. A bare
        # host keeps the legacy Pi form.
        if "://" in self.pi_host:
            return self.pi_host.rstrip("/")
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
