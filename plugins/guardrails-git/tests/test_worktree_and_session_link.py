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


COMMIT_WITH_LINK = f'git commit -m "x {LINK_URL}"'
PR_WITH_LINK = f'gh pr create --title t --body "x {LINK_URL}"'
NESTED_WRAPPERS = [
    'timeout 5 bash -c "{}"',
    'timeout -s KILL 5 bash -lc "{}"',
    'env -i bash -c "{}"',
    'env FOO=1 -u BAR bash -c "{}"',
    'sudo -u bob sh -c "{}"',
    'sudo -E bash -c "{}"',
    'nice -n 5 bash -c "{}"',
    'nohup sh -c "{}"',
    "env -S '{}'",
    'su -c "{}"',
    'xargs -P4 -n1 bash -c "{}"',
    'xargs -0 -I % sh -c "{}"',
    'ssh host "{}"',
    "ssh -p 22 -i key user@host '{}'",
    'ssh host sh -c "{}"',
    'ssh -o StrictHostKeyChecking=no host "cd repo && {}"',
    "bash -c 'bash -c \"{}\"'",
    'timeout 5 sudo -u bob env -i bash -c "{}"',
    'docker exec c bash -c "{}"',
]
PREFIXED_DIRECT = [
    "timeout 5 {}",
    "timeout -s KILL -k 2 5 {}",
    "env -i {}",
    "env -u X FOO=1 {}",
    "sudo -u bob {}",
    "sudo -E {}",
    "nice -n 5 {}",
    "ionice -c3 {}",
    "stdbuf -o0 {}",
    "setsid {}",
    "watch {}",
    "find . -name x -exec {} \\;",
    "find . -exec {} {{}} +",
    "find . -execdir {} \\;",
    "find . -ok {} \\;",
    "find . -type d -exec timeout 5 {} \\;",
    "xargs {}",
    "xargs -n1 {}",
    "echo a | xargs {}",
]


# Why: prefixed and nested command forms ran the same blocked command, so each must be refused too.
def test_worktree_blocked_in_prefixed_and_nested_forms():
    assert_all_blocked([form.format(BAD_WORKTREE) for form in NESTED_WRAPPERS + PREFIXED_DIRECT])


def test_session_link_blocked_in_prefixed_and_nested_forms():
    assert_all_blocked([form.format(cmd) for form in NESTED_WRAPPERS + PREFIXED_DIRECT
                        for cmd in (COMMIT_WITH_LINK, PR_WITH_LINK)])


# Why: a correctly placed worktree inside any wrapper form is the supported workflow and must keep working.
def test_placed_worktree_and_clean_publish_allowed_in_prefixed_and_nested_forms():
    placed = "git worktree add .worktrees/x -b fix/x"
    clean = 'git commit -m "feat: x"'
    assert_all_allowed([form.format(cmd) for form in NESTED_WRAPPERS + PREFIXED_DIRECT for cmd in (placed, clean)])


STDIN_WORKTREE_BLOCKED = [
    "echo ../x | xargs git worktree add",
    "echo ../x main | xargs git worktree add",
    "echo -b br ../x | xargs git worktree add",
    "printf '%s\\n' ../x | xargs -n1 git worktree add",
    "echo x | xargs -I{} git worktree add ../{}",
    "echo x | xargs -I % git worktree add ../%",
    "echo x | xargs -i git worktree add ../{}",
    "echo x | xargs -I{} git worktree add {}",
    "cat paths.txt | xargs git worktree add",
    "ls | xargs -I{} git worktree add -b br {}",
    "echo ../x | xargs bash -c 'git worktree add ../x'",
    'echo "git worktree add ../x" | bash',
    "echo 'git worktree add ../x' | sh -s",
    "echo 'git worktree add ../x' | ssh host",
    "printf '%s\\n' 'cd r' 'git worktree add ../x' | bash",
    "cat <<'EOF' | ssh host\ngit worktree add ../x\nEOF",
    "ssh host <<EOF\ngit worktree add ../x\nEOF",
]
STDIN_WORKTREE_ALLOWED = [
    "echo .worktrees/x | xargs git worktree add",
    "echo .worktrees/x main | xargs git worktree add",
    "echo x | xargs -I{} git worktree add .worktrees/{}",
    "echo x | xargs -I % git worktree add .worktrees/% -b b-%",
    "printf '%s\\n' .worktrees/a .worktrees/b | xargs -n1 git worktree add",
    "echo x | xargs git worktree add .worktrees/x",
    "echo 'git worktree add .worktrees/x' | bash",
    "ssh host <<EOF\ngit worktree add .worktrees/x\nEOF",
    "cat <<'EOF' | ssh host\ngit status\nEOF",
    "echo .worktrees/x | xargs git worktree list",
    "echo x | xargs git status",
    "ls | xargs git status",
]


