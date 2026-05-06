"""Plugin manager smoke + lifecycle tests.

Phase 0's whole value proposition is "plumbing is solid." These tests
cover the bugs that bite later:
  - registry CRUD round-trips
  - spawn-then-stop releases the process cleanly
  - sidecar dying flips is_running to False within 5 seconds
  - install() is correctly a 501-stub for Phase 0
  - marketplace enriches with installed flag

The lifecycle test uses a real stdlib HTTP server as a fake sidecar
(inline `python -c <code>`) so health checks exercise actual sockets.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

from services.plugin_manager import (
    InstalledPlugin,
    PluginManager,
    REGISTRY_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind to port 0, get a free port, release it. Race-prone but fine
    for a single-test process."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


_FAKE_SIDECAR = textwrap.dedent(
    """
    import json
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[1])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/video/health':
                body = json.dumps({'status': 'ok', 'version': 'fake'}).encode()
                self.send_response(200)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a, **kw):
            pass

    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
    """
).strip()


def _spawn_fake_sidecar(port: int) -> subprocess.Popen:
    """Start the fake sidecar in a subprocess and wait until it's
    accepting connections."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _FAKE_SIDECAR, str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Poll until the server starts accepting connections (≤3s)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            try:
                s.connect(("127.0.0.1", port))
                return proc
            except OSError:
                time.sleep(0.05)
    proc.kill()
    raise RuntimeError(f"Fake sidecar didn't start on port {port}")


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------


def test_registry_round_trip(tmp_path: Path) -> None:
    """upsert -> save -> reload -> matches."""
    registry = tmp_path / "registry.json"
    mgr = PluginManager(registry_path=registry)

    plugin = InstalledPlugin(
        id="cortex-vision",
        version="dev",
        variant="dev",
        host="127.0.0.1",
        port=8004,
        auto_start=False,
    )
    mgr.upsert(plugin)
    assert registry.exists()

    # Reload from disk
    mgr2 = PluginManager(registry_path=registry)
    got = mgr2.get("cortex-vision")
    assert got is not None
    assert got.version == "dev"
    assert got.port == 8004
    assert got.auto_start is False


