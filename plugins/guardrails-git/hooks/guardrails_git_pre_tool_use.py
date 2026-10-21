#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# ///

import json
import os
import shlex
import sys
import re
import subprocess


def create_timeout_error_message(command_type, wanted_timeout, current_timeout):
    minutes = int(wanted_timeout / 60 / 1000)
    return f"You must set a timeout of {wanted_timeout}ms ({minutes} minutes) for {command_type} commands. This is not a test timeout error. Please retry the command with 'timeout': {wanted_timeout} in the tool parameters."


# --- Branch policy ------------------------------------------------------------
# New branches may only be created from 'main', and 'main' itself may not be checked out.
# Rules run on shlex tokens per shell segment, so equivalent forms and chained commands are covered.
# Recognized front-ends: git, gitp, git-as-app.sh and gh, ghp, gh-as-app.sh.
# 'git worktree' skips the branch-creation-must-be-from-main check for every subcommand and flag.
# 'git worktree add <path> main' with no -b/-B/--detach is refused like 'git checkout main'.
# 'git worktree add' must target a path under a '.worktrees' folder with no '..' component.
# Commit and PR text must not carry a Claude session link, including in nested shell text.

# Any of these flags means 'git branch' is listing/deleting/moving/copying/
# configuring — not creating a branch.
BRANCH_NON_CREATE_OPTS = {'-d', '-D', '--delete', '-m', '-M', '--move',
                          '-c', '-C', '--copy', '-l', '--list', '-a', '--all',
                          '-r', '--remotes', '--show-current', '-v', '-vv',
                          '--verbose', '--merged', '--no-merged', '--contains',
                          '--no-contains', '--points-at', '--sort', '--format',
                          '--column', '--edit-description', '--set-upstream-to',
                          '-u', '--unset-upstream'}
# Branch-creating flags of 'git checkout' (-b/-B) and 'git switch' (-c/-C, --create).
CREATE_OPTS = {'-b', '-B', '-c', '-C', '--create', '--force-create', '--orphan'}

BRANCH_CREATION_REASON = ("New branches should only be created from 'main', but you're currently on "
                          "'{branch}'. You're already on the correct branch for your work, so no need "
                          "to create a new one.")
CHECKOUT_MAIN_REASON = ("Claude isn't allowed to checkout the main branch. "
                        "Please ask the user what to do instead.")
UNRESOLVED_BRANCH_REASON = ("Could not determine the current branch for this invocation — "
                            "refusing rather than assuming it's safe to proceed.")


def _block(reason):
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(1)


def get_current_branch(cwd=None):
    """Look up the current branch of the repo at `cwd`. `cwd` is passed
    explicitly to subprocess.run rather than baked into the argv via
    'git -C' — the hook process's own OS cwd is never a valid resolution
    base (it's never deliberately set to match the caller's shell), so
    every caller must resolve an actual directory first and hand it here.
    A falsy `cwd` means no caller ever resolved a real directory — refuse
    outright rather than letting subprocess.run(cwd=None) silently fall
    back to inheriting the hook process's own OS cwd, which would violate
    that same invariant."""
    if not cwd:
        return None
    try:
        result = subprocess.run(['git', 'branch', '--show-current'], capture_output=True,
                                text=True, timeout=5, cwd=cwd)
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return None


