#!/usr/bin/env python3
"""One-command setup for the Cortex voice agent.

Run with your system Python from the cortex-desktop/ directory:
    python voice_agent/setup.py

It creates the sidecar venv, installs dependencies, and scaffolds the local config
files WITHOUT overwriting anything you already have. It never writes personal
data; you add your OpenRouter key and (optionally) your persona afterward.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent   # voice_agent/
REPO = HERE.parent                        # cortex-desktop/
VENV = HERE / ".venv"
REQS = HERE / "requirements.txt"


def _venv_python(venv: Path) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return venv / sub / exe


def _cortex_home() -> Path:
    # ~/.cortex holds secrets.toml (shared with the rest of Cortex).
    return Path.home() / ".cortex"


def _appdata_cortex() -> Path:
    # voice.local.toml + runtime data live next to the Hub's config.
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "Cortex"
    else:
        base = Path.home() / ".cortex"
    return base


def make_venv() -> Path:
    py = _venv_python(VENV)
    if py.is_file():
        print(f"venv exists: {VENV}")
    else:
        print(f"creating venv: {VENV}")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        py = _venv_python(VENV)
    print("installing dependencies (this can take a few minutes)...")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(REQS)], check=True)
    return py


def scaffold_secrets() -> None:
    p = _cortex_home() / "secrets.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        print(f"secrets exist (left untouched): {p}")
        return
    p.write_text('[openrouter]\napi_key = ""  # paste your OpenRouter key here\n',
                 encoding="utf-8")
    print(f"created (fill in the key): {p}")


def scaffold_persona() -> None:
    example = HERE / "voice.local.toml.example"
    p = _appdata_cortex() / "voice.local.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        print(f"persona exists (left untouched): {p}")
        return
    if example.is_file():
        shutil.copyfile(example, p)
        print(f"created (optional, edit to personalize): {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up the Cortex voice agent.")
    ap.add_argument("--no-venv", action="store_true",
                    help="skip venv creation + dependency install (config only)")
    args = ap.parse_args()

    py = _venv_python(VENV)
    if not args.no_venv:
        py = make_venv()
    scaffold_secrets()
    scaffold_persona()

    secrets = _cortex_home() / "secrets.toml"
    print("\nNext steps:")
    print(f"  1. Put your OpenRouter key in {secrets}")
    print("  2. Make sure your Cortex (cortex-core) is reachable; set CORTEX_HOST "
          "if it is not 10.0.0.25")
    print("  3. Run it from the cortex-desktop/ directory:")
    if os.name == "nt":
        print(f'       set PYTHONUTF8=1 && "{py}" -m voice_agent.bot -t webrtc')
    else:
        print(f'       PYTHONUTF8=1 "{py}" -m voice_agent.bot -t webrtc')
    print("     then open http://localhost:7860/ (voice) + http://localhost:7861/ (monitor)")
    print("  Or launch it from the Cortex Hub by setting this in "
          "%APPDATA%/Cortex/config.json:")
    print(f'       "voice_agent_python": "{py}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
