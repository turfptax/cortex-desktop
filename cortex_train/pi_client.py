"""Pi Zero SSH/SCP client for data sync and deployment.

All Pi communication for the training pipeline goes through this module.
Uses subprocess calls to scp/ssh (no paramiko dependency).
"""

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from cortex_train.errors import SyncError, DeployError


def scp_from_pi(
    user: str,
    host: str,
    remote_path: str,
    local_path: Path,
    timeout: int = 30,
) -> None:
    """SCP a file from the Pi to local filesystem.

    Raises SyncError on failure.
    """
    remote = f"{user}@{host}:{remote_path}"
    local_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["scp", remote, str(local_path)],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise SyncError(f"SCP failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise SyncError("'scp' not found. Install OpenSSH.")
    except subprocess.TimeoutExpired:
        raise SyncError(f"SCP timed out after {timeout}s. Is the Pi reachable?")


def scp_to_pi(
    local_path: Path,
    user: str,
    host: str,
    remote_path: str,
    timeout: int = 120,
) -> None:
    """SCP a file from local filesystem to the Pi.

    Raises DeployError on failure.
    """
    if not local_path.exists():
        raise DeployError(f"Local file not found: {local_path}")

    remote = f"{user}@{host}:{remote_path}"

    try:
        result = subprocess.run(
            ["scp", str(local_path), remote],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise DeployError(f"SCP to Pi failed: {result.stderr.strip()}")
    except FileNotFoundError:
        raise DeployError("'scp' not found. Install OpenSSH.")
    except subprocess.TimeoutExpired:
        raise DeployError(f"SCP timed out after {timeout}s.")


def ssh_command(
    user: str,
    host: str,
    command: str,
    timeout: int = 30,
) -> str:
    """Run a command on the Pi via SSH. Returns stdout.

    Raises SyncError on failure.
    """
    try:
        result = subprocess.run(
            ["ssh", f"{user}@{host}", command],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise SyncError(f"SSH command failed: {result.stderr.strip()}")
        return result.stdout
    except FileNotFoundError:
        raise SyncError("'ssh' not found. Install OpenSSH.")
    except subprocess.TimeoutExpired:
        raise SyncError(f"SSH timed out after {timeout}s.")


def restart_service(user: str, host: str, service_name: str, timeout: int = 15) -> None:
    """Restart a systemd service on the Pi via SSH."""
    ssh_command(user, host, f"sudo systemctl restart {service_name}", timeout=timeout)


def pi_http_command(
    host: str,
    port: int,
    command: str,
    payload: Optional[Dict[str, Any]] = None,
    username: str = "cortex",
    password: str = "cortex",
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Send a command to the Pi's HTTP API (same as MCP bridge).

    Returns the response dict. Raises SyncError on failure.
    """
    import base64

    url = f"http://{host}:{port}/api/cmd"
    body = {"command": command}
    if payload is not None:
        body["payload"] = payload

    data = json.dumps(body).encode("utf-8")
    creds = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        result = json.loads(resp.read())
        if not result.get("ok"):
            raise SyncError(f"Pi command '{command}' failed: {result.get('error', 'unknown')}")
        return result
    except urllib.error.HTTPError as e:
        raise SyncError(f"Pi HTTP error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise SyncError(f"Pi unreachable: {e.reason}")