def test_registry_writes_schema_version(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    mgr = PluginManager(registry_path=registry)
    mgr.upsert(InstalledPlugin(id="x", version="0.1.0", port=9999))

    import json
    data = json.loads(registry.read_text())
    assert data["schema_version"] == REGISTRY_SCHEMA_VERSION
    assert "x" in data["plugins"]


def test_registry_remove(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    mgr = PluginManager(registry_path=registry)
    mgr.upsert(InstalledPlugin(id="x", version="0.1.0", port=9999))
    assert mgr.remove("x") is True
    assert mgr.get("x") is None
    assert mgr.remove("nonexistent") is False


def test_registry_ignores_future_schema(tmp_path: Path) -> None:
    """If the registry was written by a newer cortex-desktop,
    we refuse to load it (avoid corrupting it)."""
    registry = tmp_path / "registry.json"
    registry.write_text(
        '{"schema_version": 99, "plugins": {"x": {"version": "0.1.0", '
        '"port": 9999, "host": "127.0.0.1"}}}'
    )
    mgr = PluginManager(registry_path=registry)
    assert mgr.list_installed() == []


# ---------------------------------------------------------------------------
# Install / marketplace stubs
# ---------------------------------------------------------------------------


def test_install_rejects_unknown_plugin(tmp_path: Path) -> None:
    """install() validates plugin_id against MARKETPLACE before
    touching the network."""
    mgr = PluginManager(registry_path=tmp_path / "registry.json")

    async def _call():
        await mgr.install("not-a-real-plugin")

    with pytest.raises(ValueError, match="not in marketplace"):
        asyncio.run(_call())


def test_install_rejects_non_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release assets are windows zips; refuse to attempt install
    on other platforms with a clear message rather than a confusing
    asset-not-found further down the pipeline."""
    monkeypatch.setattr("services.plugin_manager.sys.platform", "linux")
    mgr = PluginManager(registry_path=tmp_path / "registry.json")

    async def _call():
        await mgr.install("cortex-vision")

    with pytest.raises(RuntimeError, match="Windows only"):
        asyncio.run(_call())


def test_install_end_to_end_with_mocked_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full install flow end-to-end with the network stubbed:
       fetch release -> download zip -> verify sha256 -> extract ->
       upsert -> start.

    Builds a real zip on disk so the extract path is exercised. The
    'executable' is a tiny .py shim wrapped in a .exe-named file —
    we don't actually try to start it; we patch start() to a no-op
    so the test stays hermetic."""
    import services.plugin_manager as pm_mod

    monkeypatch.setattr(pm_mod.sys, "platform", "win32")

    # Build a fake bundle zip with a top-level cortex-vision.exe stub
    bundle_root = tmp_path / "bundle_src"
    bundle_root.mkdir()
    (bundle_root / "cortex-vision.exe").write_bytes(b"fake-exe-stub")
    zip_src = tmp_path / "cortex-vision-0.2.0-windows-cpu.zip"
    with zipfile.ZipFile(zip_src, "w") as zf:
        for f in bundle_root.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(bundle_root))
    expected_sha = hashlib.sha256(zip_src.read_bytes()).hexdigest()

    fake_release = {
        "tag_name": "v0.2.0",
        "assets": [
            {
                "name": "cortex-vision-0.2.0-windows-cpu.zip",
                "size": zip_src.stat().st_size,
                "browser_download_url": "https://example.test/zip",
            },
            {
                "name": "cortex-vision-0.2.0-windows-cpu.zip.sha256",
                "size": 100,
                "browser_download_url": "https://example.test/sha",
            },
        ],
    }

    # Stub the manager's network helpers to return the fake release,
    # download the local zip into the requested dest, and serve the
    # known sha256.
    async def fake_fetch_release(self, repo, version):
        assert repo == "turfptax/cortex-vision"
        assert version == "latest"
        return fake_release

    async def fake_download_to(self, url, dest):
        assert url == "https://example.test/zip"
        shutil.copy(zip_src, dest)

    async def fake_fetch_sha256(self, url):
        assert url == "https://example.test/sha"
        return expected_sha

    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_release", fake_fetch_release)
    monkeypatch.setattr(pm_mod.PluginManager, "_download_to", fake_download_to)
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_sha256", fake_fetch_sha256)
    # No real subprocess spawn during the test.
    monkeypatch.setattr(pm_mod.PluginManager, "start", lambda self, pid: None)

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    plugin = asyncio.run(mgr.install("cortex-vision"))

    assert plugin.id == "cortex-vision"
    assert plugin.version == "0.2.0"
    assert plugin.variant == "cpu"
    assert plugin.executable == "cortex-vision.exe"
    assert plugin.install_dir is not None
    install_path = Path(plugin.install_dir)
    assert (install_path / "cortex-vision.exe").exists()


def test_rename_with_retry_eventually_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a held handle that releases on the 3rd attempt — the
    helper should retry past the transient error and succeed."""
    import services.plugin_manager as pm_mod

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()

    real_rename = Path.rename
    state = {"attempts": 0}

    def flaky_rename(self, target):
        state["attempts"] += 1
        if state["attempts"] < 3:
            raise PermissionError("WinError 5: file in use")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    monkeypatch.setattr(pm_mod, "RENAME_RETRY_DELAY_S", 0.01)

    pm_mod._rename_with_retry(src, dst)

    assert state["attempts"] == 3
    assert dst.exists()
    assert not src.exists()


def test_rename_with_retry_raises_install_locked_after_n_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.plugin_manager as pm_mod

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()

    def always_locked(self, target):
        raise PermissionError("WinError 5: file in use")
    monkeypatch.setattr(Path, "rename", always_locked)
    monkeypatch.setattr(pm_mod, "RENAME_RETRY_DELAY_S", 0.01)

    with pytest.raises(pm_mod.InstallLocked, match="after"):
        pm_mod._rename_with_retry(src, dst)


def test_install_pre_cleans_stale_bak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a previous install crashed mid-rollback and left a .bak dir
    behind, the next install should clean it before reusing the name
    rather than failing with FileExistsError."""
    import services.plugin_manager as pm_mod

    monkeypatch.setattr(pm_mod.sys, "platform", "win32")
    # Plant a leftover .bak from a hypothetical prior failed install
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    leftover_bak = plugins_root / "cortex-vision.bak"
    leftover_bak.mkdir()
    (leftover_bak / "junk.txt").write_text("old")

    # Build the install bundle
    bundle_root = tmp_path / "bundle_src"
    bundle_root.mkdir()
    (bundle_root / "cortex-vision.exe").write_bytes(b"x")
    zip_src = tmp_path / "cortex-vision-0.2.0-windows-cpu.zip"
    with zipfile.ZipFile(zip_src, "w") as zf:
        for f in bundle_root.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(bundle_root))
    expected_sha = hashlib.sha256(zip_src.read_bytes()).hexdigest()

    fake_release = {
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "cortex-vision-0.2.0-windows-cpu.zip",
             "size": zip_src.stat().st_size,
             "browser_download_url": "https://x/zip"},
            {"name": "cortex-vision-0.2.0-windows-cpu.zip.sha256",
             "size": 100,
             "browser_download_url": "https://x/sha"},
        ],
    }

    async def fake_fetch_release(self, repo, version): return fake_release
    async def fake_download_to(self, url, dest): shutil.copy(zip_src, dest)
    async def fake_fetch_sha256(self, url): return expected_sha
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_release", fake_fetch_release)
    monkeypatch.setattr(pm_mod.PluginManager, "_download_to", fake_download_to)
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_sha256", fake_fetch_sha256)
    monkeypatch.setattr(pm_mod.PluginManager, "start", lambda self, pid: None)
    # Don't shell out to PowerShell during the test
    monkeypatch.setattr(pm_mod, "_force_release_install_dir", lambda d, p: 0)

    mgr = pm_mod.PluginManager(registry_path=plugins_root / "registry.json")
    plugin = asyncio.run(mgr.install("cortex-vision"))

    assert plugin.id == "cortex-vision"
    # Stale .bak should be gone, fresh install in place
    assert not leftover_bak.exists()
    assert (plugins_root / "cortex-vision" / "cortex-vision.exe").exists()


