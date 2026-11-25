# tests/test_suspended.py
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HUB_PATH = Path(__file__).parent.parent / "hub" / "attention_hub.py"
HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def load_hub():
    spec = importlib.util.spec_from_file_location("attention_hub", HUB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(session_id, name="", state="working", host="mac", **extra):
    return {
        "session_id": session_id,
        "session_name": name,
        "state": state,
        "project": "proj",
        "host": host,
        **extra,
    }


def make_store(tmp_path, prune_hours=24, start=1000.0):
    hub = load_hub()
    clock = {"now": start}
    store = hub.AttentionStore(
        str(tmp_path / "state.json"), prune_hours=prune_hours, now=lambda: clock["now"]
    )
    return hub, clock, store


def group(snapshot, story_id):
    return next(g for g in snapshot["groups"] if g["story_id"] == story_id)


@pytest.fixture
def hub_server(tmp_path):
    hub = load_hub()
    server = hub.create_server("127.0.0.1", 0, str(tmp_path / "state.json"), 24)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None


# --- T-1 .. T-4, T-11: store and API ---


def test_events_api_accepts_suspended_and_rejects_unknown(hub_server):
    # Why: the SessionEnd hook reports suspended through the normal events API.
    status, body = call(
        f"{hub_server}/api/events", "POST", event("a", state="suspended")
    )
    assert status == 200
    assert body["session"]["state"] == "suspended"
    status, _ = call(f"{hub_server}/api/events", "POST", event("a", state="bogus"))
    assert status == 400


def test_force_status_accepts_suspended_and_dashboard_lists_it_last(hub_server):
    # Why: the dashboard force buttons must reach the new state, after the existing ones.
    call(f"{hub_server}/api/events", "POST", event("a"))
    status, body = call(
        f"{hub_server}/api/sessions/a/state", "POST", {"state": "suspended"}
    )
    assert status == 200
    assert body["session"]["state"] == "suspended"
    hub = load_hub()
    assert (
        'const FORCE_STATES = ["working", "done", "needs_input", "waiting", "suspended"];'
        in hub.DASHBOARD_HTML
    )


def test_suspended_row_survives_reload(tmp_path):
    # Why: a hub restart must not turn resumable sessions into nothing.
    hub, clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-9-developer-x", state="suspended", host="box"))
    reloaded = hub.AttentionStore(
        str(tmp_path / "state.json"), now=lambda: clock["now"]
    )
    row = reloaded.list_sessions()[0]
    assert (row["state"], row["session_name"], row["host"]) == (
        "suspended",
        "sc-9-developer-x",
        "box",
    )
    assert [h["state"] for h in row["history"]] == ["suspended"]


def test_prune_keeps_suspended_and_drops_done(tmp_path):
    # Why: stories outlive the prune window; a pruned suspended row hides a resumable session.
    _hub, clock, store = make_store(tmp_path, prune_hours=1)
    store.upsert(event("s", "sc-9-developer-x", state="suspended"))
    store.upsert(event("d", "sc-9-reviewer-x", state="done"))
    clock["now"] += 7200
    assert [r["session_id"] for r in store.list_sessions()] == ["s"]


def test_flat_list_sorts_suspended_after_working(tmp_path):
    _hub, clock, store = make_store(tmp_path)
    store.upsert(event("s", state="suspended"))
    clock["now"] += 1
    store.upsert(event("w", state="working"))
    assert [r["session_id"] for r in store.list_sessions()] == ["w", "s"]


def test_dashboard_has_suspended_label_colors_and_toggle():
    # Why: the dashboard is one embedded string; these are the pieces R-3, R-9, R-10 and R-11 name.
    html = load_hub().DASHBOARD_HTML
    assert 'suspended: "suspended"' in html
    assert "#30363d" in html and "#6e7681" in html
    assert ".card.suspended" in html and ".group.suspended" in html
    assert 'toggle.setAttribute("aria-expanded"' in html
    assert '" suspended"' in html
    assert '"all suspended"' in html
    assert ".members[hidden] { display: none; }" in html
    assert "const expandedSuspended = new Set();" in html
    assert expandedSuspended_is_module_level(html)


def expandedSuspended_is_module_level(html):
    return html.index("const expandedSuspended") < html.index("function buildGroup")


# --- T-6, T-7: groups ---


def test_group_splits_live_and_suspended_members(tmp_path):
    # Why: suspended members must not count toward waiting or total, yet stay listed and labelled.
    _hub, _clock, store = make_store(tmp_path)
    store.upsert(event("t", "sc-9-tester-x", state="suspended"))
    store.upsert(event("d", "sc-9-developer-x", state="waiting"))
    store.upsert(event("r", "sc-9-reviewer-x", state="suspended"))
    g = group(store.snapshot(), "sc-9")
    assert g["session_ids"] == ["d"]
    assert g["suspended_session_ids"] == ["r", "t"]
    assert (g["waiting"], g["total"], g["suspended"]) == (1, 1, 2)
    assert set(g["labels"]) == {"d", "r", "t"}


@pytest.mark.parametrize(
    "states,color",
    [
        (["suspended", "suspended"], "suspended"),
        (["working", "suspended"], "green"),
        (["done", "suspended"], "gray"),
        (["waiting", "suspended"], "red"),
        (["needs_input", "suspended"], "red"),
        (["working", "waiting"], "green"),
        (["done", "waiting"], "red"),
    ],
)
def test_group_color_ignores_suspended_members(tmp_path, states, color):
    # Why: a finished, suspended session must never turn a group red or hold it green.
    _hub, _clock, store = make_store(tmp_path)
    for i, state in enumerate(states):
        store.upsert(event(f"s{i}", f"sc-9-developer-r{i}", state=state))
    assert group(store.snapshot(), "sc-9")["color"] == color


# --- T-10: resume ---


def test_resume_same_id_turns_row_live(tmp_path):
    _hub, _clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-9-developer-x", state="suspended"))
    store.upsert(event("a", "sc-9-developer-x", state="working"))
    rows = store.list_sessions()
    assert [(r["session_id"], r["state"]) for r in rows] == [("a", "working")]


def test_resume_new_id_drops_suspended_row(tmp_path):
    # Why: a resumed session may get a new id; the name and host key must leave one row.
    _hub, _clock, store = make_store(tmp_path)
    store.upsert(event("old", "sc-9-developer-x", state="suspended"))
    store.upsert(event("new", "sc-9-developer-x", state="working"))
    assert [r["session_id"] for r in store.list_sessions()] == ["new"]


# --- T-9: SessionEnd hook ---


def write_checkpoint(story_id, stages):
    path = Path.home() / ".claude" / "dev-workflow" / "state" / f"{story_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"repos": {f"r{i}": {"stage": s} for i, s in enumerate(stages)}})
    )


