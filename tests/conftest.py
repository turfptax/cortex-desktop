"""Test bootstrap: make hub/backend importable and the hub/frontend package
not importable as a side effect."""
from __future__ import annotations

import sys
from pathlib import Path

# hub/backend isn't a package — it's a flat dir of modules. Add it to the
# import path so tests can `from services.plugin_manager import ...` the
# same way main.py does.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_HUB_BACKEND = _REPO_ROOT / "hub" / "backend"
if str(_HUB_BACKEND) not in sys.path:
    sys.path.insert(0, str(_HUB_BACKEND))
