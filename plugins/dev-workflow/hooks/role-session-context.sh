#!/usr/bin/env bash
# hooks/role-session-context.sh — SessionStart hook: hand role sessions the absolute plugin root
# Role sessions get neither their agent file's path nor CLAUDE_PLUGIN_ROOT, so the relative
# skills/ paths in the role agents cannot be resolved there. Acts only when DEV_WORKFLOW_ROLE
# is set. Exits 0 on any error and never blocks session start.
set -uo pipefail

[ -n "${DEV_WORKFLOW_ROLE:-}" ] || exit 0

main() {
  local root standards message
  root="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  standards="$root/skills/shared/standards.md"
  [ -f "$standards" ] || return 0

  message="$(printf '%s\n%s\n%s' \
    "dev-workflow plugin root (resolved, authoritative): $root" \
    "dev-workflow standards path (resolved, authoritative): $standards" \
    "Resolve every relative skills/ path in your instructions against the plugin root above. Do not search the disk for other copies.")"

  jq -n --arg context "$message" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $context}}'
}

main "$@" 2>/dev/null || true
exit 0