def test_uninstall_preserves_registry_when_rmtree_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the install dir can't be released (handle held by some
    process we couldn't kill), uninstall MUST raise InstallLocked
    and leave the registry entry intact. Previously the registry
    flipped to 'uninstalled' while the disk stayed dirty."""
    import services.plugin_manager as pm_mod

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    install_dir = plugins_root / "cortex-vision"
    install_dir.mkdir()
    (install_dir / "stuck.txt").write_text("locked")

    mgr = pm_mod.PluginManager(registry_path=plugins_root / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="0.2.0", port=8004,
        install_dir=str(install_dir),
        executable="cortex-vision.exe",
    ))

    # Force _rmtree_with_retry to fail, simulating a held handle that
    # the kill+retry loop couldn't release.
    def always_locked(path):
        raise pm_mod.InstallLocked(f"locked: {path}")
    monkeypatch.setattr(pm_mod, "_rmtree_with_retry", always_locked)
    monkeypatch.setattr(pm_mod, "_force_release_install_dir", lambda d, p: 0)

    with pytest.raises(pm_mod.InstallLocked):
        mgr.uninstall("cortex-vision", keep_user_data=False)

    # Registry MUST still have the entry — that's the whole point
    assert mgr.get("cortex-vision") is not None


def test_maybe_respawn_starts_dead_managed_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a sidecar dies and restart_on_crash=True, the next health
    tick triggers a restart."""
    import services.plugin_manager as pm_mod

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="0.2.0",
        install_dir=str(tmp_path), executable="cortex-vision.exe",
        port=8004, restart_on_crash=True,
    ))

    start_calls: list[str] = []
    monkeypatch.setattr(
        pm_mod.PluginManager, "start",
        lambda self, pid: start_calls.append(pid),
    )

    mgr._maybe_respawn("cortex-vision")
    assert start_calls == ["cortex-vision"]
    assert mgr._crash_attempts["cortex-vision"] == 1


