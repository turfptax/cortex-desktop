"""Cortex Daemon Client — TCP bridge to the cortex-daemon.

DaemonBridge is a drop-in replacement for SerialBridge that routes all
serial operations through the TCP daemon instead of touching the COM
port directly. This allows multiple processes to share the ESP32.

Usage:
    from cortex_mcp.daemon_client import DaemonBridge, ensure_daemon

    if ensure_daemon():
        bridge = DaemonBridge()
        lines = bridge.send_and_wait("CMD:ping", timeout=5)
"""

import json
import os
import socket
import subprocess
import sys
import time

from cortex_mcp.daemon import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    get_daemon_host,
    get_daemon_port,
    read_lock_file,
    read_secret,
    is_pid_alive,
)


class DaemonBridge:
    """Drop-in replacement for SerialBridge that routes through the daemon.

    Public interface matches SerialBridge: send_and_wait(), read_pending(),
    is_connected, port_name, baud_rate, buffered_count.
    """

    def __init__(self, host=None, port=None):
        self._host = host or get_daemon_host()
        self._port = port or get_daemon_port()
        self._cached_info = None
        self._token = read_secret() or ""

    def _request(self, data, timeout=10):
        """Send a JSON request to the daemon and return the response dict."""
        # Inject auth token into every request
        data = dict(data)
        data["token"] = self._token

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout + 2)  # TCP timeout slightly longer than command timeout
        try:
            sock.connect((self._host, self._port))
            line = json.dumps(data) + "\n"
            sock.sendall(line.encode("utf-8"))

            # Read response
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk

            if buf:
                resp = json.loads(buf.decode("utf-8").strip())
                # Re-read token on auth failure (daemon may have restarted)
                if not resp.get("ok") and "auth" in resp.get("error", "").lower():
                    fresh_token = read_secret() or ""
                    if fresh_token and fresh_token != self._token:
                        self._token = fresh_token
                return resp
            return {"ok": False, "error": "No response from daemon"}
        except socket.timeout:
            return {"ok": False, "error": "Daemon request timed out"}
        except ConnectionRefusedError:
            return {"ok": False, "error": "Daemon not running"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def send_and_wait(self, message, timeout=None, settle=0.4):
        """Send a message via daemon and return response lines."""
        req = {
            "cmd": "send_and_wait",
            "message": message,
            "timeout": timeout or 5,
            "settle": settle,
        }
        resp = self._request(req, timeout=(timeout or 5))
        if resp.get("ok"):
            return resp.get("lines", [])
        raise ConnectionError(resp.get("error", "Unknown daemon error"))

    def send(self, message):
        """Send a raw message via daemon (fire-and-forget)."""
        resp = self._request({"cmd": "send_raw", "message": message})
        if not resp.get("ok"):
            raise ConnectionError(resp.get("error", "Unknown daemon error"))

    def read_pending(self):
        """Read buffered messages from daemon."""
        resp = self._request({"cmd": "read_pending"})
        if resp.get("ok"):
            return resp.get("lines", [])
        return []

    def connect(self, port=None, baud=None):
        """No-op — daemon manages the serial connection."""
        pass

    def disconnect(self):
        """No-op — daemon manages the serial connection."""
        pass

    def _get_info(self):
        """Fetch daemon info (cached for 5 seconds)."""
        if self._cached_info and (time.time() - self._cached_info.get("_ts", 0)) < 5:
            return self._cached_info
        resp = self._request({"cmd": "info"}, timeout=3)
        if resp.get("ok"):
            resp["_ts"] = time.time()
            self._cached_info = resp
            return resp
        return {}

    @property
    def is_connected(self):
        info = self._get_info()
        return info.get("connected", False)

    @property
    def port_name(self):
        info = self._get_info()
        return info.get("port")

    @property
    def baud_rate(self):
        info = self._get_info()
        return info.get("baud", 115200)

    @property
    def buffered_count(self):
        info = self._get_info()
        return info.get("buffered", 0)

    @property
    def default_timeout(self):
        return 5.0

    def _ensure_connected(self):
        """No-op — daemon manages the serial connection."""
        pass


def is_daemon_running(host=None, port=None):
    """Check if the cortex-daemon is running and accepting connections.

    Sends an authenticated ping to verify the daemon is alive and we
    have a valid token.
    """
    host = host or get_daemon_host()
    port = port or get_daemon_port()
    token = read_secret() or ""

    # Try authenticated TCP ping
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect((host, port))
        line = json.dumps({"cmd": "ping", "token": token}) + "\n"
        sock.sendall(line.encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(1024)
            if not chunk:
                break
            buf += chunk
        sock.close()
        if buf:
            resp = json.loads(buf.decode("utf-8").strip())
            return resp.get("ok", False)
    except Exception:
        pass

    return False


def ensure_daemon(serial_port=None, baud=None, timeout=None):
    """Ensure the daemon is running. Spawns it if needed.

    Returns True if daemon is available, False otherwise.
    """
    host = get_daemon_host()
    port = get_daemon_port()

    # Already running?
    if is_daemon_running(host, port):
        return True

    # Check stale lock file
    lock_info = read_lock_file()
    if lock_info:
        pid = lock_info.get("pid")
        if pid and not is_pid_alive(pid):
            # Stale lock file — clean up
            try:
                from cortex_mcp.daemon import LOCK_FILE
                LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    # Spawn daemon
    import shutil
    daemon_exe = shutil.which("cortex-daemon")

    cmd = []
    if daemon_exe:
        cmd = [daemon_exe]
    else:
        # Fall back to python -m
        cmd = [sys.executable, "-m", "cortex_mcp.daemon"]

    # Add serial port args if specified
    if serial_port:
        cmd.extend(["--port", serial_port])
    if baud:
        cmd.extend(["--baud", str(baud)])
    if timeout:
        cmd.extend(["--timeout", str(timeout)])

    try:
        if sys.platform == "win32":
            # Detached process with visible console window on Windows
            # so the user can see the daemon banner and status
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen(
                cmd,
                creationflags=CREATE_NEW_CONSOLE,
            )
        else:
            # Detached process on Linux/Mac
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
    except Exception:
        return False

    # Wait for daemon to be ready
    for _ in range(30):  # 3 seconds max
        time.sleep(0.1)
        if is_daemon_running(host, port):
            return True

    return False
