#!/usr/bin/env bash
# Tests that Local Mode, the pre-release pin flow and the base-root rule appear where an agent or orchestrator acts on them.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_MODE="$PLUGIN_ROOT/skills/shared/local-mode.md"
PIN="$PLUGIN_ROOT/skills/shared/pre-release-pin.md"
FULL_CYCLE="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"
DEVELOPER="$PLUGIN_ROOT/agents/developer.md"
REVIEWER="$PLUGIN_ROOT/agents/reviewer.md"
TESTER="$PLUGIN_ROOT/agents/tester.md"
READER="$PLUGIN_ROOT/agents/pr-state-reader.md"
REVIEWING="$PLUGIN_ROOT/skills/reviewing-prs/SKILL.md"
TESTING="$PLUGIN_ROOT/skills/testing-prs/SKILL.md"
ROLE_SESSIONS="$PLUGIN_ROOT/skills/shared/role-sessions.md"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

has() {
  grep -qF -- "$2" "$1"
}

# headings FILE : prints the h2 headings of a file in order, joined by '|'.
headings() {
  grep -E '^## ' "$1" | sed -e 's/^## //' | tr '\n' '|'
}

# Why: the agents and orchestration skills cite these files by heading, so a missing file or heading breaks every pointer.
[ -f "$LOCAL_MODE" ] || fail "skills/shared/local-mode.md does not exist"
[ -f "$PIN" ] || fail "skills/shared/pre-release-pin.md does not exist"

# Why: the heading order is the contract the specs and the agents cite.
expected_local="Local Mode Extension|Base Root for Wrapper Agents|Push Detection|Mode per Repo|Notes|Resume Order|Process Fidelity — Local Mode|"
[ "$(headings "$LOCAL_MODE")" = "$expected_local" ] || fail "local-mode.md headings are '$(headings "$LOCAL_MODE")', expected '$expected_local'"
expected_pin="Cross-Repo In-Flight Dependency|Draft-PR Resume Routing|Pre-Release Pin Disclosure|"
[ "$(headings "$PIN")" = "$expected_pin" ] || fail "pre-release-pin.md headings are '$(headings "$PIN")', expected '$expected_pin'"

# Why: a wrapper fills the extension by key name, so every key must be documented with its default.
for key in '**push-by-rule:**' '**trusted tag owners:**' '**git network command:**' '**gh command:**'; do
  has "$LOCAL_MODE" "$key" || fail "local-mode.md does not define the extension key $key"
done

# Why: a worker with no base root must fail loudly instead of guessing a path.
has "$LOCAL_MODE" 'base-root:' || fail "local-mode.md does not name the base-root: line"
has "$LOCAL_MODE" 'error: base-root missing' || fail "local-mode.md does not define the pr-state-reader base-root error"
has "$FULL_CYCLE" 'base-root:' || fail "full-cycle does not add the base-root: line to dispatches"

# Why: the probe procedure is the one cross-repo directory change allowed, so its guards must stay intact.
has "$LOCAL_MODE" 'probe-{story-id}-dryrun' || fail "local-mode.md lost the probe ref name"
has "$LOCAL_MODE" '900000' || fail "local-mode.md lost the probe timeout"
has "$PLUGIN_ROOT/skills/shared/standards.md" 'three separate Bash calls' || fail "standards.md does not name the probe exception"

# Why: full-cycle must route Local Mode and approval-only repos and read only the two fix prefixes.
has "$FULL_CYCLE" '## Local Mode and Approval-Only Repos' || fail "full-cycle is missing the Local Mode and Approval-Only Repos section"
has "$FULL_CYCLE" 'head mismatch:' || fail "full-cycle does not name the head mismatch: prefix"
has "$FULL_CYCLE" 'push rejected:' || fail "full-cycle does not name the push rejected: prefix"
has "$FULL_CYCLE" 'Draft-PR Resume Routing' || fail "full-cycle does not point the draft row at Draft-PR Resume Routing"

# Why: the developer is the only role that pins, re-pins and writes the develop note.
has "$DEVELOPER" 'mode: repin' || fail "developer agent does not name mode: repin"
has "$DEVELOPER" 'Re-pin mode' || fail "developer agent is missing Re-pin mode"
has "$DEVELOPER" 'Pin lines' || fail "developer agent is missing Pin lines"
has "$DEVELOPER" 'backstop: true' || fail "developer agent does not name backstop: true"
has "$DEVELOPER" 'mode: approval-only' || fail "developer agent does not name mode: approval-only"

# Why: reviewer and tester need the exact subsection the wrapper agents point at.
has "$REVIEWER" '### Local Mode and approval-only rounds' || fail "reviewer agent is missing the Local Mode and approval-only rounds subsection"
has "$TESTER" '### Local Mode and approval-only rounds' || fail "tester agent is missing the Local Mode and approval-only rounds subsection"

# Why: the orchestrator resumes from these lines, so the reader must keep returning them.
has "$READER" 'local=' || fail "pr-state-reader does not return the local= line"
has "$READER" 'push=' || fail "pr-state-reader does not return the push= line"
has "$READER" 'probe:' || fail "pr-state-reader does not accept probe: lines"

# Why: the pin disclosure only reaches the report if both stages read the shared section every round.
has "$REVIEWING" 'pre-release-pin.md' || fail "reviewing-prs does not read pre-release-pin.md"
has "$TESTING" 'pre-release-pin.md' || fail "testing-prs does not read pre-release-pin.md"

# Why: a recovering orchestrator must read the notes, not GitHub, for a Local Mode repo.
has "$ROLE_SESSIONS" 'local-mode.md' || fail "role-sessions.md does not name the notes recovery source"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
