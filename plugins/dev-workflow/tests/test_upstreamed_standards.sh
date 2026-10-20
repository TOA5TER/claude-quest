#!/usr/bin/env bash
# Tests that the generic standards, review checklist, story-state config and story-to-PR lookup appear where an agent acts on them.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STANDARDS="$PLUGIN_ROOT/skills/shared/standards.md"
REVIEWING="$PLUGIN_ROOT/skills/reviewing-prs/SKILL.md"
TESTING="$PLUGIN_ROOT/skills/testing-prs/SKILL.md"
DEVELOPING="$PLUGIN_ROOT/skills/developing/SKILL.md"
ADDRESSING="$PLUGIN_ROOT/skills/addressing-pr-comments/SKILL.md"
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

# Why: the new standards sections are read by every skill, so a missing heading silently drops a rule.
for heading in \
  "## Prod Boundary" \
  "## Quality Gates" \
  "## PR Description Updates" \
  "## Review Standards Checklist" \
  "## Local Playwright E2E Verification" \
  "## Recurring Resource-Limit Finding Escalation"; do
  grep -qxF -- "$heading" "$STANDARDS" || fail "standards.md is missing '$heading'"
done

# Why: the section order decides which rules sit next to the sections they extend.
order="$(grep -n -E '^## (Story Creation Gate|Prod Boundary|Quality Gates|PR Description Updates|Review Standards Checklist|Testing Standards|Local Playwright E2E Verification|Recurring Resource-Limit Finding Escalation|Code Comments)$' "$STANDARDS" | sed -e 's/^[0-9]*:## //' | tr '\n' '|')"
expected="Story Creation Gate|Prod Boundary|Quality Gates|PR Description Updates|Review Standards Checklist|Testing Standards|Local Playwright E2E Verification|Recurring Resource-Limit Finding Escalation|Code Comments|"
[ "$order" = "$expected" ] || fail "standards.md section order is '$order', expected '$expected'"

# Why: the temp-file rule keeps throwaway files out of system temp directories.
has "$STANDARDS" 'Temporary files in `./.scratch/tmp/` only' || fail "standards.md is missing the temp-file rule"

# Why: the one-line rule stops multi-line prose comment blocks.
has "$STANDARDS" '**One line, never a block**' || fail "standards.md is missing the one-line comment rule"

# Why: the fenced why-comment example must itself obey the one-line rule.
example_lines="$(section "$STANDARDS" "## Testing Standards" | awk '/^  ```/ { n++; next } n == 1 { c++ } END { print c + 0 }')"
[ "$example_lines" = "1" ] || fail "the why-comment example in Testing Standards has $example_lines lines, expected 1"

# Why: a PR body must be updated through REST so the session-link guard and wrapper rules apply.
section "$STANDARDS" "## PR Description Updates" | grep -qF -- '-X PATCH' || fail "PR Description Updates does not give the REST PATCH form"

# Why: reviewer and tester must run the same checklist, so each must point at the one shared section.
has "$REVIEWING" "Review Standards Checklist" || fail "reviewing-prs does not reference the Review Standards Checklist"
has "$TESTING" "Review Standards Checklist" || fail "testing-prs does not reference the Review Standards Checklist"

# Why: in Re-Review the Critical Exception Threshold would otherwise swallow checklist findings.
has "$REVIEWING" "The one exception to this threshold is a Review Standards Checklist violation" || fail "reviewing-prs does not name the checklist as the threshold exception"

# Why: the post-test transition must read its config keys and must never use the PR-level decision.
transition="$(awk '/^### Story State Transition \(after test approval\)/ { on = 1 } on { print }' "$TESTING")"
printf '%s' "$transition" | grep -qF -- 'story_states.after_test_approval' || fail "testing-prs transition does not name story_states.after_test_approval"
printf '%s' "$transition" | grep -qF -- 'reviewer_login' || fail "testing-prs transition does not name reviewer_login"
printf '%s' "$transition" | grep -qF -- '## Test Report' || fail "testing-prs transition does not exclude test-report reviews"
printf '%s' "$transition" | grep -qF -- 'Never use the PR-level `reviewDecision`' || fail "testing-prs transition does not forbid reviewDecision"
printf '%s' "$transition" | grep -qF -- 'unset' || fail "testing-prs transition does not define the unset case"

# Why: a story ID given to developing must reach the configured in-development state on the single-repo path too.
pm_context="$(section "$DEVELOPING" "## PM Context (if story ID provided)")"
printf '%s' "$pm_context" | grep -qF -- 'story_states.in_development' || fail "developing PM Context does not fire story_states.in_development"
printf '%s' "$pm_context" | grep -qF -- 'transition: fired' || fail "developing PM Context does not honour transition: fired"

# Why: the PR2 scratch paths in the Terraform plan check must follow the temp-file rule and the REST PATCH rule.
if grep -q 'gh-as-app.sh developer pr edit' "$DEVELOPING"; then fail "developing still uses pr edit for the PR body"; fi
if grep -qE '(^|[^/])\.scratch/(pr-body-updated|terraform-plan)' "$DEVELOPING"; then fail "developing still writes plan files outside ./.scratch/tmp/"; fi

# Why: addressing-pr-comments must resolve a PM story ID to its PR, with the delimited branch-token fallback.
step1="$(awk '/^## Step 1: Identify the PR/ { on = 1; next } on && /^## / { exit } on { print }' "$ADDRESSING")"
printf '%s' "$step1" | grep -qF -- 'PM story ID' || fail "addressing-pr-comments Step 1 does not resolve a story ID"
printf '%s' "$step1" | grep -qF -- 'gh pr list --state all --limit 1000 --json number,headRefName' || fail "addressing-pr-comments Step 1 lacks the branch-token fallback"
printf '%s' "$step1" | grep -qF -- 'sc-12' || fail "addressing-pr-comments Step 1 lacks the delimited-match example"

# Why: users configure the story states in the README, so the keys must be documented there.
for key in 'story_states.in_development' 'story_states.after_test_approval' 'reviewer_login'; do
  has "$README" "$key" || fail "README does not document $key"
done
grep -qxF -- '### Story States' "$README" || fail "README is missing the Story States subsection"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
