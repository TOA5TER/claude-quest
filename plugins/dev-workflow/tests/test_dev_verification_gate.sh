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

# Why: several skills share one procedure, so the shared file must own every rule the others point at.
DD="$S/shared/dev-deploy.md"
need "$DD" "dev-deploy.md" \
  "dev-verification sha=" "run=" "workflow=" "exempt=" \
  "missing: no marked comment" "stale: the marker" "invalid: the SHA matches" "valid: every check passes" \
  "headSha" "created after the trigger" \
  "treated as the same word" "in any segment (caller or callee)" "terraform-plan / Security Scan" ".github/workflows/*" \
  "whose author is the PR author or the developer's authenticated identity" \
  "-R <owner>/<repo>" \
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
[ "$(grep -c '^### Step 8: Dev Deploy & Verify' "$DBG")" -eq 1 ] || fail "debugging: debug mode needs exactly one Step 8 gate heading"
[ "$(grep -c '^### Step 7: Dev Deploy & Verify' "$DBG")" -eq 2 ] || fail "debugging: development and rework modes each need a Step 7 gate heading"
need "$DBG" "debugging" "does not inherit the gate"

# Why: reviewer must bounce without evidence but never for an exempt repo, and may reuse only a covered deploy run.
RV="$S/reviewing-prs/SKILL.md"
forbid "$RV" "reviewing-prs" "Always run it fresh on the PR's current HEAD"
need "$RV" "reviewing-prs" "CI path:" "dev-evidence-bounce" "path (a) or path (b)" "not an evidence-bounce review" "Developer Evidence Gate" "ci_gate_exempt_repos" "deploy_gate_exempt_repos" \
  "no build" "REQUEST_CHANGES" "coverage" "skills/shared/dev-deploy.md" "fresh dev build CI"

# Why: tester must bounce without evidence, reuse only when dev still serves HEAD, and may not demand a fresh deploy unconditionally.
TS="$S/testing-prs/SKILL.md"
need "$TS" "testing-prs" "either the validated reused developer run or a fresh tester run" "Deploy path:" "Developer Evidence Gate" "ci_gate_exempt_repos" "deploy_gate_exempt_repos" \
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
need "$S/dev-verify/SKILL.md" "dev-verify" "skills/shared/adapter-loading.md"
forbid "$A/reviewer.md" "agents/reviewer.md" "re-runs the fresh dev build CI"
forbid "$A/tester.md" "agents/tester.md" "every round re-deploys fresh"

# Why: the developer agent must gate its own result, expose verify mode, and check the mode before the transition logic.
DA="$A/developer.md"
sed -n '/^## Story mode/,/^## Rework mode/p' "$DA" | grep 'Dev Deploy & Verify' >/dev/null || fail "agents/developer.md: story mode must name the gate"
sed -n '/^## Rework mode/,/^## Verify mode/p' "$DA" | grep 'Dev Deploy & Verify' >/dev/null || fail "agents/developer.md: rework mode must name the gate"
need "$DA" "agents/developer.md" "Verify mode" "mode: verify" \
  "dev-workflow:dev-verify" "dev-verification blocked:" "status: error"
before "$DA" "agents/developer.md" "## Mode selection" "If the request's first body line is \`transition: fired\`"

# Why: the orchestrator must stop a draft or unverified PR before it reaches review, resume drafts at the developer, and keep the parser fail-closed.
FC="$S/full-cycle/SKILL.md"
need "$FC" "full-cycle" "hand-off check" "handoff-check" "mode: verify" \
  "does not count against the Loop Safety Guard" \
  "finished/verify/testing-prs/reviewing-prs" "exactly seven" "draft=<true|false>" \
  "dev-verification blocked:" "deploy_gate_exempt_repos" "ci_gate_exempt_repos"
