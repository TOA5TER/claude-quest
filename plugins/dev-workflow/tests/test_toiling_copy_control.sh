#!/usr/bin/env bash
# Tests the toiling copy control: the SKILL.md wording, the shipped copy-control.html snippet, and the Verification checks that prove a written file carries the snippet.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$PLUGIN_ROOT/skills/toiling/SKILL.md"
SNIPPET="$PLUGIN_ROOT/skills/toiling/copy-control.html"

STYLE_OPEN='<style id="toiling-copy-control-style">'
STYLE_CLOSE='</style>'
SCRIPT_OPEN='<script id="toiling-copy-control-script">'
SCRIPT_CLOSE='</script>'

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

# The R-10 checks, written the way the toiling Verification section describes them. Prints the failing check letters; empty output means all pass.
check_file() {
  local file="$1" tag count lines script_line pre_line
  for tag in "$STYLE_OPEN" "$SCRIPT_OPEN"; do
    count="$(grep -c -x -F "$tag" "$file")"
    [ "$count" = "1" ] || echo "a"
  done
  if ! diff <(sed -n "\\#^${STYLE_OPEN}\$#,\\#^${STYLE_CLOSE}\$#p" "$SNIPPET") <(sed -n "\\#^${STYLE_OPEN}\$#,\\#^${STYLE_CLOSE}\$#p" "$file") >/dev/null 2>&1; then echo "b"; fi
  if ! diff <(sed -n "\\#^${SCRIPT_OPEN}\$#,\\#^${SCRIPT_CLOSE}\$#p" "$SNIPPET") <(sed -n "\\#^${SCRIPT_OPEN}\$#,\\#^${SCRIPT_CLOSE}\$#p" "$file") >/dev/null 2>&1; then echo "b"; fi
  script_line="$(grep -n -x -F "$SCRIPT_OPEN" "$file" | head -n1 | cut -d: -f1)"
  if [ -n "$script_line" ]; then
    grep -n '<pre[ >]' "$file" | cut -d: -f1 | while read -r pre_line; do
      [ "$pre_line" -lt "$script_line" ] || echo "c"
    done
  fi
}

# T-1: the mode regions and the new section name the control.
RUNBOOK_REGION="$(region "$SKILL" '^## Runbook mode' '^## Operations Standards')"
AGENT_REGION="$(region "$SKILL" '^## Agent-executed mode' '^## Runbook mode')"
SECTION="$(region "$SKILL" '^## Command blocks and the copy control' '^## Agent-executed mode')"
for pair in "Agent-executed mode:$AGENT_REGION" "Runbook mode:$RUNBOOK_REGION"; do
  label="${pair%%:*}"
  text="${pair#*:}"
  require "T-1 $label" "$text" "<pre>"
  require "T-1 $label" "$text" "copy control"
done
for needle in "copy-control.html" "clipboard SVG icon" "no text label" "check" "X" "2 seconds"; do
  require "T-1 new section" "$SECTION" "$needle"
done

# T-2: the new section states the technical constraints.
for needle in "inline CSS and JS only" "navigator.clipboard.writeText" "document.execCommand('copy')" "file://" "theme-aware"; do
  require "T-2 new section" "$SECTION" "$needle"
done

# T-3: the snippet.
if [ ! -f "$SNIPPET" ]; then
  fail "T-3: copy-control.html does not exist"
else
  for tag in "$STYLE_OPEN" "$SCRIPT_OPEN"; do
    [ "$(grep -c -x -F "$tag" "$SNIPPET")" = "1" ] || fail "T-3: '$tag' is not alone on one line exactly once"
  done
  for tag in "$STYLE_CLOSE" "$SCRIPT_CLOSE"; do
    [ "$(grep -c -x -F "$tag" "$SNIPPET")" = "1" ] || fail "T-3: '$tag' is not alone on one line exactly once"
  done
  SNIPPET_TEXT="$(cat "$SNIPPET")"
  for needle in "querySelectorAll('pre')" "writeText" "execCommand('copy')" "prefers-color-scheme: dark" "2000" "aria-label" \
    'd="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"' \
    '<rect x="8" y="2" width="8" height="4" rx="1"/>' \
    '<polyline points="20 6 9 17 4 12"/>' \
    '<line x1="18" y1="6" x2="6" y2="18"/>' \
    '<line x1="6" y1="6" x2="18" y2="18"/>'; do
    has "$SNIPPET_TEXT" "$needle" || fail "T-3: snippet missing '$needle'"
  done
  for needle in "http:" "https:" "src=" "<link" "@import" "<pre"; do
    if has "$SNIPPET_TEXT" "$needle"; then fail "T-3: snippet contains forbidden '$needle'"; fi
  done
fi

# T-4: the Verification region carries the checks.
VERIFY="$(region "$SKILL" '^\*\*Verification\.\*\*' '^\*\*Completion comment')"
for needle in "toiling-copy-control-style" "toiling-copy-control-script" "diff" "<pre" "early stop" "runbook.html" "record.html"; do
  require "T-4 Verification" "$VERIFY" "$needle"
done
COMPLETION="$(region "$SKILL" '^\*\*Completion Criteria\.\*\*' '^\*\*Final response')"
require "T-4 Completion Criteria" "$COMPLETION" "copy-control"

# T-5: the R-10 checks pass on a good page and fail on each broken copy.
if [ -f "$SNIPPET" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  STYLE_BLOCK="$(sed -n "\\#^${STYLE_OPEN}\$#,\\#^${STYLE_CLOSE}\$#p" "$SNIPPET")"
  SCRIPT_BLOCK="$(sed -n "\\#^${SCRIPT_OPEN}\$#,\\#^${SCRIPT_CLOSE}\$#p" "$SNIPPET")"
  {
    echo '<!DOCTYPE html>'
    echo '<html><head><meta charset="utf-8"><title>Sample</title>'
    echo "$STYLE_BLOCK"
    echo '</head><body>'
    echo '<h1>Sample</h1>'
    echo '<pre>psql -f a.sql &lt; b &amp;&amp; echo ok</pre>'
    echo "$SCRIPT_BLOCK"
    echo '</body></html>'
  } > "$TMP/good.html"
  [ -z "$(check_file "$TMP/good.html")" ] || fail "T-5: checks fail on a good page: $(check_file "$TMP/good.html" | tr '\n' ' ')"

  grep -v -F "$SCRIPT_OPEN" "$TMP/good.html" > "$TMP/no-script.html"
  has "$(check_file "$TMP/no-script.html")" "a" || fail "T-5: check (a) did not fail with the script opening line removed"

  sed 's/#f6f8fa/#f6f8fb/' "$TMP/good.html" > "$TMP/changed.html"
  has "$(check_file "$TMP/changed.html")" "b" || fail "T-5: check (b) did not fail with a changed style line"

  {
    cat "$TMP/good.html" | sed '$d'
    echo '<pre>kubectl get pods</pre>'
    echo '</body></html>'
  } > "$TMP/late-pre.html"
  has "$(check_file "$TMP/late-pre.html")" "c" || fail "T-5: check (c) did not fail with a pre after the script"
fi

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
