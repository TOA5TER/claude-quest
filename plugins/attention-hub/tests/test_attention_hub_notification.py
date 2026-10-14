"""
Tests for attention_hub_notification.py — attention-hub's own Notification
hook. Reports `waiting` directly to the hub; no macOS/Slack (that stays in
the notifications plugin).
"""

import importlib.util
import json
import os
import time
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


def age(path, seconds=120):
    """Make a transcript look like it was last written `seconds` ago."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_idle_prompt_with_nothing_pending_reports_done_and_sets_no_marker(
        base_hook_input, transcript_without_ask, marker_home):
    # Why: live v2 run (2.1.299): every done card turned red about a minute after Stop
    # because idle_prompt reported waiting. With nothing in flight and nothing pending
    # the session is idle, so it reports done (this also ends a card an Esc interrupt
    # left at working, since an interrupted turn sends no Stop) and never waiting.
    age(transcript_without_ask)
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "notification_type": "idle_prompt",
        "transcript_path": transcript_without_ask,
    }))
    assert [e["state"] for e in events] == ["done"]
    assert not (marker_home / "test-session-123").exists()


def test_idle_prompt_never_overwrites_a_pending_permission_wait(
        base_hook_input, transcript_without_ask, marker_home):
    # Why: a permission or elicitation wait is real; a later idle_prompt must not
    # replace it with done.
    marker_home.mkdir(parents=True)
    (marker_home / "test-session-123").touch()
    age(transcript_without_ask)
    requests = run_hook_capture_hub({
        **base_hook_input, "notification_type": "idle_prompt",
        "transcript_path": transcript_without_ask,
    })
    assert hub_events(requests) == []
    assert (marker_home / "test-session-123").is_file()


def test_idle_prompt_leaves_a_needs_input_card_alone(
        base_hook_input, transcript_with_ask, marker_home):
    # Why: a Stop after AskUserQuestion reported needs_input; an idle prompt while that
    # question is still open must not turn the card done.
    age(transcript_with_ask)
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "notification_type": "idle_prompt",
        "transcript_path": transcript_with_ask,
    }))
    assert events == []


HOOK_SCRIPTS = {
    "stop": "attention_hub_stop.py",
    "ups": "attention_hub_user_prompt_submit.py",
    "notification": "attention_hub_notification.py",
    "post": "attention_hub_post_tool_use.py",
}


def hook_events(script, hook_input):
    """Run one real hook script against captured stdin; return the hub events it sent."""
    spec = importlib.util.spec_from_file_location(script[:-3], HOOKS_DIR / script)
    requests = []

    def capture(req, timeout=None):
        requests.append(json.loads(req.data.decode("utf-8")) if req.data else None)
        return MagicMock(status=200)

    with patch("sys.stdin", StringIO(json.dumps(hook_input))), \
         patch("urllib.request.urlopen", side_effect=capture):
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit:
            pass
    return [r for r in requests if r and "state" in r]


def replay_through_hub(tmp_path, steps):
    """Feed each real hook's event through the hub store; return the card state after each."""
    spec = importlib.util.spec_from_file_location("attention_hub", HOOKS_DIR.parent / "hub" / "attention_hub.py")
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    store = hub.AttentionStore(str(tmp_path / "state.json"))
    states = []
    for name, hook_input in steps:
        for event in hook_events(HOOK_SCRIPTS[name], hook_input):
            store.upsert(event)
        sessions = store.list_sessions()
        states.append(sessions[0]["state"] if sessions else None)
    return states


def test_replay_done_then_idle_prompt_keeps_the_card_done(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: this is the trace from the live run: done at Stop, then an idle_prompt about
    # a minute later turned the card waiting. It must stay done.
    base = {**base_hook_input, "transcript_path": transcript_without_ask}
    age(transcript_without_ask)
    states = replay_through_hub(tmp_path, [
        ("stop", {**base, "background_tasks": [], "session_crons": []}),
        ("notification", {**base, "notification_type": "idle_prompt"}),
    ])
    assert states == ["done", "done"]


def test_replay_in_flight_work_survives_idle_prompt(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: the earlier in-flight case must still hold: working stays working.
    base = {**base_hook_input, "transcript_path": transcript_without_ask}
    age(transcript_without_ask)
    shell = {"id": "b1", "type": "shell", "status": "running", "description": "sleep"}
    states = replay_through_hub(tmp_path, [
        ("stop", {**base, "background_tasks": [shell], "session_crons": []}),
        ("notification", {**base, "notification_type": "idle_prompt"}),
    ])
    assert states == ["working", "working"]


def test_replay_permission_prompt_still_turns_the_card_waiting(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: only idle_prompt was demoted; a permission prompt after a done Stop must
    # still show as waiting, and a later idle_prompt must not clear it.
    base = {**base_hook_input, "transcript_path": transcript_without_ask}
    age(transcript_without_ask)
    states = replay_through_hub(tmp_path, [
        ("stop", {**base, "background_tasks": [], "session_crons": []}),
        ("notification", {**base, "notification_type": "permission_prompt"}),
        ("notification", {**base, "notification_type": "idle_prompt"}),
    ])
    assert states == ["done", "waiting", "waiting"]


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


def test_replay_late_idle_prompt_after_a_new_prompt_does_not_set_done(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: reviewer replay of the race: Stop done, the user submits a prompt, then a late
    # idle_prompt arrives. The submitted prompt was just written to the transcript, so
    # the idle_prompt must be ignored and the card must stay working through the turn.
    base = {**base_hook_input, "transcript_path": transcript_without_ask}
    states = replay_through_hub(tmp_path, [
        ("stop", {**base, "background_tasks": [], "session_crons": []}),
        ("ups", {**base, "prompt": "next question"}),
        ("notification", {**base, "notification_type": "idle_prompt"}),
    ])
    assert states == ["done", "working", "working"]


def test_replay_idle_prompt_on_a_quiet_transcript_ends_an_interrupted_turn(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: Esc sends no Stop, so the card sits at working; once the transcript has been
    # quiet, the idle_prompt is the only signal and must set done.
    base = {**base_hook_input, "transcript_path": transcript_without_ask}
    age(transcript_without_ask)
    states = replay_through_hub(tmp_path, [
        ("ups", {**base, "prompt": "do something long"}),
        ("notification", {**base, "notification_type": "idle_prompt"}),
    ])
    assert states == ["working", "done"]


def test_transcript_recently_modified_is_bounded_and_safe(tmp_path):
    # Why: the staleness check runs in a hook; it must be false for missing paths and
    # special files without blocking, and honor the window for a regular file.
    spec = importlib.util.spec_from_file_location("c", HOOKS_DIR / "attention_hub_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    regular = tmp_path / "t.jsonl"
    regular.write_text("{}\n")
    assert client.transcript_recently_modified(str(regular)) is True
    age(regular)
    assert client.transcript_recently_modified(str(regular)) is False
    assert client.transcript_recently_modified(str(tmp_path / "missing")) is False
    assert client.transcript_recently_modified(None) is False
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert client.transcript_recently_modified(str(fifo)) is False
