#!/usr/bin/env bash
# Tests for hooks/role-session-context.sh and its registration and agent wiring.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$PLUGIN_ROOT/hooks/role-session-context.sh"
ROOT_MARKER="dev-workflow plugin root (resolved, authoritative)"
STANDARDS_MARKER="dev-workflow standards path (resolved, authoritative)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

make_root() {
  mkdir -p "$1/skills/shared"
  echo "# standards" > "$1/skills/shared/standards.md"
}

# Why: a role session must be told the absolute plugin root and standards path it cannot otherwise resolve.
ROLE_OUT="$(DEV_WORKFLOW_ROLE=developer CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK" </dev/null)"
ROLE_STATUS=$?
[ "$ROLE_STATUS" -eq 0 ] || fail "role session exit code $ROLE_STATUS"
if printf '%s' "$ROLE_OUT" | jq -e . >/dev/null 2>&1; then
  [ "$(printf '%s' "$ROLE_OUT" | jq -r '.hookSpecificOutput.hookEventName')" = "SessionStart" ] || fail "event is not SessionStart"
  CONTEXT="$(printf '%s' "$ROLE_OUT" | jq -r '.hookSpecificOutput.additionalContext')"
  case "$CONTEXT" in *"$ROOT_MARKER: $PLUGIN_ROOT"*) ;; *) fail "missing plugin root line" ;; esac
  case "$CONTEXT" in *"$STANDARDS_MARKER: $PLUGIN_ROOT/skills/shared/standards.md"*) ;; *) fail "missing standards path line" ;; esac
  case "$CONTEXT" in *"Resolve relative skills/ paths in the dev-workflow plugin's own agent and skill files against the plugin root above."*) ;; *) fail "missing scoped resolution sentence" ;; esac
  case "$CONTEXT" in *"Resolve every relative skills/ path"*) fail "injected instruction is not scoped to this plugin's files" ;; esac
  [ -f "$PLUGIN_ROOT/skills/shared/standards.md" ] || fail "standards file missing from the plugin"
else
  fail "role session output is not valid JSON: $ROLE_OUT"
fi

# Why: a non-role session must see no new output at all.
for value in "unset" "empty"; do
  if [ "$value" = "unset" ]; then
    OUT="$(env -u DEV_WORKFLOW_ROLE CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK" </dev/null)"
  else
    OUT="$(DEV_WORKFLOW_ROLE= CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK" </dev/null)"
  fi
  STATUS=$?
  [ "$STATUS" -eq 0 ] || fail "non-role ($value) exit code $STATUS"
  [ -z "$OUT" ] || fail "non-role ($value) produced output: $OUT"
done

# Why: without the host-provided plugin root the hook must derive the same paths from its own location.
FALLBACK_OUT="$(DEV_WORKFLOW_ROLE=reviewer env -u CLAUDE_PLUGIN_ROOT bash "$HOOK" </dev/null)"
FALLBACK_CONTEXT="$(printf '%s' "$FALLBACK_OUT" | jq -r '.hookSpecificOutput.additionalContext' 2>/dev/null)"
case "$FALLBACK_CONTEXT" in *"$STANDARDS_MARKER: $PLUGIN_ROOT/skills/shared/standards.md"*) ;; *) fail "unset-root fallback did not derive the standards path: $FALLBACK_OUT" ;; esac
case "$FALLBACK_CONTEXT" in *"$ROOT_MARKER: $PLUGIN_ROOT"*) ;; *) fail "unset-root fallback did not derive the plugin root: $FALLBACK_OUT" ;; esac

# Why: a plugin root without the standards file is not an error and must not inject a path that does not exist.
EMPTY_ROOT="$TMP/empty-root"
mkdir -p "$EMPTY_ROOT"
MISSING_OUT="$(DEV_WORKFLOW_ROLE=tester CLAUDE_PLUGIN_ROOT="$EMPTY_ROOT" bash "$HOOK" </dev/null)"
MISSING_STATUS=$?
[ "$MISSING_STATUS" -eq 0 ] || fail "missing standards exit code $MISSING_STATUS"
[ -z "$MISSING_OUT" ] || fail "missing standards produced output: $MISSING_OUT"

