#!/usr/bin/env bash
# Tests that the dev deploy and verification gate wording appears where each role acts.
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

# Why: five skills share one procedure, so the shared file must own every rule the others point at.
DD="$S/shared/dev-deploy.md"
need "$DD" "dev-deploy.md" \
  "dev-verification sha=" "run=" "workflow=" "exempt=" \
  "missing" "stale" "invalid" "valid" \
  "headSha" "created after the trigger" \
  "treated as the same word" "plan" "apply" "terraform-plan / Security Scan" \
  "touches the build workflow file" "builds fresh" \
  "newest run of the deploy workflow across all branches and triggers" \
  "ci_gate_exempt_repos" "deploy_gate_exempt_repos" \
  "never satisfy the gate"

# Why: the verify entry point must resolve by skill name and reuse the shared procedure.
need "$S/dev-verify/SKILL.md" "dev-verify" "skills/shared/dev-deploy.md" "PR number" "dev-verification blocked:"

# Why: a draft PR is the "not ready" signal until evidence is posted, and the gate must follow adversarial review.
DEV="$S/developing/SKILL.md"
need "$DEV" "developing" "--draft" "Dev Deploy & Verify" "skills/shared/dev-deploy.md" \
  "re-runs the gate" "dev-workflow:dev-verify" "not-applicable"
before "$DEV" "developing" "## Adversarial Review" "## Dev Deploy & Verify"
forbid "$DEV" "developing" "PR exists and is not draft"

# Why: fix rounds add commits, so every hand-off HEAD needs fresh evidence and no local-run escape hatch.
APC="$S/addressing-pr-comments/SKILL.md"
need "$APC" "addressing-pr-comments" "skills/shared/dev-deploy.md"
need "$APC" "addressing-pr-comments" "no \"run the service locally\" fallback"

# Why: all three debugging modes open their own PR and do not inherit the gate from anywhere.
DBG="$S/debugging/SKILL.md"
need "$DBG" "debugging" "skills/shared/dev-deploy.md" "Dev Deploy & Verify" "draft"
if [ "$(grep -c "Dev Deploy & Verify" "$DBG")" -lt 3 ]; then
  fail "debugging: the gate must be referenced in each of the three modes"
fi
need "$DBG" "debugging" "does not inherit the gate"

# Why: reviewer must bounce without evidence but never for an exempt repo, and may reuse only a covered deploy run.
RV="$S/reviewing-prs/SKILL.md"
need "$RV" "reviewing-prs" "Developer Evidence Gate" "ci_gate_exempt_repos" "deploy_gate_exempt_repos" \
  "no build" "REQUEST_CHANGES" "coverage" "skills/shared/dev-deploy.md" "fresh dev build CI"

# Why: tester must bounce without evidence, reuse only when dev still serves HEAD, and may not demand a fresh deploy unconditionally.
TS="$S/testing-prs/SKILL.md"
need "$TS" "testing-prs" "Developer Evidence Gate" "ci_gate_exempt_repos" "deploy_gate_exempt_repos" \
  "tests-failing" "newest run" "skills/shared/dev-deploy.md" "catch" "developer missed"
forbid "$TS" "testing-prs" \
  "Always deploy and test from scratch" "Always deploy the branch fresh" "has no exceptions" \
  "fresh dev deploy CI run is a necessary precondition"

# Why: agent descriptions and role-session lines must not keep promising an unconditional fresh build or deploy.
for f in reviewer tester; do
  need "$A/$f.md" "agents/$f.md" "Evidence Gate" "deploy_gate_exempt_repos"
done
need "$A/reviewer.md" "agents/reviewer.md" "reuse"
need "$A/tester.md" "agents/tester.md" "reuse"
forbid "$A/reviewer.md" "agents/reviewer.md" "re-runs the fresh dev build CI"
forbid "$A/tester.md" "agents/tester.md" "every round re-deploys fresh"

# Why: the developer agent must gate its own result, expose verify mode, and check the mode before the transition logic.
DA="$A/developer.md"
need "$DA" "agents/developer.md" "Dev Deploy & Verify" "Verify mode" "mode: verify" \
  "dev-workflow:dev-verify" "dev-verification blocked:" "status: error"
before "$DA" "agents/developer.md" "mode: verify" "transition: fired"

# Why: the orchestrator must stop a draft or unverified PR before it reaches review, resume drafts at the developer, and keep the parser fail-closed.
FC="$S/full-cycle/SKILL.md"
need "$FC" "full-cycle" "hand-off check" "handoff-check" "mode: verify" \
  "does not count against the Loop Safety Guard" \
  "finished/verify/testing-prs/reviewing-prs" "seven" "draft" \
  "dev-verification blocked:" "deploy_gate_exempt_repos" "ci_gate_exempt_repos"
forbid "$FC" "full-cycle" "exactly six fields"
before "$FC" "full-cycle" "| 4 | A repo's linked PR is open and a draft" "| 5 | A repo's linked PR carries the"

PSR="$A/pr-state-reader.md"
need "$PSR" "pr-state-reader" "hand-off check" "draft=" "evidence=" \
  "valid" "missing" "stale" "invalid" "exempt" "not-applicable" "never returns" "seven"

# Why: the checkpoint mapping and row references must follow the renumbered entry table.
CC="$S/shared/context-compaction.md"
need "$CC" "context-compaction" "verify" "rows 5-9"
forbid "$CC" "context-compaction" "rows 4-8"

# Why: a mode request carries no transition line and develop/fix rounds now include deploy waits.
RS="$S/shared/role-sessions.md"
need "$RS" "role-sessions" "mode:" "120" "round_timeout_minutes"

# Why: Process Fidelity must name the new hard-fail gates and the single either-list exemption.
need "$S/shared/standards.md" "standards.md" "dev-verification gate" "evidence gate" \
  "ci_gate_exempt_repos" "deploy_gate_exempt_repos" "validated reused"

# Why: user-facing docs must match the gates and expose the new stage keys.
RM="$PLUGIN_ROOT/README.md"
need "$RM" "README" "handoff-check" "verify" "either"
forbid "$RM" "README" "The two gates are independent."

[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)" >&2; exit 1; }
echo "all dev verification gate checks passed"
