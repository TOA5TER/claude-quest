# tests/test_hub_client.py
import importlib.util
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


def load_client():
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client",
        Path(__file__).parent.parent / "hooks" / "attention_hub_client.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Payload identity fields ---

def test_payload_contains_identity_fields(monkeypatch):
    # Why: the dashboard can only label a row if every event identifies the session,
    # project, host, state, and time. Guards against silently dropping a field the
    # hub needs to render "which instance is waiting on me".
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    payload = client.build_event_payload(
        "sess-1", "/home/user/my-project", "waiting", "Need permission?"
    )
    assert payload["session_id"] == "sess-1"
    assert payload["project"] == "my-project"
    assert payload["state"] == "waiting"
    assert payload["message"] == "Need permission?"
    assert payload["host"]  # non-empty hostname fallback
    assert payload["timestamp"]


def test_payload_host_label_env_override(monkeypatch):
    # Why: docker containers and remote servers need friendly names instead of
    # generated hostnames; CLAUDE_HOST_LABEL must win over the machine hostname.
    monkeypatch.setenv("CLAUDE_HOST_LABEL", "docker-build-box")
    client = load_client()
    payload = client.build_event_payload("s", "/srv/app", "working", None)
    assert payload["host"] == "docker-build-box"


def test_payload_truncates_long_message(monkeypatch):
    # Why: dashboard rows show a snippet, not a full transcript; unbounded messages
    # would bloat hub state and the JSON persistence file.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    payload = client.build_event_payload("s", "/srv/app", "needs_input", "x" * 1000)
    assert len(payload["message"]) <= 200


def test_payload_empty_message_allowed(monkeypatch):
    # Why: working/removed states carry no snippet; payload building must not
    # require one. Guards the None-message path used by UserPromptSubmit.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    payload = client.build_event_payload("s", "/srv/app", "working", None)
    assert payload["message"] == ""


# --- Session name resolution ---

def write_transcript(tmp_path, records):
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(path)


def test_session_name_from_hook_input_field():
    # Why: when Claude Code passes the session title directly in hook input, the
    # client must use it without touching the transcript — cheapest, freshest source.
    client = load_client()
    assert client.get_session_name({"session_title": "tester"}) == "tester"
    assert client.get_session_name({"custom_title": "tester"}) == "tester"


def test_session_name_from_transcript_last_custom_title(tmp_path):
    # Why: /rename writes {"type":"custom-title"} records into the transcript; the
    # LAST one is the current name. Picking the first would show stale names after
    # a re-rename.
    client = load_client()
    path = write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "custom-title", "customTitle": "old-name", "sessionId": "s1"},
        {"type": "custom-title", "customTitle": "tester", "sessionId": "s1"},
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
    ])
    assert client.get_session_name({"transcript_path": path}) == "tester"


def test_session_name_absent_returns_empty(tmp_path):
    # Why: unnamed sessions are the norm; the client must return "" (not None, not
    # an error) so the hub falls back to the project label. Guards the missing-file
    # path too — get_session_name must never raise inside a hook.
    client = load_client()
    path = write_transcript(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ])
    assert client.get_session_name({"transcript_path": path}) == ""
    assert client.get_session_name({"transcript_path": str(tmp_path / "missing.jsonl")}) == ""
    assert client.get_session_name({}) == ""


def test_payload_includes_session_name(monkeypatch):
    # Why: the hub can only display a session's name if every event carries it;
    # dropping the field would silently revert rows to project-only labels.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    payload = client.build_event_payload(
        "s", "/srv/app", "working", None, session_name="tester"
    )
    assert payload["session_name"] == "tester"


def test_payload_session_name_defaults_empty(monkeypatch):
    # Why: callers that pass no name must still produce a valid payload with an
    # empty session_name — unnamed sessions stay on the project label.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    payload = client.build_event_payload("s", "/srv/app", "working", None)
    assert payload["session_name"] == ""


