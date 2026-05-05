"""Plugin sidecar lifecycle manager.

Each plugin runs as its own subprocess (e.g. cortex-vision.exe on
localhost:8004). cortex-desktop spawns them, polls a health
endpoint, and proxies HTTP traffic into them via dedicated routers
(see hub/backend/routers/video.py).

Registry lives at:
    %APPDATA%/Cortex/plugins/registry.json

Dev mode: when an entry has executable=null but a process is
already responding on the configured port, the manager treats it
as an externally-launched dev sidecar — tracks health, never
attempts to spawn.

Subprocess management is threading-based to match the rest of
cortex-desktop (uvicorn on Windows uses SelectorEventLoop, which
doesn't support asyncio.create_subprocess_exec). The health loop
itself is an asyncio task that calls into thread-safe helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("cortex.hub.plugins")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGISTRY_SCHEMA_VERSION = 1
HEALTH_INTERVAL_S = 5.0
HEALTH_TIMEOUT_S = 2.0
GRACEFUL_STOP_TIMEOUT_S = 5.0

# Reserved port range for plugins
PLUGIN_PORT_RANGE = range(8004, 8100)

# Marketplace (Phase 0: hardcoded; Phase 5 will fetch from a registry).
# `default_port` is what dev-register uses when the user clicks "Register
# dev sidecar" without specifying a port — pulled from each plugin's
# plugin.json manifest at the time it was added.
MARKETPLACE: list[dict[str, Any]] = [
    {
        "id": "cortex-vision",
        "name": "Cortex Vision",
        "description": (
            "Process videos, watch your screen live, record video journals."
        ),
        "github_repo": "turfptax/cortex-vision",
        "manifest_url": (
            "https://raw.githubusercontent.com/turfptax/cortex-vision/main/plugin.json"
        ),
        "default_port": 8004,
    },
]


def is_marketplace_id(plugin_id: str) -> bool:
    """True if plugin_id is in the hardcoded marketplace list. Used to
    gate dev-register so we don't accept arbitrary plugin ids."""
    return any(p["id"] == plugin_id for p in MARKETPLACE)


def marketplace_default_port(plugin_id: str) -> int | None:
    for p in MARKETPLACE:
        if p["id"] == plugin_id:
            return p.get("default_port")
    return None


def _appdata() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Cortex"
    # POSIX fallback (mostly for tests on macOS/Linux)
    return Path.home() / ".cortex"


