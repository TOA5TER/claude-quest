#!/usr/bin/env bash
# Tests that every role-session launch is preceded by a by-name lookup and that implementation work stays with the developer session.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RS="$PLUGIN_ROOT/skills/shared/role-sessions.md"
FC="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"

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

line_starting() {
  awk -v lead="$2" 'index($0, lead) == 1 {print; exit}' "$1" | normalise
}

bullet_block() {
  awk -v lead="$2" 'index($0, lead) == 1 {p=1; print; next} p && /^[^[:space:]]/{exit} p{print}' "$1" | normalise
}

stage_section() {
  awk -v head="## Stage — $2" 'index($0, head) == 1 {p=1; next} /^## /{p=0} p{print}' "$1" | normalise
}

[ -f "$RS" ] || { fail "role-sessions.md missing"; exit 1; }
[ -f "$FC" ] || { fail "full-cycle/SKILL.md missing"; exit 1; }

# Why: the lookup rule is the single source of the by-name match, so its name family, candidate name and exemptions must be spelled out in role-sessions.md.
rs_text="$(whole "$RS")"
need_text "$rs_text" "role-sessions.md" \
  "Lookup before every launch" \
  "including a lazy mid-run launch" \
  "name family" \
  "candidate name" \
  "offline Remote Control" \
  "name [ref]"

# Why: the rule must name the commands it runs and ban a duplicate launch; these phrases exist elsewhere in the file, so they are checked only inside the bullet.
lookup_bullet="$(bullet_block "$RS" '- **Lookup before every launch.**')"
[ -n "$lookup_bullet" ] || fail "role-sessions.md: Lookup before every launch bullet not found"
need_text "$lookup_bullet" "Lookup bullet" \
  "claude agents --json --all" \
  "ListAgents" \
  "claude respawn" \
  "ping" \
  "Never launch under a name that a counting entry already has"

# Why: the host-rename remark must not read as permission to launch a duplicate.
names_bullet="$(line_starting "$RS" '- **Names.**')"
[ -n "$names_bullet" ] || fail "role-sessions.md: Names bullet not found"
need_text "$names_bullet" "Names bullet" \
  "never permission to launch under a name that a counting entry already has"

# Why: the Resume bullet must apply mid-run and cover every finished state, not only re-invocation and stopped sessions.
resume_bullet="$(line_starting "$RS" '- **Resume.**')"
[ -n "$resume_bullet" ] || fail "role-sessions.md: Resume bullet not found"
need_text "$resume_bullet" "Resume bullet" \
  "Whenever a session is needed (on re-invocation or mid-run)" \
  "a done, stopped, failed, or crashed one is respawned" \
  "Lookup before every launch"
forbid_text "$resume_bullet" "Resume bullet" \
  "Resume. On re-invocation, list sessions"

# Why: the Lazy launch bullet must defer to the lookup instead of launching unconditionally.
fc_text="$(whole "$FC")"
lazy_bullet="$(line_starting "$FC" '- **Lazy launch.**')"
[ -n "$lazy_bullet" ] || fail "full-cycle: Lazy launch bullet not found"
need_text "$lazy_bullet" "Lazy launch bullet" "Lookup before every launch"
forbid_text "$fc_text" "full-cycle/SKILL.md" \
  "On resume, match existing sessions by these per-repo names."

# Why: each stage launches a role session, so each stage must run the lookup first.
for stage in writing-specs developing reviewing-prs testing-prs; do
  section="$(stage_section "$FC" "$stage")"
  [ -n "$section" ] || fail "full-cycle: Stage — $stage section not found"
  need_text "$section" "Stage — $stage" "Lookup before every launch"
done

# Why: the resume paragraph must respawn every finished state and name the matching rule.
resume_para="$(line_starting "$FC" '**Role sessions on resume.**')"
[ -n "$resume_para" ] || fail "full-cycle: Role sessions on resume paragraph not found"
need_text "$resume_para" "Role sessions on resume" "respawn a done, stopped, failed, or crashed one"

# Why: implementation work must never bypass an existing developer session.
scope="$(line_starting "$FC" '**SCOPE BOUNDARY:**')"
[ -n "$scope" ] || fail "full-cycle: SCOPE BOUNDARY paragraph not found"
need_text "$scope" "SCOPE BOUNDARY" "never to the orchestrator itself or a fresh subagent while that session exists"

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
echo "PASS: role-session lookup-before-launch wording is in place"