def test_report_state_posts_session_name(monkeypatch):
    # Why: report_state is the single delivery path for all hooks; if it drops the
    # session_name kwarg, no hook can ever surface a name on the dashboard.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return MagicMock(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.report_state("s", "/work/proj", "working", session_name="tester")

    assert captured["body"]["session_name"] == "tester"


# --- Container detection ---

EXT4_ROOT_MOUNTINFO = (
    "25 0 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
    "26 25 0:5 / /dev rw,nosuid shared:2 - devtmpfs devtmpfs rw\n"
)
OVERLAY_ROOT_MOUNTINFO = (
    "1573 1280 0:211 / / rw,relatime - overlay overlay "
    "rw,lowerdir=/var/lib/docker/overlay2/l/AAA,upperdir=/var/lib/docker/"
    "overlay2/x/diff,workdir=/var/lib/docker/overlay2/x/work\n"
    "1574 1573 0:215 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw\n"
)


def neutralize_container_signals(client, monkeypatch, tmp_path):
    """Point every detection signal at a clean (non-container) source so tests
    are deterministic even when the suite itself runs inside a container."""
    monkeypatch.setattr(client, "CONTAINER_MARKER_FILES",
                        (str(tmp_path / "no-dockerenv"),
                         str(tmp_path / "no-containerenv")))
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(EXT4_ROOT_MOUNTINFO, encoding="utf-8")
    monkeypatch.setattr(client, "MOUNTINFO_PATH", str(mountinfo))
    monkeypatch.delenv("container", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)


def test_container_detection_clean_environment_false(monkeypatch, tmp_path):
    # Why: a plain laptop/VM session must NOT be badged as a container — false
    # positives would make the badge meaningless across a mixed fleet.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    assert client.detect_container() is False


def test_container_detection_dockerenv_marker(monkeypatch, tmp_path):
    # Why: /.dockerenv is the canonical Docker marker; its presence alone must
    # flag the session as containerized.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    marker = tmp_path / "dockerenv"
    marker.touch()
    monkeypatch.setattr(client, "CONTAINER_MARKER_FILES",
                        (str(marker), str(tmp_path / "no-containerenv")))
    assert client.detect_container() is True


def test_container_detection_containerenv_marker(monkeypatch, tmp_path):
    # Why: Podman writes /run/.containerenv instead of /.dockerenv; either
    # marker file alone must trigger detection.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    marker = tmp_path / "containerenv"
    marker.touch()
    monkeypatch.setattr(client, "CONTAINER_MARKER_FILES",
                        (str(tmp_path / "no-dockerenv"), str(marker)))
    assert client.detect_container() is True


def test_container_detection_container_env_var(monkeypatch, tmp_path):
    # Why: Podman and systemd-nspawn export container=...; the env var alone
    # must trigger detection even with no marker files.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    monkeypatch.setenv("container", "podman")
    assert client.detect_container() is True


def test_container_detection_kubernetes_env(monkeypatch, tmp_path):
    # Why: Kubernetes pods expose KUBERNETES_SERVICE_HOST but may lack Docker/
    # Podman markers; the variable alone must trigger detection.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    assert client.detect_container() is True


def test_container_detection_overlay_root_fallback(monkeypatch, tmp_path):
    # Why: some dev containers (verified empirically in this repo's own dev
    # container) expose no marker or env signal, but their root filesystem is
    # an overlayfs mount — the mountinfo fallback must catch them.
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    mountinfo = tmp_path / "mountinfo-overlay"
    mountinfo.write_text(OVERLAY_ROOT_MOUNTINFO, encoding="utf-8")
    monkeypatch.setattr(client, "MOUNTINFO_PATH", str(mountinfo))
    assert client.detect_container() is True


def test_container_detection_unreadable_mountinfo_false(monkeypatch, tmp_path):
    # Why: detection runs inside hooks, which must never raise; an unreadable
    # or missing mountinfo (non-Linux hosts) degrades to "not a container".
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    monkeypatch.setattr(client, "MOUNTINFO_PATH", str(tmp_path / "missing"))
    assert client.detect_container() is False


