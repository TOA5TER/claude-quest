"""
Tests for session-continuation detection in attention_hub_client.continued_from and
for the `supersedes` field the hooks send. Claude Code moves a running conversation
to a new session ID (for example after backgrounding it into agents view and coming
back); the old transcript then ends with a continued-in record and the new transcript
carries the old records with their original session_id.
"""

import importlib.util
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


@pytest.fixture
def client():
    spec = importlib.util.spec_from_file_location(
        "attention_hub_client", HOOKS_DIR / "attention_hub_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_transcript(directory, session_id, copied_from=(), continued_into=None, tail=None):
    """A transcript with records copied from earlier sessions and an optional link."""
    lines = [json.dumps({"type": "mode", "sessionId": session_id})]
    for old in copied_from:
        lines.append(json.dumps({"type": "attachment", "session_id": old, "sessionId": session_id}))
    lines.append(json.dumps({"type": "assistant", "session_id": session_id, "sessionId": session_id}))
    if continued_into is not None:
        lines.append(json.dumps({"type": "continued-in", "sessionId": session_id,
                                 "continuedInSessionId": continued_into}))
    if tail is not None:
        lines.append(tail)
    path = directory / f"{session_id}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_simple_continuation_finds_the_old_session(client, tmp_path):
    # Why: this is the user's trigger: the old transcript links forward and the new
    # one carries its records, so the old ID is the one to retire.
    write_transcript(tmp_path, "old", continued_into="new")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == ["old"]


def test_chain_a_to_b_to_c_finds_both_ancestors(client, tmp_path):
    # Why: C's head only shows A's copied records, so B must be found by following
    # A's link forward; otherwise B's row would stay stuck.
    write_transcript(tmp_path, "a", continued_into="b")
    write_transcript(tmp_path, "b", copied_from=["a"], continued_into="c")
    c = write_transcript(tmp_path, "c", copied_from=["a"])
    assert client.continued_from(c, "c") == ["a", "b"]


def test_copied_records_without_a_link_are_not_a_continuation(client, tmp_path):
    # Why: a fork copies records too while the original keeps running; its row
    # must not be retired just because its ID appears in the copy.
    write_transcript(tmp_path, "original")
    fork = write_transcript(tmp_path, "fork", copied_from=["original"])
    assert client.continued_from(fork, "fork") == []


def test_link_to_a_different_session_is_not_a_continuation(client, tmp_path):
    # Why: the old session continued into someone else, not into this session.
    write_transcript(tmp_path, "old", continued_into="elsewhere")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []


def test_link_must_be_the_last_record(client, tmp_path):
    # Why: a continued-in record followed by more activity means the old session
    # kept going, so it is not retired.
    write_transcript(tmp_path, "old", continued_into="new",
                     tail=json.dumps({"type": "assistant", "sessionId": "old"}))
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []


def test_link_cycle_terminates(client, tmp_path):
    # Why: a corrupt pair of transcripts pointing at each other must not loop.
    write_transcript(tmp_path, "a", continued_into="b")
    write_transcript(tmp_path, "b", copied_from=["a"], continued_into="a")
    c = write_transcript(tmp_path, "c", copied_from=["a"])
    assert client.continued_from(c, "c") == []


def test_chain_longer_than_the_hop_limit_is_not_followed(client, tmp_path):
    # Why: following links is bounded so a long or hostile chain cannot make a hook
    # read an unbounded number of files.
    ids = [f"s{i}" for i in range(client.CONTINUATION_MAX_HOPS + 2)]
    for old, nxt in zip(ids, ids[1:]):
        write_transcript(tmp_path, old, continued_into=nxt)
    last = write_transcript(tmp_path, ids[-1], copied_from=[ids[0]])
    assert client.continued_from(last, ids[-1]) == []


def test_missing_old_transcript_means_no_continuation(client, tmp_path):
    # Why: without the old transcript the link cannot be proven, so nothing is retired.
    new = write_transcript(tmp_path, "new", copied_from=["gone"])
    assert client.continued_from(new, "new") == []


@pytest.mark.parametrize("transcript_path,session_id", [
    (None, "new"), ("", "new"), ("/nonexistent/path.jsonl", "new"), ("TRANSCRIPT", ""), ("TRANSCRIPT", None),
])
def test_missing_inputs_return_nothing_and_do_not_raise(client, tmp_path, transcript_path, session_id):
    # Why: hook inputs can lack a transcript or session ID; the reporter must keep
    # working without a continuation list.
    real = write_transcript(tmp_path, "new")
    path = real if transcript_path == "TRANSCRIPT" else transcript_path
    assert client.continued_from(path, session_id) == []


@pytest.mark.parametrize("tail", ["not json at all", "[]", "{\"type\": \"continued-in\"}",
                                  "{\"type\": \"continued-in\", \"continuedInSessionId\": 7}",
                                  "{\"type\": \"continued-in\", \"continuedInSessionId\": \"\"}"])
def test_malformed_old_transcript_tail_means_no_continuation(client, tmp_path, tail):
    # Why: transcripts are written by another program and can be truncated or odd;
    # anything but a well-formed link is ignored.
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"type": "mode"}) + "\n" + tail + "\n")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []


