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
for role in spec-writer developer reviewer tester; do
  agent="$PLUGIN_ROOT/agents/$role.md"
  grep -qF "$ROOT_MARKER" "$agent" || fail "$role agent does not quote the plugin root marker"
  grep -qF "$STANDARDS_MARKER" "$agent" || fail "$role agent does not quote the standards path marker"
done

HOOK_SENTENCE="Role-session contract: answer every task message with an ack, then end the request with a result or blocked message sent through the SendMessage tool; plain text is never a reply."
ROLE_SESSIONS="$PLUGIN_ROOT/skills/shared/role-sessions.md"
FULL_CYCLE="$PLUGIN_ROOT/skills/full-cycle/SKILL.md"
STANDARDS="$PLUGIN_ROOT/skills/shared/standards.md"

# Why: extract one section's body so copies of a block can be compared byte for byte.
section_body() {
  awk -v pat="$2" -v maxlevel="$3" '
    /^#+ / {
      match($0, /^#+/)
      level = RLENGTH
      if (inside && level <= maxlevel) exit
      if (!inside && $0 ~ pat) { inside = 1; next }
    }
    inside { sub(/[ \t]+$/, ""); if ($0 != "") print }
  ' "$1"
}

# Why: the worker rules must be word-for-word identical in the protocol and every worker agent file so copies cannot drift.
CANONICAL="$(section_body "$ROLE_SESSIONS" '^### Canonical worker rules' 6)"
[ -n "$CANONICAL" ] || fail "role-sessions.md has no Canonical worker rules body"
for role in spec-writer developer reviewer tester; do
  agent="$PLUGIN_ROOT/agents/$role.md"
  BODY="$(section_body "$agent" '^## Communication contract$' 2)"
  [ -n "$BODY" ] || fail "$role agent has no Communication contract section"
  [ "$BODY" = "$CANONICAL" ] || fail "$role agent Communication contract differs from the canonical worker rules"
  # Why: the section must name the receipt, the terminal reply, and the tool that actually delivers messages.
  case "$BODY" in *'`ack`'*) ;; *) fail "$role agent contract missing backticked ack" ;; esac
  case "$BODY" in *"terminal reply"*) ;; *) fail "$role agent contract missing terminal reply" ;; esac
  case "$BODY" in *"SendMessage"*) ;; *) fail "$role agent contract missing SendMessage" ;; esac
  case "$BODY" in *"only when you run as a role session"*) ;; *) fail "$role agent contract missing role-session-only scope" ;; esac
  case "$BODY" in *"one-shot subagent"*) ;; *) fail "$role agent contract missing one-shot subagent carve-out" ;; esac
done

# Why: the protocol must carry the contract section and list ack as a worker-to-orchestrator type.
grep -qE '^## Communication contract' "$ROLE_SESSIONS" || fail "role-sessions.md has no Communication contract heading"
grep -E '^\| Worker to orchestrator' "$ROLE_SESSIONS" | grep -q '`ack`' || fail "message types table row for worker to orchestrator does not list ack"

# Why: the orchestrator skill must apply the contract at every send site and name the watcher.
for needle in "Communication contract" "Monitor" "Hand-off discipline." "wait for the shutdown result"; do
  grep -qF "$needle" "$FULL_CYCLE" || fail "full-cycle SKILL.md missing: $needle"
done

# Why: the wait standard must point at the watcher ladder and must not keep the old unnamed re-check wording in that paragraph.
WAIT_PARAGRAPH="$(awk '/^\*\*Message-wait variant \(role sessions\)\.\*\*/ {p=1} p && /^$/ {exit} p' "$STANDARDS")"
[ -n "$WAIT_PARAGRAPH" ] || fail "standards.md has no Message-wait variant paragraph"
case "$WAIT_PARAGRAPH" in *"Monitor"*) ;; *) fail "Message-wait variant paragraph does not mention the Monitor" ;; esac
case "$WAIT_PARAGRAPH" in *"arm a bounded, visible fallback re-check"*) fail "Message-wait variant paragraph keeps the old re-check wording" ;; esac

# Why: the hook must carry the contract reminder in a role session so it survives compaction and respawn.
case "$CONTEXT" in *"$HOOK_SENTENCE"*) ;; *) fail "role session context missing the contract sentence" ;; esac

flat_text() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' -e 's/\*\*//g' "$1" | tr '\n' ' ' | sed -e 's/  */ /g'
}
RS_TEXT="$(flat_text "$ROLE_SESSIONS")"
FC_TEXT="$(flat_text "$FULL_CYCLE")"
DEV_TEXT="$(flat_text "$PLUGIN_ROOT/skills/developing/SKILL.md")"
AGENT_TEXT="$(flat_text "$PLUGIN_ROOT/agents/developer.md")"
need() {
  case "$2" in *"$3"*) ;; *) fail "$1 missing: $3" ;; esac
}
refuse() {
  case "$2" in *"$3"*) fail "$1 still contains: $3" ;; esac
}