def test_payload_carries_container_flag_true(monkeypatch, tmp_path):
    # Why: the hub can only badge a session if every event carries the flag;
    # build_event_payload is the single payload source for all hooks.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    payload = client.build_event_payload("s", "/srv/app", "working", None)
    assert payload["is_container"] is True


def test_payload_carries_container_flag_false(monkeypatch, tmp_path):
    # Why: non-container sessions must send an explicit False (not omit the
    # field) so the hub never has to guess.
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    neutralize_container_signals(client, monkeypatch, tmp_path)
    payload = client.build_event_payload("s", "/srv/app", "working", None)
    assert payload["is_container"] is False


# --- Hub URL resolution ---

def test_hub_url_default(monkeypatch):
    # Why: with no configuration the hooks must target the documented default
    # http://localhost:8765 so local setups work out of the box.
    monkeypatch.delenv("CLAUDE_ATTENTION_HUB_URL", raising=False)
    client = load_client()
    assert client.get_hub_url() == "http://localhost:8765"


def test_hub_url_env_override(monkeypatch):
    # Why: docker containers and remote servers reach the hub via a configurable
    # address; the env var must override the default and tolerate trailing slashes.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://10.0.0.5:9999/")
    client = load_client()
    assert client.get_hub_url() == "http://10.0.0.5:9999"


# --- Graceful degradation ---

def test_report_state_unreachable_hub_no_exception(monkeypatch):
    # Why: an unreachable hub must never block or error a Claude session — the
    # core graceful-degradation guarantee of the whole feature.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    client = load_client()
    result = client.report_state("s", "/srv/app", "waiting", "msg")
    assert result is False


def test_remove_session_unreachable_hub_no_exception(monkeypatch):
    # Why: SessionEnd must exit cleanly even when the hub is down; a raised
    # exception here would surface as a hook error in Claude Code.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://127.0.0.1:1")
    client = load_client()
    result = client.remove_session("s")
    assert result is False


