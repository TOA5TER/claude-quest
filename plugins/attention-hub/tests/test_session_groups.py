# tests/test_session_groups.py
import importlib.util
import json
import threading
import urllib.request
from pathlib import Path

import pytest


def load_hub():
    spec = importlib.util.spec_from_file_location(
        "attention_hub",
        Path(__file__).parent.parent / "hub" / "attention_hub.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(session_id, name="", state="working", **extra):
    return {"session_id": session_id, "session_name": name, "state": state,
            "project": "proj", "host": "mac", **extra}


def make_store(tmp_path, start=1000.0):
    hub = load_hub()
    clock = {"now": start}
    store = hub.AttentionStore(str(tmp_path / "state.json"), now=lambda: clock["now"])
    return hub, clock, store


def group(snapshot, story_id):
    matches = [g for g in snapshot["groups"] if g["story_id"] == story_id]
    assert len(matches) == 1, f"expected exactly one group {story_id!r}"
    return matches[0]


def layout_keys(snapshot):
    return [item.get("story_id") or item.get("session_id") for item in snapshot["layout"]]


def assert_consistent(snapshot):
    ids = {s["session_id"] for s in snapshot["sessions"]}
    grouped = [sid for g in snapshot["groups"] for sid in g["session_ids"]]
    ungrouped = [item["session_id"] for item in snapshot["layout"]
                 if item["type"] == "session"]
    assert set(grouped) <= ids
    assert set(ungrouped) <= ids
    assert sorted(grouped + ungrouped) == sorted(ids), "every session appears exactly once"
    group_ids = [item["story_id"] for item in snapshot["layout"] if item["type"] == "group"]
    assert sorted(group_ids) == sorted(g["story_id"] for g in snapshot["groups"])
    for g in snapshot["groups"]:
        assert set(g["labels"]) == set(g["session_ids"])


@pytest.fixture
def hub_server(tmp_path):
    hub = load_hub()
    server = hub.create_server("127.0.0.1", 0, str(tmp_path / "state.json"), 24)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def http_json(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read().decode()
        return resp.status, json.loads(raw) if raw else None


def post(base, body):
    status, _ = http_json(f"{base}/api/events", "POST", body)
    assert status == 200


def listing(base):
    _, data = http_json(f"{base}/api/sessions")
    return data


# --- Grouping ---

def test_developer_and_reviewer_of_one_story_form_one_group(tmp_path):
    # Why: the core feature: sessions sharing a story id read as one story.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    store.upsert(event("b", "sc-1000-reviewer"))
    snap = store.snapshot()
    g = group(snap, "sc-1000")
    assert g["session_ids"] == ["a", "b"]
    assert g["labels"] == {"a": "developer", "b": "reviewer"}
    assert g["total"] == 2
    assert snap["layout"] == [{"type": "group", "story_id": "sc-1000"}]


def test_repo_suffixes_are_separate_rows(tmp_path):
    # Why: a multi-repo story runs one developer per repo; each needs its own row.
    _, _, store = make_store(tmp_path)
    store.upsert(event("w", "sc-1234-developer-web"))
    store.upsert(event("a", "sc-1234-developer-api"))
    g = group(store.snapshot(), "sc-1234")
    assert g["session_ids"] == ["a", "w"]
    assert g["labels"] == {"a": "developer · api", "w": "developer · web"}


def test_identical_names_are_two_rows_both_counted_for_color(tmp_path):
    # Why: a restarted session can share a name with its stale record; each is
    # its own row, and a stale waiting duplicate must still count toward color.
    _, _, store = make_store(tmp_path)
    store.upsert(event("s2", "sc-1000-developer", state="waiting"))
    store.upsert(event("s1", "sc-1000-developer", state="waiting"))
    g = group(store.snapshot(), "sc-1000")
    assert g["session_ids"] == ["s1", "s2"]
    assert g["labels"] == {"s1": "developer", "s2": "developer"}
    assert (g["color"], g["waiting"], g["total"]) == ("red", 2, 2)
    store.upsert(event("s1", "sc-1000-developer", state="working"))
    g = group(store.snapshot(), "sc-1000")
    assert (g["color"], g["waiting"], g["total"]) == ("green", 1, 2)


def test_single_matching_session_still_forms_a_group(tmp_path):
    # Why: a story with one live agent is still a story; it must not flip
    # between card and group as agents come and go.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    snap = store.snapshot()
    assert snap["layout"] == [{"type": "group", "story_id": "sc-1000"}]


def test_non_matching_sessions_are_ungrouped_cards(tmp_path):
    # Why: unnamed or ordinary sessions keep today's card, alongside groups.
    _, _, store = make_store(tmp_path)
    store.upsert(event("u1", ""))
    store.upsert(event("u2", "scratch"))
    store.upsert(event("g", "sc-1000-developer"))
    snap = store.snapshot()
    assert [g["story_id"] for g in snap["groups"]] == ["sc-1000"]
    assert {"type": "session", "session_id": "u1"} in snap["layout"]
    assert {"type": "session", "session_id": "u2"} in snap["layout"]
    assert_consistent(snap)


def test_membership_is_not_persisted(tmp_path):
    # Why: membership is derived from the current name every listing; storing
    # it would let a stale group outlive a rename.
    hub, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    stored = json.loads((tmp_path / "state.json").read_text())["sessions"]["a"]
    assert "story_id" not in stored and "label" not in stored and "group" not in stored


# --- Live regrouping ---

def test_rename_into_and_out_of_a_group(tmp_path):
    # Why: an unnamed orchestrator renamed to the story's name must join the
    # group on the next listing, and leave it when renamed away.
    _, _, store = make_store(tmp_path)
    store.upsert(event("d", "sc-1000-developer"))
    store.upsert(event("o", ""))
    assert layout_keys(store.snapshot()) == ["o", "sc-1000"]
    store.upsert(event("o", "sc-1000-orchestrator"))
    snap = store.snapshot()
    assert layout_keys(snap) == ["sc-1000"]
    assert group(snap, "sc-1000")["session_ids"] == ["d", "o"]
    store.upsert(event("o", "notes"))
    snap = store.snapshot()
    assert layout_keys(snap) == ["o", "sc-1000"]
    assert group(snap, "sc-1000")["session_ids"] == ["d"]


def test_grouping_survives_hub_restart(tmp_path):
    # Why: groups are rebuilt from persisted names after a restart.
    hub, clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    store.upsert(event("b", "sc-1000-reviewer"))
    restarted = hub.AttentionStore(str(tmp_path / "state.json"), now=lambda: clock["now"])
    assert group(restarted.snapshot(), "sc-1000")["session_ids"] == ["a", "b"]


def test_rename_regroups_over_http(hub_server):
    # Why: the regroup must reach the dashboard through the real listing.
    post(hub_server, event("o", ""))
    post(hub_server, event("d", "sc-1000-developer"))
    assert layout_keys(listing(hub_server)) == ["o", "sc-1000"]
    post(hub_server, event("o", "sc-1000-orchestrator"))
    data = listing(hub_server)
    assert layout_keys(data) == ["sc-1000"]
    assert group(data, "sc-1000")["session_ids"] == ["d", "o"]


# --- Group color ---

@pytest.mark.parametrize("states,color", [
    (["waiting", "waiting"], "red"),
    (["needs_input", "needs_input"], "red"),
    (["waiting", "needs_input"], "red"),
    (["waiting", "working"], "green"),
    (["waiting", "done"], "green"),
    (["done", "done"], "green"),
    (["waiting"], "red"),
    (["working"], "green"),
])
def test_group_color_red_only_when_every_member_waits(tmp_path, states, color):
    # Why: one agent working while others wait means the story is not blocked
    # on the user; only an all-waiting group is red.
    _, _, store = make_store(tmp_path)
    for i, state in enumerate(states):
        store.upsert(event(f"s{i}", f"sc-1000-role{i}", state=state))
    g = group(store.snapshot(), "sc-1000")
    assert g["color"] == color
    assert g["waiting"] == sum(s in ("waiting", "needs_input") for s in states)
    assert g["total"] == len(states)


# --- Ordering ---

def test_ordering_over_http(hub_server):
    # Why: the dashboard renders layout and session_ids verbatim, so the
    # alphabetical, numeric-aware order must hold in the served listing.
    post(hub_server, event("g1", "sc-1000-orchestrator"))
    post(hub_server, event("g2", "sc-1000-developer"))
    post(hub_server, event("g3", "sc-1000--x"))
    post(hub_server, event("h1", "sc-999-tester"))
    post(hub_server, event("r1", "sc-1234-developer2-api"))
    post(hub_server, event("r2", "sc-1234-developer-web"))
    post(hub_server, event("u-zeta", "zeta"))
    post(hub_server, event("u-alpha", "alpha"))
    post(hub_server, event("u-mid", "sc-1100 notes"))
    data = listing(hub_server)
    assert layout_keys(data) == ["u-alpha", "sc-999", "sc-1000", "u-mid", "sc-1234", "u-zeta"]
    g = group(data, "sc-1000")
    assert g["session_ids"] == ["g3", "g2", "g1"]
    assert g["labels"]["g3"] == "-x"
    assert group(data, "sc-1234")["session_ids"] == ["r2", "r1"]
    assert_consistent(data)


def test_forcing_every_status_never_moves_anything(hub_server):
    # Why: rows jumping while the user reads was rejected; status must never
    # affect grouped ordering.
    names = {"a": "sc-1000-reviewer", "b": "sc-1000-developer", "c": "sc-1000-tester",
             "d": "beta", "e": "sc-2000-developer"}
    for sid, name in names.items():
        post(hub_server, event(sid, name))
    before = listing(hub_server)
    order = (layout_keys(before), [g["session_ids"] for g in before["groups"]])
    for state in ("waiting", "done", "needs_input", "working"):
        for sid in names:
            status, _ = http_json(f"{hub_server}/api/sessions/{sid}/state", "POST",
                                  {"state": state})
            assert status == 200
            after = listing(hub_server)
            assert (layout_keys(after), [g["session_ids"] for g in after["groups"]]) == order


def test_group_sorts_before_ungrouped_card_with_equal_key(tmp_path):
    # Why: ties must break deterministically; a group wins over a card whose
    # display name equals the story id.
    _, _, store = make_store(tmp_path)
    store.upsert(event("aaa", "sc-1000"))
    store.upsert(event("zzz", "sc-1000-developer"))
    assert store.snapshot()["layout"] == [
        {"type": "group", "story_id": "sc-1000"},
        {"type": "session", "session_id": "aaa"},
    ]


def test_ungrouped_cards_with_same_name_order_by_session_id(tmp_path):
    # Why: identical display names still need a fixed order.
    _, _, store = make_store(tmp_path)
    store.upsert(event("s2", "notes"))
    store.upsert(event("s1", "notes"))
    assert layout_keys(store.snapshot()) == ["s1", "s2"]


def test_ungrouped_card_without_name_sorts_by_session_id(tmp_path):
    # Why: an unnamed card's display name is its session id, so it sorts by it.
    _, _, store = make_store(tmp_path)
    store.upsert(event("m-session", ""))
    store.upsert(event("x", "sc-1000-developer"))
    store.upsert(event("a", "beta"))
    assert layout_keys(store.snapshot()) == ["a", "m-session", "sc-1000"]


def test_case_differing_story_ids_are_two_groups_in_fixed_order(tmp_path):
    # Why: story ids match exactly as typed; case variants never merge and
    # their order must not depend on insertion.
    for first, second in (("lower", "upper"), ("upper", "lower")):
        sub = tmp_path / first
        sub.mkdir()
        _, _, store = make_store(sub)
        names = {"lower": "sc-1000-x", "upper": "SC-1000-x"}
        store.upsert(event(first, names[first]))
        store.upsert(event(second, names[second]))
        snap = store.snapshot()
        assert layout_keys(snap) == ["SC-1000", "sc-1000"]
        assert group(snap, "SC-1000")["session_ids"] == ["upper"]
        assert group(snap, "sc-1000")["session_ids"] == ["lower"]


def test_flat_sessions_list_keeps_needs_attention_sort(tmp_path):
    # Why: API consumers of the flat list rely on its needs-attention-first sort.
    _, clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", state="working"))
    clock["now"] += 1
    store.upsert(event("b", "sc-1000-reviewer", state="waiting"))
    snap = store.snapshot()
    assert [s["session_id"] for s in snap["sessions"]] == ["b", "a"]
    assert snap["sessions"] == store.list_sessions()


# --- Titles ---

def test_title_header_with_and_without_title(tmp_path):
    # Why: the header reads "id: title", or just the id with no stray colon.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="Group sessions"))
    store.upsert(event("b", "sc-2000-developer"))
    snap = store.snapshot()
    assert group(snap, "sc-1000")["title"] == "sc-1000: Group sessions"
    assert group(snap, "sc-2000")["title"] == "sc-2000"


def test_title_sticky_across_events_without_it(tmp_path):
    # Why: not every event carries the title; a known one must not flicker away.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="Group sessions"))
    store.upsert(event("a", "sc-1000-developer", state="waiting"))
    store.upsert(event("a", "", state="working"))
    assert group(store.snapshot(), "sc-1000")["title"] == "sc-1000: Group sessions"


def test_title_clamped(tmp_path):
    # Why: the title is client-supplied; the hub bounds it like other fields.
    hub, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="t" * 5000))
    row = store.list_sessions()[0]
    assert row["story_title"] == "t" * hub.FIELD_MAX_CHARS


