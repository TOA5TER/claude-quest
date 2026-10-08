# tests/test_hook_state_reporting.py
import importlib.util
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def run_hook_capture_hub(script_name, hook_input):
    """Run a hook script, return list of (url, method, body) hub requests."""
    spec = importlib.util.spec_from_file_location(
        script_name.replace(".py", ""), HOOKS_DIR / script_name
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


# --- UserPromptSubmit hook -> working ---

def test_user_prompt_submit_reports_working(base_hook_input):
    # Why: the user answering a session is the "addressed" signal — it must flip
    # the row green automatically with no manual bookkeeping.
    events = hub_events(run_hook_capture_hub("attention_hub_user_prompt_submit.py", {
        **base_hook_input,
        "prompt": "please continue",
    }))
    assert len(events) == 1
    assert events[0]["state"] == "working"
    assert events[0]["session_id"] == "test-session-123"


def test_user_prompt_submit_without_session_id_reports_nothing(base_hook_input):
    # Why: without a session ID the hub cannot key the row; sending would create
    # a garbage entry. The hook must stay silent and exit 0.
    hook_input = {**base_hook_input, "prompt": "hi"}
    hook_input["session_id"] = ""
    events = hub_events(run_hook_capture_hub("attention_hub_user_prompt_submit.py", hook_input))
    assert events == []


def named_transcript(tmp_path, base_lines_path):
    """Copy a fixture transcript and append a /rename custom-title record."""
    base = Path(base_lines_path).read_text(encoding="utf-8")
    path = tmp_path / "named_transcript.jsonl"
    path.write_text(
        base + json.dumps({"type": "custom-title", "customTitle": "tester",
                           "sessionId": "test-session-123"}) + "\n",
        encoding="utf-8",
    )
    return str(path)


def test_user_prompt_submit_reports_session_name(base_hook_input, transcript_without_ask, tmp_path):
    # Why: UserPromptSubmit fires on every prompt, so it is the event that picks up
    # a fresh /rename fastest; it must carry the name, not just flip state to green.
    events = hub_events(run_hook_capture_hub("attention_hub_user_prompt_submit.py", {
        **base_hook_input,
        "prompt": "please continue",
        "transcript_path": named_transcript(tmp_path, transcript_without_ask),
    }))
    assert len(events) == 1
    assert events[0]["session_name"] == "tester"


# --- Waiting marker lifecycle across hooks ---

def set_marker(marker_home, session_id="test-session-123"):
    """Drop a waiting marker for a session, as the Notification hook would."""
    marker_home.mkdir(parents=True, exist_ok=True)
    (marker_home / session_id).touch()


def test_post_tool_use_with_marker_reports_working_and_clears(base_hook_input, marker_home):
    # Why: a tool can only complete after a pending permission was granted, so the
    # hub must flip to "working" exactly once and the marker must be consumed.
    set_marker(marker_home)
    events = hub_events(run_hook_capture_hub("attention_hub_post_tool_use.py", {
        **base_hook_input,
        "tool_name": "Bash",
    }))
    assert len(events) == 1
    assert events[0]["state"] == "working"
    assert events[0]["session_id"] == "test-session-123"
    assert not (marker_home / "test-session-123").exists()


def test_post_tool_use_without_marker_reports_nothing(base_hook_input, marker_home):
    # Why: PostToolUse fires after every tool call forever; without a marker it must
    # make zero network calls or an unreachable hub adds 2s latency to every tool.
    events = hub_events(run_hook_capture_hub("attention_hub_post_tool_use.py", {
        **base_hook_input,
        "tool_name": "Bash",
    }))
    assert events == []


def test_post_tool_use_without_session_id_reports_nothing(base_hook_input, marker_home):
    # Why: without a session ID there is no marker to key and no hub row to update;
    # the hook must stay silent and exit 0 rather than send a garbage event.
    set_marker(marker_home)
    hook_input = {**base_hook_input, "tool_name": "Bash"}
    hook_input["session_id"] = ""
    events = hub_events(run_hook_capture_hub("attention_hub_post_tool_use.py", hook_input))
    assert events == []
    assert (marker_home / "test-session-123").is_file()


def test_user_prompt_submit_clears_marker(base_hook_input, marker_home):
    # Why: a new prompt is an authoritative "user is engaged" signal — a leftover
    # marker would make the next tool call send a redundant "working" report.
    set_marker(marker_home)
    run_hook_capture_hub("attention_hub_user_prompt_submit.py", {
        **base_hook_input,
        "prompt": "please continue",
    })
    assert not (marker_home / "test-session-123").exists()


def test_session_end_clears_marker(base_hook_input, marker_home):
    # Why: ended sessions must not leak marker files into ~/.claude — and a recycled
    # session ID must never inherit a stale waiting state.
    set_marker(marker_home)
    run_hook_capture_hub("attention_hub_session_end.py", {
        **base_hook_input,
        "reason": "exit",
    })
    assert not (marker_home / "test-session-123").exists()


def test_post_tool_use_exits_zero_when_hub_down(base_hook_input, marker_home, monkeypatch):
    # Why: graceful degradation — a dead hub must never error the hook, and the
    # marker must be cleared FIRST so the 2s timeout is paid once, not per tool call.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    set_marker(marker_home)
    spec = importlib.util.spec_from_file_location(
        "attention_hub_post_tool_use", HOOKS_DIR / "attention_hub_post_tool_use.py"
    )
    with patch("sys.stdin", StringIO(json.dumps({**base_hook_input, "tool_name": "Bash"}))):
        mod = importlib.util.module_from_spec(spec)
        exit_code = 0
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit as e:
            exit_code = e.code or 0
    assert exit_code == 0, "post_tool_use must exit 0 when hub is unreachable"
    assert not (marker_home / "test-session-123").exists(), \
        "marker must be cleared even when the hub is down (bounds retries to one)"


# --- SessionEnd hook -> removal ---

def test_session_end_removes_session(base_hook_input):
    # Why: ended sessions must leave the dashboard, otherwise rows accumulate and
    # the "which instance needs me" answer drowns in dead entries.
    hub_requests = run_hook_capture_hub("attention_hub_session_end.py", {
        **base_hook_input,
        "reason": "exit",
    })
    deletes = [(url, method) for url, method, body in hub_requests if method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0][0].endswith("/api/sessions/test-session-123")


def set_active_subagent_marker(active_subagent_home, session_id="test-session-123", name="marker-1"):
    """Drop an active-subagent marker for a session, as PreToolUse(Task) would."""
    marker_dir = active_subagent_home / session_id
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / name).touch()


