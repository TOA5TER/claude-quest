import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "guardrails_hook",
    Path(__file__).resolve().parent.parent / "hooks" / "guardrails_git_pre_tool_use.py",
)
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def run_policy(command, current_branch, monkeypatch, capsys):
    """Run the branch policy check. Returns the block decision dict, or None if allowed."""
    monkeypatch.setattr(hook, "get_current_branch", lambda cwd=None: current_branch)
    try:
        hook.check_git_branch_policy("Bash", {"command": command})
    except SystemExit:
        return json.loads(capsys.readouterr().out)
    return None


def mirror_git_frontends(commands):
    # Why: authenticated commands in this workspace route through the gitp and
    # git-as-app.sh wrappers instead of bare git; every policy case must hold
    # for those spellings too or the whole branch policy is bypassable.
    mirrored = []
    for cmd in commands:
        mirrored.append(cmd)
        for variant in (cmd.replace("git ", "gitp "),
                        cmd.replace("git ", "git-as-app.sh developer ")):
            if variant != cmd:
                mirrored.append(variant)
    return mirrored


BLOCKED_CREATION_OFF_MAIN = mirror_git_frontends([
    "git checkout -b feature/new-thing",
    "git checkout -B feature/new-thing",
    "git checkout --orphan feature/new-thing",
    "git branch chore/sc-1056-disable-faro-telemetry origin/main",
    "git branch feature/new-thing",
    "git branch -f feature/new-thing origin/main",
    "git switch -c feature/new-thing",
    "git switch -C feature/new-thing",
    "git switch --create feature/new-thing",
    "git switch --force-create feature/new-thing",
    "echo hi && git branch x y",
    "ls; git switch -c feature/new-thing",
    "git fetch | git branch feature/new-thing",
    "GIT_TRACE=1 git branch feature/new-thing",
    "env git branch feature/new-thing",
    "git checkout -bfeature/new-thing",
    "git checkout -Bfeature/new-thing",
    "git switch -cfeature/new-thing",
    "git switch -Cfeature/new-thing",
    "git switch -c=feature/new-thing",
    "git switch --create=feature/new-thing",
])


@pytest.mark.parametrize("command", BLOCKED_CREATION_OFF_MAIN)
def test_branch_creation_blocked_off_main(command, monkeypatch, capsys):
    decision = run_policy(command, "feature/existing", monkeypatch, capsys)
    assert decision is not None, f"expected block: {command}"
    assert decision["decision"] == "block"
    assert "New branches should only be created from 'main'" in decision["reason"]
    assert "feature/existing" in decision["reason"]


@pytest.mark.parametrize("command", BLOCKED_CREATION_OFF_MAIN)
def test_branch_creation_allowed_on_main(command, monkeypatch, capsys):
    assert run_policy(command, "main", monkeypatch, capsys) is None


ALLOWED_NON_CREATION = mirror_git_frontends([
    "git branch",
    "git branch -a",
    "git branch -l",
    "git branch --list 'feature/*'",
    "git branch -vv",
    "git branch --show-current",
    "git branch -D feature/old",
    "git branch -d feature/old",
    "git branch --delete feature/old",
    "git branch -m old new",
    "git branch -M old new",
    "git branch -c old new",
    "git branch --copy old new",
    "git branch --merged main",
    "git branch --no-merged",
    "git branch --contains abc123",
    "git branch -u origin/feature/x",
    "git branch --set-upstream-to=origin/feature/x",
    "git branch --unset-upstream",
    "git branch --sort=-committerdate --list",
    "git branch --edit-description",
    "git branch -uorigin/feature/x mybranch",
    "git branch -c=old new",
    "git checkout feature/existing-branch",
    "git switch feature/existing-branch",
    "git switch -",
    "git status && git log --oneline",
    'echo "git branch x"',
    "git stash list",
])


@pytest.mark.parametrize("command", ALLOWED_NON_CREATION)
def test_non_creation_forms_allowed(command, monkeypatch, capsys):
    assert run_policy(command, "feature/existing", monkeypatch, capsys) is None


BLOCKED_MAIN_CHECKOUT = mirror_git_frontends([
    "git checkout main",
    "git switch main",
    "git checkout -q main",
    "cd /repo && git checkout main",
])


@pytest.mark.parametrize("command", BLOCKED_MAIN_CHECKOUT)
def test_checkout_main_blocked(command, monkeypatch, capsys):
    decision = run_policy(command, "feature/existing", monkeypatch, capsys)
    assert decision is not None, f"expected block: {command}"
    assert "checkout the main branch" in decision["reason"]


