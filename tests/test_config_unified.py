"""Unified config regression tests (Phase 1 of the redesign).

Contract under test: %APPDATA%/Cortex/config.json is the single source
of truth, with precedence env var > config.json > hardcoded default,
for BOTH the Hub backend (config.Settings) and the MCP server
(cortex_mcp.wifi_bridge).
"""
from __future__ import annotations

import json

import pytest

from cortex_mcp import wifi_bridge

# Flat-module import; tests/conftest.py puts hub/backend on sys.path.
from config import Settings


BACKEND_ENV_VARS = [
    "CORTEX_HUB_PI_HOST", "CORTEX_HUB_PI_PORT", "CORTEX_HUB_PI_USERNAME",
    "CORTEX_HUB_PI_PASSWORD", "CORTEX_HUB_LMSTUDIO_URL",
    "CORTEX_HUB_LMSTUDIO_DEFAULT_MODEL", "CORTEX_HUB_HOST",
    "CORTEX_HUB_PORT",
]
MCP_ENV_VARS = [
    "CORTEX_PI_HOST", "CORTEX_PI_PORT", "CORTEX_PI_USERNAME",
    "CORTEX_PI_PASSWORD",
]


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    """Point both Windows and XDG config roots at a temp dir, and clear
    every env var that could shadow config.json values."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in BACKEND_ENV_VARS + MCP_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Keep the real ~/.cortex-wifi.json out of these tests.
    monkeypatch.setattr(
        wifi_bridge, "DISCOVERY_FILE", str(tmp_path / "no-discovery.json"))
    return tmp_path


def write_user_config(home, values):
    cortex_dir = home / "Cortex"
    cortex_dir.mkdir(parents=True, exist_ok=True)
    (cortex_dir / "config.json").write_text(json.dumps(values))


# ── Hub backend (config.Settings) ────────────────────────────────


def test_backend_reads_config_json(config_home):
    write_user_config(config_home, {
        "pi_host": "10.9.9.9",
        "pi_port": 9420,
        "lmstudio_model": "test-model",
        "hub_port": 9999,
    })
    s = Settings()
    assert s.pi_host == "10.9.9.9"
    assert s.pi_port == 9420
    assert s.lmstudio_default_model == "test-model"
    assert s.port == 9999
    # Untouched fields keep their defaults
    assert s.pi_username == "cortex"


def test_backend_env_beats_config_json(config_home, monkeypatch):
    write_user_config(config_home, {"pi_host": "10.9.9.9"})
    monkeypatch.setenv("CORTEX_HUB_PI_HOST", "10.1.1.1")
    assert Settings().pi_host == "10.1.1.1"


def test_backend_defaults_without_config_json(config_home):
    s = Settings()
    assert s.pi_host == "10.0.0.25"
    assert s.port == 8003


def test_backend_survives_corrupt_config_json(config_home):
    write_user_config(config_home, {})
    (config_home / "Cortex" / "config.json").write_text("{not json")
    assert Settings().pi_host == "10.0.0.25"


# ── MCP server (cortex_mcp.wifi_bridge) ──────────────────────────


def test_mcp_reads_config_json(config_home):
    write_user_config(config_home, {
        "pi_host": "10.7.7.7",
        "pi_port": 7420,
        "pi_username": "alice",
        "pi_password": "secret",
    })
    assert wifi_bridge.get_pi_host() == "10.7.7.7"
    assert wifi_bridge.get_pi_port() == 7420
    assert wifi_bridge.get_pi_credentials() == ("alice", "secret")


def test_mcp_env_beats_config_json(config_home, monkeypatch):
    write_user_config(config_home, {"pi_host": "10.7.7.7"})
    monkeypatch.setenv("CORTEX_PI_HOST", "10.2.2.2")
    assert wifi_bridge.get_pi_host() == "10.2.2.2"


def test_mcp_discovery_fallback(config_home):
    discovery = config_home / "discovery.json"
    discovery.write_text(json.dumps({"ip": "10.3.3.3", "port": 3420}))
    wifi_bridge.DISCOVERY_FILE = str(discovery)
    assert wifi_bridge.get_pi_host() == "10.3.3.3"
    assert wifi_bridge.get_pi_port() == 3420


def test_mcp_defaults_when_nothing_configured(config_home):
    assert wifi_bridge.get_pi_host() == "10.0.0.25"
    assert wifi_bridge.get_pi_port() == 8420
    assert wifi_bridge.get_pi_credentials() == ("cortex", "cortex")


def test_mcp_config_json_read_fresh_per_call(config_home):
    """A Settings-UI save must reach the next bridge construction
    without an MCP restart -- the file is read at call time."""
    write_user_config(config_home, {"pi_host": "10.5.5.5"})
    assert wifi_bridge.get_pi_host() == "10.5.5.5"
    write_user_config(config_home, {"pi_host": "10.6.6.6"})
    assert wifi_bridge.get_pi_host() == "10.6.6.6"
