#!/usr/bin/env bash
# Tests that the review verification phase, severity tiers, Do Not Flag lists and their docs appear where an agent acts on them.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REVIEWING="$PLUGIN_ROOT/skills/reviewing-prs/SKILL.md"
ADDRESSING="$PLUGIN_ROOT/skills/addressing-pr-comments/SKILL.md"
STANDARDS="$PLUGIN_ROOT/skills/shared/standards.md"
ROLE_SESSIONS="$PLUGIN_ROOT/skills/shared/role-sessions.md"
SUBAGENTS="$PLUGIN_ROOT/skills/subagents"
PLUGIN_CLAUDE="$PLUGIN_ROOT/CLAUDE.md"
README="$PLUGIN_ROOT/README.md"
REVIEWER_AGENT="$PLUGIN_ROOT/agents/reviewer.md"

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

# subsection FILE HEADING : prints the body of an h3 section up to the next h2 or h3.
subsection() {
  awk -v h="$2" '
    $0 == h { on = 1; next }
    on && /^##+ / { exit }
    on { print }
  ' "$1"
}

# lineno FILE LITERAL : prints the first line number of an exact-line match.
lineno() {
  grep -nxF -- "$2" "$1" | head -1 | cut -d: -f1
}

# Why: the phase must sit between the fan-out and the final check, or findings reach the review unverified.
PHASE_HEADING="## Phase 5.2: Verify Findings"
count="$(grep -cxF -- "$PHASE_HEADING" "$REVIEWING")"
[ "$count" = "1" ] || fail "SKILL.md has $count '$PHASE_HEADING' lines, expected 1"
phase_line="$(lineno "$REVIEWING" "$PHASE_HEADING")"
checklist_line="$(lineno "$REVIEWING" "### Review Standards Checklist (both modes)")"
final_line="$(lineno "$REVIEWING" "## Phase 5.5: Verify Before Submitting Review")"
if [ -n "$phase_line" ] && [ -n "$checklist_line" ] && [ -n "$final_line" ]; then
  [ "$phase_line" -gt "$checklist_line" ] || fail "Phase 5.2 is not after the Review Standards Checklist section"
  [ "$phase_line" -lt "$final_line" ] || fail "Phase 5.2 is not before Phase 5.5"
else
  fail "could not locate Phase 5.2, the checklist section or Phase 5.5 headings"
fi
has "$REVIEWING" "before proceeding to Phase 5.2" || fail "Step 2 does not point at Phase 5.2"

# Why: the flag-everything rule is what produced speculative Required Changes.
has "$REVIEWING" "If unsure whether something is worth flagging, flag it" && fail "SKILL.md still carries the flag-everything rule"
for needle in "SEVERITY TIERS:" "Blocking (must fix)" "Non-blocking (nit)" "Pre-existing (not introduced by this PR)"; do
  has "$REVIEWING" "$needle" || fail "SKILL.md is missing '$needle'"
done

# Why: each phase rule is the only thing stopping an unverified or unevidenced finding from blocking a PR.
phase_body="$(section "$REVIEWING" "$PHASE_HEADING")"
for needle in \
  "Re-Review Mode skips this phase" \
  "Agreement alone never confirms a finding." \
  "models.review" \
  "WORKSPACE RULE" \
  "(unverified)" \
  "Migration Immutability" \
  "Review Standards Checklist violations are not gate findings" \
  "VERDICT:" "PR-SCOPE:" "BLOCKING-CLASS:" "EVIDENCE:" "REASON:" \
  "refuted" "unconfirmed" "confirmed" \
  "Required Changes" "Suggestions" "Pre-existing Issues" "Dropped"; do
  printf '%s\n' "$phase_body" | grep -qF -- "$needle" || fail "Phase 5.2 is missing '$needle'"
done
checklist_body="$(subsection "$REVIEWING" "### Review Standards Checklist (both modes)")"
printf '%s\n' "$checklist_body" | grep -qF -- "Each violation enters Phase 5.2 as a Blocking finding" \
  || fail "Review Standards Checklist section does not route violations through Phase 5.2"

# Why: the review body, score and inline-comment rules must reflect the dispositions or dropped findings leak back in.
for needle in \
  "**Finding verification:**" \
  "## Pre-existing Issues (not required for this PR)" \
  "Pre-existing and dropped findings never lower the score."; do
  has "$REVIEWING" "$needle" || fail "SKILL.md is missing '$needle'"
done
inline_body="$(subsection "$REVIEWING" "### Inline Comments")"
printf '%s\n' "$inline_body" | grep -qF -- "Pre-existing or dropped findings" || fail "Inline Comments section is missing the R-21 wording"
final_body="$(section "$REVIEWING" "## Phase 5.5: Verify Before Submitting Review")"
printf '%s\n' "$final_body" | grep -qF -- "raised = confirmed + demoted + dropped" || fail "Phase 5.5 is missing the count identity"

