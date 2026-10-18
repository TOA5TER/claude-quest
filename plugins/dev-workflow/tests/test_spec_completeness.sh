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

# Why: an out-of-scope edge case must not bypass the user-confirmed Viability Descope route for required behavior.
require "SKILL.md Phase 7" "$P7_TEXT" "cannot be put out of scope this way"

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