def test_empty_old_transcript_means_no_continuation(client, tmp_path):
    # Why: a zero-byte file has no last record to read.
    (tmp_path / "old.jsonl").write_text("")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []


@pytest.mark.parametrize("hostile", ["../outside", "a/b", "a\\b", "..", ".", "", "x" * 300, "bad id", "a\x00b"])
def test_hostile_ids_in_copied_records_are_never_used_as_paths(client, tmp_path, hostile):
    # Why: IDs read from a transcript become filenames; a traversal ID must not make
    # the hook open a file outside the transcript directory, even one that links to us.
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outside.jsonl").write_text(json.dumps(
        {"type": "continued-in", "continuedInSessionId": "new"}) + "\n")
    new = write_transcript(project, "new", copied_from=[hostile])
    assert client.continued_from(new, "new") == []


def test_hostile_link_target_is_rejected(client, tmp_path):
    # Why: the link target is also untrusted text and must pass the same ID check.
    write_transcript(tmp_path, "old", continued_into="../new")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []


def test_own_id_in_copied_records_is_ignored(client, tmp_path):
    # Why: a transcript that only mentions its own ID has no earlier session.
    new = write_transcript(tmp_path, "new", copied_from=["new"])
    assert client.continued_from(new, "new") == []


def test_oversized_head_is_read_in_a_bounded_way(client, tmp_path):
    # Why: transcripts reach hundreds of megabytes; the scan must stop at the head.
    write_transcript(tmp_path, "old", continued_into="new")
    path = tmp_path / "new.jsonl"
    filler = json.dumps({"type": "attachment", "blob": "x" * 1000}) + "\n"
    path.write_text(filler * (client.CONTINUATION_HEAD_BYTES // len(filler) + 50)
                    + json.dumps({"session_id": "old", "sessionId": "new"}) + "\n")
    assert client.continued_from(str(path), "new") == []


def run_hook(script, hook_input):
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


@pytest.mark.parametrize("script,extra", [
    ("attention_hub_stop.py", {}),
    ("attention_hub_user_prompt_submit.py", {"prompt": "hello"}),
    ("attention_hub_notification.py", {"notification_type": "permission_prompt"}),
])
def test_hooks_send_the_continued_session_ids(tmp_path, script, extra):
    # Why: the first report from the new session can come from any of these hooks
    # (the live capture's first event was a Stop), and each must retire the old row.
    write_transcript(tmp_path, "old", continued_into="new")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    events = run_hook(script, {"session_id": "new", "transcript_path": new, "cwd": "/fake/p", **extra})
    assert len(events) == 1
    assert events[0]["supersedes"] == ["old"]


def test_post_tool_use_hook_sends_the_continued_session_ids(tmp_path, marker_home):
    # Why: PostToolUse reports working after a block clears and may also be the first
    # report of a continued session.
    marker_home.mkdir(parents=True)
    (marker_home / "new").touch()
    write_transcript(tmp_path, "old", continued_into="new")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    events = run_hook("attention_hub_post_tool_use.py",
                      {"session_id": "new", "transcript_path": new, "cwd": "/fake/p"})
    assert events[0]["supersedes"] == ["old"]


def test_hooks_omit_the_field_for_an_ordinary_session(tmp_path):
    # Why: no continuation means the payload stays exactly as before.
    plain = write_transcript(tmp_path, "solo")
    events = run_hook("attention_hub_stop.py", {"session_id": "solo", "transcript_path": plain, "cwd": "/fake/p"})
    assert "supersedes" not in events[0]


def test_stop_hook_still_reports_when_the_transcript_is_unreadable(tmp_path):
    # Why: continuation detection is best-effort and must never block the state report.
    events = run_hook("attention_hub_stop.py",
                      {"session_id": "s", "transcript_path": str(tmp_path / "missing.jsonl"), "cwd": "/fake/p"})
    assert [e["state"] for e in events] == ["done"]
    assert "supersedes" not in events[0]


import os
import resource
import subprocess
import sys

CHILD = """
import importlib.util, json, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
spec = importlib.util.spec_from_file_location("c", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(json.dumps(mod.continued_from(sys.argv[2], sys.argv[3])))
"""


def continued_from_in_child(transcript, session_id):
    """Run continued_from in a child process with a deadline and a memory cap.

    A regression that blocks on a FIFO or reads a device forever then fails the
    test by timeout instead of hanging or exhausting memory in the test run.
    """
    done = subprocess.run(
        [sys.executable, "-I", "-c", CHILD, str(HOOKS_DIR / "attention_hub_client.py"), transcript, session_id],
        capture_output=True, text=True, timeout=10)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def make_special(directory, name, kind):
    path = directory / f"{name}.jsonl"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        if not Path("/dev/zero").exists():
            pytest.skip("no /dev/zero on this platform")
        os.symlink("/dev/zero", path)
    return path


@pytest.mark.parametrize("kind", ["fifo", "chardev"])
def test_open_regular_rejects_special_files_at_once(client, tmp_path, kind):
    # Why: the guard itself must refuse a FIFO or a symlink to a character device
    # without blocking or reading; checked directly because continued_from swallows errors.
    special = make_special(tmp_path, "special", kind)
    with pytest.raises(OSError):
        client._open_regular(special)


@pytest.mark.parametrize("kind", ["fifo", "chardev"])
def test_special_file_as_old_transcript_returns_quickly_and_retires_nothing(tmp_path, kind):
    # Why: a Stop hook must never hang or exhaust memory on a hostile file planted
    # where an earlier session's transcript would be (a FIFO blocks open, /dev/zero
    # never ends); the child process has a deadline so a regression fails, not hangs.
    make_special(tmp_path, "old", kind)
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert continued_from_in_child(new, "new") == []


@pytest.mark.parametrize("kind", ["fifo", "chardev"])
def test_special_file_beside_a_real_chain_does_not_block_the_real_one(tmp_path, kind):
    # Why: one hostile candidate must not stop a genuine continuation from being
    # found in the same transcript.
    make_special(tmp_path, "evil", kind)
    write_transcript(tmp_path, "old", continued_into="new")
    new = write_transcript(tmp_path, "new", copied_from=["evil", "old"])
    assert continued_from_in_child(new, "new") == ["old"]


@pytest.mark.parametrize("kind", ["fifo", "chardev"])
def test_special_file_as_the_reporters_own_transcript_returns_quickly(tmp_path, kind):
    # Why: the bounded head read of the reporter's own transcript uses the same guard.
    special = make_special(tmp_path, "new", kind)
    assert continued_from_in_child(str(special), "new") == []


def test_symlink_to_a_regular_old_transcript_still_works(client, tmp_path):
    # Why: the guard rejects by file type, not by symlink, so a transcript that is a
    # symlink to a real file keeps working.
    real = tmp_path / "elsewhere"
    real.mkdir()
    write_transcript(real, "old", continued_into="new")
    os.symlink(real / "old.jsonl", tmp_path / "old.jsonl")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == ["old"]


def test_old_transcript_tail_read_is_bounded(client, tmp_path):
    # Why: only the last few kilobytes are read, so a link buried before a huge
    # final record is not found and memory stays bounded.
    path = tmp_path / "old.jsonl"
    link = json.dumps({"type": "continued-in", "continuedInSessionId": "new"})
    path.write_text(link + "\n" + json.dumps({"type": "assistant", "blob": "x" * (client.CONTINUATION_TAIL_BYTES * 3)}) + "\n")
    new = write_transcript(tmp_path, "new", copied_from=["old"])
    assert client.continued_from(new, "new") == []
