#!/usr/bin/env python3
"""Build whisper.cpp from a pinned commit and install the CLI binary
into hub/backend/bin/ for both local dev runs and CI/CD bundling.

Slice 7 CP2 (voice transcription via whisper.cpp).

Why a script rather than just a CI step? Because we want both:
  - GitHub Actions builds it before PyInstaller bundles the exe
  - Local devs run the same command and get the binary in the
    same place, so `python -m cortex_desktop` from source works
    out of the box

Usage
-----
    python scripts/build_whisper_cpp.py
    python scripts/build_whisper_cpp.py --force   # ignore version marker

Idempotent: writes hub/backend/bin/whisper-cli.version with the pinned
tag, skips the build entirely if the marker matches.

Output
------
    hub/backend/bin/whisper-cli.exe      (Windows) or
    hub/backend/bin/whisper-cli          (Unix)
    hub/backend/bin/whisper-cli.version

The PyInstaller spec includes hub/backend/bin/ as a data dir, so
the binary lands inside the bundle at runtime. cortex-desktop's
transcribe router prefers _MEIPASS/hub/backend/bin/whisper-cli
when running from the bundle, falling back to the repo path for
source dev.

Pinned to a specific tag for reproducibility. Bump WHISPER_CPP_TAG
intentionally to update.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Bump this to update whisper.cpp. Pick a tag from:
#   https://github.com/ggerganov/whisper.cpp/tags
WHISPER_CPP_TAG = "v1.7.4"
WHISPER_CPP_REPO = "https://github.com/ggerganov/whisper.cpp.git"


REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "hub" / "backend" / "bin"
VERSION_MARKER = BIN_DIR / "whisper-cli.version"


def is_windows() -> bool:
    return platform.system() == "Windows"


def binary_name() -> str:
    return "whisper-cli.exe" if is_windows() else "whisper-cli"


def already_built(force: bool) -> bool:
    """True if the marker matches and the binary exists."""
    if force:
        return False
    if not VERSION_MARKER.is_file():
        return False
    if not (BIN_DIR / binary_name()).is_file():
        return False
    try:
        existing = VERSION_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return existing == WHISPER_CPP_TAG


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """Run a command, stream output to our stdout, fail loudly."""
    pretty = " ".join(cmd)
    cwd_label = f" (cwd={cwd})" if cwd else ""
    print(f"==> {pretty}{cwd_label}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def have_cmake() -> bool:
    return shutil.which("cmake") is not None


def build(work_dir: Path) -> Path:
    """Clone + build whisper.cpp inside work_dir. Returns the path
    to the produced whisper-cli binary."""
    src = work_dir / "whisper.cpp"
    if not src.exists():
        run(["git", "clone", "--depth", "1", "--branch",
             WHISPER_CPP_TAG, WHISPER_CPP_REPO, str(src)])
    else:
        # Re-fetching: keep idempotency. Pin to the tag.
        run(["git", "-C", str(src), "fetch", "--tags",
             "--depth", "1", "origin", WHISPER_CPP_TAG])
        run(["git", "-C", str(src), "checkout", "--detach",
             WHISPER_CPP_TAG])

    build_dir = src / "build"
    # whisper.cpp uses CMake; -DBUILD_SHARED_LIBS=OFF keeps the
    # binary self-contained. -DWHISPER_BUILD_EXAMPLES=ON ships
    # the whisper-cli executable.
    cmake_cfg = [
        "cmake", "-B", str(build_dir), "-S", str(src),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DWHISPER_BUILD_EXAMPLES=ON",
    ]
    run(cmake_cfg)
    run(["cmake", "--build", str(build_dir),
         "--config", "Release", "--target", "whisper-cli"])

    # Find the produced binary. Windows + multi-config generators
    # put it under build/bin/Release/; Unix generators under
    # build/bin/. Try both.
    candidates = [
        build_dir / "bin" / "Release" / binary_name(),
        build_dir / "bin" / binary_name(),
        build_dir / binary_name(),
    ]
    for c in candidates:
        if c.is_file():
            return c
    # Fall back: search whole build tree
    matches = list(build_dir.rglob(binary_name()))
    if matches:
        return matches[0]
    raise RuntimeError(
        "Build succeeded but couldn't locate {} under {}".format(
            binary_name(), build_dir,
        ))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if the version marker matches.")
    args = ap.parse_args()

    if already_built(args.force):
        print(f"whisper-cli {WHISPER_CPP_TAG} already at {BIN_DIR}; "
              f"skipping build (use --force to override)")
        return 0

    if not have_cmake():
        print("ERROR: cmake not found on PATH. Install CMake "
              "(winget install Kitware.CMake / brew install cmake / "
              "apt install cmake) and re-run.", file=sys.stderr)
        return 2

    BIN_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="whispercpp-build-") as td:
        work = Path(td)
        try:
            built = build(work)
        except subprocess.CalledProcessError as e:
            print(f"\nBuild failed: {e}", file=sys.stderr)
            return 1
        target = BIN_DIR / binary_name()
        if target.exists():
            target.unlink()
        shutil.copy2(built, target)
        # Make sure it's executable on Unix
        if not is_windows():
            target.chmod(target.stat().st_mode | 0o111)

    VERSION_MARKER.write_text(WHISPER_CPP_TAG + "\n", encoding="utf-8")
    print(f"\nDone. whisper-cli {WHISPER_CPP_TAG} installed at "
          f"{BIN_DIR / binary_name()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
