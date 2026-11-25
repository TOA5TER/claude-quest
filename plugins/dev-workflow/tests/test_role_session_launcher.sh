#!/usr/bin/env bash
# Tests scripts/role-session.sh against stub tmux and claude executables, and the role-session docs that describe how it is used.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SCRIPT="$PLUGIN_ROOT/scripts/role-session.sh"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

[ -f "$SCRIPT" ] || { fail "scripts/role-session.sh missing"; exit 1; }
[ -x "$SCRIPT" ] || fail "scripts/role-session.sh is not executable"

mkdir -p "$REPO_ROOT/.scratch/tmp"
TMPD="$(mktemp -d "$REPO_ROOT/.scratch/tmp/role-session-test.XXXXXX")"
trap 'rm -rf "$TMPD"' EXIT

BIN="$TMPD/bin"
STATE="$TMPD/state"
WORK="$TMPD/work"
mkdir -p "$BIN" "$STATE" "$WORK"

US=$'\x1f'

cat > "$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
# Stub tmux: sessions live as directories under $STUB_STATE/s.
S="$STUB_STATE/s"
mkdir -p "$S"
printf '%q ' "$@" >> "$STUB_STATE/tmux.log"
printf '\n' >> "$STUB_STATE/tmux.log"
US=$'\x1f'
sub="$1"
shift
target=""
name_of() { local n="${1#=}"; printf '%s' "${n%:}"; }
case "$sub" in
  has-session)
    while [ $# -gt 0 ]; do [ "$1" = "-t" ] && target="$2"; shift; done
    [ -d "$S/$(name_of "$target")" ]
    ;;
  new-session)
    [ "${STUB_FAIL_NEW:-0}" = 1 ] && exit 1
    name="" cwd=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -d) shift ;;
        -s) name="$2"; shift 2 ;;
        -c) cwd="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    [ -d "$S/$name" ] && { echo "duplicate session: $name" >&2; exit 1; }
    mkdir -p "$S/$name/opts"
    printf '%s' "$cwd" > "$S/$name/cwd"
    printf '%s' "$((1000 + RANDOM))" > "$S/$name/pid"
    printf '0' > "$S/$name/dead"
    printf '%s\n' "$@" > "$S/$name/argv"
    ;;
  set-option)
    [ "${STUB_FAIL_SETOPT:-0}" = 1 ] && exit 1
    if [ "${STUB_VANISH:-0}" = 1 ]; then
      while [ $# -gt 0 ]; do [ "$1" = "-t" ] && target="$2"; shift; done
      rm -rf "$S/$(name_of "$target")"
      exit 1
    fi
    while [ $# -gt 0 ]; do
      case "$1" in
        -w) shift ;;
        -t) target="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    d="$S/$(name_of "$target")"
    [ -d "$d" ] || exit 1
    printf '%s' "$2" > "$d/opts/$1"
    ;;
  show-options)
    while [ $# -gt 0 ]; do
      case "$1" in
        -v) shift ;;
        -t) target="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    d="$S/$(name_of "$target")"
    [ -d "$d" ] || exit 1
    [ -f "$d/opts/$1" ] && cat "$d/opts/$1"
    exit 0
    ;;
  display-message)
    while [ $# -gt 0 ]; do
      case "$1" in
        -p) shift ;;
        -t) target="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    d="$S/$(name_of "$target")"
    [ -d "$d" ] || exit 1
    cat "$d/dead"
    echo
    ;;
  list-sessions)
    fmt=""
    while [ $# -gt 0 ]; do [ "$1" = "-F" ] && fmt="$2"; shift; done
    n=0
    for d in "$S"/*/; do
      [ -d "$d" ] || continue
      n=$((n + 1))
      nm="$(basename "$d")"
      if [ "$fmt" = '#{session_name}' ]; then echo "$nm"; continue; fi
      sid=""
      [ -f "$d/opts/@dwf_session_id" ] && sid="$(cat "$d/opts/@dwf_session_id")"
      printf '%s%s%s%s%s%s%s%s%s%s%s\n' "$nm" "$US" "$sid" "$US" "$(cat "$d/dead")" "$US" "$(cat "$d/pid")" "$US" "$(cat "$d/cwd")" "$US" "$(cat "$d/opts/@dwf_cwd" 2> /dev/null)"
    done
    [ "$n" -gt 0 ] || { echo "no server running on /tmp/stub" >&2; exit 1; }
    ;;
  load-buffer) cat > "$STUB_STATE/buffer" ;;
  paste-buffer | send-keys) ;;
  kill-session)
    while [ $# -gt 0 ]; do [ "$1" = "-t" ] && target="$2"; shift; done
    rm -rf "$S/$(name_of "$target")"
    ;;
  respawn-pane)
    while [ $# -gt 0 ]; do
      case "$1" in
        -k) shift ;;
        -c) shift 2 ;;
        -t) target="$2"; shift 2 ;;
        *) break ;;
      esac
    done
    d="$S/$(name_of "$target")"
    [ -d "$d" ] || exit 1
    printf '%s\n' "$@" > "$d/respawn-argv"
    printf '0' > "$d/dead"
    ;;
  *) exit 1 ;;
esac
STUB

cat > "$BIN/claude" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "agents" ]; then
  [ "${STUB_AGENTS_FAIL:-0}" = 1 ] && exit 1
  cat "$STUB_STATE/agents.json" 2> /dev/null || echo '[]'
  exit 0
fi
exit 0
STUB
chmod +x "$BIN/tmux" "$BIN/claude"

# Why: the no-tmux and no-claude cases need a PATH that still holds every tool the script itself uses.
TOOLS="$TMPD/tools"
mkdir -p "$TOOLS"
for tool in tr cat sleep jq python3 uuidgen dirname basename mkdir env bash; do
  found="$(command -v "$tool" 2> /dev/null)" || continue
  case "$found" in /*) ln -sf "$found" "$TOOLS/$tool" ;; esac
done
NOTMUX_PATH="$TMPD/path-notmux"
NOCLAUDE_PATH="$TMPD/path-noclaude"
mkdir -p "$NOTMUX_PATH" "$NOCLAUDE_PATH"
cp -P "$TOOLS"/* "$NOTMUX_PATH"/
cp -P "$TOOLS"/* "$NOCLAUDE_PATH"/
cp "$BIN/claude" "$NOTMUX_PATH/claude"
cp "$BIN/tmux" "$NOCLAUDE_PATH/tmux"

reset_state() {
  rm -rf "$STATE"
  mkdir -p "$STATE"
  : > "$STATE/tmux.log"
}

OUT="" ERR="" RC=0
CFG=""
write_cfg() {
  CFG="$TMPD/config.json"
  if [ -z "$1" ]; then
    printf '{}' > "$CFG"
  else
    printf '{"role_sessions":{"permission_mode":"%s"%s}}' "$1" "${2:+,\"allow_bypass_permissions\":$2}" > "$CFG"
  fi
}
rs() {
  local errfile="$TMPD/stderr"
  OUT="$(PATH="$BIN:$PATH" STUB_STATE="$STATE" ROLE_SESSION_ENTER_DELAY=0 ROLE_SESSION_EXIT_WAIT="${EXIT_WAIT:-0}" ROLE_SESSION_CONFIG="${CFG:-$TMPD/no-such-config.json}" "$SCRIPT" "$@" 2> "$errfile")"
  RC=$?
  ERR="$(cat "$errfile")"
}

rs_path() {
  local path="$1" errfile="$TMPD/stderr"
  shift
  OUT="$(PATH="$path" STUB_STATE="$STATE" ROLE_SESSION_ENTER_DELAY=0 ROLE_SESSION_CONFIG="${CFG:-$TMPD/no-such-config.json}" bash "$SCRIPT" "$@" 2> "$errfile")"
  RC=$?
  ERR="$(cat "$errfile")"
}

expect_rc() {
  local label="$1" want="$2"
  [ "$RC" = "$want" ] || fail "$label: exit $RC, wanted $want (stderr: $ERR)"
}

expect_err() {
  case "$ERR" in role-session.sh:*) ;; *) fail "$1: stderr does not start with 'role-session.sh:' (got: $ERR)" ;; esac
}

log_has() {
  grep -qF -- "$2" "$STATE/tmux.log" || fail "$1: tmux log lacks '$2'"
}

log_lacks() {
  if grep -qF -- "$2" "$STATE/tmux.log"; then fail "$1: tmux log has '$2'"; fi
}

LAUNCH=(launch --name sc-1-developer-api --cwd "$WORK" --agent dev-workflow:developer --model sonnet --settings '{"env":{"DEV_WORKFLOW_ROLE":"developer"}}' --prompt 'boot "now" it'"'"'s ok')

# T-1: launch passes each argument to claude as its own argv entry.
reset_state
rs "${LAUNCH[@]}"
expect_rc "T-1 launch" 0
case "$OUT" in "launched sc-1-developer-api session-id="*) ;; *) fail "T-1: unexpected launch output '$OUT'" ;; esac
sid="${OUT##*session-id=}"
re='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
[[ "$sid" =~ $re ]] || fail "T-1: session id '$sid' is not a lowercase UUID"
argv="$STATE/s/sc-1-developer-api/argv"
want=$'claude\n--name\nsc-1-developer-api\n--agent\ndev-workflow:developer\n--model\nsonnet\n--effort\nmedium\n--settings\n{"env":{"DEV_WORKFLOW_ROLE":"developer"}}\n--session-id\n'"$sid"$'\n--\nboot "now" it'"'"'s ok'
[ "$(cat "$argv")" = "$want" ] || fail "T-1: claude argv differs: $(cat "$argv")"
log_has "T-1" "new-session -d -s sc-1-developer-api -c $WORK"
[ "$(cat "$STATE/s/sc-1-developer-api/cwd")" = "$WORK" ] || fail "T-1: cwd not passed to tmux"
if grep -q -- '--permission-mode' "$argv"; then fail "T-1: --permission-mode present when omitted"; fi
[ "$(cat "$STATE/s/sc-1-developer-api/opts/@dwf_session_id")" = "$sid" ] || fail "T-1: session id not stored"
log_has "T-1 remain-on-exit" "remain-on-exit on"

reset_state
write_cfg bypassPermissions true
rs "${LAUNCH[@]}" --effort high --permission-mode bypassPermissions
CFG=""
expect_rc "T-1 effort" 0
argv="$STATE/s/sc-1-developer-api/argv"
grep -qx -- 'high' "$argv" || fail "T-1: --effort high not passed"
{ grep -qx -- '--permission-mode' "$argv" && grep -qx -- 'bypassPermissions' "$argv"; } || fail "T-1: --permission-mode not passed"

reset_state
rs launch --name sc-1-x --cwd "$WORK" --agent a --model m --settings '{}' --prompt 'ends with a semicolon;'
expect_rc "T-1 semicolon" 0
log_has "T-1 semicolon" "$(printf '%q' 'ends with a semicolon\;')"

# T-2: no tmux on PATH.
reset_state
for sub in "launch --name n --cwd $WORK --agent a --model m --settings {} --prompt p" "list" "status --name n" "send --name n --reply-to r --message m" "resume --name n --session-id 3f2b8a10-5c6d-4e7f-9a01-b2c3d4e5f607 --cwd $WORK" "respawn --name n" "suspend --name n" "stop --name n"; do
  rs_path "$NOTMUX_PATH" $sub
  expect_rc "T-2 $sub" 3
  expect_err "T-2 $sub"
  case "$ERR" in *tmux*) ;; *) fail "T-2 $sub: message does not name tmux: $ERR" ;; esac
done

# T-3: no claude on PATH.
reset_state
rs_path "$NOCLAUDE_PATH" launch --name n --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
expect_rc "T-3 launch" 4
expect_err "T-3 launch"
if [ -d "$STATE/s/n" ]; then fail "T-3: a session was created"; fi
rs_path "$NOCLAUDE_PATH" list
expect_rc "T-3 list" 4
rs_path "$NOCLAUDE_PATH" respawn --name n
expect_rc "T-3 respawn" 4

# T-4: exact-name collision exits 5; a prefix of an existing name launches.
reset_state
rs "${LAUNCH[@]}"
rs "${LAUNCH[@]}"
expect_rc "T-4 duplicate" 5
expect_err "T-4 duplicate"
reset_state
rs launch --name sc-1-developer-api-2 --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
rs "${LAUNCH[@]}"
expect_rc "T-4 prefix" 0
log_has "T-4 prefix" "has-session -t =sc-1-developer-api"

# T-5: usage errors exit 2.
reset_state
rs launch --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
expect_rc "T-5 missing --name" 2
expect_err "T-5 missing --name"
rs launch --name n --cwd "$WORK" --agent a --model m --settings '{}' --prompt ""
expect_rc "T-5 empty prompt" 2
for bad in "a.b" "a:b" "a b" "-a" ""; do
  rs launch --name "$bad" --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
  expect_rc "T-5 name '$bad'" 2
done
rs "${LAUNCH[@]}" --effort huge
expect_rc "T-5 effort" 2
rs launch --name n --cwd "$WORK/nope" --agent a --model m --settings '{}' --prompt p
expect_rc "T-5 cwd" 2
rs frobnicate
expect_rc "T-5 unknown subcommand" 2
expect_err "T-5 unknown subcommand"
rs
expect_rc "T-5 no subcommand" 2
rs status --name n --bogus x
expect_rc "T-5 unknown option" 2
if [ -d "$STATE/s/n" ]; then fail "T-5: a session was created"; fi

# DT-1: a tmux failure exits 8 and leaves nothing behind.
reset_state
STUB_FAIL_NEW=1 rs "${LAUNCH[@]}"
expect_rc "DT-1 new-session failure" 8
reset_state
STUB_FAIL_SETOPT=1 rs "${LAUNCH[@]}"
expect_rc "DT-1 set-option failure" 8
if [ -d "$STATE/s/sc-1-developer-api" ]; then fail "DT-1: session left behind after a failed option store"; fi

# T-6: list prints launcher, other and foreign rows.
reset_state
rs "${LAUNCH[@]}"
sid="${OUT##*session-id=}"
mkdir -p "$STATE/s/plain/opts"
printf '/home/plain' > "$STATE/s/plain/cwd"
printf '4242' > "$STATE/s/plain/pid"
printf '0' > "$STATE/s/plain/dead"
launcher_pid="$(cat "$STATE/s/sc-1-developer-api/pid")"
cat > "$STATE/agents.json" <<JSON
[
  {"pid": $launcher_pid, "cwd": "$WORK", "kind": "interactive", "sessionId": "$sid", "name": "sc-1-developer-api", "status": "busy"},
  {"pid": 4242, "cwd": "/home/plain", "kind": "interactive", "sessionId": "other-id", "name": "plain", "status": "idle"},
  {"pid": 777, "cwd": "/elsewhere", "kind": "interactive", "sessionId": "foreign-id", "name": "sc-9-reviewer-x", "status": "idle"},
  {"id": "ab12cd34", "cwd": "/bg", "kind": "background", "sessionId": "bg-id", "name": "sc-8-tester-y", "state": "done"}
]
JSON
rs list
expect_rc "T-6 list" 0
printf '%s\n' "$OUT" | grep -qxF "$(printf 'sc-1-developer-api\tlauncher\talive\tbusy\t%s\t%s' "$WORK" "$sid")" || fail "T-6: launcher row wrong: $OUT"
printf '%s\n' "$OUT" | grep -qxF "$(printf 'plain\tother\talive\tidle\t/home/plain\t-')" || fail "T-6: other row wrong: $OUT"
printf '%s\n' "$OUT" | grep -qxF "$(printf 'sc-9-reviewer-x\tforeign\t-\tidle\t/elsewhere\tforeign-id')" || fail "T-6: foreign row wrong: $OUT"
printf '%s\n' "$OUT" | grep -qxF "$(printf 'sc-8-tester-y\tforeign\t-\tdone\t/bg\tbg-id')" || fail "T-6: background foreign row wrong: $OUT"
[ "$(printf '%s\n' "$OUT" | wc -l | tr -d ' ')" = 4 ] || fail "T-6: unexpected row count: $OUT"
STUB_AGENTS_FAIL=1 rs list
expect_rc "T-6 agents failure" 0
printf '%s\n' "$OUT" | grep -qxF "$(printf 'sc-1-developer-api\tlauncher\talive\tunknown\t%s\t%s' "$WORK" "$sid")" || fail "T-6: status is not unknown when claude agents fails: $OUT"
if printf '%s\n' "$OUT" | grep -q 'foreign'; then fail "T-6: foreign rows listed although claude agents failed"; fi
reset_state
rs list
expect_rc "T-6 no sessions" 0
[ -z "$OUT" ] || fail "T-6: rows printed with no sessions: $OUT"

# T-7: status.
reset_state
rs "${LAUNCH[@]}"
rs status --name sc-1-developer-api
expect_rc "T-7 alive" 0
[ "$OUT" = "alive" ] || fail "T-7: alive output '$OUT'"
printf '1' > "$STATE/s/sc-1-developer-api/dead"
rs status --name sc-1-developer-api
expect_rc "T-7 dead" 9
[ "$OUT" = "dead" ] || fail "T-7: dead output '$OUT'"
rs status --name sc-1-developer
expect_rc "T-7 missing" 6
expect_err "T-7 missing"

# T-8: send is one bracketed paste ending in the Reply to line, then one Enter.
reset_state
rs "${LAUNCH[@]}"
: > "$STATE/tmux.log"
rs send --name sc-1-developer-api --reply-to 'sc-1-orchestrator [abc123]' --message $'DWF/1 orchestrator>developer ping sc-1 api PR#none round 1\nline two'
expect_rc "T-8 send" 0
[ "$(cat "$STATE/buffer")" = $'DWF/1 orchestrator>developer ping sc-1 api PR#none round 1\nline two\nReply to: sc-1-orchestrator [abc123]' ] || fail "T-8: buffer text wrong: $(cat "$STATE/buffer")"
[ "$(grep -c '^paste-buffer ' "$STATE/tmux.log")" = 1 ] || fail "T-8: expected one paste-buffer call"
grep '^paste-buffer ' "$STATE/tmux.log" | grep -q -- ' -p ' || fail "T-8: paste-buffer is not a bracketed paste (-p)"
[ "$(grep -c '^send-keys ' "$STATE/tmux.log")" = 1 ] || fail "T-8: expected exactly one send-keys call"
grep '^send-keys ' "$STATE/tmux.log" | grep -q 'Enter' || fail "T-8: send-keys did not press Enter"
calls="$(grep -oE '^(load-buffer|paste-buffer|send-keys)' "$STATE/tmux.log" | tr '\n' ' ')"
[ "$calls" = "load-buffer paste-buffer send-keys " ] || fail "T-8: call order was '$calls'"
rs send --name nobody --reply-to r --message m
expect_rc "T-8 missing session" 6
printf '1' > "$STATE/s/sc-1-developer-api/dead"
rs send --name sc-1-developer-api --reply-to r --message m
expect_rc "T-8 dead pane" 9
rs send --name sc-1-developer-api --message m
expect_rc "T-8 missing reply-to" 2

# T-9: respawn.
reset_state
write_cfg acceptEdits
rs "${LAUNCH[@]}" --permission-mode acceptEdits --effort high
CFG=""
sid="${OUT##*session-id=}"
rs respawn --name sc-1-developer-api
expect_rc "T-9 alive" 0
[ "$OUT" = "alive, not respawned" ] || fail "T-9: alive output '$OUT'"
log_lacks "T-9 alive" "respawn-pane"
printf '1' > "$STATE/s/sc-1-developer-api/dead"
rs respawn --name sc-1-developer-api
expect_rc "T-9 dead" 0
want=$'claude\n--name\nsc-1-developer-api\n--agent\ndev-workflow:developer\n--model\nsonnet\n--effort\nhigh\n--settings\n{"env":{"DEV_WORKFLOW_ROLE":"developer"}}\n--permission-mode\nacceptEdits\n--resume\n'"$sid"
[ "$(cat "$STATE/s/sc-1-developer-api/respawn-argv")" = "$want" ] || fail "T-9: respawn argv differs: $(cat "$STATE/s/sc-1-developer-api/respawn-argv")"
log_has "T-9 dead" "respawn-pane -k -t =sc-1-developer-api:"
rs respawn --name nobody
expect_rc "T-9 missing" 6
mkdir -p "$STATE/s/plain/opts"
printf '0' > "$STATE/s/plain/dead"
printf '9' > "$STATE/s/plain/pid"
printf '/x' > "$STATE/s/plain/cwd"
rs respawn --name plain
expect_rc "T-9 no stored options" 8
expect_err "T-9 no stored options"

# T-10: stop.
reset_state
rs "${LAUNCH[@]}"
rs launch --name sc-1-developer-api-2 --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
rs stop --name sc-1-developer-api
expect_rc "T-10 no flag" 7
expect_err "T-10 no flag"
log_lacks "T-10 no flag" "kill-session"
[ -d "$STATE/s/sc-1-developer-api" ] || fail "T-10: session removed without the flag"
rs stop --name sc-1-developer-api --user-approved
expect_rc "T-10 flag" 0
log_has "T-10 flag" "kill-session -t =sc-1-developer-api"
if [ -d "$STATE/s/sc-1-developer-api" ]; then fail "T-10: session still present"; fi
[ -d "$STATE/s/sc-1-developer-api-2" ] || fail "T-10: the prefix-sharing session was stopped too"
rs stop --name nobody --user-approved
expect_rc "T-10 missing" 6

# T-13: stop types /exit and Enter before it kills, and still refuses without the flag.
reset_state
rs "${LAUNCH[@]}"
: > "$STATE/tmux.log"
rs stop --name sc-1-developer-api
expect_rc "T-13 no flag" 7
log_lacks "T-13 no flag" "send-keys"
log_lacks "T-13 no flag" "kill-session"
[ -d "$STATE/s/sc-1-developer-api" ] || fail "T-13: session removed without the flag"
rs stop --name sc-1-developer-api --user-approved
expect_rc "T-13 flag" 0
[ "$OUT" = "stopped sc-1-developer-api" ] || fail "T-13: stop output '$OUT'"
log_has "T-13 flag" "send-keys -t =sc-1-developer-api: -l /exit"
exit_line="$(grep -nF -- '-l /exit' "$STATE/tmux.log" | head -1 | cut -d: -f1)"
enter_line="$(grep -nF -- 'Enter' "$STATE/tmux.log" | head -1 | cut -d: -f1)"
kill_line="$(grep -nF -- 'kill-session' "$STATE/tmux.log" | head -1 | cut -d: -f1)"
{ [ -n "$exit_line" ] && [ -n "$enter_line" ] && [ -n "$kill_line" ] && [ "$exit_line" -lt "$enter_line" ] && [ "$enter_line" -lt "$kill_line" ]; } || fail "T-13: stop did not type /exit, press Enter, then kill"

# T-12: suspend needs no --user-approved, exits the pane gracefully, then kills the session.
reset_state
rs "${LAUNCH[@]}"
rs launch --name sc-1-developer-api-2 --cwd "$WORK" --agent a --model m --settings '{}' --prompt p
: > "$STATE/tmux.log"
rs suspend --name sc-1-developer-api
expect_rc "T-12 live pane" 0
[ "$OUT" = "suspended sc-1-developer-api" ] || fail "T-12: suspend output '$OUT'"
log_has "T-12 live pane" "send-keys -t =sc-1-developer-api: -l /exit"
log_has "T-12 live pane" "kill-session -t =sc-1-developer-api"
exit_line="$(grep -nF -- '-l /exit' "$STATE/tmux.log" | head -1 | cut -d: -f1)"
kill_line="$(grep -nF -- 'kill-session' "$STATE/tmux.log" | head -1 | cut -d: -f1)"
[ "$exit_line" -lt "$kill_line" ] || fail "T-12: kill came before /exit"
[ -d "$STATE/s/sc-1-developer-api-2" ] || fail "T-12: the prefix-sharing session was suspended too"
printf '1' > "$STATE/s/sc-1-developer-api-2/dead"
: > "$STATE/tmux.log"
rs suspend --name sc-1-developer-api-2
expect_rc "T-12 dead pane" 0
log_lacks "T-12 dead pane" "send-keys"
log_has "T-12 dead pane" "kill-session -t =sc-1-developer-api-2"
rs suspend --name nobody
expect_rc "T-12 missing" 6
expect_err "T-12 missing"

# T-16: resume starts claude --resume in a new tmux session; respawn without a session points to it.
UUID1=3f2b8a10-5c6d-4e7f-9a01-b2c3d4e5f607
reset_state
write_cfg acceptEdits
rs resume --name sc-1-developer-api --session-id "$UUID1" --cwd "$WORK" --agent dev-workflow:developer --model sonnet --settings '{"a":1}' --permission-mode acceptEdits
CFG=""
expect_rc "T-16 resume" 0
[ "$OUT" = "launched sc-1-developer-api session-id=$UUID1" ] || fail "T-16: resume output '$OUT'"
want=$'claude\n--name\nsc-1-developer-api\n--agent\ndev-workflow:developer\n--model\nsonnet\n--effort\nmedium\n--settings\n{"a":1}\n--permission-mode\nacceptEdits\n--resume\n'"$UUID1"
[ "$(cat "$STATE/s/sc-1-developer-api/argv")" = "$want" ] || fail "T-16: resume argv differs: $(cat "$STATE/s/sc-1-developer-api/argv")"
if grep -q -- '--session-id' "$STATE/s/sc-1-developer-api/argv"; then fail "T-16: resume passed --session-id"; fi
[ "$(cat "$STATE/s/sc-1-developer-api/opts/@dwf_session_id")" = "$UUID1" ] || fail "T-16: session id not stored"
log_has "T-16 remain-on-exit" "remain-on-exit on"
rs resume --name sc-1-developer-api --session-id "$UUID1" --cwd "$WORK"
expect_rc "T-16 name taken" 5
expect_err "T-16 name taken"
reset_state
rs resume --name sc-1-bare --session-id "$UUID1" --cwd "$WORK"
expect_rc "T-16 bare" 0
if grep -qE -- '^--(agent|model|settings|permission-mode)$' "$STATE/s/sc-1-bare/argv"; then fail "T-16: omitted option was passed"; fi
rs resume --name sc-1-bad --session-id not-a-uuid --cwd "$WORK"
expect_rc "T-16 bad uuid" 2
expect_err "T-16 bad uuid"
rs resume --name sc-1-bad --cwd "$WORK"
expect_rc "T-16 missing id" 2
if [ -d "$STATE/s/sc-1-bad" ]; then fail "T-16: a session was created on bad arguments"; fi
reset_state
rs respawn --name sc-1-gone
expect_rc "T-16 respawn gone" 6
case "$ERR" in *"resume --name sc-1-gone --session-id"*) ;; *) fail "T-16: respawn message does not point to resume: $ERR" ;; esac
rs resume --name sc-1-r --session-id "$UUID1" --cwd "$WORK"
printf '1' > "$STATE/s/sc-1-r/dead"
rs respawn --name sc-1-r
expect_rc "T-16 respawn after resume" 0
grep -qx -- "$UUID1" "$STATE/s/sc-1-r/respawn-argv" || fail "T-16: respawn did not resume the stored id"

# T-18: resume details, the respawn guard, and a session that exits at once.
reset_state
rs resume --name sc-1-up --session-id "$(printf '%s' "$UUID1" | tr 'a-f' 'A-F')" --cwd "$WORK" --agent a --model m --settings '{}' --effort high --prompt-ignored x
expect_rc "T-18 unknown option" 2
rs resume --name sc-1-up --session-id "$(printf '%s' "$UUID1" | tr 'a-f' 'A-F')" --cwd "$WORK" --agent a --model m --settings 'ends;' --effort high
expect_rc "T-18 upper" 0
[ "$OUT" = "launched sc-1-up session-id=$UUID1" ] || fail "T-18: uuid not lowercased: $OUT"
grep -qx -- 'high' "$STATE/s/sc-1-up/argv" || fail "T-18: --effort not passed to resume"
log_has "T-18 semicolon" "$(printf '%q' 'ends\;')"
for o in agent model effort settings cwd resumed; do
  [ -f "$STATE/s/sc-1-up/opts/@dwf_$o" ] || fail "T-18: @dwf_$o not stored by resume"
done
rs resume --name sc-1-up2 --session-id "$UUID1"
expect_rc "T-18 missing cwd" 2
rs resume --session-id "$UUID1" --cwd "$WORK"
expect_rc "T-18 missing name" 2
rs resume --name sc-1-up2 --session-id "$UUID1" --cwd "$TMPD/nowhere"
expect_rc "T-18 bad cwd" 2
rs resume --name sc-1-up2 --session-id "$UUID1" --cwd "$WORK" --effort turbo
expect_rc "T-18 bad effort" 2
reset_state
rs "${LAUNCH[@]}"
rm -f "$STATE/s/sc-1-developer-api/opts/@dwf_agent"
printf '1' > "$STATE/s/sc-1-developer-api/dead"
rs respawn --name sc-1-developer-api
expect_rc "T-18 launch session missing agent" 8
reset_state
rs resume --name sc-1-bare --session-id "$UUID1" --cwd "$WORK"
printf '1' > "$STATE/s/sc-1-bare/dead"
rs respawn --name sc-1-bare
expect_rc "T-18 resumed session without agent" 0
reset_state
STUB_VANISH=1 rs resume --name sc-1-gone2 --session-id "$UUID1" --cwd "$WORK"
expect_rc "T-18 exits at once" 8
case "$ERR" in *"exited immediately"*) ;; *) fail "T-18: exit-at-once message missing: $ERR" ;; esac

# T-17: every doc that describes recovery names the resume path.
for f in "$PLUGIN_ROOT/README.md" "$PLUGIN_ROOT/CLAUDE.md" "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" "$PLUGIN_ROOT/skills/shared/role-sessions.md" "$PLUGIN_ROOT/skills/shared/context-compaction.md"; do
  grep -qE 'resume (--name|--session-id)' "$f" || fail "T-17: $f does not name resume --session-id"
done
for f in "$PLUGIN_ROOT/README.md" "$PLUGIN_ROOT/CLAUDE.md" "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" "$PLUGIN_ROOT/skills/shared/role-sessions.md"; do
  grep -qF 'context-recovery note' "$f" || fail "T-17: $f lacks the context-recovery note"
done
grep -qF 'sessionId' "$PLUGIN_ROOT/skills/shared/role-sessions.md" || fail "T-17: role-sessions.md lacks sessionId"
grep -qF 'Lost-server case' "$PLUGIN_ROOT/skills/shared/role-sessions.md" || fail "T-17: role-sessions.md lacks the Lost-server case"
grep -qE '^ *\| 2 \| No counting entry \|.*Lost-server case' "$PLUGIN_ROOT/skills/shared/role-sessions.md" || fail "T-17: Target row 2 does not route to the Lost-server case"

# T-13: docs name the prerequisite and the attach command.
for f in "$PLUGIN_ROOT/README.md" "$PLUGIN_ROOT/CLAUDE.md"; do
  grep -qF 'tmux attach -t' "$f" || fail "T-13: $f lacks 'tmux attach -t'"
  grep -qiE 'tmux' "$f" || fail "T-13: $f lacks the tmux prerequisite"
done
grep -qE '^- `tmux` installed' "$PLUGIN_ROOT/README.md" || fail "T-13: README prerequisites lack the tmux line"

# T-14: the Send address section carries every address row and each agent carries the Reply to rule.
RSD="$PLUGIN_ROOT/skills/shared/role-sessions.md"
send_sec="$(awk '/^## Send address/{f=1;next} /^## /{f=0} f' "$RSD")"
[ -n "$send_sec" ] || fail "T-14: Send address section not found"
for phrase in "Reply to:" "Remote Control" "never a send target" "role-session.sh send"; do
  case "$send_sec" in *"$phrase"*) ;; *) fail "T-14: Send address section lacks '$phrase'" ;; esac
done
for a in spec-writer developer reviewer tester; do
  grep -qF 'ending in `Reply to: <address>`' "$PLUGIN_ROOT/agents/$a.md" || fail "T-14: agents/$a.md lacks the Reply to rule"
done

# T-19: the Launch contract says the orchestrator launches every session itself and names the only exceptions.
launch_sec="$(awk '/^## Launch contract/{f=1;next} /^## /{f=0} f' "$RSD")"
[ -n "$launch_sec" ] || fail "T-19: Launch contract section not found"
for phrase in "launches every role session itself" "in the current session" "remembered" "another session" "failed preflight check"; do
  case "$launch_sec" in *"$phrase"*) ;; *) fail "T-19: Launch contract lacks '$phrase'" ;; esac
done

# T-20: the watcher ladder carries a Monitor command that prints only on change or heartbeat, and the command parses.
ladder="$(awk '/^\*\*The watcher ladder\.\*\*/{f=1} /^\*\*Hook reminder\.\*\*/{f=0} f' "$RSD")"
[ -n "$ladder" ] || fail "T-20: watcher ladder not found"
for phrase in "every 30 seconds" "every five minutes" "1800000" "re-arm"; do
  case "$ladder" in *"$phrase"*) ;; *) fail "T-20: watcher ladder lacks '$phrase'" ;; esac
