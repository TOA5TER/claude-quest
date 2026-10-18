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

# Why: area -> file -> detail is three nested lists; fewer means the nesting collapsed.
UL_COUNT="$(printf '%s\n' "$TC_RAW" | grep -o '<ul' | wc -l | tr -d ' ')"
[ "${UL_COUNT:-0}" -ge 3 ] || fail "template Technical Context: found ${UL_COUNT:-0} <ul openings, need at least 3"

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

# Why: the ban lists its typical forms literally so the writer and the reviewer match against the same phrases.
require "SKILL.md" "$SKILL_TEXT" "verify that"
require "SKILL.md" "$SKILL_TEXT" "confirm whether"
require "SKILL.md" "$SKILL_TEXT" "developer should decide"
require "SKILL.md" "$SKILL_TEXT" "TBD"
require "SKILL.md" "$SKILL_TEXT" "Matching is by intent"

# Why: the mapping row and design requirement must describe the new structure, not the old table.
require "SKILL.md" "$SKILL_TEXT" "nested bullet list"
forbid "SKILL.md" "$SKILL_TEXT" "file table"

# Why: each phase that produces or checks spec content must name the standard and the ban, or the rule drifts out of the phase that needs it.
for n in 5 6 7 10 11; do
  PHASE_TEXT="$(region "$SKILL" "^## Phase $n:")"
  require "SKILL.md Phase $n" "$PHASE_TEXT" "Cold-Start Completeness Standard"
  require "SKILL.md Phase $n" "$PHASE_TEXT" "Deferral ban"
done

# Why: the Phase 11 reviewer needs an explicit instruction to FAIL deferred wording and unverifiable references.
require "SKILL.md Phase 11" "$(region "$SKILL" '^## Phase 11:')" "Cold-start completeness check"

# Why: "clearly deferrable" let edge cases be pushed to a later reader, which the standard forbids.
forbid "SKILL.md Phase 7" "$(region "$SKILL" '^## Phase 7:')" "clearly deferrable"

# Why: the standard must sit after Relay Mode and before Phase 1 so it applies to every mode without renumbering phases.
STD_LINE="$(grep -n '^## Cold-Start Completeness Standard' "$SKILL" | head -n1 | cut -d: -f1)"
RELAY_LINE="$(grep -n '^## Relay Mode' "$SKILL" | head -n1 | cut -d: -f1)"
PHASE1_LINE="$(grep -n '^## Phase 1: Load Adapters' "$SKILL" | head -n1 | cut -d: -f1)"
if [ -z "$STD_LINE" ] || [ -z "$RELAY_LINE" ] || [ -z "$PHASE1_LINE" ]; then
  fail "SKILL.md: standard placement headings not all found"
elif [ "$STD_LINE" -le "$RELAY_LINE" ] || [ "$STD_LINE" -ge "$PHASE1_LINE" ]; then
  fail "SKILL.md: Cold-Start Completeness Standard is not between Relay Mode and Phase 1"
fi

# Why: the spec-writer worker never loads the skill's section, so it must carry the standard's core phrases itself.
AGENT_TEXT="$(whole "$AGENT")"
require "spec-writer agent" "$AGENT_TEXT" "## Cold-Start Completeness Standard"
require "spec-writer agent" "$AGENT_TEXT" "cold-start one-shot"
require "spec-writer agent" "$AGENT_TEXT" "never passed to the approver or the developer"

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
