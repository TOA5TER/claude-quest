#!/usr/bin/env bash
# Tests that the worktree placement and attribution rules appear where a developer acts.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SKILL="$PLUGIN_ROOT/skills/developing/SKILL.md"
DISCOVERY="$PLUGIN_ROOT/skills/shared/repo-discovery.md"
AGENT="$PLUGIN_ROOT/agents/developer.md"

CANON='Worktrees go at <repo root>/.worktrees/<slug>, where the slug is the story or task ID, never in a sibling folder of the repo and never in the workspace parent folder.'

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' | tr '\n' ' ' | sed -e 's/  */ /g'
}

# region FILE START_REGEX STOP_KIND : prints the normalised region. STOP_KIND is blank, quote, bullet or h2.
region() {
  awk -v start="$2" -v kind="$3" '
    !on && $0 ~ start { on = 1; print; next }
    on {
      if (kind == "blank" && $0 ~ /^[[:space:]]*$/) exit
      if (kind == "quote" && $0 !~ /^>/) exit
      if (kind == "bullet" && $0 ~ /^   - /) exit
      if (kind == "h2" && $0 ~ /^## /) exit
      print
    }
  ' "$1" | normalise
}

whole() {
  normalise < "$1"
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

check_canon() {
  local label="$1" text="$2"
  [ -n "$text" ] || { fail "$label: region not found"; return; }
  has "$text" "$CANON" || fail "$label: missing the canonical worktree sentence"
}

check_attribution() {
  local label="$1" text="$2"
  [ -n "$text" ] || { fail "$label: region not found"; return; }
  has "$text" "Claude-Session" || fail "$label: does not name Claude-Session trailers"
  has "$text" "claude.ai" || fail "$label: does not name claude.ai links"
  has "$text" "flag the conflict to the user" || fail "$label: does not say to flag the conflict to the user"
}

# Why: the failing worker never read standards.md, so discovery and the developer agent must each carry the placement rule themselves.
check_canon "repo-discovery.md" "$(whole "$DISCOVERY")"
check_canon "developer agent" "$(whole "$AGENT")"

# Why: each site that tells a worker to create a worktree needs the rule, and deleting one site must not be masked by another.
check_canon "developing: repo-discovery paragraph" "$(region "$SKILL" '^Otherwise, determine which checkout' blank)"
check_canon "developing: REQUIRED isolation block" "$(region "$SKILL" 'REQUIRED: Set up workspace isolation' quote)"
check_canon "developing: multi-repo isolation bullet" "$(region "$SKILL" '^   - Workspace isolation per' bullet)"

# Why: "its own sibling folder" read like a worktree placement instruction and caused sibling worktrees.
if has "$(whole "$SKILL")" "its own sibling folder"; then
  fail "developing: still says 'its own sibling folder'"
fi

# Why: commits and PRs are written at these three sites, including by leaf sub-agents that never read the others.
check_attribution "developing: Commit and PR Process" "$(region "$SKILL" '^## Commit and PR Process' h2)"
check_attribution "developing: multi-repo attribution bullet" "$(region "$SKILL" '^   - Commit and PR attribution:' bullet)"
check_attribution "developer agent: Worktrees and attribution" "$(region "$AGENT" '^## Worktrees and attribution' h2)"

# Why: the version bump must land in both manifests together and not fall below the patch that carries these rules.
PLUGIN_VERSION="$(jq -r '.version' "$PLUGIN_ROOT/.claude-plugin/plugin.json")"
MARKET_VERSION="$(jq -r '.plugins[] | select(.name == "dev-workflow") | .version' "$REPO_ROOT/.claude-plugin/marketplace.json")"
[ "$PLUGIN_VERSION" = "$MARKET_VERSION" ] || fail "plugin.json version $PLUGIN_VERSION differs from marketplace $MARKET_VERSION"
[ "$(printf '%s\n%s\n' "2.50.0" "$PLUGIN_VERSION" | sort -V | head -n1)" = "2.50.0" ] || fail "plugin version $PLUGIN_VERSION is below 2.50.0"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
