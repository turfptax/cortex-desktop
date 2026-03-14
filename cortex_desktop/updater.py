"""Auto-updater for Cortex Desktop.

Downloads the installer from GitHub releases and launches it silently.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx


def get_update_dir() -> Path:
    """Return (and create) the updates directory under %APPDATA%/Cortex/updates/."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    update_dir = base / "Cortex" / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    return update_dir


async def download_installer(url: str) -> Path:
    """Download the installer .exe from the given URL.

    Returns the path to the downloaded file.
    """
    update_dir = get_update_dir()
    # Extract filename from URL, fallback to generic name
    filename = url.rsplit("/", 1)[-1] if "/" in url else "CortexHub-Setup-latest.exe"
    dest = update_dir / filename
    tmp = update_dir / f"{filename}.tmp"

    async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    # Atomic rename
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
    return dest


def launch_installer_and_exit(installer_path: Path, silent: bool = True):
    """Launch the Inno Setup installer and exit this process.

    Args:
        installer_path: Path to the downloaded .exe installer.
        silent: If True, run with /VERYSILENT (no UI). Otherwise /SILENT (progress bar only).
    """
    args = [str(installer_path)]
    if silent:
        args.extend(["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"])
    else:
        args.extend(["/SILENT", "/NORESTART"])

    # Launch installer as detached process
    if sys.platform == "win32":
        # DETACHED_PROCESS so it survives our exit
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        subprocess.Popen(args, start_new_session=True, close_fds=True)

    # Give the installer a moment to start, then exit
    import time
    time.sleep(1)
    os._exit(0)
