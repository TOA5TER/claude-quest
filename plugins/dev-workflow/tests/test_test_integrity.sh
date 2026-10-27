#!/usr/bin/env bash
# Tests that the test-integrity checks, review precision record and dispute path appear where an agent acts on them.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTING="$PLUGIN_ROOT/skills/testing-prs/SKILL.md"
REVIEWING="$PLUGIN_ROOT/skills/reviewing-prs/SKILL.md"
ADDRESSING="$PLUGIN_ROOT/skills/addressing-pr-comments/SKILL.md"
STANDARDS="$PLUGIN_ROOT/skills/shared/standards.md"
LOCAL_MODE="$PLUGIN_ROOT/skills/shared/local-mode.md"
TESTER_AGENT="$PLUGIN_ROOT/agents/tester.md"
REVIEWER_AGENT="$PLUGIN_ROOT/agents/reviewer.md"
PLUGIN_CLAUDE="$PLUGIN_ROOT/CLAUDE.md"
README="$PLUGIN_ROOT/README.md"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

has() {
  grep -qF -- "$2" "$1"
}

# section FILE HEADING : prints the body of an h2 section up to the next h2.
section() {
  awk -v h="$2" '
    $0 == h { on = 1; next }
    on && /^## / { exit }
    on { print }
  ' "$1"
}

# lineno FILE LITERAL : prints the first line number of an exact-line match.
lineno() {
  grep -nxF -- "$2" "$1" | head -1 | cut -d: -f1
}

# need FILE LABEL LITERAL... : fails once per missing literal.
need() {
  local file="$1" label="$2"
  shift 2
  local lit
  for lit in "$@"; do
    has "$file" "$lit" || fail "$label is missing: $lit"
  done
}

# Why: the checks must be planned in Phase 4 and run in Phase 5, once each, or the tester never executes them.
p4="$(lineno "$TESTING" "## Phase 4: Design Test Scenarios")"
p5="$(lineno "$TESTING" "## Phase 5: Execute Tests")"
p55="$(lineno "$TESTING" "## Phase 5.5: Parallel Failure Investigation (conditional)")"
if [ -n "$p4" ] && [ -n "$p5" ] && [ -n "$p55" ]; then
  in4="$(awk -v s="$p4" -v e="$p5" 'NR > s && NR < e && $0 == "### Test Integrity Scenarios"' "$TESTING" | wc -l | tr -d ' ')"
  in5="$(awk -v s="$p5" -v e="$p55" 'NR > s && NR < e && $0 == "### Test Integrity"' "$TESTING" | wc -l | tr -d ' ')"
  [ "$in4" = "1" ] || fail "testing-prs Phase 4 has $in4 '### Test Integrity Scenarios' lines, expected 1"
  [ "$in5" = "1" ] || fail "testing-prs Phase 5 has $in5 '### Test Integrity' lines, expected 1"
else
  fail "could not locate the Phase 4, Phase 5 or Phase 5.5 headings in testing-prs"
fi
# Why: each literal is a command, path rule or name the revert-the-fix check cannot run without.
need "$TESTING" "testing-prs integrity wording" \
  "baseRefName,headRefOid" "revert-the-fix" "merge-base" "revert-check-" "worktree add --detach" \
  "Project Setup" "reset --hard" "--diff-filter=AMR" "__tests__" "fixtures" \
  "symbolic-ref --short refs/remotes/origin/HEAD"

# Why: every decision-table outcome must be spelled out or two testers will report the same state differently.
need "$TESTING" "testing-prs result string" \
  "N/A: no added or changed test files" "no added or changed test file fails against pre-change code" \
  "fail at the PR head" "N/A: no mutation tooling declared" "N/A: no property-based tooling declared" \
  "N/A: nothing in scope" "not possible:" "cannot be scoped to the changed files" \
  "never installs" "configured threshold" "counterexample"
# Why: only tools the table recognises count as existing tooling.
need "$TESTING" "testing-prs tool name" \
  "Stryker" "mutmut" "cosmic-ray" "PIT" "cargo-mutants" "Infection" "go-mutesting" \
  "fast-check" "Hypothesis" "proptest" "quickcheck" "jqwik" "gopter" "rapid"

# Why: the report block is the only place results, rounds and the precision table reach a reader, in a fixed order.
slice="$(awk -v s="$(lineno "$TESTING" "## Phase 6: Write Test Report")" -v e="$(lineno "$TESTING" "## Phase 7: Submit Review")" 'NR > s && NR < e' "$TESTING")"
for lit in "**Test round:**" "[Test: Test Integrity]" "## Review Precision"; do
  printf '%s\n' "$slice" | grep -qF -- "$lit" || fail "testing-prs Phase 6 is missing: $lit"