# Why: the path of a stdin-fed worktree add is only known when the producer is a literal echo or printf.
def test_stdin_fed_worktree_forms():
    assert_all_blocked(STDIN_WORKTREE_BLOCKED)
    assert_all_allowed(STDIN_WORKTREE_ALLOWED)


STDIN_LINK_BLOCKED = [
    f'echo "{LINK_URL}" | xargs git commit -m',
    f'echo "{LINK_URL}" | xargs -I{{}} git commit -m {{}}',
    f'echo "{LINK_URL}" | xargs -I{{}} sh -c "git commit -m {{}}"',
    f'printf "%s\\n" "{LINK_URL}" | xargs -n1 git commit -m',
    f'echo "{LINK_URL}" | xargs gh pr edit 5 --body',
    f'echo "git commit -m {LINK_URL}" | bash',
    f'echo "gh pr create --title t --body {LINK_URL}" | ssh host',
    f"ssh host <<EOF\ngit commit -m '{LINK_URL}'\nEOF",
    f"xargs git commit -m <<< '{LINK_URL}'",
]


def test_stdin_fed_session_link_forms():
    assert_all_blocked(STDIN_LINK_BLOCKED)


NESTED_LOOKALIKES = [
    'echo "git worktree add ../x"',
    "echo 'git worktree add ../x' | xargs echo",
    'grep "git worktree add ../x" notes.md',
    'timeout 5 grep "git worktree add ../x" notes.md',
    'timeout 5 bash -c \'echo "git worktree add ../x"\'',
    'sudo -u bob sh -c \'grep "git worktree add ../x" f\'',
    'env -i bash -c "echo git worktree add ../x"',
    'ssh host \'echo "git worktree add ../x"\'',
    'ssh host "echo git worktree add ../x"',
    'ssh git@host "ls -la"',
    "ssh host git status",
    'find . -exec grep "git worktree add ../x" {} \\;',
    "find . -name git -exec ls {} \\;",
    f'find . -name "git commit {LINK_URL}"',
    'xargs grep -l "git worktree add ../x"',
    "xargs echo git worktree add ../x",
    f'echo "{LINK_URL}" | xargs grep -c x',
    f'echo "{LINK_URL}" | xargs git log --grep',
    f'timeout 5 grep -rn "{LINK_TEXT}" docs/',
    f'sudo echo "git commit -m {LINK_URL}"',
    f'ssh host "echo {LINK_URL}"',
    f'echo "{LINK_URL}" | ssh host "cat > notes.md"',
    f'echo "{LINK_URL}" | bash -c "cat > notes.md"',
    "sudo -u git ls",
    "env -i grep git notes.md",
]


# Why: wrapper prefixes must not turn prose or data arguments into commands.
def test_quoted_mentions_behind_prefixes_allowed():
    assert_all_allowed(NESTED_LOOKALIKES)


# Why: an unquoted or word-split mention in echo/printf arguments is text, not a command at command position.
def test_unquoted_mentions_in_data_commands_allowed():
    assert_all_allowed([
        "echo git worktree add ../x",
        'echo "mention" git "worktree" add ../x',
        "echo $(echo git worktree add ../x)",
        'echo "$(echo git worktree add ../x)"',
        "printf '%s\\n' git worktree add ../x",
    ])
    assert_all_blocked([
        "echo hi; git worktree add ../x",
        "echo $(git worktree add ../x)",
        f"echo hi; git commit -m {LINK_URL}",
    ])


# Why: the older commit-boilerplate check still answers unquoted commit text at hook level, so the session-link rule is checked directly.
def test_session_link_rule_ignores_unquoted_mentions_in_data_commands():
    hook = load_hook()
    for command in (f"echo hi; echo git commit -m {LINK_URL}", f"echo git commit -m {LINK_URL} && ls",
                    f"printf '%s' git commit {LINK_URL}", f'echo "{LINK_URL}" | xargs echo git commit'):
        assert not hook.carries_session_link_in_commit_or_pr(command), command
    for command in (f"echo hi; git commit -m {LINK_URL}", f'echo "{LINK_URL}" | xargs git commit -m'):
        assert hook.carries_session_link_in_commit_or_pr(command), command


