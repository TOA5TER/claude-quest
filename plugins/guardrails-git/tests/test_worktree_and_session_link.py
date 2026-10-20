"""End-to-end checks of the worktree placement and session-link rules, run through the hook as a subprocess."""

import atexit
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "guardrails_git_pre_tool_use.py"

LINK_TEXT = "claude.ai/" + "code/session"
LINK_URL = "https://" + LINK_TEXT + "_01Abc"
TRAILER = "Claude-Session: " + LINK_URL
BAD_WORKTREE = "git worktree add ../x"
SEPARATORS = ["&&", "||", ";", "|", "&", "\n"]
PREFIXES = ["", "PERSONA=developer "]


def make_repo(tmpdir, origin_url):
    subprocess.run(["git", "init", "-q", tmpdir], check=True)
    subprocess.run(["git", "-C", tmpdir, "symbolic-ref", "HEAD", "refs/heads/feature/test"], check=True)
    if origin_url:
        subprocess.run(["git", "-C", tmpdir, "remote", "add", "origin", origin_url], check=True)
    return tmpdir


_REPO = []


def repo():
    if not _REPO:
        tmp = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, tmp, True)
        make_repo(tmp, "https://github.com/example-org/example-repo.git")
        _REPO.append(tmp)
    return _REPO[0]


def run_hook(command, cwd=None, timeout=900000):
    cwd = cwd or repo()
    tool_input = {"command": command}
    if timeout is not None:
        tool_input["timeout"] = timeout
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": tool_input, "cwd": cwd}),
        capture_output=True, text=True, cwd=cwd,
    )
    return json.loads(result.stdout.strip()) if result.stdout.strip() else {}


def blocks(command):
    return run_hook(command).get("decision") == "block"


def assert_all_blocked(commands):
    wrong = [c for c in commands if not blocks(c)]
    assert not wrong, f"Expected block for: {wrong}"


def assert_all_allowed(commands):
    wrong = [c for c in commands if blocks(c)]
    assert not wrong, f"Expected allow for: {wrong}"


def chain(first, sep, second):
    return f"{first} {sep} {second}" if sep != "\n" else f"{first}\n{second}"