def test_report_state_posts_event_to_hub(monkeypatch):
    # Why: the hub contract is POST {hub}/api/events with the JSON payload; if the
    # path or body encoding drifts, every hook silently stops reporting.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    monkeypatch.delenv("CLAUDE_HOST_LABEL", raising=False)
    client = load_client()
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["method"] = req.get_method()
        return MagicMock(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = client.report_state("sess-9", "/work/proj", "waiting", "hello")

    assert ok is True
    assert captured["url"] == "http://hub.example:8765/api/events"
    assert captured["method"] == "POST"
    assert captured["body"]["session_id"] == "sess-9"
    assert captured["body"]["state"] == "waiting"


def test_report_state_slow_hub_no_exception(monkeypatch):
    # Why: a hub that accepts the connection but never answers (slow DNS/network)
    # must surface as a swallowed timeout, never an exception in the hook.
    import socket
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    client = load_client()
    with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
        assert client.report_state("s", "/srv/app", "waiting", "msg") is False


def test_report_state_bounded_total_wall_clock(monkeypatch):
    # Why: AC4 "never block Claude" — DNS resolution runs before urlopen's socket
    # timeout applies, so a black-holed hub URL could stall every UserPromptSubmit
    # for seconds. Total hub-report time must stay bounded by the timeout budget
    # even when urlopen itself hangs indefinitely.
    import time
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    client = load_client()
    client.HUB_TIMEOUT_SECONDS = 0.2

    def hanging_urlopen(req, timeout=None):
        time.sleep(10)
        return MagicMock(status=200)

    start = time.monotonic()
    with patch("urllib.request.urlopen", side_effect=hanging_urlopen):
        result = client.report_state("s", "/srv/app", "waiting", "msg")
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0


def test_remove_session_quotes_special_characters(monkeypatch):
    # Why: session IDs are interpolated into the URL path; reserved characters
    # must be percent-encoded or the hub can never delete what it stored.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    client = load_client()
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return MagicMock(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.remove_session("odd id/1")

    assert captured["url"] == "http://hub.example:8765/api/sessions/odd%20id%2F1"


def test_remove_session_sends_delete(monkeypatch):
    # Why: SessionEnd removal must target DELETE /api/sessions/{id}; a wrong verb
    # or path would leave dead rows on the dashboard forever.
    monkeypatch.setenv("CLAUDE_ATTENTION_HUB_URL", "http://hub.example:8765")
    client = load_client()
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return MagicMock(status=200)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = client.remove_session("sess-9")

    assert ok is True
    assert captured["url"] == "http://hub.example:8765/api/sessions/sess-9"
    assert captured["method"] == "DELETE"


# --- Marker JSON record format + legacy fallback ---

def test_read_marker_parses_json_record(hub_client, active_subagent_home):
    # Why: _read_marker is the single source of truth every other marker
    # function relies on -- it must round-trip a well-formed JSON record.
    active_subagent_home.mkdir(parents=True, exist_ok=True)
    marker = active_subagent_home / "m1"
    marker.write_text('{"id": "m1", "kind": "bash", "label": "x", '
                       '"status": "active", "started_at": 123.0}')
    record = hub_client._read_marker(marker)
    assert record == {"id": "m1", "kind": "bash", "label": "x",
                       "status": "active", "started_at": 123.0}


def test_read_marker_legacy_empty_file_falls_back(hub_client, active_subagent_home):
    # Why: markers created before this story (empty touch files) must still
    # be treated as valid task-kind, active-status records, not crash or
    # silently vanish, so rollout never loses in-flight tracking.
    active_subagent_home.mkdir(parents=True, exist_ok=True)
    marker = active_subagent_home / "legacy-marker"
    marker.touch()
    record = hub_client._read_marker(marker)
    assert record["id"] == "legacy-marker"
    assert record["kind"] == "task"
    assert record["label"] == ""
    assert record["status"] == "active"
    assert isinstance(record["started_at"], float)


# --- Active work tracking ---

def test_build_event_payload_includes_active_work_when_present():
    client = load_client()
    payload = client.build_event_payload(
        "s1", "/tmp/proj", "working", active_work=[{"kind": "task", "status": "active"}]
    )
    assert payload["active_work"] == [{"kind": "task", "status": "active"}]


def test_build_event_payload_omits_active_work_when_empty():
    # Why: keep normal-state payloads unchanged in shape when nothing is
    # tracked, matching every other optional field's omit-when-empty style.
    client = load_client()
    payload = client.build_event_payload("s1", "/tmp/proj", "done")
    assert "active_work" not in payload


# --- Dev-workflow stage ---
# The conftest autouse isolated_home fixture redirects HOME to tmp_path, so the
# default checkpoint dir resolves under tmp_path for every test here.

def make_stage_client(tmp_path):
    state_dir = tmp_path / ".claude" / "dev-workflow" / "state"
    state_dir.mkdir(parents=True)
    return load_client(), state_dir


def test_state_dir_resolves_home_at_call_time(tmp_path, monkeypatch):
    # Why: HOME-redirect fixtures are the suite's isolation mechanism; the
    # lookup must resolve ~ at call time (not module import), or hook-driven
    # suites would glob the developer's real checkpoints.
    client = load_client()
    other_home = tmp_path / "other-home"
    state_dir = other_home / ".claude" / "dev-workflow" / "state"
    state_dir.mkdir(parents=True)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "review"}}})
    monkeypatch.setenv("HOME", str(other_home))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def write_checkpoint(state_dir, name, body):
    path = state_dir / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_stage_single_repo_checkpoint(tmp_path):
    # Why: the dashboard's stage line reads "repo:stage" from the checkpoint
    # whose repos dict names this cwd's repo; the basic lookup must work.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "review"}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def test_stage_multi_repo_checkpoint_lists_all_repos(tmp_path):
    # Why: a multi-repo story's card should show the whole pipeline picture,
    # not just this session's own repo — every entry joins in insertion order,
    # one per line so the dashboard can render each repo's state separately.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "review"},
                                "other-repo": {"stage": "testing"}}})
    assert (client.get_dev_workflow_stage("/home/user/my-project")
            == "my-project:review\nother-repo:testing")