def test_title_survives_restart(tmp_path):
    # Why: a restart must not drop headers back to bare ids.
    hub, clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="Group sessions"))
    restarted = hub.AttentionStore(str(tmp_path / "state.json"), now=lambda: clock["now"])
    assert group(restarted.snapshot(), "sc-1000")["title"] == "sc-1000: Group sessions"


@pytest.mark.parametrize("raw,expected", [
    (["not", "a", "string"], ""),
    (42, ""),
    (None, ""),
    ("x" * 5000, "x" * 256),
])
def test_load_sanitizes_malformed_title(tmp_path, raw, expected):
    # Why: a hand-edited state file must not inject a non-string or unbounded
    # title into the header.
    hub = load_hub()
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"sessions": {"a": {
        "session_id": "a", "session_name": "sc-1000-developer", "state": "working",
        "story_title": raw}}}), encoding="utf-8")
    store = hub.AttentionStore(str(state_file))
    assert store.list_sessions()[0]["story_title"] == expected


def test_load_drops_title_for_name_without_story_id(tmp_path):
    # Why: a session whose name has no story id never stores a title.
    hub = load_hub()
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"sessions": {"a": {
        "session_id": "a", "session_name": "notes", "state": "working",
        "story_title": "Leftover"}}}), encoding="utf-8")
    assert hub.AttentionStore(str(state_file)).list_sessions()[0]["story_title"] == ""


