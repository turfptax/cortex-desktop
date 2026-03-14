"""System tray icon for Cortex Desktop.

Provides a Windows system tray icon with menu for:
- Opening the Hub in the default browser
- Showing Pi connection status
- Accessing settings
- Quitting the application
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

import httpx
from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem, Menu
except ImportError:
    pystray = None


# Colors
COLOR_GREEN = (76, 175, 80)
COLOR_RED = (244, 67, 54)
COLOR_GRAY = (158, 158, 158)
COLOR_BRAIN = (139, 92, 246)  # Purple for Cortex


def _find_icon_png() -> Optional[str]:
    """Find the CortexIcon.png asset file."""
    # PyInstaller bundle
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "assets" / "CortexIcon.png"
        if p.exists():
            return str(p)

    # Dev mode: relative to this file
    this_dir = Path(__file__).resolve().parent
    repo_root = this_dir.parent
    p = repo_root / "assets" / "CortexIcon.png"
    if p.exists():
        return str(p)

    return None


def create_icon_image(connected: bool = False, size: int = 64) -> Image.Image:
    """Create the tray icon using the real Cortex logo with a status dot overlay.

    Falls back to a generated icon if the logo PNG is not found.

    Args:
        connected: Whether the Pi is reachable (green dot vs red dot).
        size: Icon size in pixels.
    """
    icon_path = _find_icon_png()

    if icon_path:
        # Use the real logo
        img = Image.open(icon_path).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
    else:
        # Fallback: generated purple circle with "C"
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        padding = 4
        draw.ellipse(
            [padding, padding, size - padding, size - padding],
            fill=COLOR_BRAIN,
        )
        cx, cy = size // 2, size // 2
        r = size // 4
        draw.arc(
            [cx - r, cy - r, cx + r, cy + r],
            start=45, end=315,
            fill=(255, 255, 255),
            width=max(3, size // 16),
        )

    # Draw status dot (bottom-right corner)
    draw = ImageDraw.Draw(img)
    dot_r = size // 8
    padding = 4
    dot_cx = size - padding - dot_r - 2
    dot_cy = size - padding - dot_r - 2
    status_color = COLOR_GREEN if connected else COLOR_RED
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=status_color,
        outline=(255, 255, 255),
        width=max(1, size // 32),
    )

    return img


def _check_pi_status(pi_host: str, pi_port: int, username: str, password: str) -> bool:
    """Check if the Pi is reachable."""
    try:
        r = httpx.get(
            f"http://{pi_host}:{pi_port}/health",
            auth=(username, password),
            timeout=3.0,
        )
        return r.status_code == 200
    except Exception:
        return False


class CortexTray:
    """Manages the system tray icon and menu."""

    def __init__(
        self,
        hub_port: int = 8003,
        pi_host: str = "10.0.0.25",
        pi_port: int = 8420,
        pi_username: str = "cortex",
        pi_password: str = "cortex",
        on_quit: Optional[Callable] = None,
    ):
        self.hub_port = hub_port
        self.pi_host = pi_host
        self.pi_port = pi_port
        self.pi_username = pi_username
        self.pi_password = pi_password
        self.on_quit = on_quit

        self._pi_connected = False
        self._running = False
        self._status_thread: Optional[threading.Thread] = None
        self._icon: Optional[pystray.Icon] = None
        self._update_info: Optional[dict] = None
        self._poll_count = 0

    @property
    def hub_url(self) -> str:
        return f"http://localhost:{self.hub_port}"

    def _open_hub(self, icon=None, item=None):
        """Open Hub in default browser."""
        webbrowser.open(self.hub_url)

    def _open_settings(self, icon=None, item=None):
        """Open settings page in browser."""
        webbrowser.open(f"{self.hub_url}/#/settings")

    def _quit(self, icon=None, item=None):
        """Quit the application."""
        self._running = False
        if self._icon:
            self._icon.stop()
        if self.on_quit:
            self.on_quit()

    def _get_pi_status_text(self) -> str:
        if self._pi_connected:
            return f"Pi: Connected ({self.pi_host})"
        return f"Pi: Offline ({self.pi_host})"

    def _apply_update(self, icon=None, item=None):
        """Trigger auto-update via the local API."""
        try:
            r = httpx.post(f"{self.hub_url}/api/settings/apply-update", timeout=10.0)
            data = r.json()
            if not data.get("ok"):
                # Fallback: open the release page
                release_url = (
                    data.get("release_url")
                    or (self._update_info or {}).get("release_url", "")
                )
                if release_url:
                    webbrowser.open(release_url)
        except Exception:
            # If API fails, open release page
            if self._update_info and self._update_info.get("release_url"):
                webbrowser.open(self._update_info["release_url"])

    def _build_menu(self) -> Menu:
        items = [
            MenuItem("Open Cortex Hub", self._open_hub, default=True),
            Menu.SEPARATOR,
            MenuItem(
                self._get_pi_status_text(),
                lambda: None,
                enabled=False,
            ),
            MenuItem(f"Hub: localhost:{self.hub_port}", lambda: None, enabled=False),
            Menu.SEPARATOR,
        ]

        # Show update item if available
        if self._update_info and self._update_info.get("update_available"):
            ver = self._update_info.get("latest_version", "?")
            items.append(MenuItem(f"Update Available (v{ver})", self._apply_update))
            items.append(Menu.SEPARATOR)

        items.extend([
            MenuItem("Settings", self._open_settings),
            MenuItem("Quit", self._quit),
        ])
        return Menu(*items)

    def _check_for_updates(self):
        """Check GitHub for a newer version (called from poll thread)."""
        try:
            r = httpx.get(f"{self.hub_url}/api/settings/check-update", timeout=10.0)
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    self._update_info = data
        except Exception:
            pass

    def _poll_status(self):
        """Background thread: poll Pi status every 30s, check updates every ~5 min."""
        import time

        while self._running:
            was_connected = self._pi_connected
            self._pi_connected = _check_pi_status(
                self.pi_host, self.pi_port, self.pi_username, self.pi_password
            )

            # Check for updates every 10th poll (~5 minutes)
            self._poll_count += 1
            old_update = self._update_info
            if self._poll_count % 10 == 1:  # First poll + every 10th
                self._check_for_updates()

            needs_refresh = (
                self._pi_connected != was_connected
                or self._update_info != old_update
            )
            if self._icon and needs_refresh:
                self._icon.icon = create_icon_image(connected=self._pi_connected)
                self._icon.menu = self._build_menu()

            # Sleep in small increments so we can exit quickly
            for _ in range(30):
                if not self._running:
                    return
                time.sleep(1)

    def run(self):
        """Run the tray icon (blocks — call from main thread on Windows)."""
        if pystray is None:
            print("[cortex-desktop] pystray not available, running headless")
            return

        self._running = True

        # Initial status check
        self._pi_connected = _check_pi_status(
            self.pi_host, self.pi_port, self.pi_username, self.pi_password
        )

        # Start status polling thread
        self._status_thread = threading.Thread(target=self._poll_status, daemon=True)
        self._status_thread.start()

        # Create and run tray icon (blocks)
        self._icon = pystray.Icon(
            name="CortexHub",
            title="Cortex Hub",
            icon=create_icon_image(connected=self._pi_connected),
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        """Stop the tray icon."""
        self._running = False
        if self._icon:
            self._icon.stop()