def test_stage_no_matching_checkpoint_returns_empty(tmp_path):
    # Why: sessions outside any dev-workflow story must get "" (never None,
    # never a raise) so the payload stays valid and the card shows two lines.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"unrelated": {"stage": "review"}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""
    payload = client.build_event_payload("s", "/home/user/my-project", "working")
    assert payload["stage"] == ""


def test_stage_missing_state_dir_returns_empty():
    # Why: hosts that never ran dev-workflow have no state dir at all; the
    # lookup must treat that as "no checkpoints", not an error.
    client = load_client()
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""


def test_stage_malformed_file_skipped_without_raise(tmp_path):
    # Why: checkpoint files are written by another tool and may be corrupt
    # mid-write; one bad file must not abort the scan or break the hook.
    client, state_dir = make_stage_client(tmp_path)
    (state_dir / "broken.json").write_text("{not json", encoding="utf-8")
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "development"}}})
    assert (client.get_dev_workflow_stage("/home/user/my-project")
            == "my-project:development")


def test_stage_most_recent_matching_checkpoint_wins(tmp_path):
    # Why: a repo can appear in several stories' checkpoints; the freshest
    # file (mtime) reflects the pipeline actually running now.
    client, state_dir = make_stage_client(tmp_path)
    old = write_checkpoint(state_dir, "story-old.json",
                           {"repos": {"my-project": {"stage": "spec"}}})
    new = write_checkpoint(state_dir, "story-new.json",
                           {"repos": {"my-project": {"stage": "review"}}})
    now = time.time()
    os.utime(old, (now - 300, now - 300))
    os.utime(new, (now - 200, now - 200))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"
    os.utime(old, (now - 100, now - 100))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:spec"


