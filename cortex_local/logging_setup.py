"""Rotating file logger for the Cortex Agent (CP1, brought forward
from the CORTEX_AGENT_PLAN section 2.2 layout).

Attaches to the "cortex.agent" logger namespace, NOT the root logger,
so running inside the v0.21 Hub shell does not disturb the Hub's own
logging (hub/backend/main.py owns the root config there).
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def log_dir() -> Path:
    base = Path(os.environ.get(
        "APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / "Cortex" / "logs"


def setup_logging(level: str = "INFO", console: bool = False) -> logging.Logger:
    """Idempotent: attach agent.log (5MB x 3) to the cortex.agent logger."""
    global _configured
    logger = logging.getLogger("cortex.agent")
    if _configured:
        return logger

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(d / "agent.log", maxBytes=5_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    _configured = True
    return logger