# Why: unknown front-ends keep the conservative scan so a wrapped command is not missed.
def test_unknown_command_prefixes_still_scanned():
    assert_all_blocked([
        "docker exec c git worktree add ../x",
        "kubectl exec pod -- git worktree add ../x",
        f'docker exec c git commit -m "{LINK_URL}"',
    ])


# Why: a pipe followed by || or && must not leak one command's output into the next as stdin.
def test_pipe_producer_tracking_ignores_logical_operators():
    assert_all_allowed([
        "echo ../x || xargs git worktree add .worktrees/x",
        "echo ../x && xargs git status",
    ])
    hook = load_hook()
    segments = hook.ShellParser("echo a | xargs git status || echo b | cat; ls").parse()
    producers = [seg.producer.words[0] if seg.producer else None for seg in segments]
    assert producers == [None, "echo", None, "echo", None], producers


# Why: deep nesting must stay bounded and fail closed instead of recursing without limit.
def test_deep_nesting_stays_bounded_and_blocks():
    deep = "timeout 5 " * 20 + BAD_WORKTREE
    nested = "echo " + "$(echo " * 12 + BAD_WORKTREE + ")" * 12
    assert hook_decision_within(deep, 10) == "block"
    assert hook_decision_within(nested, 10) == "block"


SHELL_OPTION_FORMS = [
    'bash -o pipefail -c "{}"',
    'bash -o pipefail -o errexit -c "{}"',
    'bash +o history -c "{}"',
    'bash -O extglob -c "{}"',
    'bash +O nullglob -lc "{}"',
    'bash -eo pipefail -c "{}"',
    'sh -eu -c "{}"',
    'sh -euc "{}"',
    'bash --norc --noprofile -c "{}"',
    'bash --rcfile rc -c "{}"',
    'zsh -o errexit -c "{}"',
    'timeout 5 bash -o pipefail -c "{}"',
    'busybox sh -c "{}"',
    'ash -c "{}"',
    "env --split-string='{}'",
    "env -S'{}'",
    'bash -c -- "{}"',
    'flock /tmp/l -c "{}"',
    'script -qc "{}" /dev/null',
    'sg staff -c "{}"',
    'sudo -s "{}"',
    'sudo -i "{}"',
    'watch -n 1 "{}"',
    'bash <<< "{}"',
    "sh <<< '{}'",
    'timeout 5 bash <<< "{}"',
]


# Why: shell option values (-o pipefail) must be skipped so the -c script is still parsed.
def test_shell_option_values_do_not_hide_the_c_script():
    assert_all_blocked([form.format(BAD_WORKTREE) for form in SHELL_OPTION_FORMS])
    assert_all_blocked([form.format(f"git commit -m {LINK_URL}") for form in SHELL_OPTION_FORMS])
    assert_all_allowed([
        'bash -o pipefail -c "git worktree add .worktrees/x"',
        'bash -O extglob -c "git status"',
        'sh -eu -c "echo git worktree add ../x"',
        "bash -o pipefail script.sh",
        "bash +o history script.sh",
        'flock /tmp/l -c "git worktree add .worktrees/x"',
        'sudo -s "git status"',
        'watch -n 1 "git status"',
        'cat <<< "git worktree add ../x"',
        "cat <<< '$(git worktree add ../x)'",
        'grep -q x <<< "git worktree add ../x"',
    ])


# Why: GNU xargs --replace without = means {}, and must not swallow the command name as its placeholder.
def test_xargs_replace_forms():
    assert_all_blocked([
        "echo ../x | xargs --replace git worktree add {}",
        "echo ../x | xargs --replace=% git worktree add %",
        "echo ../x | xargs -i git worktree add {}",
        "echo ../x | xargs -iX git worktree add X",
        "echo ../x | xargs -I % git worktree add %",
        "echo ../x | xargs -I{} git worktree add {}",
        "echo ../x | xargs --replace git worktree add ../x",
        f'echo a | xargs --replace git commit -m "x {LINK_URL}"',
    ])
    assert_all_allowed([
        "echo .worktrees/x | xargs --replace git worktree add {}",
        "echo .worktrees/x | xargs --replace=% git worktree add %",
        "echo .worktrees/x | xargs -i git worktree add {}",
        "echo x | xargs --replace git worktree add .worktrees/{}",
        "echo x | xargs --replace git status",
    ])