def load_hook():
    spec = importlib.util.spec_from_file_location("hook_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Why: a sibling or otherwise misplaced worktree must be refused whatever path form or option order is used.
def test_worktree_outside_worktrees_folder_blocked():
    paths = ["../repo-sc-1701", "/tmp/x", "~/x", "worktrees/x", ".worktree/x", "x.worktrees/y",
             "../.worktrees-old/x", "../.worktrees/x", "../../.worktrees/x", ".worktrees/../x"]
    assert_all_blocked([f"git worktree add {p}" for p in paths] + [
        "git worktree add -b feature/x ../wt main",
        "git worktree add ../wt -b feature/x",
        "git worktree add --detach ../wt HEAD",
        "git worktree add -f ../wt",
        "git worktree add -B x ../wt",
        "git worktree add --reason why ../wt",
        "git -C repo worktree add ../x",
        "git -c k=v worktree add ../x",
        "git --no-pager worktree add ../x",
        "PERSONA=developer gitp worktree add ../x",
        "git-as-app.sh developer worktree add ../x",
        "git worktree add $path -b b",
        'git worktree add "$LOCATION/$BRANCH"',
        'git worktree add "$HOME/wt"',
    ])


# Why: the rule must hold when the command rides behind any separator, with or without a persona prefix.
def test_worktree_chained_blocked():
    assert_all_blocked(
        [chain("cd repo", sep, pre + BAD_WORKTREE) for pre in PREFIXES for sep in SEPARATORS]
        + [chain(pre + BAD_WORKTREE, sep, "echo done") for pre in PREFIXES for sep in SEPARATORS]
    )


# Why: nesting and quoting must not hide a misplaced worktree add from the hook.
def test_worktree_nested_and_quoted_blocked():
    assert_all_blocked([
        "(git worktree add ../x)",
        "echo $(git worktree add ../x)",
        "echo `git worktree add ../x`",
        "{ git worktree add ../x; }",
        "if true; then git worktree add ../x; fi",
        "bash -c 'git worktree add ../x'",
        'bash -c "cd r && git worktree add ../x"',
        '"git" worktree add ../x',
        'git worktree add "../x"',
        "git worktree add '../x y'",
        "bash <<'EOF'\ngit worktree add ../x\nEOF",
        "cat <<'EOF' | bash\ngit worktree add ../x\nEOF",
    ])


# Why: the sanctioned location must stay usable in every spelling the developer actually writes.
def test_worktree_under_worktrees_folder_allowed():
    assert_all_allowed([
        "git worktree add .worktrees/sc-1701 -b feature/x",
        "git worktree add -b feature/x .worktrees/sc-1701 main",
        "git worktree add --detach .worktrees/sc-1701 HEAD",
        "git worktree add /abs/repo/.worktrees/sc-1701",
        "git -C /abs/repo worktree add /abs/repo/.worktrees/sc-1701 -b x",
        'git worktree add "$(git rev-parse --show-toplevel)/.worktrees/sc-1701"',
        'git worktree add "$PWD/.worktrees/sc-1701"',
        "cd repo && git worktree add .worktrees/x",
        "PERSONA=developer gitp worktree add .worktrees/x",
        "bash -c 'git worktree add .worktrees/x'",
    ])


# Why: only worktree add is restricted; listing and cleanup, and mere mentions, must keep working.
def test_other_worktree_commands_and_mentions_allowed():
    assert_all_allowed([
        "git worktree list",
        "git worktree list --porcelain",
        "git -C r worktree list --porcelain",
        "git worktree remove ../old",
        "git worktree prune",
        'grep -rn "worktree add" plugins/',
        'ghp pr comment 5 --body "never git worktree add ../x"',
        'PERSONA=developer gitp commit -m "never git worktree add ../x"',
        'git commit -m "never git worktree add ../x"',
    ])


# Why: argument text of wrapper calls is data, but a command substitution inside it still executes.
def test_worktree_text_in_wrapper_arguments_and_heredocs():
    prose = "use git worktree add ../x here"
    assert_all_allowed([
        f"cat <<'EOF' | PERSONA=developer ghp pr create --title t --body-file -\n{prose}\nEOF",
        f"PERSONA=developer ghp pr create --title t --body-file - <<'EOF'\n{prose}\nEOF",
        f"PERSONA=developer ghp pr create --title t --body-file - <<EOF\n{prose}\nEOF",
        f"PERSONA=developer ghp pr create --title t --body \"$(cat <<'EOF'\n{prose}\nEOF\n)\"",
    ])
    assert_all_blocked([
        f"PERSONA=developer ghp pr create --title t --body-file - <<EOF\nprose\n$({BAD_WORKTREE})\nEOF",
        f"PERSONA=developer ghp pr create --title t --body-file - <<EOF\nprose\n`{BAD_WORKTREE}`\nEOF",
    ])


COMMIT_AND_PR_WITH_LINK = [
    f'git commit -m "feat: x\n\n{TRAILER}"',
    f'git commit -m "feat: x" -m "{TRAILER}"',
    f'git commit --amend -m "x {LINK_URL}"',
    f'git -C repo commit -m "x {LINK_URL}"',
    f'PERSONA=developer gitp commit -m "x {LINK_URL}"',
    f"git commit -F - <<'EOF'\nx\n\n{TRAILER}\nEOF",
    f"git commit -m \"$(cat <<'EOF'\nx\n{TRAILER}\nEOF\n)\"",
    f'PERSONA=developer ghp pr create --title t --body "x {LINK_URL}"',
    f'PERSONA=developer ghp pr create --title "{LINK_URL}" --body b',
    f"PERSONA=developer ghp pr create --title t --body-file - <<'EOF'\n{LINK_URL}\nEOF",
    f"cat <<'EOF' | PERSONA=developer ghp pr create --title t --body-file -\n{LINK_URL}\nEOF",
    f'PERSONA=developer ghp pr edit 5 --body "{LINK_URL}"',
    f'gh pr edit 5 --body "{LINK_URL}"',
    f'PERSONA=developer ghp api repos/o/r/pulls/5 -X PATCH -f body="{LINK_URL}"',
    f'PERSONA=developer ghp api repos/o/r/pulls -X POST -f body="{LINK_URL}"',
    f'bash -c "git commit -m \'{LINK_URL}\'"',
    f'bash -c "gh pr create --title t --body \'{LINK_URL}\'"',
    f'echo $(git commit -m "{LINK_URL}")',
]


# Why: the commit and PR text paths are where a session link actually leaked into history.
def test_session_link_in_commit_or_pr_text_blocked():
    assert_all_blocked(COMMIT_AND_PR_WITH_LINK)


# Why: the match is on the link text itself, so case and scheme variants must not slip past.
def test_session_link_variants_blocked():
    assert_all_blocked([
        'git commit -m "x ' + variant + '"'
        for variant in (LINK_TEXT.upper() + "_01", "http://" + LINK_TEXT + "_01", LINK_TEXT + "_01")
    ])


# Why: the link may be written by an earlier part of the command and committed from a file.
def test_session_link_chained_blocked():
    write = f'echo "{LINK_URL}" > msg.txt'
    assert_all_blocked([chain(write, sep, "git commit -F msg.txt") for sep in SEPARATORS]
                       + [chain(write, sep, "PERSONA=developer ghp pr create --body-file msg.txt") for sep in SEPARATORS])


# Why: the new rules do not depend on the origin, so a foreign or missing origin must not switch them off.
def test_new_rules_apply_with_foreign_and_missing_origin():
    with tempfile.TemporaryDirectory() as foreign, tempfile.TemporaryDirectory() as bare:
        make_repo(foreign, "git@github.com:example-org/other-repo.git")
        make_repo(bare, None)
        for target in (foreign, bare):
            for cmd in (BAD_WORKTREE, f'gh pr create --title t --body "{LINK_URL}"',
                        f'bash -c "git commit -m \'{LINK_URL}\'"'):
                assert run_hook(cmd, cwd=target).get("decision") == "block", f"{cmd!r} must block in {target}"
            for cmd in ("git worktree add .worktrees/x", "gh pr create --title t --body ok"):
                assert run_hook(cmd, cwd=target).get("decision") != "block", f"{cmd!r} must stay allowed in {target}"


# Why: ordinary commits and PRs, and reads that merely mention the link text, must never be refused.
def test_clean_commit_pr_and_link_mentions_allowed():
    assert_all_allowed([
        'git commit -m "feat: x\n\nSecond paragraph."',
        'PERSONA=developer gitp commit -m "x"',
        'PERSONA=developer ghp pr create --title t --body "plain body"',
        'PERSONA=developer ghp pr edit 5 --body "plain body"',
        'gh pr edit 5 --body "plain body"',
        f'grep -rn "{LINK_TEXT}" plugins/',
        f'echo "{LINK_TEXT}"',
        f'git log --grep="{LINK_TEXT}"',
        "git log --oneline -3",
        f'PERSONA=reviewer ghp pr comment 5 --body "found {LINK_URL} in a commit"',
        f'PERSONA=reviewer ghp pr review 5 --request-changes --body "found {LINK_URL}"',
        f'PERSONA=reviewer ghp api repos/o/r/pulls/5/comments -f body="{LINK_URL}"',
        f'PERSONA=reviewer ghp api repos/o/r/pulls/5/reviews --field body="{LINK_URL} repos/o/r/pulls/5"',
    ])
    assert_all_blocked([
        f'PERSONA=reviewer ghp api repos/o/r/issues/5/comments -f body="{LINK_URL}"',
        f'PERSONA=reviewer ghp api repos/o/r/issues/5/comments -f body="{LINK_URL} https://api.github.com/repos/o/r/pulls/5"',
        f'PERSONA=reviewer ghp api -X POST -f body="{LINK_URL} repos/o/r/pulls/5" repos/o/r/issues/5/comments',
        f'PERSONA=developer ghp api -X PATCH -f body="{LINK_URL}" repos/o/r/pulls/5',
        f'PERSONA=developer ghp api --method POST --field body="{LINK_URL}" repos/o/r/pulls',
    ])


# Why: a parse failure must fall back to a text scan, not silently turn the new rules off.
def test_new_rules_survive_unparseable_commands():
    assert_all_blocked([
        'echo "oops; ' + BAD_WORKTREE,
        'echo "oops; git commit -m ' + LINK_URL,
        "echo ')' ; " + BAD_WORKTREE + " )",
    ])
    assert_all_allowed(['echo "oops; git status', 'echo "oops; git worktree add .worktrees/x'])


# Why: a new check whose parsing raises must block with its own message instead of failing open.
def test_new_checks_fail_closed_when_parsing_raises(capsys):
    hook = load_hook()

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    hook.ShellParser = boom
    hook.text_runs_misplaced_worktree = boom
    hook.text_runs_publish_command = boom
    assert hook.command_matches("git status", hook.worktree_add_at, ("git",), boom) is True
    for check, command in ((hook.check_worktree_placement, "git status"),
                           (hook.check_session_link_in_publish, f"echo {LINK_TEXT}")):
        try:
            check("Bash", {"command": command})
        except SystemExit:
            assert json.loads(capsys.readouterr().out)["decision"] == "block"
        else:
            raise AssertionError(f"{check.__name__} must block when its parsing raises")


# Why: each new block must tell the agent what to do instead.
def test_new_block_messages():
    wt = run_hook(BAD_WORKTREE)
    link = run_hook(f'bash -c "git commit -m \'{LINK_URL}\'"')
    assert wt.get("decision") == "block" and ".worktrees/" in wt["reason"], wt
    assert link.get("decision") == "block" and "session link" in link["reason"], link
    assert "Claude-Session:" in link["reason"] and "conflict" in link["reason"], link


# Why: when an existing rule also applies its message must still be the one returned.
def test_existing_block_message_wins_over_new_rules():
    start_point = run_hook("git worktree add ../wt main")
    timeout = run_hook(f'git commit -m "{LINK_URL}"', timeout=None)
    assert "checkout the main branch" in start_point["reason"], start_point
    assert "You must set a timeout" in timeout["reason"], timeout


def hook_decision_within(command, seconds):
    try:
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command, "timeout": 900000},
                              "cwd": repo()}),
            capture_output=True, text=True, cwd=repo(), timeout=seconds)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "block" if '"decision": "block"' in result.stdout else "allow"


