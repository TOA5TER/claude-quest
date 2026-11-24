# tests/test_needs_user_marker.py
import importlib.util
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


@pytest.fixture
def hub_client():
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def marker_dir(isolated_home):
    return isolated_home / ".claude" / "attention-hub" / "needs-user-markers"


def test_check_and_clear_roundtrip(hub_client, marker_dir):
    # Why: the check must see exactly an existing file, and clear must report
    # whether it removed one so callers can tell a wait was asserted.
    marker_dir.mkdir(parents=True)
    (marker_dir / "session-abc").touch()
    assert hub_client.has_needs_user_marker("session-abc") is True
    assert hub_client.clear_needs_user_marker("session-abc") is True
    assert hub_client.has_needs_user_marker("session-abc") is False
    assert hub_client.clear_needs_user_marker("session-abc") is False


def test_hostile_and_missing_ids_are_rejected(hub_client, marker_dir, isolated_home):
    # Why: the session ID becomes a filename; traversal must not touch files
    # outside the marker folder, and a null ID must behave as "no marker".
    outside = isolated_home / ".claude" / "attention-hub" / "escape"
    outside.parent.mkdir(parents=True)
    outside.touch()
    for hostile in ("../escape", "a/b", "a\\b", "/etc/passwd", "..", ".", "", None):
        assert hub_client.has_needs_user_marker(hostile) is False
        assert hub_client.clear_needs_user_marker(hostile) is False
    assert outside.is_file()
    assert not marker_dir.exists()


def test_client_never_sets_the_marker(hub_client):
    # Why: only the orchestrator asserts a user wait; a hook-side setter would let
    # the hub claim waits nobody asked for.
    assert not hasattr(hub_client, "set_needs_user_marker")