SUBSTITUTED_SCRIPT_FORMS = [
    'bash -c "$(echo {})"',
    "bash -c \"$(printf '%s' '{}')\"",
    'bash -c "`echo {}`"',
    'sh -c "$(echo {}; echo hi)"',
    'timeout 5 bash -o pipefail -c "$(echo {})"',
    'eval "$(echo {})"',
    'ssh host "$(echo {})"',
    'bash <(echo "{}")',
    'source <(echo "{}")',
    '. <(echo "{}")',
    'timeout 5 bash <(printf "%s" "{}")',
    'at now <<< "{}"',
    'echo "{}" | at now',
]


# Why: a substitution that echoes a command into a -c script, process substitution or at runs that command.
def test_echoed_substitutions_that_become_scripts():
    assert_all_blocked([form.format(BAD_WORKTREE) for form in SUBSTITUTED_SCRIPT_FORMS])
    assert_all_blocked([form.format(f"git commit -m {LINK_URL}") for form in SUBSTITUTED_SCRIPT_FORMS])
    assert_all_allowed([
        'bash -c "$(echo git worktree add .worktrees/x)"',
        'bash <(echo "git status")',
        'source <(echo "git worktree add .worktrees/x")',
        'at now <<< "git status"',
        'diff <(git show a:f) <(git show b:f)',
        'cat <(echo "git worktree add ../x")',
        'bash -c "echo hi"',
    ])


COMMAND_POSITION_SUBSTITUTIONS = [
    "$(echo {})",
    "`echo {}`",
    "$(printf '{}')",
    "ls; $(echo {})",
    "ls && `echo {}`",
    "x=1 $(echo {})",
    "echo y | $(echo {})",
    "$(echo $(echo {}))",
    "( $(echo {}) )",
    "if true; then $(echo {}); fi",
    'bash -c "ls; $(echo {})"',
    'bash -c "x=1 `echo {}`"',
    "timeout 5 $(echo {})",
    "sudo $(echo {})",
    "$(echo git) worktree add ../x",
]


# Why: bash runs the output of a command-position substitution as the command, so echoed text is the command.
def test_command_position_echo_substitutions_run_the_echoed_command():
    assert_all_blocked([form.format(BAD_WORKTREE) for form in COMMAND_POSITION_SUBSTITUTIONS])
    assert_all_blocked([form.format(f"git commit -m {LINK_URL}") for form in COMMAND_POSITION_SUBSTITUTIONS[:-1]])
    assert_all_allowed([
        "$(echo git worktree add .worktrees/x)",
        "$(echo git status)",
        "ls; `echo git worktree add .worktrees/x`",
        'git commit -m "docs: $(echo git worktree add ../x)"',
        'echo "$(echo git worktree add ../x)"',
        "echo $(echo git worktree add ../x)",
        "$(pwd)/script.sh arg",
        "$(git rev-parse --show-toplevel)/run.sh",
    ])


# Why: -i/-s inside an option cluster (-iu bob) still hands the trailing words to a shell.
def test_sudo_option_clusters():
    assert_all_blocked([
        "sudo -iu bob 'git worktree add ../x'",
        "sudo -su bob 'git worktree add ../x'",
        "sudo -Eiu bob 'git worktree add ../x'",
        "sudo -u bob -i 'git worktree add ../x'",
        "sudo -iu bob -- git worktree add ../x",
        "sudo -u bob git worktree add ../x",
    ])
    assert_all_allowed([
        "sudo -iu bob 'git worktree add .worktrees/x'",
        "sudo -iu bob 'git status'",
        "sudo -u bob git status",
    ])


# Why: runuser -c flags after the command word belong to that command, not to runuser.
def test_runuser_command_flags_are_not_runuser_options():
    assert_all_allowed([
        'runuser -u bob -- grep -c "git worktree add ../x" f',
        'runuser -u bob -- grep -c "git commit -m x" f',
        'runuser -u bob grep -c "git worktree add ../x" f',
    ])
    assert_all_blocked([
        'runuser -u bob -c "git worktree add ../x"',
        'runuser -c "git worktree add ../x" -u bob',
        'runuser bob -c "git worktree add ../x"',
        'su bob -c "git worktree add ../x"',
    ])