def test_maybe_respawn_skips_dev_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev-mode entries (executable=None) are externally managed —
    we never try to respawn them, even if restart_on_crash is True."""
    import services.plugin_manager as pm_mod

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="dev",
        install_dir=None, executable=None,
        port=8004, restart_on_crash=True,
    ))

    start_calls: list[str] = []
    monkeypatch.setattr(
        pm_mod.PluginManager, "start",
        lambda self, pid: start_calls.append(pid),
    )

    mgr._maybe_respawn("cortex-vision")
    assert start_calls == []


def test_maybe_respawn_caps_at_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After MAX_RESPAWN_ATTEMPTS consecutive failures, give up so a
    permanently-broken bundle doesn't burn CPU on a hot loop."""
    import services.plugin_manager as pm_mod

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="0.2.0",
        install_dir=str(tmp_path), executable="cortex-vision.exe",
        port=8004, restart_on_crash=True,
    ))
    monkeypatch.setattr(pm_mod.PluginManager, "start", lambda self, pid: None)
    # Bypass the backoff timer so the test runs fast
    monkeypatch.setattr(pm_mod, "MIN_RESPAWN_GAP_S", 0.0)

    for _ in range(pm_mod.MAX_RESPAWN_ATTEMPTS + 3):
        mgr._maybe_respawn("cortex-vision")

    # Counter caps at MAX, doesn't keep growing
    assert mgr._crash_attempts["cortex-vision"] == pm_mod.MAX_RESPAWN_ATTEMPTS


def test_check_updates_caches_on_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_updates() must populate latest_available_version and
    last_update_check_at on each plugin so the UI can show "Update
    available" without re-querying GitHub on every render."""
    import services.plugin_manager as pm_mod

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="0.2.0",
        install_dir=str(tmp_path), executable="cortex-vision.exe",
        port=8004,
    ))

    async def fake_release(self, repo, version):
        return {"tag_name": "v0.3.0", "assets": []}
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_release", fake_release)

    result = asyncio.run(mgr.check_updates())
    assert result == {"cortex-vision": "0.3.0"}

    plugin = mgr.get("cortex-vision")
    assert plugin is not None
    assert plugin.latest_available_version == "0.3.0"
    assert plugin.last_update_check_at is not None


def test_check_updates_returns_none_when_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the GitHub latest matches the installed version, the
    map value is None (caller-friendly: bool(None) => no update)
    and the cached field clears so a stale 'update available' badge
    from a previous check doesn't linger after the user updated."""
    import services.plugin_manager as pm_mod

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(pm_mod.InstalledPlugin(
        id="cortex-vision", version="0.3.0",
        install_dir=str(tmp_path), executable="cortex-vision.exe",
        port=8004,
        latest_available_version="0.3.0",  # stale from before
    ))

    async def fake_release(self, repo, version):
        return {"tag_name": "v0.3.0", "assets": []}
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_release", fake_release)

    result = asyncio.run(mgr.check_updates())
    assert result == {"cortex-vision": None}

    plugin = mgr.get("cortex-vision")
    assert plugin is not None
    assert plugin.latest_available_version is None


