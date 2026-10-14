"""
Tests for attention_hub_notification.py — attention-hub's own Notification
hook. Reports `waiting` directly to the hub; no macOS/Slack (that stays in
the notifications plugin).
"""

import importlib.util
import json
import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def run_hook_capture_hub(hook_input):
    """Run attention_hub_notification.py, return list of (url, method, body) hub requests."""
    spec = importlib.util.spec_from_file_location(
        "attention_hub_notification", HOOKS_DIR / "attention_hub_notification.py"
    )
    hub_requests = []

    def capture_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        hub_requests.append((req.full_url, req.get_method(), body))
        return MagicMock(status=200)

    with patch("sys.stdin", StringIO(json.dumps(hook_input))), \
         patch("urllib.request.urlopen", side_effect=capture_urlopen):
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit:
            pass

    return hub_requests


def hub_events(hub_requests):
    return [body for url, method, body in hub_requests
            if url.endswith("/api/events") and method == "POST"]


def test_actionable_notification_reports_waiting(base_hook_input, transcript_without_ask):
    # Why: a permission prompt means Claude is blocked on the user; attention-hub
    # must report "waiting" on its own, with no other plugin installed.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input,
        "notification_type": "permission_prompt",
        "transcript_path": transcript_without_ask,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "waiting"
    assert events[0]["session_id"] == "test-session-123"


def test_non_actionable_notification_reports_nothing(base_hook_input, transcript_without_ask):
    # Why: non-actionable types (auth_success etc.) must not pollute the dashboard
    # with phantom "waiting" rows.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input,
        "notification_type": "auth_success",
        "transcript_path": transcript_without_ask,
    }))
    assert events == []


def test_actionable_notification_sets_waiting_marker(base_hook_input, transcript_without_ask, marker_home):
    # Why: the marker is what lets attention-hub's own PostToolUse hook later
    # report "working" once the block clears — this hook must set it directly.
    run_hook_capture_hub({
        **base_hook_input,
        "notification_type": "permission_prompt",
        "transcript_path": transcript_without_ask,
    })
    assert (marker_home / "test-session-123").is_file()


def test_non_actionable_notification_sets_no_marker(base_hook_input, transcript_without_ask, marker_home):
    # Why: a marker for a non-blocked session would make the next tool call send
    # a spurious "working" report.
    run_hook_capture_hub({
        **base_hook_input,
        "notification_type": "auth_success",
        "transcript_path": transcript_without_ask,
    })
    assert not (marker_home / "test-session-123").exists()


def test_notification_without_session_id_reports_nothing(base_hook_input, transcript_without_ask):
    # Why: without a session ID the hub cannot key the row; the hook must stay
    # silent and exit 0 rather than send a garbage event.
    hook_input = {**base_hook_input, "notification_type": "permission_prompt",
                  "transcript_path": transcript_without_ask}
    hook_input["session_id"] = ""
    events = hub_events(run_hook_capture_hub(hook_input))
    assert events == []


def test_notification_reports_session_name(base_hook_input, transcript_without_ask, tmp_path):
    # Why: blocked (red) rows are triaged first; the waiting event must carry
    # the session's display name like every other hub event.
    base = Path(transcript_without_ask).read_text(encoding="utf-8")
    named = tmp_path / "named_transcript.jsonl"
    named.write_text(base + json.dumps({"type": "custom-title", "customTitle": "tester",
                                         "sessionId": "test-session-123"}) + "\n")
    events = hub_events(run_hook_capture_hub({
        **base_hook_input,
        "notification_type": "permission_prompt",
        "transcript_path": str(named),
    }))
    assert len(events) == 1
    assert events[0]["session_name"] == "tester"


def test_notification_exits_zero_when_hub_down(base_hook_input, transcript_without_ask, monkeypatch):
    # Why: graceful degradation — a dead hub must never error the hook.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    spec = importlib.util.spec_from_file_location(
        "attention_hub_notification", HOOKS_DIR / "attention_hub_notification.py"
    )
    with patch("sys.stdin", StringIO(json.dumps({
        **base_hook_input, "notification_type": "permission_prompt",
        "transcript_path": transcript_without_ask,
    }))):
        mod = importlib.util.module_from_spec(spec)
        exit_code = 0
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit as e:
            exit_code = e.code or 0
    assert exit_code == 0


def test_idle_prompt_with_work_in_flight_reports_nothing(
        base_hook_input, transcript_without_ask, marker_home):
    # Why: live capture on Claude Code 2.1.277 showed idle_prompt firing about a
    # minute after a Stop that listed a running shell; reporting waiting there
    # turned a healthy working card red.
    inflight = marker_home.parent / "inflight-markers"
    inflight.mkdir(parents=True)
    (inflight / "test-session-123").touch()
    requests = run_hook_capture_hub({
        **base_hook_input, "notification_type": "idle_prompt",
        "transcript_path": transcript_without_ask,
    })
    assert hub_events(requests) == []
    assert not (marker_home / "test-session-123").exists()


def test_idle_prompt_without_work_in_flight_reports_waiting(
        base_hook_input, transcript_without_ask, marker_home):
    # Why: with nothing in flight an idle prompt is a real wait and must still show.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "notification_type": "idle_prompt",
        "transcript_path": transcript_without_ask,
    }))
    assert [e["state"] for e in events] == ["waiting"]
    assert (marker_home / "test-session-123").is_file()


@pytest.mark.parametrize("notification_type", ["permission_prompt", "elicitation_dialog"])
@pytest.mark.parametrize("in_flight", [True, False])
def test_blocking_prompts_report_waiting_even_with_work_in_flight(
        base_hook_input, transcript_without_ask, marker_home, notification_type, in_flight):
    # Why: a permission or elicitation prompt blocks the user whatever else is
    # running, so only idle_prompt may be suppressed by the in-flight guard.
    if in_flight:
        inflight = marker_home.parent / "inflight-markers"
        inflight.mkdir(parents=True)
        (inflight / "test-session-123").touch()
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "notification_type": notification_type,
        "transcript_path": transcript_without_ask,
    }))
    assert [e["state"] for e in events] == ["waiting"]