# Why: runuser -u USER runs the command directly, so it must resolve like any other prefix.
def test_runuser_direct_command_forms():
    assert_all_blocked([
        "runuser -u bob -- git worktree add ../x",
        "runuser -u bob git worktree add ../x",
        "sudo runuser -u bob -- git worktree add ../x",
        "timeout 5 runuser -u bob -- git worktree add ../x",
        "runuser -u bob -g staff -- git worktree add ../x",
        "runuser --user=bob -- git worktree add ../x",
        "runuser -ubob -- git worktree add ../x",
        f'runuser -u bob -- git commit -m "x {LINK_URL}"',
    ])
    assert_all_allowed([
        "runuser -u bob -- git worktree add .worktrees/x",
        "runuser -u bob -- git status",
        "runuser -u bob -- echo git worktree add ../x",
    ])


PARALLEL_FORMS = [
    'parallel "{}" ::: a',
    "parallel '{}' ::: a b",
    'parallel -j2 "{}" ::: a',
    'parallel --jobs 2 "{}" ::: a',
    'parallel --halt now,fail=1 --tag -k "{}" ::: a',
    'parallel -- "{}"',
    'parallel -S host "{}" ::: a',
    'cat f | parallel "{}"',
    'echo a | parallel -I@ "{}"',
    'sem "{}"',
    'timeout 5 parallel "{}" ::: a',
]
TMUX_FORMS = [
    'tmux new-session -d "{}"',
    'tmux new-session -d -s s "{}"',
    'tmux new -s s "{}"',
    'tmux new-window "{}"',
    'tmux neww -n w "{}"',
    'tmux split-window -h "{}"',
    'tmux run-shell "{}"',
    'tmux run -b "{}"',
    'tmux send-keys "{}" Enter',
    'tmux send-keys -t 0 "{}" C-m',
    'tmux respawn-pane -k "{}"',
    'tmux if-shell true "{}"',
    'tmux display-popup "{}"',
    'tmux -L sock new-session -d "{}"',
    'screen -dmS s "{}"',
]
INTERPRETER_FORMS = [
    "python3 -c 'import os; os.system(\"{}\")'",
    "python -c 'import os; os.popen(\"{}\")'",
    "python3 -c 'import subprocess; subprocess.run(\"{}\", shell=True)'",
    "python3 -c 'import subprocess; subprocess.call(\"{}\", shell=True)'",
    "python3 -c 'import subprocess; subprocess.Popen(\"{}\", shell=True)'",
    "python3 -c 'import subprocess; subprocess.check_call(\"{}\", shell=True)'",
    "python3 -c 'import subprocess; subprocess.check_output(\"{}\", shell=True)'",
    "python3 -c 'import subprocess; print(subprocess.getoutput(\"{}\"))'",
    "python3 -c 'import subprocess; subprocess.run(\"{}\".split())'",
    "python3.11 -c 'import os; os.system(\"{}\")'",
    "python3 - <<EOF\nimport os\nos.system(\"{}\")\nEOF",
    "python3 <<'EOF'\nimport os\nos.system(\"{}\")\nEOF",
    "perl -e 'system(\"{}\")'",
    "ruby -e 'system(\"{}\")'",
    "ruby -e '`{}`'",
    "node -e 'require(\"child_process\").execSync(\"{}\")'",
    "php -r 'system(\"{}\");'",
    "lua -e 'os.execute(\"{}\")'",
    "awk 'BEGIN{{system(\"{}\")}}'",
    "expect -c 'spawn {}'",
    "timeout 5 python3 -c 'import os; os.system(\"{}\")'",
]
ARGV_FORMS = [
    "python3 -c 'import subprocess; subprocess.run([\"git\", \"worktree\", \"add\", \"../x\"])'",
    "python3 -c 'import subprocess; subprocess.check_call([\"git\", \"worktree\", \"add\", \"-b\", \"b\", \"../x\"])'",
    "python3 -c 'import subprocess; subprocess.Popen([\"git\", \"-C\", \"r\", \"worktree\", \"add\", \"../x\"])'",
    "python3 -c 'import os; os.execvp(\"git\", [\"git\", \"worktree\", \"add\", \"../x\"])'",
    "python3 -c 'import os; os.execl(\"/usr/bin/git\", \"git\", \"worktree\", \"add\", \"../x\")'",
    "python3 -c 'import os; os.spawnlp(os.P_WAIT, \"git\", \"git\", \"worktree\", \"add\", \"../x\")'",
    "python3 -c 'import os; os.posix_spawn(\"/usr/bin/git\", [\"git\", \"worktree\", \"add\", \"../x\"], {})'",
    "node -e 'require(\"child_process\").execFileSync(\"git\", [\"worktree\", \"add\", \"../x\"])'",
    "ruby -e 'system(\"git\", \"worktree\", \"add\", \"../x\")'",
    "perl -e 'system(\"git\", \"worktree\", \"add\", \"../x\")'",
]
GIT_EXEC_FORMS = [
    "git rebase --exec '{}' main",
    "git rebase -i --exec='{}' main",
    "git submodule foreach '{}'",
    "git bisect run sh -c '{}'",
    "git filter-branch --tree-filter '{}' HEAD",
    "git config alias.x '!{}'",
    "git -c alias.x='!{}' x",
    "yarn exec '{}'",
    "pnpm exec '{}'",
    "npm exec -- '{}'",
]
STRING_CARRIERS = PARALLEL_FORMS + TMUX_FORMS + INTERPRETER_FORMS + GIT_EXEC_FORMS


