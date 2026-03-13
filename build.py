"""Build script for Cortex Desktop.

Orchestrates:
1. Build frontend (npm run build)
2. Copy dist/ to cortex-desktop/frontend_dist/
3. Run PyInstaller to create the exe

Usage:
    python build.py          # Full build
    python build.py --skip-frontend  # Skip npm build (reuse existing dist)
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "hub" / "frontend"
BACKEND_DIR = ROOT / "hub" / "backend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
LOCAL_DIST = ROOT / "frontend_dist"
SPEC_FILE = ROOT / "cortex_desktop.spec"


def step(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def build_frontend():
    """Run npm build in the frontend directory."""
    step("Building frontend (npm run build)")

    if not (FRONTEND_DIR / "package.json").exists():
        print(f"ERROR: No package.json at {FRONTEND_DIR}")
        sys.exit(1)

    subprocess.run(
        ["npm", "run", "build"],
        cwd=str(FRONTEND_DIR),
        check=True,
        shell=True,  # Needed on Windows for npm
    )

    if not FRONTEND_DIST.is_dir():
        print(f"ERROR: Build did not produce {FRONTEND_DIST}")
        sys.exit(1)

    print(f"Frontend built: {FRONTEND_DIST}")


def copy_frontend_dist():
    """Copy frontend dist/ into cortex-desktop/frontend_dist/."""
    step("Copying frontend dist")

    if LOCAL_DIST.exists():
        shutil.rmtree(LOCAL_DIST)

    shutil.copytree(FRONTEND_DIST, LOCAL_DIST)
    print(f"Copied to: {LOCAL_DIST}")


def run_pyinstaller():
    """Run PyInstaller with the spec file."""
    step("Running PyInstaller")

    if not SPEC_FILE.exists():
        print(f"ERROR: Spec file not found: {SPEC_FILE}")
        sys.exit(1)

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--noconfirm"],
        cwd=str(ROOT),
        check=True,
    )

    exe_path = ROOT / "dist" / "CortexHub" / "CortexHub.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild complete: {exe_path} ({size_mb:.1f} MB)")
    else:
        print(f"\nBuild output directory: {ROOT / 'dist' / 'CortexHub'}")


def main():
    parser = argparse.ArgumentParser(description="Build Cortex Desktop")
    parser.add_argument(
        "--skip-frontend", action="store_true",
        help="Skip frontend build (reuse existing dist/)",
    )
    parser.add_argument(
        "--skip-pyinstaller", action="store_true",
        help="Only build frontend and copy, skip PyInstaller",
    )
    args = parser.parse_args()

    if not args.skip_frontend:
        build_frontend()
    else:
        print("Skipping frontend build (--skip-frontend)")

    if FRONTEND_DIST.is_dir():
        copy_frontend_dist()
    elif LOCAL_DIST.is_dir():
        print(f"Using existing frontend_dist: {LOCAL_DIST}")
    else:
        print("WARNING: No frontend dist found. Building API-only.")

    if not args.skip_pyinstaller:
        run_pyinstaller()

    step("Done!")


if __name__ == "__main__":
    main()
