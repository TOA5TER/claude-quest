#!/usr/bin/env bash
# Tests that full-cycle requires the needs-user marker at every user wait, matching attention-hub.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"
HUB="$PLUGIN_ROOT/../attention-hub"
CMD='mkdir -p ~/.claude/attention-hub/needs-user-markers && touch ~/.claude/attention-hub/needs-user-markers/"${CLAUDE_CODE_SESSION_ID:?}" && test -f ~/.claude/attention-hub/needs-user-markers/"${CLAUDE_CODE_SESSION_ID:?}" || exit 1'
REPL_SHELL='mkdir -p ~/.claude/attention-hub/needs-user-markers && touch ~/.claude/attention-hub/needs-user-markers/"$CLAUDE_CODE_SESSION_ID" && test -f ~/.claude/attention-hub/needs-user-markers/"$CLAUDE_CODE_SESSION_ID" || exit 1'
REPL_CMD='await $`'"$REPL_SHELL"'`'
WARN='Never pass the command, or the session id, as a ${} value inside your own quotes: the helper quotes every ${} value itself, so the file is created under a name that carries the quote marks and the hub never sees it.'
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
POINTER='(set the user-wait marker per "User Waits")'

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

mkdir -p "$REPO_ROOT/.scratch/tmp"
TMPD="$(mktemp -d "$REPO_ROOT/.scratch/tmp/user-wait-test.XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

# Why: one general rule must carry the exact command and its edge cases so no wait is missed.
SECTION="$(awk '/^## User Waits$/{f=1;next} /^## /{f=0} f' "$SKILL")"
[ -n "$SECTION" ] || fail "full-cycle SKILL.md has no '## User Waits' section"
for word in "every" "worker message" "AskUserQuestion" "watcher" "task notification" "scheduled wake-up" "TaskStop" "CronDelete" "paused on the user" "terminal reply" "stale watcher" "user-wait turn"; do
  case "$SECTION" in *"$word"*) ;; *) fail "User Waits section lacks the words: $word" ;; esac
done
case "$SECTION" in *"$CMD"*) ;; *) fail "User Waits section lacks the exact marker command" ;; esac
case "$REPL_CMD" in *'${'*) fail "the REPL form contains \${" ;; esac
case "$SECTION" in *"$REPL_CMD"*) ;; *) fail "User Waits section lacks the exact REPL marker command" ;; esac
case "$SECTION" in *"$WARN"*) ;; *) fail "User Waits section lacks the \${} warning sentence" ;; esac
for form in "$CMD" "$REPL_SHELL"; do
  case "$form" in *" || exit 1") ;; *) fail "marker form does not end with || exit 1 (the REPL helper reads a bare failing test as an answer): $form" ;; esac
  body="${form% || exit 1}"
  last="${body##*&& }"
  case "$last" in "test -f ~/.claude/attention-hub/needs-user-markers/"*) ;; *) fail "marker form does not have a test -f on the marker path before || exit 1: $form" ;; esac
done

# Why: both forms must create exactly the session-id file, and fail loudly when the id is missing.
check_form() {
  local label="$1" form="$2" home="$TMPD/home-$1"
  mkdir -p "$home"
  HOME="$home" CLAUDE_CODE_SESSION_ID=sid-123 bash -c "$form" || fail "$label form failed with the session id set"
  [ "$(ls "$home/.claude/attention-hub/needs-user-markers")" = "sid-123" ] || fail "$label form did not create exactly the session-id file"
  [ -f "$home/.claude/attention-hub/needs-user-markers/sid-123" ] || fail "$label form file is not a regular file"
  local bad="$TMPD/home-$1-unset"
  mkdir -p "$bad"
  if env -u CLAUDE_CODE_SESSION_ID HOME="$bad" bash -c "$form" 2>/dev/null; then fail "$label form exited 0 with the session id unset"; fi
  [ -z "$(find "$bad/.claude/attention-hub/needs-user-markers" -type f 2>/dev/null)" ] || fail "$label form created a regular file with the session id unset"
}
check_form bash "$CMD"
check_form repl "$REPL_SHELL"

# Why: the rule sits at each wait so a skipped site shows as a gray card; each anchor is unique to one wait.
check_pointer() {
  local label="$1" anchor="$2"
  local matches
  matches="$(grep -F -- "$anchor" "$SKILL" || true)"
  [ -n "$matches" ] || { fail "anchor not found for: $label"; return; }
  if printf '%s\n' "$matches" | grep -qvF -- "$POINTER"; then
    fail "a line at '$label' lacks the user-wait pointer"
  fi
}
check_pointer "spec-writer loop step 1" "Ask the user the questions verbatim"
check_pointer "spec-writer loop step 2" "request changes to specific specs"
check_pointer "spec-writer loop step 5" "resolve it with the user yourself"
check_pointer "hard gate" "re-request explicit approval from the user"
check_pointer "DT-1 row 1" "Stop that repo's pipeline and ask the user"
check_pointer "resume hard stop" "hard stop and surface it to the user"
check_pointer "COMMENTED / PENDING" "surface this to the user"
check_pointer "Loop Safety Guard" "and ask whether the user wants to authorize more cycles"
check_pointer "Local Mode blocked command" "it is an unresolved block: stop and ask"
check_pointer "Local Mode backstop" "is the backstop stop: stop and ask"
check_pointer "Local Mode Stops" "the user is asked to act once, at Termination"
check_pointer "termination ask" "say they remain available for more work, and ask once"

# Why: the plugins share no code, so the path and command are guarded here against drift.
grep -qF "needs-user-markers" "$HUB/hooks/attention_hub_client.py" || fail "attention-hub client lacks the needs-user-markers folder"
grep -qF -- "$CMD" "$HUB/README.md" || fail "attention-hub README lacks the exact marker command"
grep -qF -- "$REPL_CMD" "$HUB/README.md" || fail "attention-hub README lacks the exact REPL marker command"
grep -qF -- "$WARN" "$HUB/README.md" || fail "attention-hub README lacks the \${} warning sentence"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS: test_user_wait_marker.sh"
  exit 0
fi
echo "$FAILURES failure(s)" >&2
exit 1