# Why: launchers and interpreters that run a command string must be followed like a shell -c.
def test_string_carriers_run_misplaced_worktree():
    assert_all_blocked([form.format(BAD_WORKTREE) for form in STRING_CARRIERS])
    assert_all_blocked(ARGV_FORMS)


def test_string_carriers_run_session_link_commands():
    assert_all_blocked([form.format(f"git commit -m x{LINK_URL}") for form in STRING_CARRIERS])
    assert_all_blocked([form.format(f"gh pr create --title t --body x{LINK_URL}") for form in STRING_CARRIERS])
    assert_all_blocked([
        f"python3 -c 'import subprocess; subprocess.run([\"git\", \"commit\", \"-m\", \"x {LINK_URL}\"])'",
        f"python3 -c 'import subprocess; subprocess.run([\"gh\", \"pr\", \"create\", \"--body\", \"x {LINK_URL}\"])'",
    ])


def test_string_carriers_allow_placed_worktrees_and_other_commands():
    placed = "git worktree add .worktrees/x"
    assert_all_allowed([form.format(placed) for form in STRING_CARRIERS])
    assert_all_allowed([form.format("git status") for form in STRING_CARRIERS])
    assert_all_allowed([
        "python3 -c 'import subprocess; subprocess.run([\"git\", \"worktree\", \"add\", \".worktrees/x\"])'",
        "python3 -c 'import os; os.execvp(\"git\", [\"git\", \"status\"])'",
        "node -e 'require(\"child_process\").execFileSync(\"git\", [\"worktree\", \"add\", \".worktrees/x\"])'",
        "python3 -c 'import subprocess; subprocess.run([\"git\", \"commit\", \"-m\", \"docs\"])'",
    ])


# Why: text that only mentions a command, or is fed to a launcher as data, does not run it.
def test_string_carriers_ignore_prose_and_input_data():
    assert_all_allowed([
        "parallel echo {} ::: 'git worktree add ../x'",
        "parallel gzip ::: git worktree add ../x",
        "parallel --jobs 2 echo ::: a b",
        "tmux display-message 'git worktree add ../x'",
        "tmux list-sessions",
        "tmux select-window -t 0",
        "tmux attach -t git",
        "screen -ls",
        "python3 -c 'print(\"git worktree add ../x\")'",
        "python3 -c 'import json; print(json.dumps(\"git worktree add ../x\"))'",
        "python3 script.py 'git worktree add ../x'",
        "python3 -m pytest -k 'git worktree add ../x'",
        "python3 - <<'EOF'\nprint(\"git worktree add ../x\")\nEOF",
        "node -e 'console.log(\"git worktree add ../x\")'",
        "awk '{ print \"git worktree add ../x\" }' f",
        "perl -e 'print \"git worktree add ../x\"'",
        "git rebase main",
        "git submodule update --init",
        "git bisect start",
        "git config user.name x",
        "git commit -m 'git worktree add ../x'",
        "yarn install",
        "npm run build -- 'git worktree add ../x'",
        f"tmux display-message '{LINK_URL}'",
        f"python3 -c 'print(\"{LINK_URL}\")'",
    ])