def test_rename_to_other_story_clears_old_title(tmp_path):
    # Why: a title belongs to its story; carrying it across a rename would label
    # the new story with the old story's title.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="A"))
    store.upsert(event("a", "sc-2000-developer"))
    assert group(store.snapshot(), "sc-2000")["title"] == "sc-2000"
    store.upsert(event("a", "sc-2000-developer", story_title="B"))
    assert group(store.snapshot(), "sc-2000")["title"] == "sc-2000: B"


def test_rename_to_other_story_with_title_uses_new_title(tmp_path):
    # Why: the rename event's own title must replace the old one at once.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="A"))
    store.upsert(event("a", "sc-2000-developer", story_title="B"))
    assert group(store.snapshot(), "sc-2000")["title"] == "sc-2000: B"


def test_rename_to_non_matching_name_clears_title(tmp_path):
    # Why: leaving a story must drop its title so a later rejoin starts clean.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer", story_title="A"))
    store.upsert(event("a", "notes"))
    assert store.list_sessions()[0]["story_title"] == ""
    store.upsert(event("a", "sc-1000-developer"))
    assert group(store.snapshot(), "sc-1000")["title"] == "sc-1000"


def test_non_matching_name_with_title_stores_none(tmp_path):
    # Why: a title without a story id has no group to label.
    _, _, store = make_store(tmp_path)
    store.upsert(event("a", "notes", story_title="Stray"))
    assert store.list_sessions()[0]["story_title"] == ""


