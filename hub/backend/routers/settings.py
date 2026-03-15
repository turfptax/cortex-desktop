"""Settings API router — config management, network scan, MCP setup."""

import asyncio
import json
import os
import platform
import shutil
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

router = APIRouter()


# ── Config file path (same as cortex_desktop/config.py) ──

def _get_config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Cortex" / "config.json"


DEFAULT_CONFIG = {
    "pi_host": "",
    "pi_port": 8420,
    "pi_username": "cortex",
    "pi_password": "cortex",
    "lmstudio_url": "http://10.0.0.102:1234/v1",
    "lmstudio_model": "smollm2-135m-instruct",
    "hub_port": 8003,
    "hub_host": "127.0.0.1",
    "auto_open_browser": True,
    "auto_start_daemon": False,
    "first_run": True,
}


def _load_config() -> dict:
    path = _get_config_path()
    if path.exists():
        try:
            with open(path) as f:
                stored = json.load(f)
            return {**DEFAULT_CONFIG, **stored}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def _save_config(config: dict) -> dict:
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return config


def _apply_to_env(config: dict):
    """Push config values into env so backend picks them up."""
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
        if key in config and config[key]:
            os.environ[env_var] = str(config[key])


# ── GET /settings ──

@router.get("")
async def get_settings():
    """Return current config."""
    config = _load_config()
    return {"ok": True, "config": config}


# ── POST /settings ──

class SettingsUpdate(BaseModel):
    pi_host: Optional[str] = None
    pi_port: Optional[int] = None
    pi_username: Optional[str] = None
    pi_password: Optional[str] = None
    lmstudio_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    hub_port: Optional[int] = None
    auto_open_browser: Optional[bool] = None
    first_run: Optional[bool] = None


@router.post("")
async def save_settings(update: SettingsUpdate):
    """Save config and apply to environment."""
    config = _load_config()

    # Merge non-None fields
    for key, value in update.model_dump(exclude_none=True).items():
        config[key] = value

    _save_config(config)
    _apply_to_env(config)

    # Also update the live settings object if possible
    try:
        from config import settings
        if update.pi_host is not None:
            settings.pi_host = update.pi_host
        if update.pi_port is not None:
            settings.pi_port = update.pi_port
        if update.pi_username is not None:
            settings.pi_username = update.pi_username
        if update.pi_password is not None:
            settings.pi_password = update.pi_password
        if update.lmstudio_url is not None:
            settings.lmstudio_url = update.lmstudio_url
    except Exception:
        pass

    return {"ok": True, "config": config}


# ── POST /settings/test-connection ──

class TestConnectionRequest(BaseModel):
    host: str
    port: int = 8420
    username: str = "cortex"
    password: str = "cortex"


@router.post("/test-connection")
async def test_connection(req: TestConnectionRequest):
    """Test if a Cortex Pi is reachable at the given address."""
    url = f"http://{req.host}:{req.port}/health"
    try:
        start = time.monotonic()
        async with httpx.AsyncClient() as client:
            r = await client.get(url, auth=(req.username, req.password), timeout=5.0)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "ok": True,
                "reachable": True,
                "response_ms": elapsed_ms,
                "hostname": data.get("hostname", "unknown"),
                "uptime": data.get("uptime", ""),
                "version": data.get("version", ""),
            }
        else:
            return {"ok": True, "reachable": False, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": True, "reachable": False, "error": str(e)}


# ── POST /settings/scan ──

async def _check_host(ip: str, port: int, timeout: float = 0.5) -> Optional[dict]:
    """Try to connect to a host on the given port."""
    try:
        start = time.monotonic()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        writer.close()
        await writer.wait_closed()

        # Try to get health info
        hostname = ""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"http://{ip}:{port}/health",
                    auth=("cortex", "cortex"),
                    timeout=2.0,
                )
                if r.status_code == 200:
                    data = r.json() if "json" in r.headers.get("content-type", "") else {}
                    hostname = data.get("hostname", "")
        except Exception:
            pass

        return {
            "ip": ip,
            "port": port,
            "response_ms": elapsed_ms,
            "hostname": hostname or ip,
        }
    except Exception:
        return None