def test_uninstall_keeps_user_data_skips_rmtree(
    tmp_path: Path,
) -> None:
    """The default keep_user_data=True path skips the rmtree entirely
    so a locked install dir doesn't block deregistration when the
    user just wants to forget the plugin (registry-only uninstall)."""
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    install_dir = plugins_root / "cortex-vision"
    install_dir.mkdir()
    (install_dir / "data.txt").write_text("kept")

    mgr = PluginManager(registry_path=plugins_root / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision", version="0.2.0", port=8004,
        install_dir=str(install_dir),
        executable="cortex-vision.exe",
    ))

    mgr.uninstall("cortex-vision", keep_user_data=True)

    assert mgr.get("cortex-vision") is None  # registry cleaned
    assert install_dir.exists()                # disk preserved


def test_install_rejects_bad_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SHA256 mismatch must abort the install before extract +
    spawn — protects against MITM / corrupted downloads."""
    import services.plugin_manager as pm_mod

    monkeypatch.setattr(pm_mod.sys, "platform", "win32")

    zip_src = tmp_path / "cortex-vision-0.2.0-windows-cpu.zip"
    with zipfile.ZipFile(zip_src, "w") as zf:
        zf.writestr("cortex-vision.exe", b"x")

    fake_release = {
        "tag_name": "v0.2.0",
        "assets": [
            {
                "name": "cortex-vision-0.2.0-windows-cpu.zip",
                "size": zip_src.stat().st_size,
                "browser_download_url": "https://example.test/zip",
            },
            {
                "name": "cortex-vision-0.2.0-windows-cpu.zip.sha256",
                "size": 100,
                "browser_download_url": "https://example.test/sha",
            },
        ],
    }

    async def fake_fetch_release(self, repo, version):
        return fake_release

    async def fake_download_to(self, url, dest):
        shutil.copy(zip_src, dest)

    async def fake_fetch_sha256(self, url):
        return "0" * 64  # wrong

    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_release", fake_fetch_release)
    monkeypatch.setattr(pm_mod.PluginManager, "_download_to", fake_download_to)
    monkeypatch.setattr(pm_mod.PluginManager, "_fetch_sha256", fake_fetch_sha256)
    monkeypatch.setattr(pm_mod.PluginManager, "start", lambda self, pid: None)

    mgr = pm_mod.PluginManager(registry_path=tmp_path / "registry.json")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        asyncio.run(mgr.install("cortex-vision"))

    # Registry must NOT have been updated
    assert mgr.get("cortex-vision") is None


def test_marketplace_flags_installed(tmp_path: Path) -> None:
    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    before = mgr.marketplace()
    assert any(p["id"] == "cortex-vision" for p in before)
    assert all(p["installed"] is False for p in before)
    # default_port is exposed so the frontend can render the
    # "Register dev sidecar" hint without an extra round-trip.
    cv_before = next(p for p in before if p["id"] == "cortex-vision")
    assert cv_before["default_port"] == 8004

    mgr.upsert(InstalledPlugin(
        id="cortex-vision", version="dev", port=8004,
    ))
    after = mgr.marketplace()
    cv = next(p for p in after if p["id"] == "cortex-vision")
    assert cv["installed"] is True


def test_marketplace_helpers() -> None:
    """is_marketplace_id and marketplace_default_port are used by the
    dev-register endpoint to validate input."""
    from services.plugin_manager import (
        is_marketplace_id,
        marketplace_default_port,
    )
    assert is_marketplace_id("cortex-vision") is True
    assert is_marketplace_id("not-a-real-plugin") is False
    assert marketplace_default_port("cortex-vision") == 8004
    assert marketplace_default_port("not-a-real-plugin") is None


def test_uninstall_removes_entry(tmp_path: Path) -> None:
    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision", version="dev", port=8004,
    ))
    mgr.uninstall("cortex-vision")
    assert mgr.get("cortex-vision") is None


# ---------------------------------------------------------------------------
# Lifecycle: spawn / stop / dies-flips-red
# ---------------------------------------------------------------------------


def test_health_detects_live_sidecar(tmp_path: Path) -> None:
    """A fake sidecar serving /api/video/health is reported healthy."""
    port = _free_port()
    proc = _spawn_fake_sidecar(port)
    try:
        mgr = PluginManager(registry_path=tmp_path / "registry.json")
        mgr.upsert(InstalledPlugin(
            id="cortex-vision",
            version="fake",
            port=port,
            host="127.0.0.1",
            auto_start=False,
        ))

        h = asyncio.run(mgr.health("cortex-vision"))
        assert h.healthy is True
        assert h.detail is not None
        assert h.detail.get("status") == "ok"
    finally:
        proc.kill()
        proc.wait(timeout=2)


def test_health_reports_unreachable_when_no_sidecar(
    tmp_path: Path,
) -> None:
    port = _free_port()  # not actually bound
    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision",
        version="fake",
        port=port,
        host="127.0.0.1",
        auto_start=False,
    ))

    h = asyncio.run(mgr.health("cortex-vision"))
    assert h.healthy is False
    assert h.error in {"connect_refused", "timeout"}


def test_sidecar_dies_flips_red_within_5s(tmp_path: Path) -> None:
    """The lifecycle test Tory called out specifically.

    Setup: fake sidecar running, plugin marked is_running=True.
    Action: kill sidecar.
    Assert: is_running flips to False within 5 seconds when the
            health loop polls.
    """
    port = _free_port()
    proc = _spawn_fake_sidecar(port)

    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision",
        version="fake",
        port=port,
        host="127.0.0.1",
        auto_start=False,
    ))

    async def _scenario() -> None:
        # Confirm healthy first
        h = await mgr.health("cortex-vision")
        assert h.healthy is True

        # Kill the sidecar
        proc.kill()
        proc.wait(timeout=2)

        # Run the health loop briefly; it should observe the death
        # and flip is_running to False.
        health_task = asyncio.create_task(mgr.health_loop())
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                plugin = mgr.get("cortex-vision")
                if plugin is not None and plugin.is_running is False:
                    return  # success
                await asyncio.sleep(0.2)
            pytest.fail(
                f"is_running did not flip to False within 5s; "
                f"plugin={mgr.get('cortex-vision')}"
            )
        finally:
            mgr.stop_health_loop()
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

    asyncio.run(_scenario())


def test_stop_kills_managed_subprocess(tmp_path: Path) -> None:
    """stop() with graceful=False reaps an injected subprocess."""
    port = _free_port()
    proc = _spawn_fake_sidecar(port)

    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision", version="fake",
        port=port, host="127.0.0.1", auto_start=False,
    ))
    # Inject the externally-spawned process so stop() owns it
    mgr._processes["cortex-vision"] = proc

    mgr.stop("cortex-vision", graceful=False)
    assert proc.poll() is not None  # exited
    assert "cortex-vision" not in mgr._processes


def test_start_raises_when_executable_missing(tmp_path: Path) -> None:
    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision",
        version="0.1.0",
        port=8004,
        install_dir=str(tmp_path / "does-not-exist"),
        executable="cortex-vision.exe",
    ))
    with pytest.raises(FileNotFoundError):
        mgr.start("cortex-vision")


def test_start_skips_spawn_in_dev_mode(tmp_path: Path) -> None:
    """When executable is None, start() is a no-op (dev mode)."""
    mgr = PluginManager(registry_path=tmp_path / "registry.json")
    mgr.upsert(InstalledPlugin(
        id="cortex-vision", version="dev",
        port=8004, executable=None, install_dir=None,
        auto_start=False,
    ))
    mgr.start("cortex-vision")  # must not raise
    plugin = mgr.get("cortex-vision")
    assert plugin is not None
    assert plugin.is_dev_mode is True
    assert "cortex-vision" not in mgr._processes
