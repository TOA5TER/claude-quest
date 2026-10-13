# tests/test_waiting_marker.py
import importlib.util
import os
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


@pytest.fixture
def hub_client(marker_home):
    """Load a fresh attention_hub_client with HOME redirected to tmp_path."""
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_marker_set_check_clear_roundtrip(hub_client, marker_home):
    # Why: the marker is the entire debounce mechanism — set must create the file,
    # check must see it, and clear must remove it and report that it existed.
    assert hub_client.set_waiting_marker("session-abc") is True
    assert (marker_home / "session-abc").is_file()
    assert hub_client.has_waiting_marker("session-abc") is True
    assert hub_client.clear_waiting_marker("session-abc") is True
    assert not (marker_home / "session-abc").exists()
    assert hub_client.has_waiting_marker("session-abc") is False


def test_clear_missing_marker_returns_false(hub_client, marker_home):
    # Why: PostToolUse gates its only network call on clear's return value — a false
    # positive for a missing marker would defeat the zero-network fast path.
    assert hub_client.clear_waiting_marker("never-set") is False


def test_helpers_reject_hostile_session_ids(hub_client, marker_home):
    # Why: the session ID becomes a filename; path separators or traversal must not
    # let a malicious hook input write or delete files outside the marker dir.
    for hostile in ("../escape", "a/b", "a\\b", "/etc/passwd", "..", ".", ""):
        assert hub_client.set_waiting_marker(hostile) is False
        assert hub_client.has_waiting_marker(hostile) is False
        assert hub_client.clear_waiting_marker(hostile) is False
    assert not (marker_home.parent.parent.parent / "escape").exists()


def test_helpers_tolerate_unwritable_marker_dir(hub_client, marker_home):
    # Why: hooks must never raise — a read-only ~/.claude must degrade to "no
    # marker" silently instead of erroring the Claude session.
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    marker_home.mkdir(parents=True)
    marker_home.chmod(0o500)
    try:
        assert hub_client.set_waiting_marker("session-abc") is False
        assert hub_client.has_waiting_marker("session-abc") is False
        assert hub_client.clear_waiting_marker("session-abc") is False
    finally:
        marker_home.chmod(0o700)


def test_helpers_tolerate_none_session_id(hub_client, marker_home):
    # Why: hook inputs are untrusted JSON — a null session_id must behave as "no
    # marker", never raise.
    assert hub_client.set_waiting_marker(None) is False
    assert hub_client.has_waiting_marker(None) is False
    assert hub_client.clear_waiting_marker(None) is False