def _get_local_subnets() -> list[str]:
    """Get local IP addresses to derive subnets to scan."""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
    except Exception:
        pass

    # Fallback: try connecting to a public DNS to find local IP
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass

    return ips


@router.post("/scan")
async def scan_network():
    """Scan local subnets for Cortex Pi devices (port 8420)."""
    local_ips = _get_local_subnets()
    if not local_ips:
        return {"ok": False, "error": "Could not determine local network", "devices": []}

    # Build list of IPs to scan (all /24 subnets)
    targets = set()
    for local_ip in local_ips:
        parts = local_ip.rsplit(".", 1)
        if len(parts) == 2:
            for i in range(1, 255):
                ip = f"{parts[0]}.{i}"
                if ip not in local_ips:  # Skip self
                    targets.add(ip)

    # Scan in parallel (batches of 50 to avoid overwhelming)
    devices = []
    target_list = sorted(targets)

    for batch_start in range(0, len(target_list), 50):
        batch = target_list[batch_start:batch_start + 50]
        tasks = [_check_host(ip, 8420, timeout=0.5) for ip in batch]
        results = await asyncio.gather(*tasks)
        for result in results:
            if result is not None:
                devices.append(result)

    return {
        "ok": True,
        "scanned_subnets": [f"{ip.rsplit('.', 1)[0]}.0/24" for ip in local_ips],
        "devices": devices,
    }


# ── GET /settings/mcp-config ──

