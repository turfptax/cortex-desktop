"""Tests for the CP1 watcher (cortex_local/ingester.py).

Ports the wire-contract coverage from tests/test_import_path.py onto
the new push function (the X-Filename assertion is the July 2026
outage regression test) and covers the CP1 cycle rules: state
pre-filter, oversize skip, bootstrap degrade, 429 backoff, and the
step-2 soft-fail poison rule from CORTEX_AGENT_PLAN section 3 item 5.

Transport is stdlib urllib; tests monkeypatch urllib.request.urlopen.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request

import pytest

from cortex_local import ingester


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body


def _http_error(code):
    return urllib.error.HTTPError(
        "https://cloud.example/core/x", code, "err", {}, io.BytesIO(b""))


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Deterministic ingester environment: fake cloud config, tmp
    state + projects dir, zero inter-push delay, routed urlopen."""
    monkeypatch.setattr(ingester, "get_pi_host",
                        lambda: "https://cloud.example/core")
    monkeypatch.setattr(ingester, "get_pi_port", lambda: 8420)
    monkeypatch.setattr(ingester, "get_pi_credentials",
                        lambda: ("cortex", "token"))
    monkeypatch.setattr(ingester, "_load_user_config",
                        lambda: {"ingest_upload_delay_seconds": 0})
    monkeypatch.setattr(ingester, "state_path",
                        lambda: tmp_path / "state.json")
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(ingester, "claude_projects_dir", lambda: projects)
    monkeypatch.delenv("CORTEX_LOCAL_INGEST", raising=False)

    captured = []
    routes = {}

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        for fragment, handler in routes.items():
            if fragment in req.full_url:
                result = handler(req)
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError("unrouted URL: " + req.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def default_routes():
        routes.clear()
        routes["/plugins/overseer/imports/from-path"] = lambda req: _Resp(
            {"ok": True, "imported_id": "claude-code:test",
             "session_id": "test", "message_count": 1})
        routes["/plugins/overseer/imports?"] = lambda req: _Resp(
            {"ok": True, "imports": []})
        routes["/files/uploads"] = lambda req: _Resp(
            {"ok": True, "path": "/app/uploads/{}".format(
                req.get_header("X-filename")),
             "size": len(req.data), "file_id": 1})

    default_routes()

    class Env:
        pass

    e = Env()
    e.captured = captured
    e.routes = routes
    e.projects = projects
    e.tmp = tmp_path
    return e


def _make_session(projects, name, content=b'{"m":"hi"}\n',
                  age_s=3600, folder="C--proj"):
    d = projects / folder
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(content)
    old = time.time() - age_s
    os.utime(p, (old, old))
    return p


# == push_session_file wire contract ==============================


def test_push_sends_x_filename(env):
    """THE regression test: step 1 must carry X-Filename; a proxy that
    strips it breaks every import (the July 2026 26/26 failure)."""
    p = _make_session(env.projects, "abcd-1234.jsonl")
    result = ingester.push_session_file(p)
    assert result["imported_id"] == "claude-code:test"
    upload = env.captured[0]
    assert upload.get_header("X-filename") == "abcd-1234.jsonl"


def test_push_two_step_wiring(env):
    p = _make_session(env.projects, "wire-1.jsonl", b"body-bytes\n")
    ingester.push_session_file(p)
    upload, from_path = env.captured[0], env.captured[1]
    assert upload.full_url.endswith("/core/files/uploads")
    assert upload.get_method() == "POST"
    assert upload.get_header("Content-type") == "application/octet-stream"
    assert upload.get_header("X-tags") == "claude-code,overseer-import"
    assert upload.get_header("Authorization").startswith("Basic ")
    assert upload.data == b"body-bytes\n"
    body = json.loads(from_path.data)
    assert body == {"path": "/app/uploads/wire-1.jsonl",
                    "source": "claude-code"}
    assert from_path.get_header("Authorization").startswith("Basic ")


def test_push_step1_http_error(env):
    env.routes["/files/uploads"] = lambda req: _http_error(400)
    p = _make_session(env.projects, "bad.jsonl")
    with pytest.raises(ingester.PushError) as exc:
        ingester.push_session_file(p)
    assert exc.value.step == 1
    assert exc.value.status == 400


def test_push_skipped_is_success(env):
    env.routes["/plugins/overseer/imports/from-path"] = lambda req: _Resp(
        {"ok": True, "skipped": "already imported (same hash)"})
    p = _make_session(env.projects, "dup.jsonl")
    result = ingester.push_session_file(p)
    assert result["skipped"]


# == dedupe bootstrap =============================================


def test_fetch_server_hashes_pages(env):
    pages = [
        {"ok": True,
         "imports": [{"file_hash": "h{}".format(i)} for i in range(500)]},
        {"ok": True, "imports": [{"file_hash": "last"}, {"file_hash": ""}]},
    ]
    env.routes["/plugins/overseer/imports?"] = lambda req: _Resp(
        pages.pop(0))
    out = ingester.fetch_server_hashes()
    assert len(out) == 501
    assert "last" in out and "" not in out


# == run_cycle ====================================================


def test_cycle_drains_then_idles(env):
    _make_session(env.projects, "old-1.jsonl", b"a\n", age_s=7200)
    _make_session(env.projects, "new-1.jsonl", b"b\n", age_s=3600)
    _make_session(env.projects, "fresh.jsonl", b"c\n", age_s=60)  # not idle

    s1 = ingester.run_cycle()
    assert s1["pushed"] == 2 and s1["failed"] == 0
    # Newest-first ordering of the idle files.
    names = [r.get_header("X-filename") for r in env.captured
             if r.full_url.endswith("/files/uploads")]
    assert names == ["new-1.jsonl", "old-1.jsonl"]

    env.captured.clear()
    s2 = ingester.run_cycle()
    assert s2["pushed"] == 0 and s2["candidates"] == 0
    # Second scan makes no upload calls at all (size/mtime pre-filter).
    assert not any(r.full_url.endswith("/files/uploads")
                   for r in env.captured)


def test_cycle_repushes_grown_session(env):
    p = _make_session(env.projects, "grow-1.jsonl", b"line1\n")
    assert ingester.run_cycle()["pushed"] == 1
    p.write_bytes(b"line1\nline2\n")
    old = time.time() - 3600
    os.utime(p, (old, old))
    env.captured.clear()
    assert ingester.run_cycle()["pushed"] == 1
    names = [r.get_header("X-filename") for r in env.captured
             if r.full_url.endswith("/files/uploads")]
    assert names == ["grow-1.jsonl"]


def test_cycle_oversize_skip(env, monkeypatch):
    monkeypatch.setattr(ingester, "MAX_FILE_BYTES", 4)
    _make_session(env.projects, "big.jsonl", b"12345678\n")
    s = ingester.run_cycle()
    assert s["pushed"] == 0
    assert s["oversize_skipped"] == 1


def test_cycle_bootstrap_seeds_server_hashes(env):
    p = _make_session(env.projects, "known.jsonl", b"known-bytes\n")
    digest = ingester.file_sha256(p)
    env.routes["/plugins/overseer/imports?"] = lambda req: _Resp(
        {"ok": True, "imports": [{"file_hash": digest}]})
    s = ingester.run_cycle()
    # The server already has these bytes: recorded, never uploaded.
    assert s["pushed"] == 0 and s["candidates"] == 0
    assert not any(r.full_url.endswith("/files/uploads")
                   for r in env.captured)
    state = ingester.load_state()
    assert state["sessions"]["known"]["last_pushed_sha256"] == digest


def test_cycle_bootstrap_degrades(env):
    env.routes["/plugins/overseer/imports?"] = lambda req: \
        urllib.error.URLError("down")
    _make_session(env.projects, "x-1.jsonl")
    s = ingester.run_cycle()
    assert s["pushed"] == 1  # proceeded despite bootstrap failure
    assert ingester.load_state()["bootstrap_done_at"] is None  # retried later


def test_cycle_429_stops_without_strike(env):
    env.routes["/files/uploads"] = lambda req: _http_error(429)
    _make_session(env.projects, "throttled.jsonl")
    s = ingester.run_cycle()
    assert s["stopped_early"] is True
    assert s["failed"] == 0
    rec = ingester.load_state()["sessions"].get("throttled")
    assert rec is None or rec.get("attempts", 0) == 0


def test_step2_softfail_strikes_only_on_repeat(env):
    env.routes["/plugins/overseer/imports/from-path"] = \
        lambda req: _http_error(502)
    _make_session(env.projects, "soft-1.jsonl")

    ingester.run_cycle()
    rec = ingester.load_state()["sessions"]["soft-1"]
    assert rec["attempts"] == 0          # soft fail, no strike yet
    assert rec["pending_confirm_hash"]

    ingester.run_cycle()                 # same hash fails again
    rec = ingester.load_state()["sessions"]["soft-1"]
    assert rec["attempts"] == 1          # now it counts


def test_env_kill_switch(env, monkeypatch):
    monkeypatch.setenv("CORTEX_LOCAL_INGEST", "0")
    _make_session(env.projects, "nope.jsonl")
    s = ingester.run_cycle()
    assert s["enabled"] is False
    assert env.captured == []


def test_v1_state_migration(env):
    ingester.state_path().write_text(json.dumps(
        {"pushed_hashes": ["aaa"], "last_scan_at": "x"}), encoding="utf-8")
    state = ingester.load_state()
    assert state["version"] == 2
    assert state["server_hashes_seen"] == ["aaa"]