def test_subagent_stop_clears_exactly_one_active_marker(base_hook_input, active_subagent_home):
    # Why: SubagentStop must flip exactly one marker to completed per
    # completed subagent. Deliberately updated for this story: the old
    # assertion (file count drops to 1) tested "the file gets deleted",
    # which this story intentionally changes for task-kind markers --
    # both files remain, one flipped to completed, one still active.
    set_active_subagent_marker(active_subagent_home, name="marker-1")
    set_active_subagent_marker(active_subagent_home, name="marker-2")
    run_hook_capture_hub("attention_hub_subagent_stop.py", {**base_hook_input})

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py"
    )
    hub_client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub_client)

    marker_dir = active_subagent_home / "test-session-123"
    remaining = list(marker_dir.iterdir())
    assert len(remaining) == 2
    records = [hub_client._read_marker(m) for m in remaining]
    completed = [r for r in records if r["status"] == "completed"]
    active = [r for r in records if r["status"] == "active"]
    assert len(completed) == 1
    assert len(active) == 1


def test_session_end_clears_all_active_subagent_markers(base_hook_input, active_subagent_home):
    # Why: parallels the existing waiting-marker SessionEnd assertion — ending a
    # session must clear every one of its active-subagent markers, not just one.
    set_active_subagent_marker(active_subagent_home, name="marker-1")
    set_active_subagent_marker(active_subagent_home, name="marker-2")
    run_hook_capture_hub("attention_hub_session_end.py", {
        **base_hook_input,
        "reason": "exit",
    })
    assert not (active_subagent_home / "test-session-123").exists()


def test_hooks_exit_zero_when_hub_down(base_hook_input, monkeypatch):
    # Why: the graceful-degradation acceptance criterion — a dead hub must never
    # error any hook. Uses a real connection-refused, not a mock.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    for script, extra in [
        ("attention_hub_user_prompt_submit.py", {"prompt": "hi"}),
        ("attention_hub_session_end.py", {"reason": "exit"}),
    ]:
        spec = importlib.util.spec_from_file_location(
            script.replace(".py", ""), HOOKS_DIR / script
        )
        with patch("sys.stdin", StringIO(json.dumps({**base_hook_input, **extra}))):
            mod = importlib.util.module_from_spec(spec)
            exit_code = 0
            try:
                spec.loader.exec_module(mod)
                mod.main()
            except SystemExit as e:
                exit_code = e.code or 0
            assert exit_code == 0, f"{script} must exit 0 when hub is unreachable"


