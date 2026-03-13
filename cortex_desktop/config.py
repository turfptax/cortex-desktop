"""Cortex Desktop configuration management.

Stores user config at %APPDATA%/Cortex/config.json (Windows)
or ~/.config/cortex/config.json (Linux/Mac).
"""

import json
import os
import platform
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG = {
    "pi_host": "10.0.0.25",
    "pi_port": 8420,
    "pi_username": "cortex",
    "pi_password": "cortex",
    "lmstudio_url": "http://10.0.0.102:1234/v1",
    "lmstudio_model": "smollm2-135m-instruct",
    "hub_port": 8003,
    "hub_host": "127.0.0.1",  # localhost-only for desktop mode
    "auto_open_browser": True,
    "auto_start_daemon": False,
    "first_run": True,
}


def get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = base / "Cortex"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    """Get path to config file."""
    return get_config_dir() / "config.json"


def load_config() -> dict:
    """Load config from disk, creating defaults if needed."""
    path = get_config_path()
    if path.exists():
        try:
            with open(path) as f:
                stored = json.load(f)
            # Merge with defaults (add any new keys from defaults)
            config = {**DEFAULT_CONFIG, **stored}
            return config
        except (json.JSONDecodeError, OSError):
            pass
    # First run — create default config
    save_config(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    """Save config to disk."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def apply_config_to_env(config: dict) -> None:
    """Set environment variables so the FastAPI backend picks up our config.

    The backend uses pydantic-settings with CORTEX_HUB_ prefix.
    """
    env_map = {
        "pi_host": "CORTEX_HUB_PI_HOST",
        "pi_port": "CORTEX_HUB_PI_PORT",
        "pi_username": "CORTEX_HUB_PI_USERNAME",
        "pi_password": "CORTEX_HUB_PI_PASSWORD",
        "lmstudio_url": "CORTEX_HUB_LMSTUDIO_URL",
        "lmstudio_model": "CORTEX_HUB_LMSTUDIO_DEFAULT_MODEL",
        "hub_port": "CORTEX_HUB_PORT",
        "hub_host": "CORTEX_HUB_HOST",
    }
    for key, env_var in env_map.items():
        if key in config:
            os.environ[env_var] = str(config[key])


def is_first_run(config: dict) -> bool:
    """Check if this is the first run."""
    return config.get("first_run", True)


def mark_setup_complete(config: Optional[dict] = None) -> dict:
    """Mark first-run setup as complete."""
    if config is None:
        config = load_config()
    config["first_run"] = False
    save_config(config)
    return config