done
ux="$(printf '%s\n' "$slice" | grep -nF -- "### UX Verification" | head -1 | cut -d: -f1)"
ti="$(printf '%s\n' "$slice" | grep -nF -- "### Test Integrity" | head -1 | cut -d: -f1)"
rec="$(printf '%s\n' "$slice" | grep -nF -- "## Recommendation" | head -1 | cut -d: -f1)"
rp="$(printf '%s\n' "$slice" | grep -nF -- "## Review Precision" | head -1 | cut -d: -f1)"
if [ -n "$ux" ] && [ -n "$ti" ] && [ -n "$rec" ] && [ -n "$rp" ]; then
  [ "$ti" -gt "$ux" ] || fail "Phase 6 Test Integrity is not after UX Verification"
  [ "$rp" -gt "$rec" ] || fail "Phase 6 Review Precision is not after Recommendation"
else
  fail "could not locate the Phase 6 report headings"
fi

# Why: the revert-check worktree is a second scratch worktree, so every rule that names the first one must name it too.
need "$STANDARDS" "standards.md" "- **Revert-check worktree — testing-prs only.**"
stay="$(grep -F -- "- **Stay within repository**" "$STANDARDS")"
printf '%s\n' "$stay" | grep -qF -- "revert-check worktree" || fail "standards.md Stay within repository line does not name the revert-check worktree"
cleanup="$(grep -F -- "- **Cleanup is a separate, explicit step" "$STANDARDS")"
printf '%s\n' "$cleanup" | grep -qF -- "revert-check worktree" || fail "standards.md Cleanup line does not name the revert-check worktree"
need "$TESTER_AGENT" "tester.md" "revert-check worktree"

# Why: the precision table and tags are what make false-positive rates computable from review history alone.
for lit in "**Review round:**" "## Review Precision"; do
  count="$(grep -cF -- "$lit" "$REVIEWING")"
  [ "$count" = "2" ] || fail "reviewing-prs has $count '$lit' lines, expected 2 (one per body format)"
done
need "$REVIEWING" "reviewing-prs precision wording" \
  "| Perspective | Raised | Verifier-dropped | Developer-disputed |" "Re-Review" "Unattributed" \
  "[Gate]" "[Re-Review]" "the perspectives that raised it"

# Why: the dispute path needs matching wording in the fixer, the ruler, the local-mode note and both agents.
need "$ADDRESSING" "addressing-pr-comments dispute wording" "Disputed —" "## Disputed Findings" "— Disputed:" "[Gate]"
need "$REVIEWING" "reviewing-prs ruling status" "Disputed — upheld" "Disputed — rejected"
need "$LOCAL_MODE" "local-mode.md" "## Disputed Findings"
need "$REVIEWER_AGENT" "reviewer.md" "## Disputed Findings"
need "$TESTER_AGENT" "tester.md" "## Disputed Findings"
grep -F -- "[Gate]" "$ADDRESSING" | grep -qF -- "never disputed" || fail "addressing-pr-comments does not say [Gate] items are never disputed"

# Why: a wrapper plugin quotes the line-522 sentence and the verification line, and the threshold block is asserted elsewhere.
grep -qxF -- "[Unresolved items from Previous Changes Verification only, plus any qualifying New Critical Findings. No other new items are permitted in re-review.]" "$REVIEWING" \
  || fail "reviewing-prs lost the Re-Review Required Changes sentence"
grep -q -- '^\*\*Finding verification:\*\* N raised, C confirmed, M demoted to non-blocking, D dropped' "$REVIEWING" \
  || fail "reviewing-prs lost the Finding verification line"
grep -qxF -- "## Changes Addressed" "$ADDRESSING" || fail "addressing-pr-comments lost the Changes Addressed heading"

# Why: the README section must precede Role Sessions and state the formula, or the rate is read two ways.
rp_line="$(lineno "$README" "## Review Precision")"
rs_line="$(lineno "$README" "## Role Sessions (standalone full-cycle)")"
if [ -n "$rp_line" ] && [ -n "$rs_line" ]; then
  [ "$rp_line" -lt "$rs_line" ] || fail "README Review Precision is not before Role Sessions"
else
  fail "README is missing the Review Precision or Role Sessions heading"
fi
need "$README" "README" "(Verifier-dropped + Developer-disputed) / Raised" "rounds per PR"
need "$PLUGIN_CLAUDE" "plugin CLAUDE.md" "tests/test_test_integrity.sh"

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
echo "PASS"
