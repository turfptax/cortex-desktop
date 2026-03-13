"""Cortex Desktop — main application entry point.

Launches:
1. FastAPI backend (serves API + pre-built React frontend) in a background thread
2. System tray icon on the main thread (Windows requires main thread for tray)

Usage:
    python -m cortex_desktop.app
"""

import os
import sys
import signal
import threading
import time
import webbrowser
from pathlib import Path


def _find_backend_dir() -> Path:
    """Locate the Hub backend directory.

    In dev mode: hub/backend/ relative to this repo root.
    In PyInstaller bundle: bundled alongside the exe.
    """
    # PyInstaller sets _MEIPASS for bundled apps
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "backend"

    # Dev mode: look relative to this file
    this_dir = Path(__file__).resolve().parent
    repo_root = this_dir.parent  # cortex_desktop -> cortex-desktop

    # Self-contained: hub/backend/ in this repo
    backend_dir = repo_root / "hub" / "backend"
    if backend_dir.is_dir():
        return backend_dir

    # Legacy: alongside cortex-hub (for development in monorepo)
    monorepo_backend = repo_root.parent / "cortex-hub" / "backend"
    if monorepo_backend.is_dir():
        return monorepo_backend

    raise RuntimeError(
        f"Cannot find backend directory. Checked: {backend_dir}, {monorepo_backend}"
    )


def _find_frontend_dist() -> str:
    """Locate the pre-built frontend directory.

    In dev mode: hub/frontend/dist/ or frontend_dist/
    In PyInstaller bundle: frontend_dist/ alongside the exe.
    """
    if hasattr(sys, "_MEIPASS"):
        dist = Path(sys._MEIPASS) / "frontend_dist"
        if dist.is_dir():
            return str(dist)

    this_dir = Path(__file__).resolve().parent
    repo_root = this_dir.parent  # cortex_desktop -> cortex-desktop

    # Self-contained: hub/frontend/dist/ in this repo
    hub_dist = repo_root / "hub" / "frontend" / "dist"
    if hub_dist.is_dir():
        return str(hub_dist)

    # Pre-copied dist
    local_dist = repo_root / "frontend_dist"
    if local_dist.is_dir():
        return str(local_dist)

    # Legacy: monorepo layout
    monorepo_dist = repo_root.parent / "cortex-hub" / "frontend" / "dist"
    if monorepo_dist.is_dir():
        return str(monorepo_dist)

    return ""  # No frontend dist found — API-only mode


def _start_server(host: str, port: int, backend_dir: str, static_dir: str):
    """Start uvicorn in the current thread."""
    import uvicorn

    # Add backend to Python path so its imports work
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # Tell the backend where to find static files
    if static_dir:
        os.environ["CORTEX_STATIC_DIR"] = static_dir

    # In PyInstaller bundle, backend files are data files in _internal/backend/.
    # uvicorn's string import "main:app" executes main.py which does
    # `from fastapi import FastAPI` — this works because backend_dir is on
    # sys.path and fastapi is frozen in the bundle. But we need to ensure
    # sys.path has the backend dir FIRST so `from config import settings`
    # and `from routers import ...` resolve from the data files.
    #
    # We also need to set the working directory so relative paths work.
    original_cwd = os.getcwd()
    os.chdir(backend_dir)

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level="info",
        # No reload in production/desktop mode
    )


def _run_mcp_server():
    """Run the MCP server in stdio mode (for Claude Desktop integration)."""
    # Import and run the cortex_mcp server
    from cortex_mcp.server import main as mcp_main
    mcp_main()


def main():
    """Main entry point for Cortex Desktop.

    Usage:
        cortex-desktop          Launch the Hub (tray + web UI)
        cortex-desktop --mcp    Run as MCP server (stdio, for Claude Desktop)
    """
    # Check for --mcp flag
    if "--mcp" in sys.argv:
        _run_mcp_server()
        return

    # Hide console window in tray mode (Windows only)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0  # SW_HIDE
            )
        except Exception:
            pass

    from cortex_desktop.config import load_config, apply_config_to_env
    from cortex_desktop.tray import CortexTray

    # Load and apply configuration
    config = load_config()
    apply_config_to_env(config)

    hub_host = config.get("hub_host", "127.0.0.1")
    hub_port = config.get("hub_port", 8003)

    # Locate backend and frontend
    backend_dir = str(_find_backend_dir())
    static_dir = _find_frontend_dist()

    if static_dir:
        print(f"[cortex-desktop] Frontend: {static_dir}")
    else:
        print("[cortex-desktop] No frontend dist found — API-only mode")

    print(f"[cortex-desktop] Backend: {backend_dir}")
    print(f"[cortex-desktop] Server: http://{hub_host}:{hub_port}")

    # Shutdown event
    shutdown_event = threading.Event()

    def on_quit():
        """Called when user clicks Quit in tray."""
        print("[cortex-desktop] Shutting down...")
        shutdown_event.set()
        # Force exit after a grace period (uvicorn can be stubborn)
        threading.Timer(3.0, lambda: os._exit(0)).start()

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda *_: on_quit())

    # Start uvicorn in a background thread
    server_thread = threading.Thread(
        target=_start_server,
        args=(hub_host, hub_port, backend_dir, static_dir),
        daemon=True,
    )
    server_thread.start()

    # Wait a moment for the server to start, then open browser
    if config.get("auto_open_browser", True):
        def _open_browser():
            time.sleep(2)
            if not shutdown_event.is_set():
                webbrowser.open(f"http://localhost:{hub_port}")
        threading.Thread(target=_open_browser, daemon=True).start()

    # Run system tray on main thread (blocks until quit)
    tray = CortexTray(
        hub_port=hub_port,
        pi_host=config.get("pi_host", "10.0.0.25"),
        pi_port=config.get("pi_port", 8420),
        pi_username=config.get("pi_username", "cortex"),
        pi_password=config.get("pi_password", "cortex"),
        on_quit=on_quit,
    )

    try:
        tray.run()  # Blocks until tray.stop() or Quit
    except KeyboardInterrupt:
        on_quit()

    # If tray exits without shutdown, trigger it
    if not shutdown_event.is_set():
        on_quit()


if __name__ == "__main__":
    main()