done
watcher_cmd="$(printf '%s\n' "$ladder" | awk '/^ *```bash$/{if(!d){f=1;next}} /^ *```$/{if(f){f=0;d=1}} f' | sed -E 's/^ {0,8}//')"
[ -n "$watcher_cmd" ] || fail "T-20: watcher ladder has no fenced bash block"
printf '%s\n' "$watcher_cmd" | bash -n 2>/dev/null || fail "T-20: the watcher command does not pass bash -n"

# T-21: the Communication contract has the R9 watcher-stop rule, and every rule range reads R5 to R9.
grep -q '^| R9 Watcher stop | Orchestrator |' "$RSD" || fail "T-21: role-sessions.md lacks the R9 Watcher stop row"
r7_row="$(grep '^| R7 ' "$RSD")"
case "$r7_row" in *"The watcher is stopped when the terminal reply arrives"*) fail "T-21: R7 still carries the watcher-stop sentence" ;; esac
grep -q "R5 to R8" "$RSD" "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" && fail "T-21: a stale 'R5 to R8' remains"
grep '^- \*\*Hand-off discipline' "$PLUGIN_ROOT/skills/full-cycle/SKILL.md" | grep -q "R9" || fail "T-21: full-cycle Hand-off discipline lacks R9"

# T-22: the Monitor exits 0 with an all terminal line exactly when every named session is terminal.
mon_rs="$TMPD/mon-rs.sh"
mon_state="$TMPD/mon-state"
cat > "$mon_rs" <<'MONSTUB'
#!/usr/bin/env bash
cat "$MON_STATE"
MONSTUB
chmod +x "$mon_rs"
mon_run() {
  local sent="$1" out="$2"
  printf '%s\n' "$watcher_cmd" | sed -e "s|^RS=.*|RS=$mon_rs|" -e 's|^NAMES=.*|NAMES="mon-a"|' -e "s|^SENT=.*|SENT=$sent|" > "$TMPD/mon-cmd.sh"
  MON_STATE="$mon_state" bash "$TMPD/mon-cmd.sh" > "$out" 2>&1 &
  MON_PID=$!
}
mon_wait_exit() {
  local i
  for i in $(seq 1 50); do kill -0 "$MON_PID" 2>/dev/null || return 0; sleep 0.1; done
  return 1
}
printf 'mon-a\towner\talive\tidle\tpath\tsid\n' > "$mon_state"
mon_run "$(( $(date +%s) - 200 ))" "$TMPD/mon-idle.out"
mon_wait_exit || { kill "$MON_PID" 2>/dev/null; fail "T-22: idle row at 200 seconds did not exit"; }
grep -q '^all terminal:' "$TMPD/mon-idle.out" || fail "T-22: idle row printed no all terminal line"
: > "$mon_state"
mon_run "$(date +%s)" "$TMPD/mon-gone.out"
mon_wait_exit || { kill "$MON_PID" 2>/dev/null; fail "T-22: missing row did not exit"; }
grep -q '^all terminal:' "$TMPD/mon-gone.out" || fail "T-22: missing row printed no all terminal line"
printf 'mon-a\towner\tdead\tidle\tpath\tsid\n' > "$mon_state"
mon_run "$(date +%s)" "$TMPD/mon-dead.out"
mon_wait_exit || { kill "$MON_PID" 2>/dev/null; fail "T-22: dead row did not exit"; }
grep -q '^all terminal:' "$TMPD/mon-dead.out" || fail "T-22: dead row printed no all terminal line"
printf 'mon-a\towner\talive\tidle\tpath\tsid\n' > "$mon_state"
mon_run "$(date +%s)" "$TMPD/mon-early-idle.out"
sleep 3
kill -0 "$MON_PID" 2>/dev/null || fail "T-22: idle row before 120 seconds with no busy seen exited early"
grep -q '^all terminal:' "$TMPD/mon-early-idle.out" && fail "T-22: early idle row printed an all terminal line"
kill "$MON_PID" 2>/dev/null
wait "$MON_PID" 2>/dev/null
printf 'mon-a\towner\talive\tbusy\tpath\tsid\n' > "$mon_state"
mon_run "$(date +%s)" "$TMPD/mon-busy.out"
sleep 3
kill -0 "$MON_PID" 2>/dev/null || fail "T-22: busy row at send time exited early"
grep -q '^all terminal:' "$TMPD/mon-busy.out" && fail "T-22: busy row printed an all terminal line"
kill "$MON_PID" 2>/dev/null
wait "$MON_PID" 2>/dev/null