# Why: a path containing a double quote must still produce valid JSON.
QUOTE_ROOT="$TMP/we\"ird"
make_root "$QUOTE_ROOT"
QUOTE_OUT="$(DEV_WORKFLOW_ROLE=developer CLAUDE_PLUGIN_ROOT="$QUOTE_ROOT" bash "$HOOK" </dev/null)"
if printf '%s' "$QUOTE_OUT" | jq -e . >/dev/null 2>&1; then
  QUOTE_CONTEXT="$(printf '%s' "$QUOTE_OUT" | jq -r '.hookSpecificOutput.additionalContext')"
  case "$QUOTE_CONTEXT" in *"$QUOTE_ROOT/skills/shared/standards.md"*) ;; *) fail "quoted path not preserved" ;; esac
else
  fail "quoted path produced invalid JSON: $QUOTE_OUT"
fi

# Why: the hook ignores stdin, so empty or garbage input must never change the exit code or leak output.
for input in "" "not json" '{"broken":'; do
  OUT="$(printf '%s' "$input" | env -u DEV_WORKFLOW_ROLE CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK")"
  STATUS=$?
  [ "$STATUS" -eq 0 ] || fail "non-role with stdin '$input' exit code $STATUS"
  [ -z "$OUT" ] || fail "non-role with stdin '$input' produced output: $OUT"
  printf '%s' "$input" | DEV_WORKFLOW_ROLE=developer CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK" >/dev/null
  [ "$?" -eq 0 ] || fail "role session with stdin '$input' did not exit 0"
  printf '%s' "$input" | DEV_WORKFLOW_ROLE=developer CLAUDE_PLUGIN_ROOT="$EMPTY_ROOT" bash "$HOOK" >/dev/null
  [ "$?" -eq 0 ] || fail "missing-file role session with stdin '$input' did not exit 0"
done

# Why: the hook must be registered for SessionStart, executable, and committed with the executable mode.
jq -e . "$PLUGIN_ROOT/hooks/hooks.json" >/dev/null 2>&1 || fail "hooks.json is not valid JSON"
COMMANDS="$(jq -r '.hooks.SessionStart[]?.hooks[]?.command' "$PLUGIN_ROOT/hooks/hooks.json" 2>/dev/null)"
[ -n "$COMMANDS" ] || fail "hooks.json has no SessionStart command"
while IFS= read -r command; do
  [ -n "$command" ] || continue
  target="${command//\$\{CLAUDE_PLUGIN_ROOT\}/$PLUGIN_ROOT}"
  [ -f "$target" ] || fail "SessionStart command target missing: $command"
  [ -x "$target" ] || fail "SessionStart command target not executable: $command"
done <<< "$COMMANDS"
printf '%s' "$COMMANDS" | grep -q "role-session-context.sh" || fail "SessionStart does not register role-session-context.sh"
jq -e '.hooks.PostToolUse and .hooks.Stop' "$PLUGIN_ROOT/hooks/hooks.json" >/dev/null 2>&1 || fail "existing hook registrations changed"
if git -C "$PLUGIN_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  MODE="$(git -C "$PLUGIN_ROOT" ls-files -s hooks/role-session-context.sh | cut -d' ' -f1)"
  if [ -n "$MODE" ] && [ "$MODE" != "100755" ]; then
    fail "role-session-context.sh is committed with mode $MODE, expected 100755"
  fi
fi
for manifest in "$PLUGIN_ROOT/.claude-plugin/plugin.json" "$PLUGIN_ROOT/../../.claude-plugin/marketplace.json"; do
  jq -e . "$manifest" >/dev/null 2>&1 || fail "manifest is not valid JSON: $manifest"
done
PLUGIN_VERSION="$(jq -r '.version' "$PLUGIN_ROOT/.claude-plugin/plugin.json")"
MARKET_VERSION="$(jq -r '.plugins[] | select(.name == "dev-workflow") | .version' "$PLUGIN_ROOT/../../.claude-plugin/marketplace.json")"
[ "$PLUGIN_VERSION" = "$MARKET_VERSION" ] || fail "plugin.json ($PLUGIN_VERSION) and marketplace.json ($MARKET_VERSION) versions differ"

# Why: each role agent must point at the injected paths so it never searches the disk for a copy.
for role in developer reviewer tester; do
  agent="$PLUGIN_ROOT/agents/dev-workflow-$role.md"
  grep -qF "$ROOT_MARKER" "$agent" || fail "$role agent does not quote the plugin root marker"
  grep -qF "$STANDARDS_MARKER" "$agent" || fail "$role agent does not quote the standards path marker"
done

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES check(s) failed" >&2
  exit 1
fi
echo "PASS: role-session-context"