# Why: a stalled hook may be treated as an allow, so the new scans must stay fast on long commands.
def test_new_rules_stay_prompt_on_long_commands():
    padded = [
        BAD_WORKTREE + "; : " + "-git " * 8000,
        "git " + "-C d " * 4000 + "worktree add ../x",
        "bash -c 'git " + "-C d " * 4000 + "worktree add ../x'",
        "git commit -m x; : " + "-git " * 8000 + LINK_URL,
        "bash -c 'git " + "-c k=v " * 4000 + "commit " + LINK_URL + "'",
    ]
    results = [(hook_decision_within(c, 10), c[:30]) for c in padded]
    wrong = [r for r in results if r[0] != "block"]
    assert not wrong, f"Expected a prompt block, got: {wrong}"


# Why: a quote-heavy long command once made the text scan quadratic, so it must stay near linear cost.
def test_new_rules_scan_is_fast_on_escaped_quote_commands():
    commands = ["bash -c '" + 'git x\\" ' * 2700 + "'", "bash -c '" + 'git x\\" ' * 2700 + "' " + LINK_TEXT]
    for command in commands:
        start = time.monotonic()
        outcome = hook_decision_within(command, 8)
        elapsed = time.monotonic() - start
        assert outcome != "timeout" and elapsed < 6, f"{len(command)} bytes took {elapsed:.1f}s ({outcome})"