ALLOWED_WORKTREE = mirror_git_frontends([
    "git worktree add --detach .worktrees/wt",
    "git worktree add --detach .worktrees/wt main",
    "git worktree add .worktrees/wt",
    "git worktree add .worktrees/wt -b feature/new",
    "git worktree add -b feature/new .worktrees/wt",
    "git worktree add -b feature/new .worktrees/wt main",
    "git worktree list",
    "git worktree remove .worktrees/wt",
    "git worktree prune",
    "git worktree move .worktrees/wt .worktrees/wt2",
    "git worktree lock .worktrees/wt",
    "git worktree",
])


@pytest.mark.parametrize("command", ALLOWED_WORKTREE)
def test_worktree_commands_always_allowed(command, monkeypatch, capsys):
    # Why: every 'git worktree' subcommand and flag combination, including
    # branch-creating 'add' forms, falls through to allowed regardless of
    # the current branch. A regression here (re-narrowing this exemption to
    # route worktree creation through the branch-from-main check) strands
    # every worktree-isolated pipeline run: off main, worktree creation
    # itself gets blocked with no escape.
    assert run_policy(command, "feature/existing", monkeypatch, capsys) is None
    assert run_policy(command, "main", monkeypatch, capsys) is None


BLOCKED_WORKTREE_ADD_EXISTING_MAIN = mirror_git_frontends([
    "git worktree add .worktrees/wt main",
    "git worktree add -f .worktrees/wt main",
])


@pytest.mark.parametrize("command", BLOCKED_WORKTREE_ADD_EXISTING_MAIN)
def test_worktree_add_existing_main_blocked(command, monkeypatch, capsys):
    # Why: 'git worktree add <path> main' with no -b/-B/--detach materializes
    # 'main' as a live, checked-out working tree — exactly the state the
    # checkout-main guard exists to prevent. Must block the same way
    # 'git checkout main' does, regardless of the current branch.
    for branch in ("feature/existing", "main"):
        decision = run_policy(command, branch, monkeypatch, capsys)
        assert decision is not None, f"expected block: {command} (branch={branch})"
        assert "checkout the main branch" in decision["reason"]


def test_git_dash_c_dir_passed_to_branch_lookup(monkeypatch, capsys):
    seen = {}

    def fake_lookup(cwd=None):
        seen["cwd"] = cwd
        return "feature/existing"

    monkeypatch.setattr(hook, "get_current_branch", fake_lookup)
    with pytest.raises(SystemExit):
        hook.check_git_branch_policy("Bash", {"command": "git -C /some/dir branch x"})
    capsys.readouterr()
    assert seen["cwd"] == "/some/dir"


def test_relative_dash_c_resolved_against_payload_cwd(monkeypatch, capsys):
    # Why: a relative 'git -C' must resolve against the PreToolUse payload's
    # cwd, never against the hook process's own OS cwd (which is never
    # deliberately set to match the caller's shell).
    seen = {}

    def fake_lookup(cwd=None):
        seen["cwd"] = cwd
        return "feature/existing"

    monkeypatch.setattr(hook, "get_current_branch", fake_lookup)
    with pytest.raises(SystemExit):
        hook.check_git_branch_policy("Bash", {"command": "git -C ../wt branch x"}, "/repo/sub")
    capsys.readouterr()
    assert seen["cwd"] == "/repo/wt"


def test_unparseable_command_allowed(monkeypatch, capsys):
    # Unbalanced quoting can't be tokenized; the shell rejects it anyway.
    assert run_policy('echo "unclosed', "feature/existing", monkeypatch, capsys) is None


def test_non_bash_tool_ignored(monkeypatch, capsys):
    assert run_policy("", "feature/existing", monkeypatch, capsys) is None
    monkeypatch.setattr(hook, "get_current_branch", lambda cwd=None: "feature/x")
    assert hook.check_git_branch_policy("Edit", {"command": "git branch x"}) is None


def test_branch_lookup_failure_blocks(monkeypatch, capsys):
    # Why: an unresolvable branch (None from get_current_branch) must be
    # treated as "could not verify — block", never as "not main, so allow."
    # A fail-open default on an unresolvable directory/branch is what let a
    # detached worktree's empty-string branch lookup slip through as "not main."
    decision = run_policy("git branch new-thing", None, monkeypatch, capsys)
    assert decision is not None
    assert decision["decision"] == "block"
    assert "Could not determine the current branch" in decision["reason"]


def test_branch_lookup_empty_string_blocks(monkeypatch, capsys):
    # Why: the exact detached-worktree failure mode — 'git branch
    # --show-current' returns '' (empty string, falsy but not None) when
    # HEAD is detached. '' must be treated identically to None: block.
    decision = run_policy("git branch new-thing", "", monkeypatch, capsys)
    assert decision is not None
    assert decision["decision"] == "block"
    assert "Could not determine the current branch" in decision["reason"]