# PM-1: every DT-1 row yields exactly the config's flag for each role, and every other flag exits 10 with no session created.
CFG=""
pm_launch() {
  local role="$1" flag="$2" name="${3:-sc-9-$1-api}"
  local a=(launch --name "$name" --cwd "$WORK" --agent "dev-workflow:$role" --model m --settings '{}' --prompt p)
  [ -n "$flag" ] && a+=(--permission-mode "$flag")
  rs "${a[@]}"
}
pm_row() {
  local label="$1" cfgmode="$2" optin="$3" role="$4" exp="$5" wrong name="sc-9-$4-api"
  reset_state
  write_cfg "$cfgmode" "$optin"
  pm_launch "$role" "$exp"
  expect_rc "PM-1 $label $role ok" 0
  if [ -n "$exp" ]; then
    grep -qx -- "$exp" "$STATE/s/$name/argv" || fail "PM-1 $label $role: flag $exp missing from argv"
    [ "$(cat "$STATE/s/$name/opts/@dwf_permission_mode")" = "$exp" ] || fail "PM-1 $label $role: stored permission mode differs"
  else
    grep -q -- '--permission-mode' "$STATE/s/$name/argv" && fail "PM-1 $label $role: flag present, expected none"
  fi
  for wrong in bypassPermissions acceptEdits plan ""; do
    [ "$wrong" = "$exp" ] && continue
    reset_state
    pm_launch "$role" "$wrong"
    expect_rc "PM-1 $label $role wrong '$wrong'" 10
    expect_err "PM-1 $label $role wrong '$wrong'"
    [ -d "$STATE/s/$name" ] && fail "PM-1 $label $role wrong '$wrong': session created"
  done
}
for role in spec-writer developer reviewer tester; do
  pm_row "missing-pm" "" "" "$role" ""
  pm_row "bypass-true" bypassPermissions true "$role" bypassPermissions
  for optin in false '"true"' 1 ""; do pm_row "bypass-optin-$optin" bypassPermissions "$optin" "$role" ""; done
  pm_row "acceptEdits" acceptEdits "" "$role" acceptEdits
  pm_row "default" default "" "$role" ""
  pm_row "auto" auto "" "$role" ""