def test_group_title_from_most_recently_updated_member(tmp_path):
    # Why: members can disagree on the title; the freshest one wins, with a
    # fixed tie-break so the header is deterministic.
    _, clock, store = make_store(tmp_path)
    store.upsert(event("b", "sc-1000-developer", story_title="Old"))
    clock["now"] += 1
    store.upsert(event("c", "sc-1000-reviewer", story_title="New"))
    clock["now"] += 1
    store.upsert(event("d", "sc-1000-tester"))
    assert group(store.snapshot(), "sc-1000")["title"] == "sc-1000: New"
    clock["now"] += 1
    store.upsert(event("a", "sc-1000-orchestrator", story_title="Tie A"))
    store.upsert(event("e", "sc-1000-watcher", story_title="Tie E"))
    assert group(store.snapshot(), "sc-1000")["title"] == "sc-1000: Tie A"


def test_title_over_http(hub_server):
    # Why: the title must reach the dashboard's header through the listing.
    post(hub_server, event("a", "sc-1000-developer", story_title="Group sessions"))
    assert group(listing(hub_server), "sc-1000")["title"] == "sc-1000: Group sessions"


# --- Snapshot consistency and lifecycle ---

def test_listing_consistent_after_dismiss(hub_server):
    # Why: groups, layout and sessions must never disagree, or the dashboard
    # would reference a session it cannot render.
    for sid, name in (("a", "sc-1000-developer"), ("b", "sc-1000-reviewer"), ("c", "notes")):
        post(hub_server, event(sid, name))
    assert_consistent(listing(hub_server))
    status, _ = http_json(f"{hub_server}/api/sessions/a", "DELETE")
    assert status == 200
    data = listing(hub_server)
    assert_consistent(data)
    assert group(data, "sc-1000")["session_ids"] == ["b"]


