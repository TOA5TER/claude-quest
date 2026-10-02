#!/usr/bin/env bash
# hooks/context-meter.sh — PostToolUse hook: context usage meter
# Reads transcript JSONL, computes token % against fixed 200k baseline,
# emits additionalContext on tier crossings (60%, 75%). Exits 0 on any error.
set -euo pipefail

# Role sessions manage their own context; never touch shared state.
[ -n "${DEV_WORKFLOW_ROLE:-}" ] && exit 0

BASELINE="${DEV_WORKFLOW_COMPACT_BASELINE:-200000}"
STATE_DIR="$HOME/.claude/dev-workflow/state"
TIER_FILE="$STATE_DIR/context-meter-tier.txt"

main() {
  # Read hook input from stdin
  local input
  input="$(cat)"

  # Extract transcript path
  local transcript_path
  transcript_path="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)" || true

  if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
    exit 0
  fi

  # Extract the most recent assistant message's usage block.
  # Real Claude Code transcript JSONL: top-level keys are parentUuid, message, requestId, type, uuid, ...
  # The assistant payload (role, content, usage, ...) lives under .message.
  # Older/edge formats may have role+usage at the top level — we fall back to .usage.* for resilience.
  local last_assistant_line
  last_assistant_line="$(grep '"role":"assistant"' "$transcript_path" 2>/dev/null | tail -1)" || true

  if [ -z "$last_assistant_line" ]; then
    exit 0
  fi

  # Sum all token fields from the last assistant message.
  # Primary path: .message.usage (real Claude Code transcripts)
  # Fallback path: .usage (older/edge formats)
  local total_tokens
  total_tokens="$(printf '%s\n' "$last_assistant_line" | jq -r '
    (.message.usage.input_tokens // .usage.input_tokens // 0) +
    (.message.usage.cache_read_input_tokens // .usage.cache_read_input_tokens // 0) +
    (.message.usage.cache_creation_input_tokens // .usage.cache_creation_input_tokens // 0) +
    (.message.usage.output_tokens // .usage.output_tokens // 0)
  ' 2>/dev/null)" || true

  if [ -z "$total_tokens" ] || [ "$total_tokens" = "null" ] || [ "$total_tokens" = "0" ]; then
    exit 0
  fi

  # Compute percentage (integer arithmetic, round down)
  local pct
  pct=$(( total_tokens * 100 / BASELINE ))

  # Determine current tier: 0=below60, 60=at60, 75=at75
  local current_tier=0
  if [ "$pct" -ge 75 ]; then
    current_tier=75
  elif [ "$pct" -ge 60 ]; then
    current_tier=60
  fi

  # Read last announced tier
  mkdir -p "$STATE_DIR"
  local last_tier=0
  if [ -f "$TIER_FILE" ]; then
    last_tier="$(cat "$TIER_FILE" 2>/dev/null)" || last_tier=0
  fi

  # Emit only if tier increased
  if [ "$current_tier" -le "$last_tier" ]; then
    exit 0
  fi

  # Record new tier
  printf '%s' "$current_tier" > "$TIER_FILE"

  # Emit additionalContext JSON
  local message
  if [ "$current_tier" -eq 75 ]; then
    message="Context at ${pct}% of ${BASELINE} tokens — compact at next stage handoff."
  else
    message="Context at ${pct}% of ${BASELINE} tokens — write checkpoint at next handoff and plan compaction."
  fi

  printf '{"additionalContext":"%s"}\n' "$message"
}

main "$@" || exit 0