# Why: a crash inside an existing check must not skip the new checks.
def test_new_checks_run_when_an_existing_check_raises(capsys, monkeypatch):
    hook = load_hook()

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    for name in ("add_timeout_to_git_commit", "add_timeout_to_git_push", "add_timeout_to_gh_run_watch",
                 "check_git_commit_branch", "check_git_branch_policy", "check_git_no_verify",
                 "check_git_commit_boilerplate", "check_pr_create_boilerplate", "check_pr_comment_boilerplate"):
        monkeypatch.setattr(hook, name, boom)
    for command in (BAD_WORKTREE, f'git commit -m "x {LINK_URL}"'):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": repo()})))
        try:
            hook.main()
        except SystemExit:
            assert json.loads(capsys.readouterr().out)["decision"] == "block", command
        else:
            raise AssertionError(f"{command} must still be blocked")


# Why: prose that merely mentions a command in an argument of grep, echo or python is not a shell invocation.
def test_mentions_in_non_shell_arguments_allowed():
    assert_all_allowed([
        'grep -rn "git worktree add ../" docs/',
        'echo "run git worktree add ../x later" > notes.md',
        "python3 -c 'print(\"git worktree add ../x\")'",
        f'echo "{LINK_URL} git commit" > notes.md',
        f'grep -rn "git commit -m {LINK_URL}" docs/',
    ])
    assert_all_blocked([
        'bash -c "git worktree add ../x"',
        'echo "$(git worktree add ../x)"',
        'sh -c "echo hi; git worktree add ../x"',
    ])
