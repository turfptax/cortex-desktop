"""Cortex Ingest Agent entry point (GI ship, first test build).

Modes:
    CortexIngest.exe             tray mode: scheduled sweeps, status tooltip
    CortexIngest.exe --once      one sweep to stdout, no tray (smoke tests)
    CortexIngest.exe --connect   run the one-time OAuth consent and exit

The engine is cortex-intake (claude_time sweep + McpGateway); this shell
only schedules it, shows its pulse in the tray, and bootstraps consent on
first run. Every write goes over the connector surface with the agent's
own OAuth grant; there are no database credentials anywhere in this app.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import webbrowser

from cortex_intake import oauth
from cortex_intake.claude_time import sweep
from cortex_intake.mcp_gateway import build_mcp_gateway
from cortex_local.logging_setup import setup_logging

from cortex_agent import __version__

SWEEP_INTERVAL_S = 900
log = logging.getLogger("cortex.agent.shell")


class AgentState:
    """What the tray shows: the last sweep and the day's running total."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_summary: dict = {}
        self.last_at: float = 0.0
        self.minutes_today: int = 0
        self.last_error: str = ""
        self.sweeping = False

    def note(self, summary: dict) -> None:
        with self.lock:
            self.last_summary = summary
            self.last_at = time.time()
            self.minutes_today += int(summary.get("minutes_logged", 0))
            self.last_error = ""

    def note_error(self, error: str) -> None:
        with self.lock:
            self.last_error = error
            self.last_at = time.time()

    def line(self) -> str:
        with self.lock:
            if self.last_error:
                return "last sweep failed: " + self.last_error[:80]
            if not self.last_at:
                return "first sweep pending"
            summary = self.last_summary
            return "{} min logged this run ({} parsed); {} min today".format(
                summary.get("minutes_logged", 0),
                summary.get("parsed", 0), self.minutes_today)


def ensure_connected(*, open_browser: bool = True) -> None:
    """First run: walk the loopback consent. Afterwards the refresh chain
    keeps itself alive as long as sweeps run inside 90 days."""
    if oauth.load_auth():
        return
    log.info("no connection on file; starting the one-time consent")
    auth = oauth.first_run(open_browser=open_browser)
    log.info("connected to %s as client %s", auth["base_url"],
             auth["client_id"])


def run_sweep(state: AgentState) -> dict:
    with state.lock:
        if state.sweeping:
            return {"skipped": "sweep already running"}
        state.sweeping = True
    try:
        summary = sweep(build_mcp_gateway())
        state.note(summary)
        log.info("sweep done: %s min, %s parsed, %s unlinked",
                 summary.get("minutes_logged"), summary.get("parsed"),
                 len(summary.get("unlinked", [])))
        return summary
    except Exception as exc:  # noqa: BLE001 - the loop must survive anything
        state.note_error(f"{type(exc).__name__}: {exc}")
        log.exception("sweep failed")
        return {"error": str(exc)}
    finally:
        with state.lock:
            state.sweeping = False


def sweep_loop(state: AgentState, shutdown: threading.Event) -> None:
    while not shutdown.is_set():
        run_sweep(state)
        shutdown.wait(SWEEP_INTERVAL_S)


def tray_main(state: AgentState, shutdown: threading.Event) -> None:
    import pystray
    from pystray import Menu, MenuItem

    from cortex_desktop.tray import create_icon_image

    def on_sweep_now(icon, item):
        threading.Thread(target=run_sweep, args=(state,), daemon=True).start()

    def on_open_cortex(icon, item):
        auth = oauth.load_auth() or {}
        webbrowser.open(auth.get("base_url", oauth.DEFAULT_BASE) + "/ui")

    def on_quit(icon, item):
        shutdown.set()
        icon.stop()

    icon = pystray.Icon(
        "cortex-ingest",
        icon=create_icon_image(connected=True),
        title="Cortex Ingest Agent",
        menu=Menu(
            MenuItem(f"Cortex Ingest Agent v{__version__}",
                     None, enabled=False),
            MenuItem(lambda item: state.line(), None, enabled=False),
            MenuItem("Sweep now", on_sweep_now),
            MenuItem("Open Cortex", on_open_cortex),
            MenuItem("Quit", on_quit),
        ),
    )
    icon.run()


def _hide_console() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="cortex-ingest")
    parser.add_argument("--once", action="store_true",
                        help="one sweep to stdout, no tray")
    parser.add_argument("--connect", action="store_true",
                        help="run the one-time consent and exit")
    parser.add_argument("--no-browser", action="store_true",
                        help="print the consent URL instead of opening it")
    args = parser.parse_args()

    console = bool(args.once or args.connect)
    setup_logging(console=console)
    log.info("Cortex Ingest Agent v%s starting", __version__)

    if args.connect:
        auth = oauth.first_run(open_browser=not args.no_browser)
        print(json.dumps({"connected": auth["base_url"],
                          "client_id": auth["client_id"]}, indent=2))
        return 0

    ensure_connected(open_browser=not args.no_browser)

    state = AgentState()
    if args.once:
        summary = run_sweep(state)
        print(json.dumps(summary, indent=2, default=str))
        return 0 if "error" not in summary else 1

    _hide_console()
    shutdown = threading.Event()
    worker = threading.Thread(target=sweep_loop, args=(state, shutdown),
                              daemon=True)
    worker.start()
    try:
        tray_main(state, shutdown)
    except KeyboardInterrupt:
        pass
    shutdown.set()
    log.info("Cortex Ingest Agent stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