def test_dismissing_last_member_removes_group(hub_server):
    # Why: an empty group would be a header with nothing under it.
    post(hub_server, event("a", "sc-1000-developer"))
    http_json(f"{hub_server}/api/sessions/a", "DELETE")
    data = listing(hub_server)
    assert data["groups"] == [] and data["layout"] == []


def test_snapshot_consistent_after_pruning(tmp_path):
    # Why: pruning runs inside the listing; groups must be built from the
    # post-prune set, never include a pruned member.
    _, clock, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    clock["now"] += 20 * 3600
    store.upsert(event("b", "sc-1000-reviewer"))
    clock["now"] += 5 * 3600
    snap = store.snapshot()
    assert_consistent(snap)
    assert group(snap, "sc-1000")["session_ids"] == ["b"]
    clock["now"] += 25 * 3600
    snap = store.snapshot()
    assert snap == {"sessions": [], "groups": [], "layout": []}


def test_snapshot_built_from_one_locked_pass(tmp_path, monkeypatch):
    # Why: building groups from a second read could race a concurrent write;
    # all three parts must come from the rows of a single pass.
    hub, _, store = make_store(tmp_path)
    store.upsert(event("a", "sc-1000-developer"))
    calls = []
    original = store._rows_locked

    def counting(now):
        calls.append(now)
        rows = original(now)
        store._sessions["late"] = dict(store._sessions["a"], session_id="late")
        return rows

    monkeypatch.setattr(store, "_rows_locked", counting)
    snap = store.snapshot()
    assert len(calls) == 1
    assert_consistent(snap)
    assert [s["session_id"] for s in snap["sessions"]] == ["a"]


def test_http_listing_has_groups_and_layout(hub_server):
    # Why: the dashboard reads all three keys from one response.
    post(hub_server, event("a", "sc-1000-developer"))
    data = listing(hub_server)
    assert set(data) == {"sessions", "groups", "layout"}
    g = data["groups"][0]
    assert set(g) == {"story_id", "title", "color", "session_ids", "labels",
                      "waiting", "total"}