done
for role in reviewer tester; do pm_row "plan" plan "" "$role" plan; done
for role in spec-writer developer; do pm_row "plan" plan "" "$role" ""; done

reset_state
rm -f "$TMPD/no-such-config.json"
CFG=""
pm_launch developer ""
expect_rc "PM-1 missing config file" 0
[ "$(cat "$STATE/s/sc-9-developer-api/opts/@dwf_config_mode")" = "none" ] || fail "PM-1: missing config did not store config mode none"

# PM-2: a reviewer launched with plan under a bypass config is refused.
reset_state
write_cfg bypassPermissions true
pm_launch developer bypassPermissions
expect_rc "PM-2 developer" 0
pm_launch reviewer plan
expect_rc "PM-2 reviewer plan" 10
case "$ERR" in *"permission mode 'plan' does not match config 'bypassPermissions' for role reviewer"*) ;; *) fail "PM-2: unexpected message: $ERR" ;; esac
[ -d "$STATE/s/sc-9-reviewer-api" ] && fail "PM-2: reviewer session created"
pm_launch reviewer ""
expect_rc "PM-2 reviewer none" 10
pm_launch reviewer bypassPermissions
expect_rc "PM-2 reviewer bypass" 0
grep -qx -- 'bypassPermissions' "$STATE/s/sc-9-reviewer-api/argv" || fail "PM-2: bypass flag missing from reviewer argv"

