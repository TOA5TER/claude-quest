#!/usr/bin/env bash
# Tests the spec-writer completeness rules and the Technical Context file map in the spec template.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SKILL="$PLUGIN_ROOT/skills/writing-specs/SKILL.md"
TEMPLATE="$PLUGIN_ROOT/skills/writing-specs/spec-template.html"
AGENT="$PLUGIN_ROOT/agents/spec-writer.md"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' | tr '\n' ' ' | sed -e 's/  */ /g'
}

# region FILE START_REGEX : prints the normalised text from the first match to the next level-2 heading.
region() {
  awk -v start="$2" '
    !on && $0 ~ start { on = 1; print; next }
    on {
      if ($0 ~ /^## /) exit
      print
    }
  ' "$1" | normalise
}

# template_region : prints the raw lines from the technical-context heading up to the implementation-steps heading.
template_region() {
  awk '
    /id="technical-context"/ { on = 1 }
    /id="implementation-steps"/ { exit }
    on { print }
  ' "$TEMPLATE"
}

whole() {
  normalise < "$1"
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

require() {
  local label="$1" text="$2" needle="$3"
  [ -n "$text" ] || { fail "$label: region not found"; return; }
  has "$text" "$needle" || fail "$label: missing '$needle'"
}

forbid() {
  local label="$1" text="$2" needle="$3"
  [ -n "$text" ] || { fail "$label: region not found"; return; }
  if has "$text" "$needle"; then fail "$label: still contains '$needle'"; fi
}

TC_RAW="$(template_region)"
TC_TEXT="$(printf '%s\n' "$TC_RAW" | normalise)"

# Why: the File / Role table read poorly in a browser, so Technical Context must not regress to a table.
forbid "template Technical Context" "$TC_TEXT" "<table"
forbid "template Technical Context" "$TC_TEXT" "Role in this change"

# Why: the file map is the replacement structure, and its class is what the styles and any reader key on.
require "template Technical Context" "$TC_TEXT" 'class="file-map"'

# Why: area -> file -> detail must be lists nested inside each other; three sibling lists would pass a bare count but not read as a map.
UL_DEPTH="$(printf '%s\n' "$TC_RAW" | awk '
  { o = gsub(/<ul/, "&"); c = gsub(/<\/ul/, "&"); depth += o; if (depth > max) max = depth; depth -= c }
  END { print max + 0 }')"
[ "${UL_DEPTH:-0}" -ge 3 ] || fail "template Technical Context: <ul> nesting depth is ${UL_DEPTH:-0}, need at least 3"

# Why: the two unstyled list levels lose list semantics in some screen readers without role="list", and the role span carries each file's one-line role.
require "template Technical Context" "$TC_TEXT" '<ul role="list">'
require "template Technical Context" "$TC_TEXT" 'class="role"'
require "template Technical Context" "$TC_TEXT" 'class="area"'

# Why: a long file path in a narrow column overflowed horizontally on phones; the style must wrap it.
TEMPLATE_TEXT="$(whole "$TEMPLATE")"
require "template" "$TEMPLATE_TEXT" ".file-map"
require "template" "$TEMPLATE_TEXT" "overflow-wrap: anywhere"

# Why: other tests and readers depend on the story-type placeholder comment staying intact.
require "template" "$TEMPLATE_TEXT" "feature | bug | chore | unknown"

SKILL_TEXT="$(whole "$SKILL")"

# Why: the standard is one named section that every phase refers back to, so its anchors must exist.
require "SKILL.md" "$SKILL_TEXT" "## Cold-Start Completeness Standard"
require "SKILL.md" "$SKILL_TEXT" "cold-start one-shot complete"
require "SKILL.md" "$SKILL_TEXT" "Deferral ban"
require "SKILL.md" "$SKILL_TEXT" "never passed to the approver or the developer"

STD_TEXT="$(region "$SKILL" '^## Cold-Start Completeness Standard')"

# Why: the ban lists its typical forms literally inside the standard so the writer and the reviewer match against the same phrases; a mention elsewhere in the file would not define the ban.
require "standard" "$STD_TEXT" "verify that"
require "standard" "$STD_TEXT" "confirm whether"
require "standard" "$STD_TEXT" "developer should decide"
require "standard" "$STD_TEXT" "TBD"
require "standard" "$STD_TEXT" "[Open Question]"
require "standard" "$STD_TEXT" "Matching is by intent"

# Why: the exemption for quoted phrases and the limit on the verification sections must be spelled out in the standard, or a quoted instruction or a hidden decision slips through.
require "standard" "$STD_TEXT" "cited as an example of the phrase itself"
require "standard" "$STD_TEXT" "only for checks against the finished change"

# Why: the standard must not demand code-level detail, which the AUDIENCE rule and Phase 9 leave to the developer.
require "standard" "$STD_TEXT" "Code-level how"
require "standard" "$STD_TEXT" "not a deferral"

# Why: the standard overrides Autonomy First and Scope Discipline for specs, so it must name them or the rules contradict each other.
require "standard" "$STD_TEXT" "Autonomy First"
require "standard" "$STD_TEXT" "Scope Discipline"

# Why: verifying a command must not mean running builds, tests or deploys while writing a spec.
require "standard" "$STD_TEXT" "confirming it exists"
require "standard" "$STD_TEXT" "not obtained by running builds, test suites or deploys"

# Why: the mapping row and design requirement must describe the new structure, not the old table.
require "SKILL.md" "$SKILL_TEXT" "nested bullet list"
forbid "SKILL.md" "$SKILL_TEXT" "file table"

# Why: each phase that produces or checks spec content must name the standard and the ban, or the rule drifts out of the phase that needs it.
for n in 5 6 7 10 11; do
  PHASE_TEXT="$(region "$SKILL" "^## Phase $n:")"
  require "SKILL.md Phase $n" "$PHASE_TEXT" "Cold-Start Completeness Standard"
  require "SKILL.md Phase $n" "$PHASE_TEXT" "Deferral ban"
done

P11_TEXT="$(region "$SKILL" '^## Phase 11:')"

# Why: the Phase 11 reviewer starts with no context, so it must be told to read the standard and must have each FAIL condition spelled out.
require "SKILL.md Phase 11" "$P11_TEXT" "Cold-start completeness check"
require "SKILL.md Phase 11" "$P11_TEXT" "read skills/writing-specs/SKILL.md"
require "SKILL.md Phase 11" "$P11_TEXT" "before auditing"
require "SKILL.md Phase 11" "$P11_TEXT" "a file path that does not exist"
require "SKILL.md Phase 11" "$P11_TEXT" "line range or named heading"
require "SKILL.md Phase 11" "$P11_TEXT" "leaves a choice open"
require "SKILL.md Phase 11" "$P11_TEXT" "code-level how stays with the developer"
require "SKILL.md Phase 11" "$P11_TEXT" "out-of-scope item that conflicts with the story"
forbid "SKILL.md Phase 11" "$P11_TEXT" "three context"

# Why: both enforcement points (Phase 10 self-check, Phase 11 review) must carry the quoted-mention qualifier and the verification-section limit, or a quoted instruction or a hidden decision passes.
P10_TEXT="$(region "$SKILL" '^## Phase 10:')"
for pair in "Phase 10:$P10_TEXT" "Phase 11:$P11_TEXT"; do
  require "SKILL.md ${pair%%:*}" "${pair#*:}" "cited as an example of the phrase itself"
  require "SKILL.md ${pair%%:*}" "${pair#*:}" "only for checks against the finished change"
done

# Why: "clearly deferrable" let edge cases be pushed to a later reader, which the standard forbids.
P7_TEXT="$(region "$SKILL" '^## Phase 7:')"
forbid "SKILL.md Phase 7" "$P7_TEXT" "clearly deferrable"

# Why: Phase 7 names the standard in two places, so each needs its own anchor or deleting one is hidden by the other; the go/no-go checkbox is the point where the standard blocks writing.
require "SKILL.md Phase 7" "$P7_TEXT" "nothing the Deferral ban forbids would remain"
require "SKILL.md Phase 7" "$P7_TEXT" "make this absolute"

# Why: Phase 10 lists the standard among its rules in addition to the self-check, and the self-check alone must not mask that bullet's removal.
require "SKILL.md Phase 10" "$P10_TEXT" "including its Deferral ban: every claim verified before it is written"

# Why: the go/no-go framing must use the same bar as the standard, not a weaker "junior developer" or "critical unknowns" bar.
forbid "SKILL.md Phase 7" "$P7_TEXT" "significant guesswork"
forbid "SKILL.md Phase 7" "$P7_TEXT" "critical unknowns"
require "SKILL.md Phase 7" "$P7_TEXT" "zero prior context could implement in one pass"
require "SKILL.md Phase 7" "$P7_TEXT" "No unknowns about behavior, scope, file locations, values, interfaces or approach remain"

# Why: an out-of-scope edge case must not bypass the user-confirmed Viability Descope route for required behavior.
require "SKILL.md Phase 7" "$P7_TEXT" "cannot be put out of scope on your own authority"

# Why: Viability Descope covers only list capabilities, so Phase 7 must give required edge cases their own confirm-or-build path, including the autonomous always-build rule.
require "SKILL.md Phase 7" "$P7_TEXT" "Required edge cases"
require "SKILL.md Phase 7" "$P7_TEXT" "without re-opening the Viability List"
require "SKILL.md Phase 7" "$P7_TEXT" "the edge case is always built"

# Why: without a carve-out the Phase 11 reviewer would FAIL a user-confirmed descope on every iteration until the cap.
require "SKILL.md Phase 11" "$P11_TEXT" "unless covered by a user-confirmed"
require "SKILL.md Phase 11" "$P11_TEXT" "capability or required edge case"

# Why: the standard must sit after Relay Mode and before Phase 1 so it applies to every mode without renumbering phases.
STD_LINE="$(grep -n '^## Cold-Start Completeness Standard' "$SKILL" | head -n1 | cut -d: -f1)"
RELAY_LINE="$(grep -n '^## Relay Mode' "$SKILL" | head -n1 | cut -d: -f1)"
PHASE1_LINE="$(grep -n '^## Phase 1: Load Adapters' "$SKILL" | head -n1 | cut -d: -f1)"
if [ -z "$STD_LINE" ] || [ -z "$RELAY_LINE" ] || [ -z "$PHASE1_LINE" ]; then
  fail "SKILL.md: standard placement headings not all found"
elif [ "$STD_LINE" -le "$RELAY_LINE" ] || [ "$STD_LINE" -ge "$PHASE1_LINE" ]; then
  fail "SKILL.md: Cold-Start Completeness Standard is not between Relay Mode and Phase 1"
fi

# Why: the story requires the agent definition to state the standard, but the agent loads the skill, so it states the headline phrases and points to the skill rather than restating rules that could drift.
AGENT_TEXT="$(region "$AGENT" '^## Cold-Start Completeness Standard')"
require "spec-writer agent" "$AGENT_TEXT" "cold-start one-shot"
require "spec-writer agent" "$AGENT_TEXT" "never passed to the approver or the developer"
require "spec-writer agent" "$AGENT_TEXT" "owns the full rules"
forbid "spec-writer agent" "$AGENT_TEXT" "TBD"

# Why: each change gets one named section inside its phase, with an S form so small specs stay small.
P5_TEXT="$(region "$SKILL" '^## Phase 5:')"
P7_TEXT="$(region "$SKILL" '^## Phase 7:')"
P10_TEXT="$(region "$SKILL" '^## Phase 10:')"
for h in "C1 Size Gate" "C2 Type Checklists" "C3 Evidence Ledger" "C4 Impact Inventory"; do
  require "SKILL.md Phase 5" "$P5_TEXT" "### $h"
done
for h in "C5 Coverage Scan" "C6 Input Classes" "C7 Pre-mortem"; do
  require "SKILL.md Phase 7" "$P7_TEXT" "### $h"
done
for h in "C8 Requirement Quality" "C9 Traceability" "C10 Length Caps" "C11 Lint Entry Gate"; do
  require "SKILL.md Phase 10" "$P10_TEXT" "### $h"
done
for pair in "Phase 5:$P5_TEXT:4" "Phase 7:$P7_TEXT:3" "Phase 10:$P10_TEXT:4"; do
  label="${pair%%:*}"; rest="${pair#*:}"; n="${rest##*:}"
  count="$(printf '%s\n' "${rest%:*}" | grep -o '\*\*S:\*\*' | wc -l | tr -d ' ')"
  [ "$count" -ge "$n" ] || fail "SKILL.md $label: expected at least $n S-form lines, found $count"
done

# Why: replay and review-record rules must sit between the standard and Phase 1 so they apply to every mode without renumbering phases.
REPLAY_LINE="$(grep -n '^## Replay Mode' "$SKILL" | head -n1 | cut -d: -f1)"
RECORD_LINE="$(grep -n '^## Review Record' "$SKILL" | head -n1 | cut -d: -f1)"
if [ -z "$REPLAY_LINE" ] || [ -z "$RECORD_LINE" ]; then
  fail "SKILL.md: Replay Mode or Review Record heading missing"
elif [ "$REPLAY_LINE" -le "$STD_LINE" ] || [ "$RECORD_LINE" -le "$STD_LINE" ] || [ "$REPLAY_LINE" -ge "$PHASE1_LINE" ] || [ "$RECORD_LINE" -ge "$PHASE1_LINE" ]; then
  fail "SKILL.md: Replay Mode and Review Record are not between the standard and Phase 1"
fi

# Why: the old cap wording and the old generic-review sentence would contradict the 3-round regime.
forbid "SKILL.md" "$SKILL_TEXT" "5-iteration cap"
forbid "SKILL.md" "$SKILL_TEXT" "the shared review procedure stays generic"

# Why: the replay arguments must be documented where the arguments are.
ARGS_TEXT="$(region "$SKILL" '^## Arguments')"
require "SKILL.md Arguments" "$ARGS_TEXT" "replay-story:"
require "SKILL.md Arguments" "$ARGS_TEXT" "replay-spec:"

# Why: the spec-review regime is added beside the code regime; existing headings and the code-target text must survive byte for byte.
ADV="$PLUGIN_ROOT/skills/shared/adversarial-review.md"
ADV_TEXT="$(whole "$ADV")"
for h in "## Regime Selection" "## Spec Document Regime" "### Reviewer Inputs" "### C12 Severity Rubric" "### C13 Partitioned First Pass" "### C14 Verify-Only Rounds" "### C15 Stopping Rules" \
  "## Context Variables" "## Subagent Spawn Instructions" "## Subagent Procedure" "## Adversarial Persona" "## Output Format" "## Adversarial Review Report" "### Expectations Formed" "### Audit Findings" "### Summary" "### Recommendation" "## Handling Findings"; do
  require "adversarial-review.md" "$ADV_TEXT" "$h"
done
CODE_CKSUM="$(awk '/^## Subagent Spawn Instructions/{on=1} on{print} /^- \*\*UNVERIFIED items:\*\*/{exit}' "$ADV" | cksum | cut -d' ' -f1)"
[ "$CODE_CKSUM" = "3642265483" ] || fail "adversarial-review.md: code-target text changed (cksum $CODE_CKSUM)"
for needle in "BLOCKER" "MAJOR" "NOTE" "3 rounds" "never resets" "ripple set" "Maximum 5 total adversarial review iterations"; do
  require "adversarial-review.md" "$ADV_TEXT" "$needle"
done

# Why: the template must carry the new sections, size attribute and table classes the lint and the writer rely on.
for needle in 'data-size-class' 'id="requirements"' 'id="evidence-impact"' 'id="completeness-checks"' 'id="clarifications"' 'id="glossary"' \
  'id="evidence"' 'id="assumptions"' 'id="impact"' 'id="trace"' 'id="type-checklists"' 'id="coverage"' 'id="input-classes"' 'id="pre-mortem"' \
  'class="decision-table"' 'class="else-row"' 'class="evidence-ledger"' 'class="impact-inventory"' 'class="coverage-scan"' 'class="trace-matrix"' 'class="type-checklist"' 'class="pre-mortem"' 'class="input-classes"' 'class="assumptions"'; do
  require "template" "$TEMPLATE_TEXT" "$needle"
done

# Why: the checklist file is what the writer copies rows from, so every type and row id must exist.
TYPES="$PLUGIN_ROOT/skills/writing-specs/type-checklists.md"
TYPES_TEXT="$(whole "$TYPES")"
for h in UI API Bug Infra Migration Integration Refactor Rules; do
  grep -qx "## $h" "$TYPES" || fail "type-checklists.md: missing heading '## $h'"
done
for id in UI-1 UI-2 UI-3 UI-4 UI-5 UI-6 UI-7 API-1 API-2 API-3 API-4 API-5 BUG-1 BUG-2 BUG-3 BUG-4 INF-1 INF-2 INF-3 INF-4 MIG-1 MIG-2 MIG-3 MIG-4 INT-1 INT-2 INT-3 INT-4 REF-1 REF-2 RUL-1 RUL-2; do
  require "type-checklists.md" "$TYPES_TEXT" "$id"
done

# Why: the version bump must land in both manifests together and not fall below the release that carries these rules.
PLUGIN_VERSION="$(jq -r '.version' "$PLUGIN_ROOT/.claude-plugin/plugin.json")"
MARKET_VERSION="$(jq -r '.plugins[] | select(.name == "dev-workflow") | .version' "$REPO_ROOT/.claude-plugin/marketplace.json")"
[ "$PLUGIN_VERSION" = "$MARKET_VERSION" ] || fail "plugin.json version $PLUGIN_VERSION differs from marketplace $MARKET_VERSION"
[ "$(printf '%s\n%s\n' "2.56.0" "$PLUGIN_VERSION" | sort -V | head -n1)" = "2.56.0" ] || fail "plugin version $PLUGIN_VERSION is below 2.56.0"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