def test_subagent_stop_completes_oldest_marker_under_concurrency(base_hook_input, active_subagent_home):
    # Why: SubagentStop's payload has no correlating ID (see spec's
    # marker-attribution decision) -- verify the deterministic FIFO
    # selection, not an arbitrary/undefined pick, when 2 task markers are
    # concurrently active.
    import time as _time
    marker_dir = active_subagent_home / "test-session-123"
    marker_dir.mkdir(parents=True, exist_ok=True)
    older = marker_dir / "marker-older"
    older.write_text('{"id": "marker-older", "kind": "task", "label": "first", '
                      '"status": "active", "started_at": %r}' % (_time.time() - 10))
    import os as _os
    _os.utime(older, (_time.time() - 10, _time.time() - 10))
    newer = marker_dir / "marker-newer"
    newer.write_text('{"id": "marker-newer", "kind": "task", "label": "second", '
                      '"status": "active", "started_at": %r}' % _time.time())

    run_hook_capture_hub("attention_hub_subagent_stop.py", {**base_hook_input})

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py"
    )
    hub_client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub_client)
    assert hub_client._read_marker(older)["status"] == "completed"
    assert hub_client._read_marker(newer)["status"] == "active"


# --- Subagent-originated events must never reach the hub ---

SUBAGENT_MARKERS = [
    {"agent_id": "agent-abc123", "agent_type": "Explore"},
    {"agent_id": "agent-abc123"},
]

REPORTING_HOOKS = [
    ("attention_hub_user_prompt_submit.py", {"prompt": "hi"}, True),
    ("attention_hub_notification.py", {"notification_type": "permission_prompt",
                                       "message": "needs permission"}, False),
    ("attention_hub_post_tool_use.py", {"tool_name": "Bash"}, True),
    ("attention_hub_stop.py", {}, True),
    ("attention_hub_session_end.py", {}, True),
]

SUBAGENT_FILTERED_HOOKS = [h for h in REPORTING_HOOKS
                           if h[0] != "attention_hub_notification.py"]


@pytest.mark.parametrize("marker", SUBAGENT_MARKERS, ids=["agent_id+agent_type", "agent_id"])
@pytest.mark.parametrize("script,extra,clears_marker", SUBAGENT_FILTERED_HOOKS,
                         ids=[h[0] for h in SUBAGENT_FILTERED_HOOKS])
def test_hooks_ignore_subagent_events(base_hook_input, marker_home, transcript_without_ask,
                                      script, extra, clears_marker, marker):
    # Why: in-process subagents share the parent's session_id, so any event they
    # report overwrites the parent's row (or creates one for a session the hub
    # has not seen) and can clear the parent's pending waiting marker.
    set_marker(marker_home)
    requests = run_hook_capture_hub(script, {
        **base_hook_input, "transcript_path": transcript_without_ask, **extra, **marker,
    })
    assert requests == []
    if clears_marker:
        assert (marker_home / "test-session-123").exists()


@pytest.mark.parametrize("script,extra,clears_marker", REPORTING_HOOKS,
                         ids=[h[0] for h in REPORTING_HOOKS])
def test_agent_session_main_thread_still_reports(base_hook_input, marker_home, transcript_without_ask,
                                                 script, extra, clears_marker):
    # Why: agent_type alone marks the main thread of an `--agent` session (e.g. a
    # dev-workflow role session), which is a real session the user must see.
    set_marker(marker_home)
    requests = run_hook_capture_hub(script, {
        **base_hook_input, "transcript_path": transcript_without_ask,
        **extra, "agent_type": "dev-workflow:reviewer",
    })
    assert requests != []
    if clears_marker:
        assert not (marker_home / "test-session-123").exists()


@pytest.mark.parametrize("marker", SUBAGENT_MARKERS, ids=["agent_id+agent_type", "agent_id"])
def test_subagent_permission_prompt_reports_waiting_for_parent(base_hook_input, marker_home,
                                                               transcript_without_ask, marker):
    # Why: a subagent's permission prompt blocks the parent until the user
    # answers it, so the parent's row must show waiting rather than working.
    events = hub_events(run_hook_capture_hub("attention_hub_notification.py", {
        **base_hook_input, "transcript_path": transcript_without_ask,
        "notification_type": "permission_prompt", "message": "needs permission", **marker,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "waiting"
    assert events[0]["session_id"] == base_hook_input["session_id"]
    assert (marker_home / base_hook_input["session_id"]).exists()


def test_main_thread_events_still_report(base_hook_input):
    # Why: the subagent filter must not swallow the session's own events.
    events = hub_events(run_hook_capture_hub("attention_hub_user_prompt_submit.py", {
        **base_hook_input, "prompt": "hi",
    }))
    assert len(events) == 1
    assert events[0]["state"] == "working"