# PM-3: a launch that disagrees with an earlier launch of the same story is refused.
reset_state
write_cfg acceptEdits
pm_launch developer acceptEdits
expect_rc "PM-3 first" 0
write_cfg bypassPermissions true
pm_launch reviewer bypassPermissions
expect_rc "PM-3 prior launch" 10
case "$ERR" in *"sc-9-developer-api"*) ;; *) fail "PM-3: message does not name the prior session: $ERR" ;; esac
pm_launch reviewer bypassPermissions sc-8-reviewer-api
expect_rc "PM-3 other story" 0
pm_launch reviewer bypassPermissions sc-90-reviewer-api
expect_rc "PM-3 shared prefix" 0

# PM-4: an unreadable config is refused.
reset_state
CFG="$TMPD/config.json"
printf '{oops' > "$CFG"
pm_launch developer ""
expect_rc "PM-4 invalid json" 10
case "$ERR" in *"launch: cannot read config $CFG"*) ;; *) fail "PM-4: unexpected message: $ERR" ;; esac
[ -d "$STATE/s/sc-9-developer-api" ] && fail "PM-4: session created"

# PM-5: resume applies the same check; a resume without --agent skips it.
reset_state
write_cfg bypassPermissions true
rs resume --name sc-9-reviewer-api --session-id "$UUID1" --cwd "$WORK" --agent dev-workflow:reviewer --permission-mode plan
expect_rc "PM-5 resume mismatch" 10
case "$ERR" in "role-session.sh: resume: "*) ;; *) fail "PM-5: message lacks the resume: prefix: $ERR" ;; esac
rs resume --name sc-9-reviewer-api --session-id "$UUID1" --cwd "$WORK" --agent dev-workflow:reviewer --permission-mode bypassPermissions
expect_rc "PM-5 resume match" 0
[ "$(cat "$STATE/s/sc-9-reviewer-api/opts/@dwf_config_mode")" = "bypassPermissions" ] || fail "PM-5: config mode not stored on resume"

