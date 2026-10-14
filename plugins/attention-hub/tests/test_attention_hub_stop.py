"""
Tests for attention_hub_stop.py — attention-hub's own Stop hook. Computes
needs_input/working(background_tasks, session_crons)/done and reports directly to the hub;
no macOS/Slack (that stays in the notifications plugin).
"""

import importlib.util
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

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
    # Why: waiting and done rows must show WHAT the session finished with.
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


# Fixtures in fixtures/stop_background_work.json: shell, subagent, monitor as
# shell, workflow and cron are real Stop entries captured live on Claude Code
# 2.1.277. monitor, teammate, cloud session, MCP task and unseen were never seen
# live and are synthetic, built from the hooks reference.

WORK = json.loads((Path(__file__).parent / "fixtures" / "stop_background_work.json").read_text())
TASK_TYPES = ["shell", "subagent", "monitor as shell", "workflow", "monitor", "teammate",
              "cloud session", "MCP task", "unseen"]


def stop_events(base_hook_input, transcript, **fields):
    return hub_events(run_hook_capture_hub({
        **base_hook_input, "transcript_path": transcript, **fields,
    }))


@pytest.mark.parametrize("task_type", TASK_TYPES)
def test_stop_with_one_task_reports_working_with_matching_entry(
        base_hook_input, transcript_without_ask, task_type):
    # Why: each in-flight task type must make the card working and surface as one
    # entry carrying its id, type, description and status. Shell, subagent, monitor as
    # shell and workflow are live captures; the other types are synthetic.
    task = WORK[task_type]
    events = stop_events(base_hook_input, transcript_without_ask, background_tasks=[task])
    assert len(events) == 1
    assert events[0]["state"] == "working"
    assert events[0]["active_work"] == [{
        "id": task["id"], "kind": task["type"],
        "label": task["description"], "status": task["status"],
    }]


def test_stop_with_empty_lists_reports_done_and_explicit_empty_active_work(
        base_hook_input, transcript_without_ask):
    # Why: nothing in flight means done, and the explicit empty list is what makes
    # the hub drop the previous Active work.
    events = stop_events(base_hook_input, transcript_without_ask,
                         background_tasks=[], session_crons=[])
    assert events[0]["state"] == "done"
    assert events[0]["active_work"] == []


def test_stop_without_background_fields_reports_done(base_hook_input, transcript_without_ask):
    # Why: older Claude Code builds omit both lists; they must read as nothing in
    # flight, with no marker fallback.
    events = stop_events(base_hook_input, transcript_without_ask)
    assert events[0]["state"] == "done"
    assert events[0]["active_work"] == []


@pytest.mark.parametrize("tasks", ["running", 5, {"id": "t"}, [None, "x", 3]])
def test_stop_with_non_list_or_malformed_tasks_does_not_crash(
        base_hook_input, transcript_without_ask, tasks):
    # Why: hook input is untrusted JSON; garbage must degrade to done, not raise.
    events = stop_events(base_hook_input, transcript_without_ask, background_tasks=tasks)
    assert len(events) == 1
    assert events[0]["state"] == "done"


def test_stop_counts_only_valid_entries_among_malformed(base_hook_input, transcript_without_ask):
    # Why: one bad entry must not hide the valid in-flight task beside it.
    events = stop_events(base_hook_input, transcript_without_ask,
                         background_tasks=[None, WORK["shell"], "junk"])
    assert events[0]["state"] == "working"
    assert [e["id"] for e in events[0]["active_work"]] == ["b3b2ar8fo"]


def test_stop_needs_input_wins_over_in_flight_work(base_hook_input, transcript_with_ask):
    # Why: a pending question outranks background work, but the list must still
    # reach the card.
    events = stop_events(base_hook_input, transcript_with_ask,
                         background_tasks=[WORK["shell"]])
    assert events[0]["state"] == "needs_input"
    assert [e["id"] for e in events[0]["active_work"]] == ["b3b2ar8fo"]


def test_stop_with_cron_alone_reports_working_with_cron_entry(
        base_hook_input, transcript_without_ask):
    # Why: a pending scheduled wakeup means the session will wake itself, so it
    # is working; the entry id is the cron's own id. Live capture.
    events = stop_events(base_hook_input, transcript_without_ask,
                         session_crons=[WORK["cron"]])
    assert events[0]["state"] == "working"
    assert events[0]["active_work"] == [{
        "id": "63ca06a8", "kind": "cron", "label": "check", "status": "scheduled",
    }]


def test_stop_with_cron_and_task_reports_both_entries(base_hook_input, transcript_without_ask):
    # Why: tasks and crons share one Active work list.
    events = stop_events(base_hook_input, transcript_without_ask,
                         background_tasks=[WORK["shell"]], session_crons=[WORK["cron"]])
    assert events[0]["state"] == "working"
    assert [e["id"] for e in events[0]["active_work"]] == ["b3b2ar8fo", "63ca06a8"]


def test_stop_skips_cron_without_id(base_hook_input, transcript_without_ask):
    # Why: without an id the hub cannot keep a stable start time, and an unusable
    # entry must not flip a finished session to working.
    events = stop_events(base_hook_input, transcript_without_ask,
                         session_crons=[{"schedule": "* * * * *", "prompt": "no id"}])
    assert events[0]["state"] == "done"
    assert events[0]["active_work"] == []