def tokenize_segments(command):
    """Split a shell command into token lists, one per `;`/`&&`/`||`/`&`
    separated simple command. `(`, `)`, and `|` are kept as their own
    single-token segments (rather than discarded like the other
    separators) so callers can detect subshell/pipe-stage scope
    boundaries — a `cd` inside `(...)` or on one side of a `|` runs in a
    forked subshell whose directory changes never reach anything outside
    that scope. Raises ValueError on unbalanced quoting."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segments, current = [], []
    for tok in lex:
        if tok in ('(', ')', '|'):
            if current:
                segments.append(current)
                current = []
            segments.append([tok])
        elif all(ch in ';|&()' for ch in tok):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _resolve_path(directory, payload_cwd):
    """Resolve a possibly-relative directory (from 'cd <dir>' or 'git -C
    <dir>') against the payload's cwd as the base — never against the hook
    process's own OS cwd, which is never deliberately set to match the
    caller's shell. An already-absolute directory passes through
    unchanged. A relative directory with no base to resolve against is
    unresolvable — returning it unchanged would let subprocess.run(cwd=...)
    silently resolve it against exactly the forbidden base, so report None
    instead."""
    if directory is None or os.path.isabs(directory):
        return directory
    if not payload_cwd:
        return None
    return os.path.normpath(os.path.join(payload_cwd, directory))


def _cd_argument(seg):
    """Classify a segment as a 'cd' invocation's directory argument.
    Returns (kind, value): 'none' (not a 'cd' segment, or a 'cd' with no
    resolvable operand — e.g. bare 'cd', or only flag tokens) leaves the
    accumulated directory unchanged; 'unresolved' (a bare '-' — the
    caller shell's previous directory, which this hook has no notion of
    — or a '$'-bearing argument, e.g. '$HOME/x', which this hook cannot
    safely expand) means the directory from this point is unknown;
    'path' (value is the operand with a leading flag like 'cd -P' skipped
    and '~' expanded via os.path.expanduser) is handed to _resolve_path."""
    if not seg or seg[0] != 'cd':
        return 'none', None
    for tok in seg[1:]:
        if tok == '-' or '$' in tok:
            return 'unresolved', None
        if tok.startswith('-'):
            continue
        return 'path', os.path.expanduser(tok)
    return 'none', None


def resolve_default_dir(segments, index, payload_cwd):
    """The directory a git invocation at `index` actually runs in, absent
    an explicit 'git -C': replay every 'cd <dir>' segment before it in the
    command chain, each resolved against the directory accumulated so far
    (not against a fixed payload snapshot — a 'cd' chain composes, e.g. in
    'cd <repo> && cd <relative-subdir> && git commit' the second cd must
    resolve against the first, not against the shell's cwd before either
    ran), starting from the PreToolUse hook payload's 'cwd' (which reflects
    the persistent shell's current directory, not this hook process's own
    OS cwd).

    A '(' opens a new subshell scope (its own copy of the accumulated
    directory, discarded when the matching ')' closes it — nothing a 'cd'
    does inside survives past that ')'). A '|' resets the current scope's
    accumulated directory back to what it was before the current pipeline
    stage started — each stage of a pipe forks its own subshell, so a
    'cd' in one stage never reaches a later stage or anything after the
    pipe, even within the same '(...)' scope. An 'unresolved' cd argument
    (Required Change 3 forms this hook can't safely interpret) poisons the
    accumulated directory for the rest of its scope, same as a resolvable
    'cd' would overwrite it — later relative operands then correctly fail
    to resolve too, via _resolve_path's own falsy-base handling."""
    dir_stack = [payload_cwd]
    checkpoint_stack = [payload_cwd]
    for seg in segments[:index]:
        if seg == ['(']:
            dir_stack.append(dir_stack[-1])
            checkpoint_stack.append(dir_stack[-1])
        elif seg == [')']:
            if len(dir_stack) > 1:
                dir_stack.pop()
                checkpoint_stack.pop()
        elif seg == ['|']:
            dir_stack[-1] = checkpoint_stack[-1]
        else:
            kind, value = _cd_argument(seg)
            if kind == 'path':
                dir_stack[-1] = _resolve_path(value, dir_stack[-1])
            elif kind == 'unresolved':
                dir_stack[-1] = None
    return dir_stack[-1]


# Recognized git/gh front-end tokens, each mapped to how many positional
# (non-flag) tokens to skip after it — the wrapper's persona argument —
# before the real subcommand.
GIT_FRONTENDS = {'git': 0, 'gitp': 0, 'git-as-app.sh': 1}
GH_FRONTENDS = {'gh': 0, 'ghp': 0, 'gh-as-app.sh': 1}


def _find_frontend(segment, frontends):
    """(index, positional-skip count) of the first front-end token in the
    segment, or None."""
    for i, tok in enumerate(segment):
        if tok in frontends:
            return i, frontends[tok]
    return None


def parse_git_invocation(segment):
    """If the segment invokes a git front-end (git, gitp, git-as-app.sh),
    return (subcommand, args, explicit_dir); otherwise None. 'explicit_dir'
    is the raw (possibly relative, possibly None) argument of an explicit
    '-C <dir>' on the invocation — resolution against a default directory
    happens in git_invocations, which has the segment's position in the
    command chain and can rank -C above any 'cd'. Skips the wrapper's
    persona argument and git's global options to find the subcommand."""
    found = _find_frontend(segment, GIT_FRONTENDS)
    if found is None:
        return None
    idx, skip = found
    rest = segment[idx + 1:]
    git_dir = None
    while rest:
        if rest[0].startswith('-'):
            opt = rest.pop(0)
            if opt == '-C' and rest:
                git_dir = rest.pop(0)
            elif opt in ('-c', '--git-dir', '--work-tree') and rest:
                rest.pop(0)
        elif skip:
            rest.pop(0)
            skip -= 1
        else:
            break
    if not rest:
        return None
    return rest[0], rest[1:], git_dir


def git_invocations(command, payload_cwd=None):
    """All git invocations in the command as (subcommand, args, dir) tuples,
    'dir' resolved per invocation in precedence order: an explicit 'git -C'
    on that invocation, else the last 'cd <dir>' segment before it anywhere
    in the command chain, else payload_cwd as the final fallback. Relative
    '-C'/'cd' directories resolve against payload_cwd, never the hook
    process's own OS cwd."""
    try:
        segments = tokenize_segments(command)
    except ValueError:
        return []  # unbalanced quoting — the shell will reject the command anyway
    invocations = []
    for index, seg in enumerate(segments):
        parsed = parse_git_invocation(seg)
        if parsed is None:
            continue
        subcommand, args, explicit_dir = parsed
        base_dir = resolve_default_dir(segments, index, payload_cwd)
        if explicit_dir is not None:
            git_dir = _resolve_path(explicit_dir, base_dir)
        else:
            git_dir = base_dir
        invocations.append((subcommand, args, git_dir))
    return invocations


def gh_invocations(command):
    """Token lists following a gh front-end (gh, ghp, gh-as-app.sh) in each
    shell segment, with the wrapper's persona argument stripped."""
    try:
        segments = tokenize_segments(command)
    except ValueError:
        return []
    invocations = []
    for seg in segments:
        found = _find_frontend(seg, GH_FRONTENDS)
        if found is None:
            continue
        idx, skip = found
        rest = seg[idx + 1:]
        while rest and skip:
            if not rest[0].startswith('-'):
                skip -= 1
            rest.pop(0)
        invocations.append(rest)
    return invocations


def _matches_opt(tok, opts):
    """True if tok is one of opts, in --opt=value form, or in git's stuck
    short-option form ('-bname' for '-b name')."""
    return (tok.split('=', 1)[0] in opts
            or (not tok.startswith('--') and len(tok) > 2 and tok[:2] in opts))


def evaluate_git_invocation(subcommand, args, git_dir):
    """Return a block reason for a policy violation, else None."""
    creates = False
    if subcommand in ('checkout', 'switch'):
        flags = [tok for tok in args if tok.startswith('-') and tok != '--']
        positionals = [tok for tok in args if not tok.startswith('-')]
        creates = any(_matches_opt(tok, CREATE_OPTS) for tok in flags)
        if not creates and positionals and positionals[0] == 'main':
            return CHECKOUT_MAIN_REASON
    elif subcommand == 'branch':
        flags = [tok for tok in args if tok.startswith('-') and tok != '--']
        positionals = [tok for tok in args if not tok.startswith('-')]
        creates = bool(positionals) and not any(
            _matches_opt(tok, BRANCH_NON_CREATE_OPTS) for tok in flags)
    elif subcommand == 'worktree':
        # Only 'add <path> <existing-ref>' without -b/-B/--detach is narrowed, and only for 'main'.
        if args and args[0] == 'add':
            rest = args[1:]
            flags = [tok for tok in rest if tok.startswith('-') and tok != '--']
            positionals = [tok for tok in rest if not tok.startswith('-')]
            branch_creating = any(_matches_opt(tok, {'-b', '-B'}) for tok in flags)
            detached = any(_matches_opt(tok, {'--detach'}) for tok in flags)
            if (not branch_creating and not detached
                    and len(positionals) >= 2 and positionals[1] == 'main'):
                return CHECKOUT_MAIN_REASON
    if creates:
        branch = get_current_branch(git_dir)
        if branch == 'main':
            pass  # creating a new branch from main is exactly the allowed flow
        elif branch:
            return BRANCH_CREATION_REASON.format(branch=branch)
        else:
            return UNRESOLVED_BRANCH_REASON
    return None


def check_git_branch_policy(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    for invocation in git_invocations(command, payload_cwd):
        reason = evaluate_git_invocation(*invocation)
        if reason:
            _block(reason)


# --- End branch policy ----------------------------------------------------------


def check_git_commit_branch(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    for subcommand, args, git_dir in git_invocations(command, payload_cwd):
        if subcommand != 'commit':
            continue
        branch = get_current_branch(git_dir)
        if branch == 'main':
            _block("Direct commits to the 'main' branch are not allowed. Please create a feature "
                   "branch first (e.g., 'git checkout -b feature/your-feature-name') and commit "
                   "your changes there, then create a pull request.")
        elif not branch:
            _block(UNRESOLVED_BRANCH_REASON)


def check_git_no_verify(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    for subcommand, args, git_dir in git_invocations(tool_input.get('command', ''), payload_cwd):
        for tok in args:
            if tok == '--':
                break
            if tok == '--no-verify' or (subcommand == 'commit' and tok == '-n'):
                _block("The '--no-verify' flag is not allowed in git commands. "
                       "You are never allowed to skip hooks")


CLAUDE_BOILERPLATE_PATTERNS = [
    r'Generated with Claude Code',
    r'Co-Authored by Claude',
    r'Generated with \[Claude Code\]',
    r'Co-Authored-By:\s*Claude',
    r'<noreply@anthropic\.com>',
    r'claude\.ai/code',
    r'Generated by Claude',
    r'Created with Claude Code',
    r'Assisted by Claude',
    r'With help from Claude',
    r'@anthropic\.com',
]
AI_BOILERPLATE_PATTERNS = CLAUDE_BOILERPLATE_PATTERNS + [
    r'AI-generated',
    r'AI generated',
    r'Generated by AI',
    r'Created by AI',
    r'Built with AI',
]


def _has_boilerplate(command, patterns):
    return any(re.search(p, command, re.IGNORECASE) for p in patterns)


def _gh_subcommand_present(command, words):
    """True when the command runs `gh <words...>` (flags between words ignored)."""
    for args in gh_invocations(command):
        positionals = [tok for tok in args if not tok.startswith('-')]
        if positionals[:len(words)] == words:
            return True
    return False


def check_git_commit_boilerplate(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    if (any(sub == 'commit' for sub, _, _ in git_invocations(command, payload_cwd))
            and _has_boilerplate(command, CLAUDE_BOILERPLATE_PATTERNS)):
        _block("Boilerplate code patterns are not allowed in git commit messages. Please remove "
               "references to Claude Code, co-authorship with Claude, or Anthropic email addresses "
               "from your commit message and try again.")


def _is_pull_request_api_call(command):
    for args in gh_invocations(command):
        if 'api' not in args or [tok for tok in args if not tok.startswith('-')][:1] != ['api']:
            continue
        endpoint = api_endpoint(args, args.index('api') + 1)
        if endpoint is not None and PULLS_ENDPOINT_RE.search(endpoint):
            return True
    return False


def check_pr_create_boilerplate(tool_name, tool_input):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    if ((_gh_subcommand_present(command, ['pr', 'create'])
            or _gh_subcommand_present(command, ['pr', 'edit'])
            or _is_pull_request_api_call(command))
            and _has_boilerplate(command, AI_BOILERPLATE_PATTERNS)):
        _block("Boilerplate patterns are not allowed in PR titles or descriptions. Please remove "
               "references to Claude Code, AI generation, co-authorship with Claude, or Anthropic "
               "email addresses from your PR content and try again.")


def _is_pr_comment_api_call(command):
    for args in gh_invocations(command):
        positionals = [tok for tok in args if not tok.startswith('-')]
        if positionals[:1] == ['api'] and any(
                re.search(r'repos/.+/issues/\d+/comments', tok) for tok in args):
            return True
    return False


def check_pr_comment_boilerplate(tool_name, tool_input):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    if _is_pr_comment_api_call(command) and _has_boilerplate(command, AI_BOILERPLATE_PATTERNS):
        _block("Boilerplate patterns are not allowed in PR comments. Please remove references to "
               "Claude Code, AI generation, co-authorship with Claude, or Anthropic email addresses "
               "from your comment and try again.")


def _require_timeout(tool_input, command_type, wanted_timeout):
    timeout = tool_input.get('timeout')
    if timeout != wanted_timeout:
        _block(create_timeout_error_message(command_type, wanted_timeout, timeout))


def add_timeout_to_git_commit(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    if any(sub == 'commit' for sub, _, _ in git_invocations(tool_input.get('command', ''), payload_cwd)):
        _require_timeout(tool_input, "Git commit", 900000)


def add_timeout_to_git_push(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    if any(sub == 'push' for sub, _, _ in git_invocations(tool_input.get('command', ''), payload_cwd)):
        _require_timeout(tool_input, "Git push", 900000)


def add_timeout_to_gh_run_watch(tool_name, tool_input):
    if tool_name != 'Bash':
        return
    if _gh_subcommand_present(tool_input.get('command', ''), ['run', 'watch']):
        _require_timeout(tool_input, "gh run watch", 1800000)


# --- Worktree placement and session links ---------------------------------------
# These checks also read nested shell text and fall back to a raw text scan when a command cannot be parsed.

WRAPPER_NAMES = ('git', 'gitp', 'git-as-app.sh', 'gh', 'ghp', 'gh-as-app.sh')
PERSONA_FRONTENDS = ('git-as-app.sh', 'gh-as-app.sh')
SHELL_NAMES = ('bash', 'sh', 'zsh', 'dash', 'ksh', 'ash', 'mksh', 'fish', 'csh', 'tcsh')
PREFIX_SPECS = {
    'env': (('-u', '--unset', '-C', '--chdir'), 0),
    'sudo': (('-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from', '-D', '--chdir',
              '-R', '--chroot', '-T', '--command-timeout', '-U', '--other-user', '-r', '--role', '-t', '--type'), 0),
    'doas': (('-u', '-C'), 0),
    'timeout': (('-s', '--signal', '-k', '--kill-after'), 1),
    'nice': (('-n', '--adjustment'), 0),
    'ionice': (('-c', '--class', '-n', '--classdata', '-p', '-P', '-u'), 0),
    'stdbuf': (('-i', '-o', '-e', '--input', '--output', '--error'), 0),
    'watch': (('-n', '--interval'), 0),
    'xargs': (('-a', '--arg-file', '-d', '--delimiter', '-E', '-I', '-L', '-n', '--max-args', '-P', '--max-procs',
               '-s', '--max-chars', '--max-lines', '--process-slot-var'), 0),
    'exec': (('-a',), 0),
    'nohup': ((), 0),
    'command': ((), 0),
    'builtin': ((), 0),
    'time': ((), 0),
    'setsid': ((), 0),
    'chronic': ((), 0),
    'unbuffer': ((), 0),
    'busybox': ((), 0),
}
ASSIGNMENT_PREFIXES = ('env', 'sudo', 'doas')
SSH_VALUE_OPTIONS = ('-b', '-c', '-D', '-E', '-e', '-F', '-I', '-i', '-J', '-L', '-l', '-m', '-O', '-o', '-p', '-Q',
                     '-R', '-S', '-W', '-w')
FIND_EXEC_FLAGS = ('-exec', '-execdir', '-ok', '-okdir')
USER_SWITCH_NAMES = ('su', 'runuser')
COMMAND_STRING_NAMES = ('sg', 'script', 'flock')
INTERPRETER_RE = re.compile(r'(?:python|pypy|perl|ruby|node|nodejs|php|lua|luajit|expect|osascript|tclsh|deno|bun)[0-9.\-]*$')
AWK_RE = re.compile(r'[gm]?awk$')
INTERPRETER_CODE_FLAG_RE = re.compile(r'-[A-Za-z]*[ceErp][A-Za-z]*$|--(?:eval|print)$')
EXEC_CALL_RE = re.compile(
    r'system|popen|exec|spawn|\bcall\b|\brun\b|Popen|check_(?:call|output)|getoutput|getstatusoutput|eval\b|shell_exec|'
    r'passthru|proc_open|child_process|\bqx\b|`|getline|\bshell\b')
PARALLEL_NAMES = ('parallel', 'sem')
PARALLEL_INPUT_MARKERS = (':::', '::::', ':::+', '::::+')
PARALLEL_VALUE_OPTIONS = ('-j', '--jobs', '-S', '--sshlogin', '-a', '--arg-file', '-N', '-n', '--max-args',
                          '--max-replace-args', '-I', '--halt', '--timeout', '--delay', '-d', '--delimiter', '--colsep',
                          '-C', '--results', '--joblog', '-L', '-l', '--max-lines', '--retries', '--tagstring', '--shell',
                          '--load', '--memfree', '-E', '--eof', '--basefile', '--wd', '--workdir', '--sshloginfile',
                          '--slf', '--limit', '--rpl', '--plus')
PARALLEL_PLACEHOLDERS = ('{/.}', '{//}', '{/}', '{.}', '{#}', '{}')
PARALLEL_POSITIONAL_RE = re.compile(r'\{-?[0-9]+(?:/|//|\.|/\.)?\}')
PARALLEL_FILE_INPUT_OPTIONS = ('-a', '--arg-file', '--arg-file-sep')
TMUX_COMMAND_NAMES = ('new-session', 'new', 'new-window', 'neww', 'split-window', 'splitw', 'run-shell', 'run', 'send-keys',
                      'send', 'respawn-pane', 'respawnp', 'respawn-window', 'respawnw', 'if-shell', 'if', 'display-popup',
                      'popup', 'pipe-pane', 'pipep', 'set-hook', 'bind-key', 'bind', 'confirm-before', 'confirm',
                      'command-prompt', 'source-file', 'source')
GIT_EXEC_SUBCOMMANDS = ('rebase', 'submodule', 'bisect', 'filter-branch')
RUNUSER_VALUE_OPTIONS = ('-u', '--user', '-g', '--group', '-G', '--supp-group', '-s', '--shell', '-w',
                         '--whitelist-environment', '-c', '--command')
DATA_COMMANDS = ('echo', 'printf', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'ack', 'cat', 'sed', 'awk', 'gawk', 'head',
                 'tail', 'wc', 'sort', 'uniq', 'tee', 'less', 'more', 'man', 'which', 'type', 'ls', 'cut', 'tr',
                 'diff', 'cmp', 'test', '[', 'python', 'python3', 'node', 'ruby', 'perl', 'jq', 'yq', 'touch',
                 'mkdir', 'rm', 'cp', 'mv', 'cd', 'true', 'false', ':', 'export', 'set', 'unset', 'alias', 'read')
STDIN_UNKNOWN = '$STDIN'
RESERVED_WORDS = ('if', 'elif', 'while', 'until', 'then', 'do', 'else', '!', '{', '}', 'fi', 'done', 'time')
ASSIGNMENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\+?=')
GIT_VALUE_OPTIONS = ('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix', '--config-env')
GH_VALUE_OPTIONS = ('-R', '--repo')
GH_API_VALUE_OPTIONS = ('-X', '--method', '-f', '--raw-field', '-F', '--field', '-H', '--header', '-q', '--jq',
                        '-t', '--template', '--input', '--hostname', '--cache', '-p', '--preview')
WORKTREE_ADD_VALUE_OPTIONS = ('-b', '-B', '--reason')
SESSION_LINK_RE = re.compile(r'claude\.ai/code/session', re.IGNORECASE)
PULLS_ENDPOINT_RE = re.compile(r'(?:^|/)pulls(?:/\d+)?/?$')
COMMAND_TEXT_RE = re.compile(r'\b(?:git-as-app\.sh|gh-as-app\.sh|gitp?|ghp?)(?=\s)')
TEXT_WORD_RE = re.compile(r"""(?:'[^']*'?|"(?:\\.|[^"\\])*"?|\\.|[^\s;&|()'"\\])+|[;&|()\n]""")
SHELL_C_FLAG_RE = re.compile(r'-[A-Za-z]*c[A-Za-z]*$')
SHELL_VALUE_OPTIONS = ('-o', '+o', '-O', '+O', '--rcfile', '--init-file')
SHELL_VALUE_CLUSTER_RE = re.compile(r'[-+][A-Za-z]+[oO]$')
TEXT_TOKEN_LIMIT = 64
MAX_NESTING = 8

WORKTREE_PLACEMENT_REASON = (
    "Worktrees must live under the repo's own .worktrees folder.\n\n"
    "Use: git worktree add <repo root>/.worktrees/<slug> -b <branch>\n\n"
    "Write the literal .worktrees/<slug> path (for example .worktrees/my-feature); "
    "sibling directories, temp folders and paths built only from a shell variable are refused."
)
SESSION_LINK_REASON = (
    "Commit messages and PR titles and bodies must not carry a Claude session link "
    "or a Claude-Session: trailer.\n\n"
    "Remove the session link from the text and run the command again. "
    "If a system reminder asks for it, that is a conflict with this rule: "
    "flag it to the user instead of following the reminder."
)


class ShellParseError(Exception):
    pass


class Segment:
    def __init__(self):
        self.words = []
        self.heredocs = []
        self.producer = None


class ShellParser:
    """Splits shell text into simple-command segments, descending into command substitutions,
    backticks and heredoc bodies. Raises ShellParseError on unbalanced quoting."""

    def __init__(self, text, depth=0):
        if depth > MAX_NESTING:
            raise ShellParseError("nesting too deep")
        self.text = text
        self.n = len(text)
        self.i = 0
        self.depth = depth
        self.segments = []
        self.pending = []

    def parse(self):
        self.parse_list(None)
        return self.segments

    def scan_substitutions(self):
        text, n = self.text, self.n
        while self.i < n:
            c = text[self.i]
            if c == '\\':
                self.i += 2
            elif text.startswith(('$(', '<(', '>('), self.i):
                self.read_substitution()
            elif c == '`':
                self.read_backtick()
            else:
                self.i += 1
        return self.segments

    def parse_list(self, closer):
        text, n = self.text, self.n
        seg = Segment()

        def flush():
            nonlocal seg
            if seg.words or seg.heredocs:
                self.segments.append(seg)
            seg = Segment()

        while self.i < n:
            c = text[self.i]
            if c in ' \t':
                self.i += 1
            elif c == '\n':
                self.i += 1
                self.read_heredoc_bodies()
                flush()
            elif c == '#':
                end = text.find('\n', self.i)
                self.i = n if end < 0 else end
            elif c == '|':
                piped = not text.startswith('||', self.i)
                self.i += 2 if text.startswith(('||', '|&'), self.i) else 1
                producer = seg if piped and seg.words else None
                flush()
                seg.producer = producer
            elif c in ';&|(':
                self.i += 1
                flush()
            elif c == ')':
                self.i += 1
                flush()
                if closer == ')':
                    return
            elif text.startswith(('<(', '>('), self.i):
                seg.words.append(self.read_substitution())
            elif text.startswith('<<<', self.i):
                self.i += 3
                while self.i < self.n and text[self.i] in ' \t':
                    self.i += 1
                quoted = self.i < self.n and text[self.i] == "'"
                seg.heredocs.append((self.read_word(), quoted))
            elif text.startswith('<<', self.i):
                self.read_heredoc_marker(seg)
            else:
                seg.words.append(self.read_word())
        flush()
        if closer == ')':
            raise ShellParseError("unterminated command substitution")

    def read_word(self):
        text, n = self.text, self.n
        out = []
        while self.i < n:
            c = text[self.i]
            if c in ' \t\n;&|()':
                break
            if c == '<' and text.startswith('<<', self.i) and not text.startswith('<<<', self.i):
                break
            if c == '\\':
                if self.i + 1 < n and text[self.i + 1] != '\n':
                    out.append(text[self.i + 1])
                self.i += 2
            elif c == "'":
                end = text.find("'", self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated single quote")
                out.append(text[self.i + 1:end])
                self.i = end + 1
            elif c == '"':
                self.read_double_quoted(out)
            elif c == '$' and text.startswith('$(', self.i):
                out.append(self.read_substitution())
            elif c == '`':
                out.append(self.read_backtick())
            else:
                out.append(c)
                self.i += 1
        return ''.join(out)

    def read_double_quoted(self, out):
        text, n = self.text, self.n
        self.i += 1
        while self.i < n:
            c = text[self.i]
            if c == '"':
                self.i += 1
                return
            if c == '\\' and self.i + 1 < n:
                nxt = text[self.i + 1]
                if nxt in '$`"\\':
                    out.append(nxt)
                elif nxt != '\n':
                    out.append('\\' + nxt)
                self.i += 2
            elif c == '$' and text.startswith('$(', self.i):
                out.append(self.read_substitution())
            elif c == '`':
                out.append(self.read_backtick())
            else:
                out.append(c)
                self.i += 1
        raise ShellParseError("unterminated double quote")

    def read_substitution(self):
        start = self.i
        nested = ShellParser(self.text, self.depth + 1)
        nested.i = start + 2
        nested.pending = self.pending
        nested.parse_list(')')
        self.segments.extend(nested.segments)
        self.i = nested.i
        return self.text[start:self.i]

    def read_backtick(self):
        text, n = self.text, self.n
        j = self.i + 1
        while j < n and text[j] != '`':
            j += 2 if text[j] == '\\' else 1
        if j >= n:
            raise ShellParseError("unterminated backtick")
        self.segments.extend(ShellParser(text[self.i + 1:j].replace('\\`', '`'), self.depth + 1).parse())
        raw = text[self.i:j + 1]
        self.i = j + 1
        return raw

    def read_heredoc_marker(self, seg):
        text, n = self.text, self.n
        self.i += 2
        strip_tabs = self.i < n and text[self.i] == '-'
        if strip_tabs:
            self.i += 1
        while self.i < n and text[self.i] in ' \t':
            self.i += 1
        quoted = False
        delim = []
        while self.i < n and text[self.i] not in ' \t\n;&|()<>':
            c = text[self.i]
            if c in '\'"':
                end = text.find(c, self.i + 1)
                if end < 0:
                    raise ShellParseError("unterminated heredoc delimiter")
                delim.append(text[self.i + 1:end])
                quoted = True
                self.i = end + 1
            elif c == '\\':
                quoted = True
                delim.append(text[self.i + 1:self.i + 2])
                self.i += 2
            else:
                delim.append(c)
                self.i += 1
        if not delim:
            raise ShellParseError("empty heredoc delimiter")
        self.pending.append((seg, ''.join(delim), quoted, strip_tabs))

    def read_heredoc_bodies(self):
        text, n = self.text, self.n
        while self.pending:
            seg, delim, quoted, strip_tabs = self.pending.pop(0)
            lines = []
            while True:
                if self.i >= n:
                    raise ShellParseError("unterminated heredoc")
                end = text.find('\n', self.i)
                end = n if end < 0 else end
                line = text[self.i:end]
                self.i = min(end + 1, n)
                if (line.lstrip('\t') if strip_tabs else line) == delim:
                    break
                lines.append(line)
            seg.heredocs.append(('\n'.join(lines), quoted))


def command_base(literal):
    return literal.rpartition('/')[2]


def skip_global_options(tokens, j, value_options):
    count = len(tokens)
    while j < count and tokens[j].startswith('-'):
        j += 2 if tokens[j] in value_options else 1
    return j


def subcommand_index(tokens, k, value_options):
    """Index of the subcommand after the front-end word at tokens[k], skipping the persona argument of the as-app scripts."""
    j = skip_global_options(tokens, k + 1, value_options)
    if command_base(tokens[k]) in PERSONA_FRONTENDS:
        j = skip_global_options(tokens, j + 1, value_options)
    return j


def worktree_path_is_placed(path):
    parts = path.split('/')
    return '.worktrees' in parts and '..' not in parts


def worktree_add_is_misplaced(tokens, j):
    """tokens[j] is the first word after `worktree add`; True when the target path is not under a .worktrees folder."""
    count = len(tokens)
    while j < count:
        token = tokens[j]
        if token == '--':
            j += 1
            break
        if token.startswith('-'):
            j += 2 if token in WORKTREE_ADD_VALUE_OPTIONS else 1
            continue
        break
    if j >= count:
        return False
    return not worktree_path_is_placed(tokens[j])


def worktree_add_at(tokens, k):
    """tokens[k] is a command word; True for a git worktree add whose target is misplaced."""
    if command_base(tokens[k]) not in ('git', 'gitp', 'git-as-app.sh'):
        return False
    j = subcommand_index(tokens, k, GIT_VALUE_OPTIONS)
    if j + 1 < len(tokens) and tokens[j] == 'worktree' and tokens[j + 1] == 'add':
        return worktree_add_is_misplaced(tokens, j + 2)
    return False


def api_endpoint(tokens, j):
    """First positional after `api`, skipping option values such as `-f body=...`; None when absent."""
    count = len(tokens)
    while j < count:
        token = tokens[j]
        if token == '--':
            return tokens[j + 1] if j + 1 < count else None
        if token.startswith('-'):
            j += 2 if token in GH_API_VALUE_OPTIONS else 1
            continue
        return token
    return None


def publish_command_at(tokens, k):
    """tokens[k] is a command word; True for a commit, a PR create/edit, or a pull-request API call."""
    name = command_base(tokens[k])
    count = len(tokens)
    if name in ('git', 'gitp', 'git-as-app.sh'):
        j = subcommand_index(tokens, k, GIT_VALUE_OPTIONS)
        return j < count and tokens[j] == 'commit'
    if name in ('gh', 'ghp', 'gh-as-app.sh'):
        j = subcommand_index(tokens, k, GH_VALUE_OPTIONS)
        if j >= count:
            return False
        if tokens[j] == 'pr':
            return j + 1 < count and tokens[j + 1] in ('create', 'edit')
        if tokens[j] == 'api':
            endpoint = api_endpoint(tokens, j + 1)
            return endpoint is not None and PULLS_ENDPOINT_RE.search(endpoint) is not None
    return False


def text_command_tokens(text, pos):
    tokens = []
    for match in TEXT_WORD_RE.finditer(text, pos):
        word = match.group(0)
        if word in (';', '&', '|', '(', ')', '\n'):
            break
        tokens.append(re.sub(r'[\'"]', '', word))
        if len(tokens) >= TEXT_TOKEN_LIMIT:
            break
    return tokens


def text_matches_command(text, predicate):
    for match in COMMAND_TEXT_RE.finditer(text):
        if predicate([match.group(0)] + text_command_tokens(text, match.end()), 0):
            return True
    return False


def text_runs_misplaced_worktree(text):
    return text_matches_command(text, worktree_add_at)


def text_runs_publish_command(text):
    return text_matches_command(text, publish_command_at)


def command_position(tokens):
    for k, token in enumerate(tokens):
        if not ASSIGNMENT_RE.match(token) and token not in RESERVED_WORDS:
            return k
    return None


class Resolved:
    """What one segment runs: command words to test, nested scripts to parse, and whether a shell reads stdin."""

    def __init__(self):
        self.commands = []
        self.scripts = []
        self.reads_stdin = False
        self.texts = []
        self.text_stdin = False


def skip_prefix_options(tokens, k, out):
    """Index of the command word after the prefix command at tokens[k]; also reports the xargs placeholder."""
    base = command_base(tokens[k])
    value_options, positionals = PREFIX_SPECS[base]
    count = len(tokens)
    j = k + 1
    repl = None
    while j < count:
        token = tokens[j]
        if token == '--':
            j += 1
            break
        if base in ASSIGNMENT_PREFIXES and ASSIGNMENT_RE.match(token):
            j += 1
        elif base == 'env' and token in ('-S', '--split-string') and j + 1 < count:
            out.scripts.append(tokens[j + 1])
            return None, repl
        elif base == 'env' and (token.startswith('--split-string=') or (token.startswith('-S') and len(token) > 2)):
            out.scripts.append(token.split('=', 1)[1] if token.startswith('--') else token[2:])
            return None, repl
        elif token.startswith('-') and len(token) > 1:
            if base == 'xargs':
                if token == '-I' and j + 1 < count:
                    repl = tokens[j + 1]
                elif token.startswith('-I') and len(token) > 2:
                    repl = token[2:]
                elif token.startswith('--replace='):
                    repl = token.split('=', 1)[1]
                elif token in ('-i', '--replace'):
                    repl = '{}'
                elif token.startswith('-i') and len(token) > 2 and not token.startswith('--'):
                    repl = token[2:]
            cluster_value = (base in ASSIGNMENT_PREFIXES and len(token) > 2 and token[1] != '-'
                             and '-' + token[-1] in value_options)
            j += 2 if token in value_options or cluster_value else 1
        else:
            break
    j += positionals
    return (j if j < count else None), repl


def runuser_command_index(tokens, k):
    """Index of the command `runuser -u USER` runs, or None when it uses the user-then-arguments form."""
    j = k + 1
    direct = False
    while j < len(tokens):
        tok = tokens[j]
        if tok == '--':
            j += 1
            break
        if tok in RUNUSER_VALUE_OPTIONS:
            direct = direct or tok in ('-u', '--user')
            j += 2
        elif tok.startswith('-') and len(tok) > 1:
            direct = direct or tok.startswith('--user=') or (tok.startswith('-u') and not tok.startswith('--'))
            j += 1
        else:
            break
    return j if direct else None


def parallel_substitute(token, value, replace):
    base = value.rstrip('/').rpartition('/')[2]
    forms = {
        '{/.}': base.rsplit('.', 1)[0] if '.' in base else base,
        '{//}': value.rstrip('/').rpartition('/')[0] or '.',
        '{/}': base,
        '{.}': value.rsplit('.', 1)[0] if '.' in base else value,
    }
    for mark in PARALLEL_PLACEHOLDERS + ((replace,) if replace else ()):
        token = token.replace(mark, forms.get(mark, value))
    return PARALLEL_POSITIONAL_RE.sub(lambda match: value, token)


def resolve_parallel(tokens, k, out, names, depth):
    """GNU parallel and sem: the command template, the ::: inputs substituted into or appended to it, or stdin lines."""
    count = len(tokens)
    end = next((i for i in range(k + 1, count) if tokens[i] in PARALLEL_INPUT_MARKERS), count)
    replace = None
    j = k + 1
    while j < end:
        tok = tokens[j]
        if tok == '--':
            j += 1
            break
        if tok == '-I' and j + 1 < end:
            replace = tokens[j + 1]
        elif tok.startswith('-I') and len(tok) > 2 and not tok.startswith('--'):
            replace = tok[2:]
        if not tok.startswith('-') or len(tok) == 1:
            break
        j += 2 if tok in PARALLEL_VALUE_OPTIONS else 1
    template = tokens[j:end]
    out.texts.extend(tok for tok in template if re.search(r'\s', tok))
    inputs = []
    marker = None
    file_inputs = any(tok in PARALLEL_FILE_INPUT_OPTIONS for tok in tokens[k + 1:j])
    for tok in tokens[end:]:
        if tok in PARALLEL_INPUT_MARKERS:
            marker = tok
            file_inputs = file_inputs or tok in ('::::', '::::+')
        elif marker in (':::', ':::+'):
            inputs.append(tok)
    inputs = inputs[:TEXT_TOKEN_LIMIT]
    placeholder = any(
        mark in tok or PARALLEL_POSITIONAL_RE.search(tok)
        for tok in template for mark in PARALLEL_PLACEHOLDERS + ((replace,) if replace else ()))
    unknown_fed = None if '--pipe' in tokens[k + 1:j] or '--pipepart' in tokens[k + 1:j] else (
        (replace or '{}') if placeholder else '')
    if end < count:
        if not template:
            out.scripts.extend(inputs)
            out.reads_stdin = out.reads_stdin or file_inputs
            return
        for value in inputs:
            if placeholder:
                words = [parallel_substitute(tok, value, replace) for tok in template]
            else:
                words = template + [value]
            if template[0].startswith(PARALLEL_PLACEHOLDERS):
                out.scripts.append(' '.join(words))
            resolve_command(tokens[:j] + words, j, out, names, depth + 1)
        if not inputs or file_inputs:
            resolve_command(tokens[:end], j, out, names, depth + 1, unknown_fed if file_inputs else None)
    elif template:
        resolve_command(tokens, j, out, names, depth + 1, unknown_fed)
    else:
        out.reads_stdin = True


def resolve_command(tokens, k, out, names, depth=0, fed=None):
    """Follows the command word at tokens[k] through prefixes, shells, ssh and find -exec; `fed` is the xargs placeholder when stdin adds arguments."""
    if k is None or k >= len(tokens):
        return
    if depth > MAX_NESTING:
        raise ShellParseError("nesting too deep")
    base = command_base(tokens[k])
    count = len(tokens)
    if tokens[k][:2] == '$(' or tokens[k][:1] == '`':
        out.scripts += [' '.join([text] + tokens[k + 1:]) for text in substitution_scripts(tokens[k], depth)]
    if base in WRAPPER_NAMES:
        out.commands.append((tokens, k, fed))
        rest = tokens[k + 1:]
        if any(tok in GIT_EXEC_SUBCOMMANDS or tok.startswith(('alias.', '--exec')) for tok in rest):
            out.texts.append(' '.join(rest))
    elif base == 'eval':
        out.scripts.append(' '.join(tokens[k + 1:]))
    elif base in SHELL_NAMES:
        j = k + 1
        while j < count and tokens[j][:1] in ('-', '+') and not SHELL_C_FLAG_RE.match(tokens[j]):
            j += 2 if tokens[j] in SHELL_VALUE_OPTIONS or SHELL_VALUE_CLUSTER_RE.match(tokens[j]) else 1
        if j + 1 < count and SHELL_C_FLAG_RE.match(tokens[j]):
            j += 2 if tokens[j + 1] == '--' and j + 2 < count else 1
            out.scripts.append(tokens[j])
        else:
            out.reads_stdin = True
            out.scripts.extend(tok for tok in tokens[j:] if tok.startswith('<('))
    elif base in ('source', '.'):
        out.scripts.extend(tok for tok in tokens[k + 1:] if tok.startswith('<('))
    elif base in ('at', 'batch'):
        out.reads_stdin = True
    elif base in USER_SWITCH_NAMES:
        command_at = runuser_command_index(tokens, k) if base == 'runuser' else None
        scan_end = count if command_at is None else command_at
        for j in range(k + 1, scan_end - 1):
            if tokens[j] == '--command' or SHELL_C_FLAG_RE.match(tokens[j]):
                out.scripts.append(tokens[j + 1])
        if command_at is not None:
            resolve_command(tokens, command_at, out, names, depth + 1)
    elif base in PARALLEL_NAMES:
        resolve_parallel(tokens, k, out, names, depth)
    elif base in ('yarn', 'pnpm', 'npm') and 'exec' in tokens[k + 1:]:
        out.texts.append(' '.join(tokens[tokens.index('exec', k + 1) + 1:]))
    elif base == 'tmux':
        if any(tok in TMUX_COMMAND_NAMES for tok in tokens[k + 1:]):
            out.texts.append(' '.join(tokens[k + 1:]))
    elif base == 'screen':
        out.texts.append(' '.join(tokens[k + 1:]))
    elif AWK_RE.match(base):
        text = ' '.join(tokens[k + 1:])
        if EXEC_CALL_RE.search(text):
            out.texts.append(text)
    elif INTERPRETER_RE.match(base):
        flag = next((j for j in range(k + 1, count) if INTERPRETER_CODE_FLAG_RE.match(tokens[j])), None)
        if flag is not None:
            text = ' '.join(tokens[flag + 1:])
            if EXEC_CALL_RE.search(text):
                out.texts.append(text)
        elif all(tok.startswith('-') for tok in tokens[k + 1:]):
            out.text_stdin = True
    elif base == 'ssh':
        j = k + 1
        while j < count and tokens[j].startswith('-'):
            j += 2 if tokens[j] in SSH_VALUE_OPTIONS else 1
        if j + 1 < count:
            out.scripts.append(' '.join(tokens[j + 1:]))
            resolve_command(tokens, j + 1, out, names, depth + 1)
        else:
            out.reads_stdin = True
    elif base == 'find':
        for j in range(k + 1, count):
            if tokens[j] in FIND_EXEC_FLAGS:
                end = next((e for e in range(j + 1, count) if tokens[e] in (';', '+')), count)
                resolve_command(tokens[j + 1:end], 0, out, names, depth + 1)
    elif base in PREFIX_SPECS:
        nxt, repl = skip_prefix_options(tokens, k, out)
        if nxt is not None and (base == 'watch' or (base == 'sudo' and any(
                tok in ('--shell', '--login') or (tok[:1] == '-' and tok[1:2] != '-' and set(tok[1:]) & set('is'))
                for tok in tokens[k + 1:nxt]))):
            out.scripts.append(' '.join(tokens[nxt:]))
        if base == 'xargs':
            fed = repl or ''
        resolve_command(tokens, nxt, out, names, depth + 1, fed)
    elif base not in DATA_COMMANDS:
        if base in COMMAND_STRING_NAMES:
            out.scripts.extend(tokens[j + 1] for j in range(k + 1, count - 1)
                               if tokens[j] == '--command' or SHELL_C_FLAG_RE.match(tokens[j]))
        cleared = k + 1
        for j in range(k + 1, count):
            if j < cleared:
                continue
            if command_base(tokens[j]) in names:
                out.commands.append((tokens, j, fed))
                cleared = j + 1
                while cleared < count and tokens[cleared].startswith('-'):
                    cleared += 1
            elif command_base(tokens[j]) in SHELL_NAMES:
                resolve_command(tokens, j, out, names, depth + 1)


def resolve_segment(seg, names):
    out = Resolved()
    resolve_command(seg.words, command_position(seg.words), out, names)
    return out


def producer_lines(seg):
    """Literal lines an echo or printf producer writes to a pipe; None when the producer is anything else."""
    if seg is None or seg.heredocs:
        return None
    pos = command_position(seg.words)
    if pos is None or command_base(seg.words[pos]) not in ('echo', 'printf'):
        return None
    args = [t for t in seg.words[pos + 1:] if not (t.startswith('-') and len(t) > 1)]
    if command_base(seg.words[pos]) == 'echo':
        return [' '.join(args)]
    return [t for t in args if '%' not in t]


def producer_texts(seg):
    """Texts a pipe producer writes: literal echo or printf lines, or the heredoc and here-string bodies of cat."""
    lines = producer_lines(seg) or []
    texts = lines + [' '.join(lines)] if lines else []
    pos = command_position(seg.words) if seg is not None else None
    if pos is not None and command_base(seg.words[pos]) == 'cat':
        texts += [body for body, _ in seg.heredocs]
    return texts


def fed_variants(tokens, repl, producer):
    """Argument lists the xargs-run command may get from the piped lines; $STDIN stands for unknown input."""
    lines = producer_lines(producer)
    if lines is None:
        lines = [STDIN_UNKNOWN]
    lines = lines[:TEXT_TOKEN_LIMIT]
    if repl:
        return [[tok.replace(repl, line) for tok in tokens] for line in lines]
    words = ' '.join(lines).split()
    variants = [tokens + words]
    if any(re.match(r'-[nLl]|--max-(?:args|lines)', tok) for tok in tokens):
        variants += [tokens + [word] for word in words]
    return variants


def substitution_scripts(script, depth):
    """Texts that echo or printf substitutions inside `script` hand to the command that consumes them."""
    if not any(mark in script for mark in ('$(', '`', '<(', '>(')):
        return []
    texts = []
    for seg in ShellParser(script, depth + 1).scan_substitutions():
        lines = producer_lines(seg)
        if lines:
            texts += lines + [' '.join(lines)]
    return texts


def carrier_text_matches(text, text_predicate):
    """True when text handed to a launcher or interpreter spells a blocked command, also as a quoted argv list."""
    return text_predicate(text) or text_predicate(re.sub(r'["\',\[\]]', ' ', text))


def segments_match(segments, predicate, names, text_predicate, depth):
    if depth > MAX_NESTING:
        raise ShellParseError("nesting too deep")
    resolved = [resolve_segment(seg, names) for seg in segments]
    shell_stdin = any(out.reads_stdin for out in resolved)
    for seg, out in zip(segments, resolved):
        for tokens, k, fed in out.commands:
            if fed is None:
                if predicate(tokens, k):
                    return True
            else:
                head = tokens[:k + 1]
                rest = tokens[k + 1:]
                for variant in fed_variants(rest, fed, seg.producer):
                    if predicate(head + variant, k):
                        return True
        scripts = list(out.scripts)
        if out.reads_stdin and seg.producer is not None:
            scripts += producer_texts(seg.producer)
        if out.text_stdin and seg.producer is not None and any(
                EXEC_CALL_RE.search(text) and carrier_text_matches(text, text_predicate)
                for text in producer_texts(seg.producer)):
            return True
        scripts += [text for script in scripts for text in substitution_scripts(script, depth)]
        for script in scripts:
            if segments_match(ShellParser(script, depth + 1).parse(), predicate, names, text_predicate, depth + 1):
                return True
        for text in out.texts:
            if carrier_text_matches(text, text_predicate):
                return True
        for body, quoted in seg.heredocs:
            if out.text_stdin and EXEC_CALL_RE.search(body) and carrier_text_matches(body, text_predicate):
                return True
            if shell_stdin:
                nested = ShellParser(body, depth + 1).parse()
            elif not quoted:
                nested = ShellParser(body, depth + 1).scan_substitutions()
            else:
                continue
            if segments_match(nested, predicate, names, text_predicate, depth + 1):
                return True
    return False


def command_matches(command, predicate, names, text_predicate):
    try:
        return segments_match(ShellParser(command).parse(), predicate, names, text_predicate, 0)
    except Exception:
        try:
            return text_predicate(command)
        except Exception:
            return True


def runs_misplaced_worktree(command):
    return command_matches(command, worktree_add_at, ('git', 'gitp', 'git-as-app.sh'), text_runs_misplaced_worktree)


def carries_session_link_in_commit_or_pr(command):
    if not SESSION_LINK_RE.search(command):
        return False
    return command_matches(command, publish_command_at, WRAPPER_NAMES, text_runs_publish_command)


def check_worktree_placement(tool_name, tool_input, payload_cwd=None):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    if not command:
        return
    try:
        misplaced = runs_misplaced_worktree(command)
    except Exception:
        misplaced = True
    if misplaced:
        _block(WORKTREE_PLACEMENT_REASON)


def check_session_link_in_publish(tool_name, tool_input):
    if tool_name != 'Bash':
        return
    command = tool_input.get('command', '')
    if not command:
        return
    try:
        carries_link = carries_session_link_in_commit_or_pr(command)
    except Exception:
        carries_link = True
    if carries_link:
        _block(SESSION_LINK_REASON)


# --- End worktree placement and session links -----------------------------------


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})
        payload_cwd = input_data.get('cwd')

        existing_checks = (
            lambda: add_timeout_to_git_commit(tool_name, tool_input, payload_cwd),
            lambda: add_timeout_to_git_push(tool_name, tool_input, payload_cwd),
            lambda: add_timeout_to_gh_run_watch(tool_name, tool_input),
            lambda: check_git_commit_branch(tool_name, tool_input, payload_cwd),
            lambda: check_git_branch_policy(tool_name, tool_input, payload_cwd),
            lambda: check_git_no_verify(tool_name, tool_input, payload_cwd),
            lambda: check_git_commit_boilerplate(tool_name, tool_input, payload_cwd),
            lambda: check_pr_create_boilerplate(tool_name, tool_input),
            lambda: check_pr_comment_boilerplate(tool_name, tool_input),
        )
        for run_check in existing_checks:
            try:
                run_check()
            except Exception:
                pass
        check_worktree_placement(tool_name, tool_input, payload_cwd)
        check_session_link_in_publish(tool_name, tool_input)

        sys.exit(0)
    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == '__main__':
    main()