def test_stage_non_dict_repos_skipped(tmp_path):
    # Why: a checkpoint whose repos is a list, missing, or whose body is not
    # an object doesn't match the schema — skip it, don't crash on it.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "list-repos.json", {"repos": ["my-project"]})
    write_checkpoint(state_dir, "no-repos.json", {"other": 1})
    write_checkpoint(state_dir, "non-dict.json", ["my-project"])
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""
    write_checkpoint(state_dir, "valid.json",
                     {"repos": {"my-project": {"stage": "review"}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def test_stage_non_string_stage_entry_skipped(tmp_path):
    # Why: an entry with a missing or non-string stage can't render as
    # "repo:stage"; drop that entry but keep the rest of the winner's repos.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "review"},
                                "numeric": {"stage": 3},
                                "stageless": {}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def test_stage_own_repo_without_valid_stage_does_not_match(tmp_path):
    # Why: a checkpoint naming this repo with no valid stage of its own must
    # not match just because some other repo in the same checkpoint does —
    # the rendered line would otherwise name an unrelated repo, not this one.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {}, "other-repo": {"stage": "testing"}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""


def test_stage_newer_checkpoint_without_valid_own_stage_falls_through(tmp_path):
    # Why: a checkpoint whose own-repo entry has no valid stage string must
    # not shadow an older checkpoint that does — the match predicate and the
    # render join must agree on what counts as a valid stage.
    client, state_dir = make_stage_client(tmp_path)
    newer = write_checkpoint(state_dir, "newer.json",
                             {"repos": {"my-project": {"stage": 3}}})
    older = write_checkpoint(state_dir, "older.json",
                             {"repos": {"my-project": {"stage": "review"}}})
    now = time.time()
    os.utime(newer, (now - 10, now - 10))
    os.utime(older, (now - 100, now - 100))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def test_stage_worktree_cwd_matches_repo_checkpoint(tmp_path):
    # Why: pipeline sessions run inside <repo>/.worktrees/<branch>, so the cwd
    # basename is never the repo name; the directory containing .worktrees
    # must also be tried or the stage line stays blank for its main audience.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-repo": {"stage": "review"}}})
    assert (client.get_dev_workflow_stage("/home/user/my-repo/.worktrees/sc-999")
            == "my-repo:review")
    assert (client.get_dev_workflow_stage(
                "/home/user/my-repo/.worktrees/sc-999/plugins/foo")
            == "my-repo:review")


def test_stage_worktree_cwd_without_match_returns_empty(tmp_path):
    # Why: the worktree fallback widens the candidate list, not the match rule;
    # a checkpoint naming neither the cwd basename nor the repo stays ignored.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"unrelated": {"stage": "review"}}})
    assert client.get_dev_workflow_stage("/home/user/my-repo/.worktrees/sc-999") == ""


def test_stage_empty_cwd_returns_empty(tmp_path):
    # Why: hooks can fire with no cwd; there is no repo to match, so the
    # lookup must short-circuit to "" instead of guessing.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "review"}}})
    assert client.get_dev_workflow_stage("") == ""
    assert client.get_dev_workflow_stage(None) == ""


def test_stage_terminal_match_skipped_falls_back_to_older_active_checkpoint(tmp_path):
    # Why: a repo's newest checkpoint can be terminal (story merged/closed)
    # while an older checkpoint still names it in an active pipeline; the
    # line should reflect the running pipeline, not the terminal one.
    client, state_dir = make_stage_client(tmp_path)
    active = write_checkpoint(state_dir, "story-active.json",
                              {"repos": {"my-project": {"stage": "review"}}})
    done = write_checkpoint(state_dir, "story-done.json",
                            {"repos": {"my-project": {"stage": "done"}}})
    now = time.time()
    os.utime(active, (now - 200, now - 200))
    os.utime(done, (now - 100, now - 100))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"


def test_stage_all_terminal_matches_returns_empty(tmp_path):
    # Why: once every checkpoint naming this repo is terminal, the pipeline
    # is over and the stage line must go blank rather than show stale state.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "done"}}})
    write_checkpoint(state_dir, "story-2.json",
                     {"repos": {"my-project": {"stage": "finished"}}})
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""


def test_stage_checkpoint_older_than_max_age_ignored(tmp_path):
    # Why: an unpruned state dir keeps checkpoints indefinitely; a match must
    # not resurrect a pipeline that stopped updating long ago.
    client, state_dir = make_stage_client(tmp_path)
    stale = write_checkpoint(state_dir, "story-1.json",
                             {"repos": {"my-project": {"stage": "review"}}})
    old_time = time.time() - client.STAGE_MAX_AGE_SECONDS - 60
    os.utime(stale, (old_time, old_time))
    assert client.get_dev_workflow_stage("/home/user/my-project") == ""


def test_stage_scan_is_stat_first_and_short_circuits_on_match(tmp_path, monkeypatch):
    # Why: the scan must sort by mtime from stat() alone and stop reading
    # files at the first match, rather than parsing every checkpoint first.
    client, state_dir = make_stage_client(tmp_path)
    now = time.time()
    for i in range(5):
        path = write_checkpoint(state_dir, f"unrelated-{i}.json",
                                {"repos": {"other-repo": {"stage": "spec"}}})
        os.utime(path, (now - 500 + i, now - 500 + i))
    winner = write_checkpoint(state_dir, "winner.json",
                              {"repos": {"my-project": {"stage": "review"}}})
    os.utime(winner, (now - 10, now - 10))

    read_calls = []
    original_read_text = Path.read_text

    def tracking_read_text(self, *args, **kwargs):
        read_calls.append(self.name)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:review"
    # Only the newest (matching) file should have been parsed.
    assert read_calls == ["winner.json"]


def test_stage_newer_non_matching_checkpoint_does_not_shadow_older_match(tmp_path):
    # Why: mtime-descending scan order must not stop at the first file it
    # sees — a newer checkpoint with no match for this repo must be skipped
    # so an older checkpoint that does match still wins.
    client, state_dir = make_stage_client(tmp_path)
    now = time.time()
    newest_non_matching = write_checkpoint(
        state_dir, "newest.json", {"repos": {"other-repo": {"stage": "spec"}}})
    matching_older = write_checkpoint(
        state_dir, "older-match.json",
        {"repos": {"my-project": {"stage": "review"},
                   "other-repo": {"stage": "testing"}}})
    even_older_match = write_checkpoint(
        state_dir, "oldest-match.json",
        {"repos": {"my-project": {"stage": "spec"}}})
    os.utime(newest_non_matching, (now - 10, now - 10))
    os.utime(matching_older, (now - 20, now - 20))
    os.utime(even_older_match, (now - 30, now - 30))
    assert (client.get_dev_workflow_stage("/home/user/my-project")
            == "my-project:review\nother-repo:testing")


def test_stage_stale_checkpoint_shadowed_by_fresh_init_seed(tmp_path):
    # Why: a standalone stage skill seeds a fresh "init" checkpoint entry so
    # the dashboard never shows a stale stage left by an earlier, unrelated
    # run for the same repo — this locks in the freshest-mtime-wins behavior
    # that seeding depends on, proving "init" is treated like any other
    # non-terminal stage rather than specially excluded. The stale checkpoint
    # is set to 1 day old (well inside STAGE_MAX_AGE_SECONDS' 7-day window),
    # not exactly 7 days, so the age filter can never be what drops it —
    # only mtime-ordering can, which is the behavior this test exists to prove.
    client, state_dir = make_stage_client(tmp_path)
    stale = write_checkpoint(state_dir, "story-old.json",
                             {"repos": {"my-project": {"stage": "developing"}}})
    fresh_init = write_checkpoint(state_dir, ".pending-123-456.json",
                                  {"repos": {"my-project": {"stage": "init"}}})
    now = time.time()
    os.utime(stale, (now - 86400, now - 86400))
    os.utime(fresh_init, (now, now))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:init"


def test_stage_stale_pending_placeholder_is_ignored_even_within_stage_window(tmp_path):
    # Why: an abandoned creating-stories interview leaves its ".pending-*.json"
    # placeholder behind with no story ID to key a real checkpoint by, and
    # nothing else prunes it — so a pending file must go stale on its own,
    # tighter cutoff (PENDING_PLACEHOLDER_MAX_AGE_SECONDS) well before the
    # general 7-day STAGE_MAX_AGE_SECONDS window would otherwise let it keep
    # shadowing a real, older-but-still-live checkpoint for a full week.
    client, state_dir = make_stage_client(tmp_path)
    real = write_checkpoint(state_dir, "story-real.json",
                            {"repos": {"my-project": {"stage": "developing"}}})
    orphaned_pending = write_checkpoint(state_dir, ".pending-999-111.json",
                                        {"repos": {"my-project": {"stage": "init"}}})
    now = time.time()
    # Older than PENDING_PLACEHOLDER_MAX_AGE_SECONDS (1 hour) but still newer
    # than "real" and well inside the general 7-day window.
    os.utime(orphaned_pending, (now - 7200, now - 7200))
    os.utime(real, (now - 86400, now - 86400))
    assert client.get_dev_workflow_stage("/home/user/my-project") == "my-project:developing"


def test_payload_always_includes_stage_key():
    # Why: the store treats stage as non-sticky, so every payload must carry
    # the key — even "" — or a stale stage would linger on the card.
    client = load_client()
    payload = client.build_event_payload("s", "/srv/app", "working")
    assert payload["stage"] == ""


def test_payload_carries_matching_stage(tmp_path):
    # Why: the stage line is only visible if the payload actually delivers the
    # looked-up value to the hub on ordinary state events.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "story-1.json",
                     {"repos": {"my-project": {"stage": "development"}}})
    payload = client.build_event_payload("s", "/home/user/my-project", "working")
    assert payload["stage"] == "my-project:development"


# --- Story title lookup ---

import pytest

from tests.story_name_cases import GROUPED_NAMES, UNGROUPED_NAMES


@pytest.mark.parametrize("name,story_id,role,repo,label", GROUPED_NAMES)
def test_story_id_from_name_matches_hub_rule(name, story_id, role, repo, label):
    # Why: the client looks a title up by the same story id the hub groups by;
    # both run this shared table so the two copies of the rule cannot drift.
    assert load_client().story_id_from_name(name) == story_id


@pytest.mark.parametrize("name", UNGROUPED_NAMES)
def test_story_id_from_name_rejects_non_matching_names(name):
    # Why: names the hub never groups must never trigger a checkpoint read.
    assert load_client().story_id_from_name(name) == ""


def test_story_title_from_seeded_checkpoint(tmp_path):
    # Why: the checkpoint is the title's only source; a seeded one must be read.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "sc-1000.json",
                     {"story_id": "sc-1000", "story_title": "Group sessions",
                      "repos": {"proj": {"stage": "developing"}}})
    assert client.get_story_title("sc-1000") == "Group sessions"


def test_story_title_ignores_age_and_finished_stage(tmp_path):
    # Why: the title is static story information, so neither the stage
    # lookup's age cutoff nor a terminal stage may hide it.
    client, state_dir = make_stage_client(tmp_path)
    path = write_checkpoint(state_dir, "sc-1000.json",
                            {"story_title": "Old story",
                             "repos": {"proj": {"stage": "done"}}})
    old = time.time() - 60 * 24 * 60 * 60
    os.utime(path, (old, old))
    assert client.get_story_title("sc-1000") == "Old story"


@pytest.mark.parametrize("body", [
    {"repos": {}},
    {"story_title": ""},
    {"story_title": 42},
    ["not", "an", "object"],
])
def test_story_title_empty_without_usable_title(tmp_path, body):
    # Why: checkpoints from older writers have no title; that is "no title".
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "sc-1000.json", body)
    assert client.get_story_title("sc-1000") == ""