forbid "$FC" "full-cycle" "exactly six fields" "trigger the dev build CI fresh"
need "$FC" "full-cycle" "A draft PR still blocks" "-R {owner}/{repo}"
forbid "$FC" "full-cycle" "proceed regardless of draft"
need "$DEV" "developing" "always treats a draft PR as not ready to send to review"
before "$FC" "full-cycle" "| 4 | A repo's linked PR is open and a draft" "| 5 | A repo's linked PR carries the"

PSR="$A/pr-state-reader.md"
need "$PSR" "pr-state-reader" "hand-off check" "draft=" "evidence=" \
  "evidence=<valid|missing|stale|invalid|exempt|not-applicable>" "never returns" "seven" "-R <owner>/<repo>"
forbid "$PSR" "pr-state-reader" "The four jobs"

# Why: the checkpoint mapping and row references must follow the renumbered entry table.
CC="$S/shared/context-compaction.md"
need "$CC" "context-compaction" "verify" "rows 4-9"
forbid "$CC" "context-compaction" "rows 4-8" "rows 5-9"

# Why: a mode request carries no transition line and develop/fix rounds now include deploy waits.
RS="$S/shared/role-sessions.md"
need "$RS" "role-sessions" "mode:" "120 minutes for develop and fix" "round_timeout_minutes"

# Why: Process Fidelity must name the new hard-fail gates and the single either-list exemption.
need "$S/shared/standards.md" "standards.md" "dev-verification gate" "evidence gate" \
  "ci_gate_exempt_repos" "deploy_gate_exempt_repos" "validated reused"

# Why: user-facing docs must match the gates and expose the new stage keys.
RM="$PLUGIN_ROOT/README.md"
need "$RM" "README" "handoff-check" "models.stages.verify" "either"
forbid "$RM" "README" "The two gates are independent."

# Why: inside the mandated .worktrees/<slug> layout --show-toplevel is the worktree slug, so exemption and spec lookups must derive the repo name from the common git dir.
REPO_NAME_CMD='basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"'
for f in shared/dev-deploy.md reviewing-prs/SKILL.md testing-prs/SKILL.md addressing-pr-comments/SKILL.md notes-adapter/interface.md notes-adapter/obsidian.md; do
  need "$S/$f" "$f" "$REPO_NAME_CMD"
  forbid "$S/$f" "$f" "git rev-parse --show-toplevel | xargs basename"
done
need "$S/writing-specs/SKILL.md" "writing-specs" "git -C {repo_root} rev-parse --path-format=absolute --git-common-dir"
forbid "$S/writing-specs/SKILL.md" "writing-specs" "rev-parse --show-toplevel | xargs basename"
need "$PLUGIN_ROOT/README.md" "README" "$REPO_NAME_CMD"
forbid "$PLUGIN_ROOT/README.md" "README" "git rev-parse --show-toplevel | xargs basename"

# Why: pin the behavior, not just the text: the command must return the repo name from a linked worktree.
SCRATCH="$PLUGIN_ROOT/../../.scratch/tmp"
mkdir -p "$SCRATCH"
WT_DIR="$(mktemp -d -p "$SCRATCH" worktree-name.XXXXXX)"
if [ -n "$WT_DIR" ] && git init -q "$WT_DIR/my-repo" \
  && git -C "$WT_DIR/my-repo" -c user.email=t@example.com -c user.name=t commit -q --allow-empty -m init \
  && git -C "$WT_DIR/my-repo" worktree add -q "$WT_DIR/my-repo/.worktrees/sc-0000" -b scratch-branch; then
  GOT="$(cd "$WT_DIR/my-repo/.worktrees/sc-0000" && sh -c "$REPO_NAME_CMD")"
  [ "$GOT" = "my-repo" ] || fail "worktree-safe repo name returned '$GOT', expected 'my-repo'"
else
  fail "could not create the scratch repo and worktree"
fi
python3 -I -c 'import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$WT_DIR"

[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)" >&2; exit 1; }
echo "all dev verification gate checks passed"