# PM-6: respawn --force restarts a live pane with the flag recomputed from the config.
reset_state
write_cfg plan
pm_launch reviewer plan
expect_rc "PM-6 launch" 0
write_cfg bypassPermissions true
: > "$STATE/tmux.log"
rs respawn --name sc-9-reviewer-api
expect_rc "PM-6 no force" 0
[ "$OUT" = "alive, not respawned" ] || fail "PM-6: no-force output '$OUT'"
log_lacks "PM-6 no force" "respawn-pane"
rs respawn --name sc-9-reviewer-api --force
expect_rc "PM-6 force" 0
case "$OUT" in "respawned sc-9-reviewer-api session-id="*) ;; *) fail "PM-6: force output '$OUT'" ;; esac
log_has "PM-6" "respawn-pane -k"
grep -qx -- 'bypassPermissions' "$STATE/s/sc-9-reviewer-api/respawn-argv" || fail "PM-6: recomputed flag missing"
grep -qx -- 'plan' "$STATE/s/sc-9-reviewer-api/respawn-argv" && fail "PM-6: stale flag still passed"
[ "$(cat "$STATE/s/sc-9-reviewer-api/opts/@dwf_config_mode")" = "bypassPermissions" ] || fail "PM-6: config mode not updated"
[ "$(cat "$STATE/s/sc-9-reviewer-api/opts/@dwf_permission_mode")" = "bypassPermissions" ] || fail "PM-6: permission mode not updated"
printf '{oops' > "$CFG"
: > "$STATE/tmux.log"
rs respawn --name sc-9-reviewer-api --force
expect_rc "PM-6 invalid json" 10
log_lacks "PM-6 invalid json" "respawn-pane"
CFG=""

if [ "$FAILURES" -gt 0 ]; then
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
echo "PASS: role-session launcher behaves as specified"
