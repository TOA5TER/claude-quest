#!/usr/bin/env bash
# Tests that mandatory developer steps cannot be skipped and the orchestrator enforces them.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$PLUGIN_ROOT/skills"
A="$PLUGIN_ROOT/agents"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' -e 's/\*\*//g' | tr '\n' ' ' | sed -e 's/  */ /g'
}

whole() {
  normalise < "$1"
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

# need FILE LABEL PHRASE... : every phrase must appear in the normalised file.
need() {
  local file="$1" label="$2" text phrase
  shift 2
  [ -f "$file" ] || { fail "$label: file missing"; return; }
  text="$(whole "$file")"
  for phrase in "$@"; do
    has "$text" "$phrase" || fail "$label: missing '$phrase'"
  done
}

# forbid FILE LABEL PHRASE... : none of the phrases may appear.
forbid() {
  local file="$1" label="$2" text phrase
  shift 2
  text="$(whole "$file")"
  for phrase in "$@"; do
    has "$text" "$phrase" && fail "$label: still contains '$phrase'"
  done
}

# before FILE LABEL FIRST SECOND : FIRST must appear earlier in the file than SECOND.
before() {
  local file="$1" label="$2" a b
  a="$(grep -n -m1 -F -- "$3" "$file" | cut -d: -f1)"
  b="$(grep -n -m1 -F -- "$4" "$file" | cut -d: -f1)"
  { [ -n "$a" ] && [ -n "$b" ]; } || { fail "$label: ordering anchors not found ('$3' / '$4')"; return; }
  [ "$a" -lt "$b" ] || fail "$label: '$3' must appear before '$4'"
}

# Why: a mandatory review skipped by the worker must be enforced, not turned into a question.
DV="$S/developing/SKILL.md"
DEV="$A/developer.md"
ST="$S/shared/standards.md"
FC="$S/full-cycle/SKILL.md"
RS="$S/shared/role-sessions.md"
CC="$S/shared/context-compaction.md"
TO="$S/toiling/SKILL.md"

need "$DV" "developing" "mandatory step" "hard-fail rule" "Internal Code Review ran before the PR was opened" \
  "Adversarial Review ran, and its fixes are committed before the final dev gate" \
  "step blocked: <step name>:" "never skipped" "before the branch is pushed and the PR is opened"
before "$DV" "developing" "## Internal Code Review" "## Commit and PR Process"
before "$DV" "developing" "## Debugging and Problem Solving" "## Internal Code Review"

need "$DEV" "developer.md" "mode: complete" "## Complete mode (develop with mode: complete)" \
  "dev-workflow:dev-verify" "mode: local" "full branch diff" "skipped-steps: none" \
  "Internal Code Review, Adversarial Review and the Dev Deploy & Verify gate all ran" \
  "skipped: <step names>"

need "$ST" "standards.md" "skipped-steps" "Any value other than none requires status: error" \
  "A worker's rule break is the orchestrator's to enforce" "without asking the user"

need "$FC" "full-cycle" "mode: complete" "without asking the user" "Counts one cycle" \
  "else, when the key is absent or names nothing, both \"Internal Code Review\" and \"Adversarial Review\"" \
  "skipped: none" "rows 2 and 3 count" "DT-1 rows 1, 2 and 4 apply" "skipped-steps, plus" \
  "does not count against the Loop Safety Guard" "A draft PR still blocks"
forbid "$FC" "full-cycle" "only as a hint" "A round blocked at the hand-off check does not count as a cycle" \
  "a failed check surfaces a developer block and does not increment the loop counter"
if grep -qi "skip the PR-number read and the hand-off check for Local Mode repos" "$FC"; then
  fail "full-cycle: unqualified Local Mode hand-off skip remains"
fi

need "$RS" "role-sessions.md" "mode: complete" "Developer result names a skipped mandatory step"
need "$CC" "context-compaction.md" "After a DT-1 re-request is sent" "review_loop_count"
need "$TO" "toiling" "skipped-steps"

if [ "$FAILURES" -ne 0 ]; then
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
echo "PASS: mandatory steps"
