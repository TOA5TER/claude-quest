#!/usr/bin/env bash
# Tests that worker agents carry short role names and every documented dispatch value is plugin-qualified.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
PLUGIN_NAME="dev-workflow"
ROLES="developer orchestrator pr-state-reader reviewer spec-writer tester"
DOCS=("$PLUGIN_ROOT/README.md" "$PLUGIN_ROOT/CLAUDE.md" "$PLUGIN_ROOT"/skills/*/SKILL.md "$PLUGIN_ROOT"/skills/shared/*.md)
DISPATCH_DOCS=("$PLUGIN_ROOT/skills/full-cycle/SKILL.md" "$PLUGIN_ROOT/skills/epic/SKILL.md" "$PLUGIN_ROOT/skills/shared/standards.md" "$PLUGIN_ROOT/skills/shared/role-sessions.md")

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

for role in $ROLES; do
  agent="$PLUGIN_ROOT/agents/$role.md"
  [ -f "$agent" ] || { fail "missing agent file agents/$role.md"; continue; }
  [ "$(sed -n 's/^name: //p' "$agent" | head -n1)" = "$role" ] || fail "agents/$role.md front-matter name is not $role"
done

for agent in "$PLUGIN_ROOT"/agents/*.md; do
  base="$(basename "$agent" .md)"
  case "$base" in "$PLUGIN_NAME"-*) fail "agent file $base.md starts with the plugin name" ;; esac
  [ "$(sed -n 's/^name: //p' "$agent" | head -n1)" = "$base" ] || fail "agent file $base.md name differs from its front-matter name"
done

# Why: a repeated plugin prefix or an unqualified role in a doc points at a name the Agent tool does not register.
for doc in "${DOCS[@]}"; do
  if grep -nE "$PLUGIN_NAME-(developer|orchestrator|pr-state-reader|reviewer|spec-writer|tester|\\\$)" "$doc" >/dev/null; then
    fail "$(basename "$doc") refers to an old-style worker name"
  fi
done
for t in "$PLUGIN_ROOT"/tests/*.sh; do
  [ "$t" = "$0" ] && continue
  grep -qE "$PLUGIN_NAME-(developer|orchestrator|pr-state-reader|reviewer|spec-writer|tester|\\\$)" "$t" && fail "$(basename "$t") refers to an old-style worker name"
done

# Why: dispatch and launch values must be plugin-qualified roles that match a real agent file.
for doc in "${DISPATCH_DOCS[@]}"; do
  while IFS= read -r value; do
    [ -n "$value" ] || continue
    case "$value" in
      general-purpose) ;;
      "$PLUGIN_NAME":*) [ -f "$PLUGIN_ROOT/agents/${value#"$PLUGIN_NAME":}.md" ] || fail "$(basename "$doc") dispatch value $value has no agent file" ;;
      *) fail "$(basename "$doc") dispatch value $value is not plugin-qualified" ;;
    esac
  done < <(grep -oE 'subagent_type: `?[A-Za-z0-9:_-]+' "$doc" | sed -E 's/subagent_type: `?//')
done

for doc in "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" "$PLUGIN_ROOT/skills/shared/standards.md"; do
  while IFS= read -r cell; do
    value="${cell//\`/}"
    value="${value// /}"
    case "$value" in
      "$PLUGIN_NAME":*) [ -f "$PLUGIN_ROOT/agents/${value#"$PLUGIN_NAME":}.md" ] || fail "$(basename "$doc") table value $value has no agent file" ;;
      *) fail "$(basename "$doc") table value $value is not plugin-qualified" ;;
    esac
  done < <(awk -F'|' '/^\| *Stage \/ dispatch/ {t=1; next} t && /^\|[- |]+$/ {next} t && /^\|/ {print $3; next} {t=0}' "$doc")
done

for role in developer reviewer tester; do
  grep -qF "$PLUGIN_NAME:$role" "$PLUGIN_ROOT/skills/shared/role-sessions.md" "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" || fail "no qualified launch value $PLUGIN_NAME:$role documented"
done

# Why: every relative link to an agent file must resolve.
for doc in "${DOCS[@]}"; do
  while IFS= read -r link; do
    target="$(dirname "$doc")/$link"
    [ -f "$target" ] || fail "$(basename "$doc") links to missing agent file $link"
  done < <(grep -oE '\]\([^)]*agents/[A-Za-z0-9._-]+\.md\)' "$doc" | sed -E 's/^\]\(//; s/\)$//')
done

PLUGIN_VERSION="$(jq -r '.version' "$PLUGIN_ROOT/.claude-plugin/plugin.json")"
MARKET_VERSION="$(jq -r --arg n "$PLUGIN_NAME" '.plugins[] | select(.name == $n) | .version' "$REPO_ROOT/.claude-plugin/marketplace.json")"
[ "$PLUGIN_VERSION" = "$MARKET_VERSION" ] || fail "plugin.json version $PLUGIN_VERSION differs from marketplace $MARKET_VERSION"
[ "$PLUGIN_VERSION" = "2.50.0" ] || fail "plugin version $PLUGIN_VERSION is not 2.50.0"

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES check(s) failed" >&2
  exit 1
fi
echo "PASS: agent-names"