@router.get("/mcp-config")
async def get_mcp_config():
    """Generate MCP config JSON for Claude Desktop / Claude Code."""
    config = _load_config()

    # Detect Python path
    python_path = sys.executable
    if platform.system() == "Windows":
        python_path = python_path.replace("\\", "\\\\")

    # Check if cortex-mcp is installed
    mcp_installed = shutil.which("cortex-mcp") is not None
    if not mcp_installed:
        # Also check if importable
        try:
            import importlib
            importlib.import_module("cortex_mcp")
            mcp_installed = True
        except ImportError:
            pass

    # Build the MCP config
    env_vars = {}
    if config.get("pi_host"):
        env_vars["CORTEX_PI_HOST"] = config["pi_host"]
    if config.get("pi_port") and config["pi_port"] != 8420:
        env_vars["CORTEX_PI_PORT"] = str(config["pi_port"])

    mcp_config = {
        "mcpServers": {
            "cortex": {
                "type": "stdio",
                "command": "python",
                "args": ["-m", "cortex_mcp.server"],
            }
        }
    }
    if env_vars:
        mcp_config["mcpServers"]["cortex"]["env"] = env_vars

    # Claude Desktop config file path
    if platform.system() == "Windows":
        claude_config_path = str(
            Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
        )
    elif platform.system() == "Darwin":
        claude_config_path = str(
            Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
    else:
        claude_config_path = str(Path.home() / ".config" / "claude" / "claude_desktop_config.json")

    return {
        "ok": True,
        "mcp_config": mcp_config,
        "mcp_config_json": json.dumps(mcp_config, indent=2),
        "python_path": sys.executable,
        "claude_config_path": claude_config_path,
        "mcp_installed": mcp_installed,
        "pip_install_cmd": "pip install cortex-mcp",
    }


# ── GET /settings/check-update ──

GITHUB_REPO = "turfptax/cortex-desktop"

def _current_version() -> str:
    """Get the current app version."""
    try:
        from cortex_desktop import __version__
        return __version__
    except ImportError:
        return "0.1.0"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse '0.1.0' or 'v0.1.0' into (0, 1, 0)."""
    v = v.lstrip("vV").strip()
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


@router.get("/check-update")
async def check_update():
    """Check GitHub releases for a newer version."""
    current = _current_version()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=10.0)

        if r.status_code == 404:
            # No releases yet
            return {
                "ok": True,
                "current_version": current,
                "latest_version": current,
                "update_available": False,
                "message": "No releases published yet.",
            }

        if r.status_code != 200:
            return {"ok": False, "error": f"GitHub API returned {r.status_code}"}

        data = r.json()
        latest_tag = data.get("tag_name", "")
        latest_version = latest_tag.lstrip("vV")
        html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
        published = data.get("published_at", "")
        body = data.get("body", "")

        # Find the Windows asset download URLs
        download_url = ""
        installer_url = ""
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            dl = asset.get("browser_download_url", "")
            # Installer .exe (e.g. CortexHub-Setup-0.3.0.exe)
            if name.lower().startswith("cortexhub-setup") and name.endswith(".exe"):
                installer_url = dl
            # .zip fallback
            elif name.endswith(".zip") and "windows" in name.lower():
                download_url = dl
            elif name.endswith(".zip") and not download_url:
                download_url = dl

        update_available = _parse_version(latest_version) > _parse_version(current)

        return {
            "ok": True,
            "current_version": current,
            "latest_version": latest_version,
            "update_available": update_available,
            "release_url": html_url,
            "download_url": download_url,
            "installer_url": installer_url,
            "published_at": published,
            "release_notes": body[:500] if body else "",
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "current_version": current}


# ── POST /settings/apply-update ──

def _find_repo_root() -> Optional[Path]:
    """Find the git repo root for cortex-desktop (dev/source installs)."""
    try:
        import cortex_desktop
        pkg_dir = Path(cortex_desktop.__file__).parent
        # Walk up to find .git directory
        for parent in [pkg_dir, pkg_dir.parent, pkg_dir.parent.parent]:
            if (parent / ".git").exists():
                return parent
    except Exception:
        pass
    return None


@router.post("/apply-update")
async def apply_update():
    """Update the app — tries installer first, falls back to git pull for dev installs."""
    import subprocess

    # First, check what's available
    update_info = await check_update()
    if not update_info.get("ok"):
        return {"ok": False, "error": update_info.get("error", "Failed to check for updates")}

    if not update_info.get("update_available"):
        return {"ok": False, "error": "No update available"}

    installer_url = update_info.get("installer_url", "")

    # Try installer-based update first (production installs)
    if installer_url:
        try:
            from cortex_desktop.updater import download_installer, launch_installer_and_exit

            installer_path = await download_installer(installer_url)

            import threading
            threading.Thread(
                target=launch_installer_and_exit,
                args=(installer_path,),
                daemon=False,
            ).start()

            return {
                "ok": True,
                "message": "Update downloading and installing. The app will restart.",
                "version": update_info.get("latest_version"),
            }
        except Exception as e:
            return {"ok": False, "error": f"Installer update failed: {e}"}

    # Fall back to git pull (dev / source installs)
    repo_root = _find_repo_root()
    if not repo_root:
        return {
            "ok": False,
            "error": "No installer found and not running from a git repo.",
            "release_url": update_info.get("release_url", ""),
        }

    try:
        old_version = _current_version()

        # Git pull
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return {
                "ok": False,
                "error": f"git pull failed: {result.stderr.strip() or result.stdout.strip()}",
            }

        # Re-read version from the updated file
        version_file = repo_root / "cortex_desktop" / "__init__.py"
        new_version = update_info.get("latest_version", "unknown")
        if version_file.exists():
            for line in version_file.read_text().splitlines():
                if line.startswith("__version__"):
                    new_version = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

        return {
            "ok": True,
            "message": f"Updated via git pull ({old_version} → {new_version}). Restart the app to use the new version.",
            "version": new_version,
            "pull_output": result.stdout.strip()[:500],
            "needs_restart": True,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git pull timed out"}
    except Exception as e:
        return {"ok": False, "error": f"Git update failed: {e}"}