def test_stop_after_shell_exits_reports_working_then_done(base_hook_input, transcript_without_ask):
    # Why: regression for the stuck-working bug; a shell that exited used to leave
    # a marker that kept the next Stop at working. Synthetic payloads.
    first = stop_events(base_hook_input, transcript_without_ask,
                        background_tasks=[WORK["shell"]])
    second = stop_events(base_hook_input, transcript_without_ask, background_tasks=[])
    assert first[0]["state"] == "working"
    assert len(first[0]["active_work"]) == 1
    assert second[0]["state"] == "done"
    assert second[0]["active_work"] == []


def test_subagent_originated_stop_with_tasks_reports_nothing(
        base_hook_input, transcript_without_ask):
    # Why: a subagent's Stop shares the parent's session_id; its task list must
    # not overwrite the parent's card.
    events = stop_events(base_hook_input, transcript_without_ask,
                         background_tasks=[WORK["shell"]], agent_id="agent-abc123")
    assert events == []


def test_hooks_json_registers_no_pre_tool_use_or_subagent_stop():
    # Why: the marker design is gone; a lingering registration would launch a
    # process per tool call (PreToolUse) or flip state on subagent exit.
    registered = json.loads((HOOKS_DIR / "hooks.json").read_text())["hooks"]
    assert "PreToolUse" not in registered
    assert "SubagentStop" not in registered


def test_removed_scripts_and_marker_helpers_no_longer_exist():
    # Why: dead marker code would silently keep the old guess-based state logic
    # reachable; waiting-marker helpers must survive.
    assert not (HOOKS_DIR / "attention_hub_pre_tool_use.py").exists()
    assert not (HOOKS_DIR / "attention_hub_subagent_stop.py").exists()
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    for name in ("mark_subagent_active", "mark_background_active", "list_active_work",
                 "count_active_subagents", "clear_active_subagent",
                 "clear_all_active_subagents", "ACTIVE_SUBAGENT_TTL_SECONDS",
                 "BACKGROUND_SUBAGENT_TTL_SECONDS", "COMPLETED_RETENTION_SECONDS"):
        assert not hasattr(client, name), name
    for name in ("set_waiting_marker", "has_waiting_marker", "clear_waiting_marker"):
        assert hasattr(client, name), name


def test_stop_events_through_the_hub_clear_work_once_the_shell_exits(
        base_hook_input, transcript_without_ask, tmp_path):
    # Why: end-to-end guard for the stuck-working bug; the empty-list Stop must
    # wipe the card's Active work and settle the session at done in the real store.
    hub_spec = importlib.util.spec_from_file_location(
        "attention_hub", HOOKS_DIR.parent / "hub" / "attention_hub.py")
    hub = importlib.util.module_from_spec(hub_spec)
    hub_spec.loader.exec_module(hub)
    store = hub.AttentionStore(str(tmp_path / "state.json"))
    first = stop_events(base_hook_input, transcript_without_ask, background_tasks=[WORK["shell"]])
    record = store.upsert(first[0])
    assert record["state"] == "working" and len(record["active_work"]) == 1
    second = stop_events(base_hook_input, transcript_without_ask, background_tasks=[])
    record = store.upsert(second[0])
    assert record["state"] == "done" and record["active_work"] == []


@pytest.fixture
def inflight_home(marker_home):
    return marker_home.parent / "inflight-markers"


def test_stop_with_work_sets_inflight_marker(
        base_hook_input, transcript_without_ask, inflight_home):
    # Why: the Notification hook reads this marker to ignore idle_prompt while work
    # is in flight; Stop is the only place that knows the list.
    stop_events(base_hook_input, transcript_without_ask, background_tasks=[WORK["shell"]])
    assert (inflight_home / "test-session-123").is_file()


def test_stop_with_only_a_cron_sets_inflight_marker(
        base_hook_input, transcript_without_ask, inflight_home):
    # Why: a pending cron also keeps the card working, so it must also guard it.
    stop_events(base_hook_input, transcript_without_ask, session_crons=[WORK["cron"]])
    assert (inflight_home / "test-session-123").is_file()


def test_stop_with_empty_lists_clears_inflight_marker(
        base_hook_input, transcript_without_ask, inflight_home):
    # Why: once the work is gone a later idle_prompt is real and must report waiting.
    inflight_home.mkdir(parents=True)
    (inflight_home / "test-session-123").touch()
    stop_events(base_hook_input, transcript_without_ask,
                background_tasks=[], session_crons=[])
    assert not (inflight_home / "test-session-123").exists()


def test_stop_with_ask_and_work_still_sets_inflight_marker(
        base_hook_input, transcript_with_ask, inflight_home):
    # Why: needs_input wins the card state, but work is still in flight and the
    # marker tracks the list, not the state.
    stop_events(base_hook_input, transcript_with_ask, background_tasks=[WORK["shell"]])
    assert (inflight_home / "test-session-123").is_file()


def test_subagent_originated_stop_leaves_inflight_marker_alone(
        base_hook_input, transcript_without_ask, inflight_home):
    # Why: subagent Stops are ignored entirely, so they cannot clear the main
    # session's marker.
    inflight_home.mkdir(parents=True)
    (inflight_home / "test-session-123").touch()
    stop_events(base_hook_input, transcript_without_ask,
                agent_id="sub-1", background_tasks=[])
    assert (inflight_home / "test-session-123").is_file()
