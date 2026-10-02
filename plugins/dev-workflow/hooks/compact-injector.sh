#!/usr/bin/env bash
# hooks/compact-injector.sh — Stop hook: consume sentinel and inject /compact via tmux
# Only acts when $TMUX is set and ~/.claude/dev-workflow/state/.compact-request exists.
# Spawns a detached injector so the Stop hook itself returns immediately.

# Role sessions manage their own context; never touch shared state.
[ -n "${DEV_WORKFLOW_ROLE:-}" ] && exit 0

STATE_DIR="$HOME/.claude/dev-workflow/state"
SENTINEL="$STATE_DIR/.compact-request"
FAILED_NOTE="$STATE_DIR/.compact-request.failed"
STALE_SECONDS=600   # 10 minutes

# ---- detached injector -------------------------------------------------------
# When called with "--inject <pane> <resume_cmd>", this script runs as a
# detached background process and performs the actual tmux injection.
if [ "${1:-}" = "--inject" ]; then
  PANE="$2"
  RESUME_CMD="${COMPACT_RESUME_CMD:-}"
  MAX_RETRIES=3
  attempt=0

  write_failed() {
    mkdir -p "$STATE_DIR"
    printf 'compact-injector failed: %s\n' "$1" > "$FAILED_NOTE"
  }

  if [ -z "$RESUME_CMD" ]; then
    write_failed "COMPACT_RESUME_CMD not set"
    exit 1
  fi

  sleep 2

  while [ "$attempt" -lt "$MAX_RETRIES" ]; do
    attempt=$(( attempt + 1 ))

    # Verify idle: pane content must end with a prompt-like pattern
    pane_content="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || {
      write_failed "capture-pane failed for pane $PANE"
      exit 1
    }
    if ! printf '%s' "$pane_content" | grep -qE '(\$|>|❯|%)[[:space:]]*$'; then
      sleep 2
      continue
    fi

    # Send /compact
    tmux send-keys -t "$PANE" "/compact" Enter 2>/dev/null || {
      write_failed "send-keys /compact failed"
      exit 1
    }

    # Poll for compaction completion (idle prompt returns, no "Compacting" visible)
    poll=0
    compaction_done=0
    while [ "$poll" -lt 60 ]; do
      sleep 2
      poll=$(( poll + 1 ))
      current="$(tmux capture-pane -p -t "$PANE" 2>/dev/null)" || break
      if printf '%s' "$current" | grep -qE '(\$|>|❯|%)[[:space:]]*$'; then
        if ! printf '%s' "$current" | grep -qi 'compacting'; then
          compaction_done=1
          break
        fi
      fi
    done

    if [ "$compaction_done" -eq 0 ]; then
      write_failed "timed out waiting for compaction to complete"
      exit 1
    fi

    # Send resume command
    tmux send-keys -t "$PANE" "$RESUME_CMD" Enter 2>/dev/null || {
      write_failed "send-keys resume command failed"
      exit 1
    }
    exit 0
  done

  write_failed "max retries ($MAX_RETRIES) exceeded without idle pane confirmation"
  exit 1
fi

# ---- main Stop hook logic ----------------------------------------------------
main() {
  # No sentinel → nothing to do
  if [ ! -f "$SENTINEL" ]; then
    exit 0
  fi

  # Stale sentinel check (mtime older than STALE_SECONDS)
  sentinel_mtime="$(stat -c '%Y' "$SENTINEL" 2>/dev/null)" || sentinel_mtime=0
  now="$(date +%s)"
  sentinel_age=$(( now - sentinel_mtime ))
  if [ "$sentinel_age" -gt "$STALE_SECONDS" ]; then
    rm "$SENTINEL"
    exit 0
  fi

  # Require tmux
  if [ -z "${TMUX:-}" ]; then
    exit 0
  fi

  # Determine current pane
  pane="$(tmux display-message -p '#S:#I.#P' 2>/dev/null)" || exit 0

  # Read resume command, then consume sentinel (delete it)
  resume_cmd="$(cat "$SENTINEL")"
  rm "$SENTINEL"

  if [ -z "$resume_cmd" ]; then
    exit 0
  fi

  # Spawn detached injector (this script itself, called with --inject).
  # Pass resume_cmd via environment to avoid word-splitting on spaces.
  COMPACT_RESUME_CMD="$resume_cmd" bash "$0" --inject "$pane" &
  disown
  exit 0
}

main "$@"
