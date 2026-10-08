"""
Tests for attention_hub_stop.py — attention-hub's own Stop hook. Computes
needs_input/working(active-subagents)/done and reports directly to the hub;
no macOS/Slack (that stays in the notifications plugin).
"""

import importlib.util
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def run_hook_capture_hub(hook_input):
    spec = importlib.util.spec_from_file_location(
        "attention_hub_stop", HOOKS_DIR / "attention_hub_stop.py"
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


def test_stop_with_ask_reports_needs_input(base_hook_input, transcript_with_ask):
    # Why: a Stop ending in AskUserQuestion must report needs_input, standalone.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_with_ask,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "needs_input"


def test_stop_without_ask_reports_done(base_hook_input, transcript_without_ask):
    # Why: a Stop with no pending question is "task complete, awaiting review".
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "done"


def test_subagent_session_stop_reports_nothing(base_hook_input, transcript_without_ask):
    # Why: a subagent's Stop shares the parent's session_id; reporting it would
    # flip the parent's row to done while the parent is still working.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
        "agent_id": "agent-abc123", "agent_type": "Explore",
    }))
    assert events == []


def test_agent_session_main_thread_stop_reports(base_hook_input, transcript_without_ask):
    # Why: agent_type without agent_id is the main thread of an `--agent` session,
    # whose own Stop must still mark it done.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
        "agent_type": "dev-workflow:reviewer",
    }))
    assert len(events) == 1
    assert events[0]["state"] == "done"


def test_stop_reports_message_snippet(base_hook_input, transcript_without_ask):
    # Why: red/yellow rows must show WHAT the session finished with.
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
    }))
    assert events[0]["message"], "Stop event must carry the latest message snippet"


def test_stop_reports_session_name(base_hook_input, transcript_without_ask, tmp_path):
    # Why: a renamed session must reach the hub with its name on the Stop event.
    base = Path(transcript_without_ask).read_text(encoding="utf-8")
    named = tmp_path / "named_transcript.jsonl"
    named.write_text(base + json.dumps({"type": "custom-title", "customTitle": "tester",
                                         "sessionId": "test-session-123"}) + "\n")
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": str(named),
    }))
    assert len(events) == 1
    assert events[0]["session_name"] == "tester"


def test_stop_clears_waiting_marker(base_hook_input, transcript_without_ask, marker_home):
    # Why: covers the denied-permission path (no tool ran) so the next turn
    # cannot report stale "working".
    marker_home.mkdir(parents=True, exist_ok=True)
    (marker_home / "test-session-123").touch()
    run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
    })
    assert not (marker_home / "test-session-123").exists()


def test_stop_with_active_subagent_reports_working_not_done(base_hook_input, transcript_without_ask, active_subagent_home):
    # Why: the core fix -- a Stop while a subagent is still running must report
    # "working", not "done".
    marker_dir = active_subagent_home / "test-session-123"
    marker_dir.mkdir(parents=True)
    (marker_dir / "marker-1").touch()
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "working"


def test_stop_with_active_subagent_and_genuine_ask_reports_needs_input(base_hook_input, transcript_with_ask, active_subagent_home):
    # Why: needs_input always wins, even with background subagents active.
    marker_dir = active_subagent_home / "test-session-123"
    marker_dir.mkdir(parents=True)
    (marker_dir / "marker-1").touch()
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_with_ask,
    }))
    assert len(events) == 1
    assert events[0]["state"] == "needs_input"


def test_stop_forwards_active_work_on_working_branch(base_hook_input, transcript_without_ask, active_subagent_home):
    # Why: the payload must carry active_work when a subagent is running so the
    # dashboard can show per-item detail.
    marker_dir = active_subagent_home / "test-session-123"
    marker_dir.mkdir(parents=True)
    (marker_dir / "marker-1").touch()
    events = hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript_without_ask,
    }))
    assert len(events) == 1
    assert len(events[0]["active_work"]) == 1


def test_stop_exits_zero_when_hub_down(base_hook_input, transcript_without_ask, monkeypatch):
    # Why: graceful degradation -- a dead hub must never error the hook.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    spec = importlib.util.spec_from_file_location(
        "attention_hub_stop", HOOKS_DIR / "attention_hub_stop.py"
    )
    with patch("sys.stdin", StringIO(json.dumps({
        **base_hook_input, "transcript_path": transcript_without_ask,
    }))):
        mod = importlib.util.module_from_spec(spec)
        exit_code = 0
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit as e:
            exit_code = e.code or 0
    assert exit_code == 0