def _default_registry_path() -> Path:
    return _appdata() / "plugins" / "registry.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class InstalledPlugin:
    """One row in registry.json — what we know about an installed plugin.

    Mutable runtime fields (last_health_check, _process, etc.) live
    on the manager-side state, NOT in the registry. Only the
    persistent fields are written back to disk.
    """

    id: str
    version: str
    variant: str = "auto"
    install_dir: str | None = None
    executable: str | None = None
    host: str = "127.0.0.1"
    port: int = 0
    auto_start: bool = True
    installed_at: str = field(default_factory=_utc_now)
    last_health_check: str | None = None

    # Runtime-only (excluded from registry serialization)
    is_running: bool = field(default=False, repr=False)
    is_dev_mode: bool = field(default=False, repr=False)

    def to_registry_dict(self) -> dict[str, Any]:
        """Serialize to registry.json format. Excludes runtime-only fields."""
        return {
            "version": self.version,
            "variant": self.variant,
            "install_dir": self.install_dir,
            "executable": self.executable,
            "host": self.host,
            "port": self.port,
            "auto_start": self.auto_start,
            "installed_at": self.installed_at,
            "last_health_check": self.last_health_check,
        }

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for /api/plugins responses. Includes runtime state."""
        d = asdict(self)
        # Drop private/non-essential fields if needed; keep it explicit
        return d


@dataclass
class PluginHealth:
    plugin_id: str
    running: bool
    healthy: bool
    last_check: str | None
    detail: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Plugin manager
# ---------------------------------------------------------------------------


class PluginManager:
    """Owns the registry + lifecycle of all sidecar plugins.

    One instance lives at app.state.plugins for the lifetime of the
    Hub backend. The health loop runs as an asyncio task started in
    main.py's startup handler.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self.registry_path = registry_path or _default_registry_path()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._plugins: dict[str, InstalledPlugin] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._stop_event = asyncio.Event()
        self._health_task: asyncio.Task | None = None
        self._load_registry()

    # ------------------------------------------------------------------ #
    # Registry persistence                                                #
    # ------------------------------------------------------------------ #

    def _load_registry(self) -> None:
        if not self.registry_path.exists():
            logger.info("No plugin registry yet at %s", self.registry_path)
            return
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read plugin registry: %s", e)
            return

        version = data.get("schema_version", 0)
        if version > REGISTRY_SCHEMA_VERSION:
            logger.warning(
                "Plugin registry schema_version=%d is newer than supported "
                "%d — refusing to load. Upgrade cortex-desktop.",
                version,
                REGISTRY_SCHEMA_VERSION,
            )
            return

        plugins = data.get("plugins", {})
        with self._lock:
            for pid, row in plugins.items():
                try:
                    self._plugins[pid] = InstalledPlugin(id=pid, **row)
                except TypeError as e:
                    logger.error(
                        "Skipping malformed registry entry %s: %s", pid, e
                    )

        logger.info(
            "Loaded plugin registry (%d entries) from %s",
            len(self._plugins),
            self.registry_path,
        )

    def _save_registry(self) -> None:
        with self._lock:
            payload = {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "plugins": {
                    pid: p.to_registry_dict() for pid, p in self._plugins.items()
                },
            }
        # Write-then-rename for atomicity
        tmp = self.registry_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.registry_path)

    # ------------------------------------------------------------------ #
    # Registry CRUD                                                       #
    # ------------------------------------------------------------------ #

    def list_installed(self) -> list[InstalledPlugin]:
        with self._lock:
            return list(self._plugins.values())

    def get(self, plugin_id: str) -> InstalledPlugin | None:
        with self._lock:
            return self._plugins.get(plugin_id)

    def upsert(self, plugin: InstalledPlugin) -> None:
        """Insert or update a registry entry. Does NOT spawn the process."""
        with self._lock:
            self._plugins[plugin.id] = plugin
        self._save_registry()

    def remove(self, plugin_id: str) -> bool:
        """Remove a registry entry. Caller must stop the process first."""
        with self._lock:
            if plugin_id not in self._plugins:
                return False
            self._plugins.pop(plugin_id)
        self._save_registry()
        return True

    # ------------------------------------------------------------------ #
    # Process lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def start(self, plugin_id: str) -> None:
        """Spawn the sidecar process. Idempotent: no-op if already running."""
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            if not plugin:
                raise KeyError(f"Plugin not registered: {plugin_id}")

            if plugin_id in self._processes:
                proc = self._processes[plugin_id]
                if proc.poll() is None:
                    logger.debug("Plugin %s already running (pid=%d)",
                                 plugin_id, proc.pid)
                    return
                # Process is dead — fall through to respawn
                self._processes.pop(plugin_id, None)

            if plugin.executable is None or plugin.install_dir is None:
                # Dev mode: externally launched. Don't spawn.
                logger.info(
                    "Plugin %s has no executable — treating as dev sidecar",
                    plugin_id,
                )
                plugin.is_dev_mode = True
                return

            exe_path = Path(plugin.install_dir) / plugin.executable
            if not exe_path.exists():
                raise FileNotFoundError(
                    f"Plugin executable not found: {exe_path}"
                )

            log_dir = self.registry_path.parent / plugin_id / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "sidecar.log"

            creationflags = 0
            if sys.platform == "win32":
                # CREATE_NO_WINDOW = 0x08000000 — suppresses console flash
                creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

            args = [str(exe_path), "serve", "--port", str(plugin.port),
                    "--host", plugin.host]

            log_fp = open(log_path, "ab", buffering=0)
            try:
                proc = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    cwd=plugin.install_dir,
                    creationflags=creationflags,
                )
            except OSError:
                log_fp.close()
                raise

            self._processes[plugin_id] = proc
            plugin.is_dev_mode = False
            logger.info("Started plugin %s (pid=%d) -> %s",
                        plugin_id, proc.pid, log_path)

    def stop(self, plugin_id: str, graceful: bool = True) -> None:
        """Stop the sidecar. SIGTERM, wait, then SIGKILL."""
        with self._lock:
            proc = self._processes.pop(plugin_id, None)
            plugin = self._plugins.get(plugin_id)

        if not proc:
            if plugin:
                plugin.is_running = False
            return

        if proc.poll() is not None:
            # Already dead
            if plugin:
                plugin.is_running = False
            return

        try:
            if graceful:
                proc.terminate()
                try:
                    proc.wait(timeout=GRACEFUL_STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Plugin %s did not exit within %ds — killing",
                        plugin_id,
                        GRACEFUL_STOP_TIMEOUT_S,
                    )
                    proc.kill()
                    proc.wait(timeout=2)
            else:
                proc.kill()
                proc.wait(timeout=2)
        except Exception as e:
            logger.error("Error stopping plugin %s: %s", plugin_id, e)

        if plugin:
            plugin.is_running = False

    def restart(self, plugin_id: str) -> None:
        self.stop(plugin_id, graceful=True)
        self.start(plugin_id)

    # ------------------------------------------------------------------ #
    # Health checks                                                       #
    # ------------------------------------------------------------------ #

    async def health(self, plugin_id: str) -> PluginHealth:
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            proc = self._processes.get(plugin_id)
        if not plugin:
            return PluginHealth(
                plugin_id=plugin_id,
                running=False,
                healthy=False,
                last_check=_utc_now(),
                error="not_registered",
            )

        # Process-alive check (managed sidecars only)
        process_alive = True
        if proc is not None:
            process_alive = proc.poll() is None

        url = f"http://{plugin.host}:{plugin.port}/api/video/health"
        # NOTE: the health endpoint comes from the plugin manifest in
        # later phases; for now it's hardcoded to the Phase-0 contract.
        # When we generalize, this will read plugin.health_endpoint.

        async with httpx.AsyncClient(timeout=httpx.Timeout(HEALTH_TIMEOUT_S)) as client:
            try:
                resp = await client.get(url)
                healthy = resp.status_code == 200
                detail = None
                if healthy:
                    try:
                        detail = resp.json()
                    except Exception:
                        detail = {"raw": resp.text[:200]}
                return PluginHealth(
                    plugin_id=plugin_id,
                    running=process_alive,
                    healthy=healthy,
                    last_check=_utc_now(),
                    detail=detail,
                    error=None if healthy else f"http_{resp.status_code}",
                )
            except httpx.ConnectError:
                return PluginHealth(
                    plugin_id=plugin_id,
                    running=process_alive,
                    healthy=False,
                    last_check=_utc_now(),
                    error="connect_refused",
                )
            except httpx.TimeoutException:
                return PluginHealth(
                    plugin_id=plugin_id,
                    running=process_alive,
                    healthy=False,
                    last_check=_utc_now(),
                    error="timeout",
                )
            except Exception as e:
                return PluginHealth(
                    plugin_id=plugin_id,
                    running=process_alive,
                    healthy=False,
                    last_check=_utc_now(),
                    error=str(e)[:200],
                )

    async def health_loop(self) -> None:
        """Background task — polls each plugin every HEALTH_INTERVAL_S."""
        logger.info("Plugin health loop started (interval=%.1fs)", HEALTH_INTERVAL_S)
        try:
            while not self._stop_event.is_set():
                ids = [p.id for p in self.list_installed()]
                for pid in ids:
                    try:
                        h = await self.health(pid)
                        with self._lock:
                            plugin = self._plugins.get(pid)
                            if plugin:
                                plugin.is_running = h.healthy
                                plugin.last_health_check = h.last_check
                    except Exception as e:
                        logger.error("health check error for %s: %s", pid, e)

                # Persist last_health_check periodically
                try:
                    self._save_registry()
                except Exception as e:
                    logger.error("Failed to persist registry: %s", e)

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=HEALTH_INTERVAL_S
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("Plugin health loop cancelled")
            raise

    def stop_health_loop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    # Install / update / uninstall (Phase 0 stubs; real impl in Phase 5)  #
    # ------------------------------------------------------------------ #

    async def install(
        self,
        plugin_id: str,
        variant: str = "auto",
        version: str = "latest",
    ) -> InstalledPlugin:
        """Phase 0: not implemented. Use registry.json hand-edit + dev mode.

        Phase 5 will:
          1. Look up plugin_id in MARKETPLACE
          2. Resolve `version` (latest = newest GitHub release)
          3. Pick a release asset matching `variant` + platform
          4. Download + checksum + extract to install_dir
          5. Write registry entry, then start()
        """
        raise NotImplementedError(
            "Plugin install lands in Phase 5. For Phase 0, hand-edit "
            f"{self.registry_path} or run the sidecar yourself in dev mode."
        )

    async def update(self, plugin_id: str) -> InstalledPlugin:
        """Phase 0: not implemented (see install)."""
        raise NotImplementedError(
            "Plugin update lands in Phase 5."
        )

    def uninstall(
        self, plugin_id: str, keep_user_data: bool = True
    ) -> None:
        """Stop + deregister. Does not delete install_dir in Phase 0
        (no install plumbing yet means there's nothing to delete)."""
        self.stop(plugin_id, graceful=True)
        self.remove(plugin_id)
        # Phase 5: if not keep_user_data and install_dir is real, rmtree it.
        # For Phase 0, registry-only uninstall is enough.

    async def check_updates(
        self, plugin_id: str | None = None
    ) -> dict[str, str | None]:
        """Phase 0: returns {pid: None} for everything. Phase 5 hits GitHub."""
        with self._lock:
            ids = [plugin_id] if plugin_id else list(self._plugins.keys())
        return {pid: None for pid in ids}

    # ------------------------------------------------------------------ #
    # Marketplace                                                         #
    # ------------------------------------------------------------------ #

    def marketplace(self) -> list[dict[str, Any]]:
        """Available plugins, with installed-flag enrichment."""
        with self._lock:
            installed_ids = set(self._plugins.keys())
        return [
            {**entry, "installed": entry["id"] in installed_ids}
            for entry in MARKETPLACE
        ]


# ---------------------------------------------------------------------------
# Module-level singleton accessor (used by routers/video.py)
# ---------------------------------------------------------------------------

_singleton: PluginManager | None = None


def get_manager() -> PluginManager:
    """Lazy-init accessor. main.py replaces this via set_manager()."""
    global _singleton
    if _singleton is None:
        _singleton = PluginManager()
    return _singleton


def set_manager(manager: PluginManager) -> None:
    global _singleton
    _singleton = manager


def get_plugin(plugin_id: str) -> InstalledPlugin | None:
    """Convenience for routers/video.py — handoff doc names this exact
    helper."""
    return get_manager().get(plugin_id)