def run_session_end(hook_input, sessions=(), host="mac", monkeypatch=None):
    requests = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        requests.append((req.full_url, req.get_method(), body))
        response = MagicMock(status=200)
        if req.get_method() == "GET":
            response.read.return_value = json.dumps(
                {"sessions": list(sessions)}
            ).encode()
        return response

    spec = importlib.util.spec_from_file_location(
        "attention_hub_session_end", HOOKS_DIR / "attention_hub_session_end.py"
    )
    with (
        patch("sys.stdin", StringIO(json.dumps(hook_input))),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        mod = importlib.util.module_from_spec(spec)
        exit_code = 0
        try:
            spec.loader.exec_module(mod)
            mod.main()
        except SystemExit as e:
            exit_code = e.code or 0
    assert exit_code == 0
    return requests


def hook_input(name, session_id="me"):
    return {
        "session_id": session_id,
        "cwd": "/repo/x",
        "reason": "exit",
        "session_title": name,
    }


@pytest.fixture(autouse=True)
def host_label(monkeypatch):
    monkeypatch.setenv("CLAUDE_HOST_LABEL", "mac")


def test_session_end_in_progress_story_reports_suspended_never_deletes():
    # Why: DT-2 row 5, an ended session of an unfinished story stays resumable on the dashboard.
    write_checkpoint("sc-9", ["developing"])
    requests = run_session_end(hook_input("sc-9-developer-x"))
    assert not [r for r in requests if r[1] == "DELETE"]
    events = [r[2] for r in requests if r[0].endswith("/api/events")]
    assert len(events) == 1
    assert events[0]["state"] == "suspended"
    assert events[0]["session_name"] == "sc-9-developer-x"
    assert events[0]["cwd"] == "/repo/x"
    assert events[0]["message"] == "session ended; story still in progress"


def test_session_end_mixed_stages_is_in_progress():
    write_checkpoint("sc-9", ["done", "testing-prs"])
    requests = run_session_end(hook_input("sc-9-tester-x"))
    assert [r[2]["state"] for r in requests if r[0].endswith("/api/events")] == [
        "suspended"
    ]


def test_session_end_finished_story_deletes_row_and_same_host_suspended_siblings():
    # Why: DT-2 row 6 plus R-13, the cleanup deletes only this story's suspended rows on this host.
    write_checkpoint("sc-9", ["done", "finished"])
    sessions = [
        {
            "session_id": "me",
            "session_name": "sc-9-developer-x",
            "state": "working",
            "host": "mac",
        },
        {
            "session_id": "t",
            "session_name": "sc-9-tester-x",
            "state": "suspended",
            "host": "mac",
        },
        {
            "session_id": "other-host",
            "session_name": "sc-9-reviewer-x",
            "state": "suspended",
            "host": "pc",
        },
        {
            "session_id": "other-story",
            "session_name": "sc-8-tester-x",
            "state": "suspended",
            "host": "mac",
        },
        {
            "session_id": "live",
            "session_name": "sc-9-reviewer-y",
            "state": "done",
            "host": "mac",
        },
    ]
    requests = run_session_end(hook_input("sc-9-developer-x"), sessions=sessions)
    deleted = sorted(r[0].rsplit("/", 1)[1] for r in requests if r[1] == "DELETE")
    assert deleted == ["me", "t"]


@pytest.mark.parametrize(
    "name,setup",
    [
        ("my-session", lambda: write_checkpoint("sc-9", ["developing"])),
        ("sc-9-developer-x", lambda: None),
        ("sc-9-developer-x", lambda: write_checkpoint("sc-9", [])),
    ],
)
def test_session_end_deletes_when_not_a_story_in_progress(name, setup):
    # Why: DT-2 rows 3 and 4, an unnamed or checkpoint-less session leaves no suspended row.
    setup()
    requests = run_session_end(hook_input(name))
    assert [r[1] for r in requests if r[1] == "DELETE"] == ["DELETE"]
    assert not [r for r in requests if r[0].endswith("/api/events")]


def test_session_end_exits_zero_with_hub_down(monkeypatch):
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    for stages in (["developing"], ["done"]):
        write_checkpoint("sc-9", stages)
        spec = importlib.util.spec_from_file_location(
            "attention_hub_session_end", HOOKS_DIR / "attention_hub_session_end.py"
        )
        with patch("sys.stdin", StringIO(json.dumps(hook_input("sc-9-developer-x")))):
            mod = importlib.util.module_from_spec(spec)
            code = 0
            try:
                spec.loader.exec_module(mod)
                mod.main()
            except SystemExit as e:
                code = e.code or 0
        assert code == 0