# Why: a nested-launch claim stated flat for every background session made a standalone run skip the preflight on inference.
refuse "role-sessions.md" "$RS_TEXT" "a background session cannot launch its own background session"
need "role-sessions.md Scope" "$RS_TEXT" "is not an epic worker: it always runs the preflight and launches role sessions"
need "role-sessions.md Scope" "$RS_TEXT" "settled by running preflight check 3, never by reasoning about what kind of session it is"

# Why: preflight checks must be executed, and only an observed failure may end role-session use.
need "role-sessions.md Preflight" "$RS_TEXT" "Executed, not inferred."
need "role-sessions.md Preflight" "$RS_TEXT" "A check fails only when it was run and its failure was observed"
need "role-sessions.md Preflight" "$RS_TEXT" "quoting the observed output"

# Why: a run that switches from fresh dispatch to role sessions needs a defined hand-over.
need "role-sessions.md" "$RS_TEXT" "### Mid-run mode change"
need "role-sessions.md mode change" "$RS_TEXT" "Do not stop an in-flight fresh developer until it has returned or has committed a work-in-progress checkpoint"
need "role-sessions.md mode change" "$RS_TEXT" "the run stays on fresh dispatch with the observed failure announced"
need "role-sessions.md mode change" "$RS_TEXT" "an earlier worker may have left changes in the worktree"

# Why: the epic worker rule must be defined by what epic dispatches, not by having no user to ask.
refuse "full-cycle SKILL.md" "$FC_TEXT" "the same autonomous-mode detection the rest of this skill uses"
need "full-cycle SKILL.md" "$FC_TEXT" "the per-task run the epic skill dispatched"
need "full-cycle SKILL.md" "$FC_TEXT" "An epic worker runs the fresh-dispatch path below"
refuse "full-cycle SKILL.md" "$FC_TEXT" "always runs the preflight. It runs the fresh-dispatch path"
need "full-cycle SKILL.md" "$FC_TEXT" "is not an epic worker and always runs the preflight."
need "full-cycle SKILL.md" "$FC_TEXT" "Mid-run mode change"
need "full-cycle SKILL.md" "$FC_TEXT" "when an executed preflight check failed"

# Why: the developer that inherits a worktree must inspect it and report what it kept or discarded.
need "developing SKILL.md" "$DEV_TEXT" "decide deliberately to keep or discard each uncommitted change (never silently)"
need "developing SKILL.md" "$DEV_TEXT" "in the message field of your first result"
need "developing SKILL.md multi-repo" "$DEV_TEXT" "first inspecting it as in the single-repo flow"
need "developer agent" "$AGENT_TEXT" "first inspect it per the developing skill"

# Why: a spec-writer role session must get the same injected paths and the generic contract sentence, and the old per-type wording must be gone so the hook cannot drift from the protocol.
SW_OUT="$(DEV_WORKFLOW_ROLE=spec-writer CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK" </dev/null)"
SW_CONTEXT="$(printf '%s' "$SW_OUT" | jq -r '.hookSpecificOutput.additionalContext' 2>/dev/null)"
case "$SW_CONTEXT" in *"$ROOT_MARKER: $PLUGIN_ROOT"*) ;; *) fail "spec-writer context missing plugin root line" ;; esac
case "$SW_CONTEXT" in *"$STANDARDS_MARKER: $PLUGIN_ROOT/skills/shared/standards.md"*) ;; *) fail "spec-writer context missing standards path line" ;; esac
case "$SW_CONTEXT" in *"$HOOK_SENTENCE"*) ;; *) fail "spec-writer context missing the contract sentence" ;; esac
case "$SW_CONTEXT" in *"develop, fix, review, or test"*) fail "spec-writer context still carries the old per-type contract wording" ;; esac

SPEC_WRITER="$PLUGIN_ROOT/agents/spec-writer.md"
WRITING_SPECS="$PLUGIN_ROOT/skills/writing-specs/SKILL.md"
SW_TEXT="$(flat_text "$SPEC_WRITER")"
WS_TEXT="$(flat_text "$WRITING_SPECS")"

# Why: the spec-writer agent must be a role-session worker and must no longer tell orchestrators to avoid dispatching it on the interactive path.
need "spec-writer agent" "$SW_TEXT" "## Running as a role session"
need "spec-writer agent" "$SW_TEXT" "## Communication contract"
need "spec-writer agent" "$SW_TEXT" "DEV_WORKFLOW_ROLE=spec-writer"
need "spec-writer agent" "$SW_TEXT" "use the absolute paths from your SessionStart context lines"
refuse "spec-writer agent" "$SW_TEXT" "do NOT dispatch this worker"

# Why: the standalone path must hand spec writing to the spec-writer instead of loading writing-specs into the orchestrator, or the orchestrator keeps writing specs itself.
refuse "full-cycle SKILL.md" "$FC_TEXT" "Invoke Skill: dev-workflow:writing-specs"
need "full-cycle SKILL.md" "$FC_TEXT" "The orchestrator never runs dev-workflow:writing-specs itself and never writes or edits a spec"
need "full-cycle SKILL.md" "$FC_TEXT" "(dev-workflow:spec-writer, role spec-writer"

