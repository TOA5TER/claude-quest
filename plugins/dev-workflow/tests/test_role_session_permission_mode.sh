#!/usr/bin/env bash
# Tests the role-session bypass opt-in, its risk text, the host prerequisite and the auto-mode warning.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RS="$PLUGIN_ROOT/skills/shared/role-sessions.md"
README="$PLUGIN_ROOT/README.md"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' -e 's/\*\*//g' | tr '\n' ' ' | sed -e 's/  */ /g'
}

whole() {
  normalise < "$1"
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

need_text() {
  local text="$1" label="$2" phrase
  shift 2
  for phrase in "$@"; do
    has "$text" "$phrase" || fail "$label: missing '$phrase'"
  done
}

forbid_text() {
  local text="$1" label="$2" phrase
  shift 2
  for phrase in "$@"; do
    has "$text" "$phrase" && fail "$label: still contains '$phrase'"
  done
}

[ -f "$RS" ] || { fail "role-sessions.md missing"; exit 1; }
[ -f "$README" ] || { fail "README.md missing"; exit 1; }

# Why: the Optional config paragraph is the single source of the permission-mode rules, so the opt-in, risk and prerequisite must live in it.
optional_config="$(awk '/^\*\*Optional config\.\*\*/{p=1} p&&/^[[:space:]]*$/{exit} p{print}' "$RS" | normalise)"
[ -n "$optional_config" ] || fail "role-sessions.md: Optional config paragraph not found"
need_text "$optional_config" "Optional config" \
  "role_sessions.allow_bypass_permissions" \
  "--permission-mode" \
  "skipDangerousModePermissionPrompt" \
  "spec-writer" "developer" "reviewer" "tester" \
  "default launches" "no flag" \
  "permissions.deny"

# Why: the auto-mode warning needs its detection source and trigger spelled out so the orchestrator reads the right files.
rs_text="$(whole "$RS")"
need_text "$rs_text" "role-sessions.md" \
  "permissions.defaultMode" \
  "settings.local.json" \
  "Auto-mode warning"

# Why: the launch contract must tell the orchestrator how an accepted mode reaches the claude command.
launch_contract="$(awk '/^## Launch contract/{p=1;next} /^## /{p=0} p{print}' "$RS" | normalise)"
need_text "$launch_contract" "Launch contract" "--permission-mode"

# Why: users read the README, so it must carry the opt-in and the host prerequisite too.
readme_text="$(whole "$README")"
need_text "$readme_text" "README.md" \
  "role_sessions.allow_bypass_permissions" \
  "skipDangerousModePermissionPrompt"

# Why: the unconditional refusals are replaced by the opt-in rule and must not linger as contradictions.
forbid_text "$rs_text" "role-sessions.md" \
  "bypassPermissions is refused, because role sessions accept inbound messages from any local session"
forbid_text "$readme_text" "README.md" \
  "bypassPermissions and any other value are refused"

# Why: the auto-mode warning is not a fifth preflight check, so the numbered list must stay at four.
preflight_checks="$(awk '/^## Preflight, handshake, fallback/{p=1;next} /^\*\*Executed, not inferred\.\*\*/{p=0} p{print}' "$RS")"
has "$preflight_checks" "1. " || fail "role-sessions.md: preflight check 1 missing"
printf '%s\n' "$preflight_checks" | grep -Eq '^5\. ' && fail "role-sessions.md: a fifth numbered preflight check exists"

if [ "$FAILURES" -ne 0 ]; then
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
echo "PASS: role-session permission-mode wording checks"
