#!/usr/bin/env bash
# Tests the shared size gate and the places that read it: writing-specs C1 and C10, the spec lint caps, creating-stories Phase 3.5 and full-cycle.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$PLUGIN_ROOT/skills/shared/size-gate.md"
WS="$PLUGIN_ROOT/skills/writing-specs/SKILL.md"
LINT="$PLUGIN_ROOT/skills/writing-specs/spec-lint.py"
CS="$PLUGIN_ROOT/skills/creating-stories/SKILL.md"
SEEDING="$PLUGIN_ROOT/skills/shared/checkpoint-seeding.md"
FC="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' | tr '\n' ' ' | sed -e 's/  */ /g'
}

# region FILE START_REGEX STOP_REGEX : prints the normalised text from the first start match up to the next stop match.
region() {
  awk -v start="$2" -v stop="$3" '
    !on && $0 ~ start { on = 1; print; next }
    on {
      if ($0 ~ stop) exit
      print
    }
  ' "$1" | normalise
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

# Why: the gate must have one definition with every section both skills read, and the numbers the lint and the skills depend on.
if [ ! -f "$GATE" ]; then
  fail "size-gate.md does not exist"
else
  prev=0
  for h in "Who Reads This" "Counting" "Thresholds" "Size Classes" "Split Patterns" "Split Plan" "Size-Accepted Marker"; do
    line="$(grep -n "^## $h\$" "$GATE" | head -n1 | cut -d: -f1)"
    if [ -z "$line" ]; then fail "size-gate.md: missing heading '## $h'"
    elif [ "$line" -le "$prev" ]; then fail "size-gate.md: '## $h' is out of order"
    else prev="$line"; fi
  done
  GATE_TEXT="$(normalise < "$GATE")"
  for needle in "Example Mapping" "above 5" "at most 3 acceptance criteria" "1,500" "4,000" "SPIDR" "Humanizing Work"; do
    require "size-gate.md" "$GATE_TEXT" "$needle"
  done
fi

# Why: C1 and C10 must cite the shared file instead of restating numbers, or the two copies drift.
C1_TEXT="$(region "$WS" '^### C1 Size Gate' '^### ')"
C10_TEXT="$(region "$WS" '^### C10 Length Caps' '^### |^## ')"
for pair in "C1:$C1_TEXT" "C10:$C10_TEXT"; do
  label="${pair%%:*}"; text="${pair#*:}"
  require "writing-specs $label" "$text" "skills/shared/size-gate.md"
  for s in "1,500" "4,000" "exceeds 5" "more than 5" "at most 3 acceptance criteria"; do
    forbid "writing-specs $label" "$text" "$s"
  done
done

# Why: the lint keeps its own numbers so it never fails to run, so a test must hold them equal to the shared table.
if [ -f "$GATE" ]; then
  gate_s="$(grep -E '^\|[[:space:]]*\*\*S\*\*' "$GATE" | head -n1 | grep -oE '[0-9],[0-9]{3}' | head -n1 | tr -d ',')"
  gate_m="$(grep -E '^\|[[:space:]]*\*\*M\*\*' "$GATE" | head -n1 | grep -oE '[0-9],[0-9]{3}' | head -n1 | tr -d ',')"
  lint_s="$(grep -E '^CAPS = ' "$LINT" | sed -E 's/.*"S": *([0-9]+).*/\1/')"
  lint_m="$(grep -E '^CAPS = ' "$LINT" | sed -E 's/.*"M": *([0-9]+).*/\1/')"
  [ -n "$gate_s" ] && [ "$gate_s" = "$lint_s" ] || fail "S cap: size-gate.md '$gate_s' vs spec-lint.py '$lint_s'"
  [ -n "$gate_m" ] && [ "$gate_m" = "$lint_m" ] || fail "M cap: size-gate.md '$gate_m' vs spec-lint.py '$lint_m'"
fi

# Why: Phase 3.5 must exist once, between Phase 3 and Phase 4, ask one question, and say why no autonomous branch exists.
count="$(grep -c '^## Phase 3\.5: Size Check$' "$CS")"
[ "$count" = "1" ] || fail "creating-stories: expected exactly one '## Phase 3.5: Size Check' heading, found $count"
p3="$(grep -n '^## Phase 3:' "$CS" | head -n1 | cut -d: -f1)"
p35="$(grep -n '^## Phase 3\.5: Size Check$' "$CS" | head -n1 | cut -d: -f1)"
p4="$(grep -n '^## Phase 4:' "$CS" | head -n1 | cut -d: -f1)"
if [ -z "$p3" ] || [ -z "$p35" ] || [ -z "$p4" ] || [ "$p35" -le "$p3" ] || [ "$p35" -ge "$p4" ]; then
  fail "creating-stories: Phase 3.5 is not between Phase 3 and Phase 4"
fi
P35_TEXT="$(region "$CS" '^## Phase 3\.5: Size Check$' '^## ')"
for needle in "skills/shared/size-gate.md" "AskUserQuestion" "Accept the split" "Keep one story" "Size accepted by user:" "2-question" "autonomous"; do
  require "creating-stories Phase 3.5" "$P35_TEXT" "$needle"
done
P0_TEXT="$(region "$CS" '^## Phase 0:' '^## Phase 1')"
require "creating-stories Phase 0" "$P0_TEXT" "Phase 3.5"
SCOPE_TEXT="$(grep -m1 '^\*\*SCOPE BOUNDARY' "$CS" | normalise)"
require "creating-stories SCOPE BOUNDARY" "$SCOPE_TEXT" "Phase 3.5"
SEEDING_TEXT="$(normalise < "$SEEDING")"
require "checkpoint-seeding.md" "$SEEDING_TEXT" "Phase 3.5"

# Why: the marker has one writer and one reader, so both ends must carry the same prefix and the shared file the exact line.
require "writing-specs C1" "$C1_TEXT" "Size accepted by user"
if [ -f "$GATE" ]; then
  require "size-gate.md" "$GATE_TEXT" "Size accepted by user: the size gate tripped at story creation ({N} rules counted) and the user kept one story."
  require "size-gate.md" "$GATE_TEXT" "create each part with creating-stories"
fi

# Why: full-cycle drives one story, so it must say what happens to the later parts of a split.
FC_TEXT="$(region "$FC" '^## Stage — creating-stories' '^## ')"
require "full-cycle creating-stories stage" "$FC_TEXT" "Phase 3.5"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