# Why: the protocol must define every new spec-round message type, or the orchestrator and worker disagree on the vocabulary.
for needle in '`spec`' '`answer`' '`revise`' '`finalize`'; do
  grep -E '^\| Orchestrator to worker' "$ROLE_SESSIONS" | grep -qF "$needle" || fail "message types table row for orchestrator to worker does not list $needle"
done

# Why: relay mode must never prompt a human who is not watching a background session, and Phase 12 must stay behind the recorded-approval check, or an unapproved spec could move the story to Ready for Dev.
need "writing-specs SKILL.md" "$WS_TEXT" "## Relay Mode"
need "writing-specs SKILL.md" "$WS_TEXT" "Never call AskUserQuestion in relay mode"
need "writing-specs SKILL.md" "$WS_TEXT" "Finalize check"
need "writing-specs SKILL.md" "$WS_TEXT" "Never run Phase 12 on the message alone"
need "standards.md" "$(flat_text "$STANDARDS")" "an explicit mode: relay overrides every autonomous-mode trigger"

need "writing-specs SKILL.md" "$WS_TEXT" "Mode detection fails closed"
need "writing-specs SKILL.md" "$WS_TEXT" "Approval fields are never yours"
need "writing-specs SKILL.md" "$WS_TEXT" "returns a kept, pre-existing spec"
need "writing-specs SKILL.md" "$WS_TEXT" "kind: questions"
need "writing-specs SKILL.md" "$WS_TEXT" "kind: stop"
need "spec-writer.md" "$SW_TEXT" "Mode fails closed"
need "spec-writer.md" "$SW_TEXT" "written solely by the orchestrator"
need "role-sessions.md" "$RS_TEXT" "kind: questions"
need "role-sessions.md" "$RS_TEXT" "kind: stop"
need "role-sessions.md" "$RS_TEXT" "spec-paths"
need "role-sessions.md" "$RS_TEXT" "transitioned"
need "role-sessions.md" "$RS_TEXT" "a spec-writer blocked with kind: questions is a normal round of the relay loop"
need "role-sessions.md" "$RS_TEXT" "read the role's recovery source per that row, then re-send once"
refuse "role-sessions.md" "$RS_TEXT" "read GitHub, check for commits newer than the request, then re-send once"
need "full-cycle SKILL.md" "$FC_TEXT" "never send it back as an answer"
need "full-cycle SKILL.md" "$FC_TEXT" "mode: autonomous"
need "context-compaction.md" "$(flat_text "$PLUGIN_ROOT/skills/shared/context-compaction.md")" "written solely by the orchestrator, never by the spec-writer"
need "standards.md" "$(flat_text "$STANDARDS")" "For writing-specs and the spec-writer only, mode detection fails closed"
need "standards.md" "$(flat_text "$STANDARDS")" "Autonomous mode: Activated when any of the following are true:"
need "writing-specs SKILL.md" "$WS_TEXT" "a run that cannot ask the user (an autonomous trigger is present) never runs Phase 12"
need "writing-specs SKILL.md" "$WS_TEXT" "An interactive run (the user invoked the skill directly and AskUserQuestion is available) is unaffected and follows the User Approval Gate and Phase 12 as before"
need "standards.md" "$(flat_text "$STANDARDS")" "a run that cannot ask the user (an autonomous trigger is present) never runs Phase 12"
need "standards.md" "$(flat_text "$STANDARDS")" "An interactive run (the user invoked the skill directly and AskUserQuestion is available) is unaffected"
refuse "writing-specs SKILL.md" "$WS_TEXT" "With neither signal and no mode: relay, never run Phase 12"
need "standards.md" "$(flat_text "$STANDARDS")" "Every other skill and worker keeps the autonomous triggers above unchanged"
refuse "standards.md" "$(flat_text "$STANDARDS")" "Autonomous mode: Requires a positive epic signal"
need "standards.md" "$(flat_text "$STANDARDS")" "are exempt from this cap"
refuse "standards.md" "$(flat_text "$STANDARDS")" "Relay mode (third mode)"
need "README.md" "$(flat_text "$PLUGIN_ROOT/README.md")" "one spec-writer, one developer per repo, plus a reviewer and a tester per PR"

# Why: the version bump must be pinned numerically without breaking on later bumps.
version_at_least() {
  local IFS=.
  local -a have=($1) want=($2)
  local i
  for i in 0 1 2; do
    [ "${have[$i]:-0}" -gt "${want[$i]:-0}" ] && return 0
    [ "${have[$i]:-0}" -lt "${want[$i]:-0}" ] && return 1
  done
  return 0
}
version_at_least "$PLUGIN_VERSION" "2.50.0" || fail "plugin.json version $PLUGIN_VERSION is below 2.50.0"
version_at_least "$PLUGIN_VERSION" "2.54.0" || fail "plugin.json version $PLUGIN_VERSION is below 2.54.0"

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES check(s) failed" >&2
  exit 1
fi
echo "PASS: role-session-context"
