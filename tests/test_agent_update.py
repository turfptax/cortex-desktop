"""The agent updater's version and channel rules.

Mirrors the old Hub's hard-won ordering: numeric tuples (never string
compare), dev.13 > dev.9, and a stable release outranks a dev build of
the same base. Channel rules: stable ignores prereleases and dev-suffixed
tags; dev takes whatever is newest.
"""

from __future__ import annotations

from cortex_agent.update import parse_version, pick_latest


def release(tag: str, prerelease: bool) -> dict:
    return {"tag_name": tag, "prerelease": prerelease, "assets": []}


def test_version_ordering():
    assert parse_version("0.2.0") > parse_version("0.1.9")
    assert parse_version("0.2.0-dev.13") > parse_version("0.2.0-dev.9")
    assert parse_version("0.2.0") > parse_version("0.2.0-dev.99")
    assert parse_version("0.10.0") > parse_version("0.9.9")
    assert parse_version("not-a-version") is None
    assert parse_version("v0.2.0") is None


def test_stable_channel_skips_dev_builds():
    releases = [
        release("agent-v0.2.0-dev.3", True),
        release("agent-v0.1.1", False),
        release("v0.22.0", False),          # the legacy Hub lane, ignored
    ]
    best = pick_latest(releases, "stable")
    assert best["tag_name"] == "agent-v0.1.1"


def test_dev_channel_takes_newest_of_all():
    releases = [
        release("agent-v0.2.0-dev.3", True),
        release("agent-v0.1.1", False),
    ]
    best = pick_latest(releases, "dev")
    assert best["tag_name"] == "agent-v0.2.0-dev.3"


def test_stable_release_beats_same_base_dev_on_dev_channel():
    releases = [
        release("agent-v0.2.0-dev.9", True),
        release("agent-v0.2.0", False),
    ]
    best = pick_latest(releases, "dev")
    assert best["tag_name"] == "agent-v0.2.0"


def test_no_agent_releases_returns_none():
    assert pick_latest([release("v0.22.0", False)], "stable") is None
