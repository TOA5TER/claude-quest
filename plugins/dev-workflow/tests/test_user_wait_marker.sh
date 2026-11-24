#!/usr/bin/env bash
# Tests that full-cycle requires the needs-user marker at every user wait and that it
# matches the attention-hub plugin's marker folder and README command.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"
HUB="$PLUGIN_ROOT/../attention-hub"
CMD='mkdir -p ~/.claude/attention-hub/needs-user-markers && touch ~/.claude/attention-hub/needs-user-markers/"${CLAUDE_CODE_SESSION_ID:?}"'
POINTER='(set the user-wait marker per "User Waits")'

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

# Why: R-8 needs one general rule that carries the exact command and its edge cases.
SECTION="$(awk '/^## User Waits$/{f=1;next} /^## /{f=0} f' "$SKILL")"
[ -n "$SECTION" ] || fail "full-cycle SKILL.md has no '## User Waits' section"
for word in "every" "worker message" "AskUserQuestion"; do
  case "$SECTION" in *"$word"*) ;; *) fail "User Waits section lacks the words: $word" ;; esac
done
case "$SECTION" in *"$CMD"*) ;; *) fail "User Waits section lacks the exact marker command" ;; esac

# Why: R-9 puts the rule where each wait happens, so a skipped site shows as a gray card.
# Each anchor is text unique to one wait; the pointer must sit on the same line.
check_pointer() {
  local label="$1" anchor="$2"
  if ! grep -F -- "$anchor" "$SKILL" | grep -qF -- "$POINTER"; then
    fail "no user-wait pointer at: $label"
  fi
}
check_pointer "spec-writer loop step 1" "Ask the user the questions verbatim"
check_pointer "spec-writer loop step 2" "request changes to specific specs"
check_pointer "spec-writer loop step 5" "resolve it with the user yourself"
check_pointer "hard gate" "re-request explicit approval from the user"
check_pointer "DT-1 row 1" "Stop that repo's pipeline and ask the user"
check_pointer "resume hard stop" "hard stop and surface it to the user"
check_pointer "COMMENTED / PENDING" "surface this to the user"
check_pointer "Loop Safety Guard" "authorize more cycles"
check_pointer "Local Mode blocked command" "it is an unresolved block: stop and ask"
check_pointer "Local Mode backstop" "is the backstop stop: stop and ask"
check_pointer "Local Mode Stops" "the user is asked to act once, at Termination"
check_pointer "termination ask" "say they remain available for more work, and ask once"

# Why: the plugins share no code, so the path and command are guarded here against drift.
grep -qF "needs-user-markers" "$HUB/hooks/attention_hub_client.py" || fail "attention-hub client lacks the needs-user-markers folder"
grep -qF -- "$CMD" "$HUB/README.md" || fail "attention-hub README lacks the exact marker command"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS: test_user_wait_marker.sh"
  exit 0
fi
echo "$FAILURES failure(s)" >&2
exit 1