def test_story_title_missing_file_and_corrupt_json(tmp_path):
    # Why: a missing or half-written checkpoint must never fail a hook.
    client, state_dir = make_stage_client(tmp_path)
    assert client.get_story_title("sc-1000") == ""
    (state_dir / "sc-2000.json").write_text("{not json", encoding="utf-8")
    assert client.get_story_title("sc-2000") == ""


@pytest.mark.parametrize("unsafe", ["../secret", "..", "a/b", "sc-1\x00", "", None])
def test_story_title_rejects_unsafe_story_id(tmp_path, unsafe):
    # Why: the id becomes a path component; a traversal payload must never be
    # read, even though a grouped name can never produce one.
    client, state_dir = make_stage_client(tmp_path)
    (tmp_path / ".claude" / "dev-workflow" / "secret.json").write_text(
        json.dumps({"story_title": "leaked"}), encoding="utf-8")
    assert client.get_story_title(unsafe) == ""


def test_payload_includes_story_title_for_grouped_name(tmp_path):
    # Why: the hub can only show the header title if the event carries it.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "sc-1000.json", {"story_title": "Group sessions"})
    payload = client.build_event_payload("s1", "/tmp/proj", "working",
                                         session_name="sc-1000-developer")
    assert payload["story_title"] == "Group sessions"


def test_payload_omits_story_title_when_empty_or_unmatched(tmp_path):
    # Why: an empty field would be noise, and an ungrouped name must not be
    # looked up at all.
    client, state_dir = make_stage_client(tmp_path)
    write_checkpoint(state_dir, "sc-1000.json", {"repos": {}})
    payload = client.build_event_payload("s1", "/tmp/proj", "working",
                                         session_name="sc-1000-developer")
    assert "story_title" not in payload
    with patch.object(client, "get_story_title", side_effect=AssertionError("looked up")):
        payload = client.build_event_payload("s1", "/tmp/proj", "working",
                                             session_name="notes")
    assert "story_title" not in payload


def test_payload_never_raises_when_title_lookup_fails(tmp_path):
    # Why: a reporting hook must never error an agent session.
    client = load_client()
    with patch.object(client.Path, "home", side_effect=RuntimeError("boom")):
        assert client.get_story_title("sc-1000") == ""