# Why: each perspective needs its own false-positive guard and the shared tier vocabulary, or the tiers do not reach the reviewers.
for name in code-quality-engineer simple-architecture-engineer security-engineer performance-engineer product-manager budget-manager; do
  f="$SUBAGENTS/$name.md"
  dnf="$(grep -cxF -- "## Do Not Flag" "$f")"
  [ "$dnf" = "1" ] || { fail "$name.md has $dnf '## Do Not Flag' lines, expected 1"; continue; }
  dnf_line="$(lineno "$f" "## Do Not Flag")"
  out_line="$(lineno "$f" "## Output Format")"
  if [ -z "$out_line" ] || [ "$dnf_line" -ge "$out_line" ]; then
    fail "$name.md Do Not Flag is not before Output Format"
  fi
  bullets="$(section "$f" "## Do Not Flag" | grep -c '^- ')"
  [ "$bullets" = "4" ] || fail "$name.md Do Not Flag has $bullets bullets, expected 4"

  for needle in "### Blocking (must fix)" "### Non-blocking (nit)" "### Pre-existing (not introduced by this PR)" "Tier definitions are in the SEVERITY TIERS block"; do
    has "$f" "$needle" || fail "$name.md is missing '$needle'"
  done
  has "$f" "### Critical Issues (must fix)" && fail "$name.md still has the Critical Issues heading"
  has "$f" "### Warnings (should fix)" && fail "$name.md still has the Warnings heading"
done
for needle in "### Cost Concerns" "### Optimization Opportunities"; do
  has "$SUBAGENTS/budget-manager.md" "$needle" && fail "budget-manager.md still has '$needle'"
done
has "$SUBAGENTS/security-engineer.md" "If you find a Blocking issue" || fail "security-engineer.md is missing 'If you find a Blocking issue'"
has "$SUBAGENTS/product-manager.md" "Blocking is what rolls into the review's Required Changes" || fail "product-manager.md is missing the Blocking rollup sentence"
has "$SUBAGENTS/product-manager.md" "Warning (Required Change): plugin '{name}' touched without a plugin.json version bump" || fail "product-manager.md lost the plugin.json finding text"
has "$SUBAGENTS/product-manager.md" "Warning (Required Change): plugin '{name}' plugin.json bumped to '{new-version}' but marketplace.json entry still shows '{marketplace-version}'" || fail "product-manager.md lost the marketplace.json finding text"

# Why: Re-Review Mode must stay byte-for-byte, and a drifting threshold changes what re-reviews accept.
for needle in \
  "> **Critical Exception Threshold** — A new finding qualifies ONLY if ALL of the following are true:" \
  "> 1. It is a security vulnerability, data loss risk, or correctness failure that would cause incorrect behavior in production" \
  "> 2. It was introduced by code added **after** the original review — it does not appear in the original diff" \
  "> 3. It is not present in the original diff in any form — not as the same code, a functionally equivalent pattern, or a related code path. If it appears anywhere in the original diff, it does not qualify regardless of how subtle or easy to overlook it was." \
  "> If a finding does not satisfy all three criteria, it must be omitted. Do not rationalize marginal findings into this threshold." \
  "The one exception to this threshold is a Review Standards Checklist violation (see below) in code added since the last review: it is a Required Change even though it is not a security, data-loss or correctness finding. Checklist violations in code already reviewed stay omitted, matching the one-review-per-cycle rule."; do
  has "$REVIEWING" "$needle" || fail "SKILL.md changed Re-Review text: '${needle:0:60}...'"
done

# Why: the docs and the test list are how maintainers learn the new flow and the new test.
has "$PLUGIN_CLAUDE" "tests/test_review_verification.sh" || fail "CLAUDE.md does not list tests/test_review_verification.sh"
has "$PLUGIN_CLAUDE" "4-perspective" && fail "CLAUDE.md still says 4-perspective"
has "$README" "finding verifiers" || fail "README.md does not mention finding verifiers"
has "$README" "every finding verified and tiered by severity" || fail "README.md does not describe verified, tiered findings"
has "$REVIEWER_AGENT" "and then one verifier per finding" || fail "agents/reviewer.md does not mention the verifier fan-out"

# Why: the fixer must not edit Pre-existing code, and the fan-out lists must name the verifier dispatch.
has "$ADDRESSING" "Items under a review's" || fail "addressing-pr-comments does not exclude Pre-existing items"
has "$STANDARDS" "perspective and finding-verifier fan-outs" || fail "standards.md does not name the verifier fan-out"
has "$ROLE_SESSIONS" "perspective and finding-verifier fan-outs stay fresh one-shot subagents" || fail "role-sessions.md does not name the verifier fan-out"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
  exit 0
fi
echo "$FAILURES check(s) failed" >&2
exit 1
