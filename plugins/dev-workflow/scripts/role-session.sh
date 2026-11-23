#!/usr/bin/env bash
# Starts, lists, checks, messages, respawns and stops dev-workflow role sessions, each one Claude running in a detached tmux session.
set -uo pipefail

PROG="role-session.sh"
US=$'\x1f'
SUB=""

die() {
  local code="$1"
  shift
  echo "$PROG: $*" >&2
  exit "$code"
}

usage_die() {
  die 2 "${SUB:-usage}: $*"
}

# Why: tmux treats an argument that ends in a semicolon as a command separator, so the semicolon is escaped the way tmux itself unescapes it.
tmarg() {
  case "$1" in
    *\;) REPLY="${1%;}\\;" ;;
    *) REPLY="$1" ;;
  esac
}

config_read() {
  CONFIG_PATH="${ROLE_SESSION_CONFIG:-$HOME/.claude/dev-workflow/config.json}"
  CFG_PM="" CFG_OPT="false"
  [ -e "$CONFIG_PATH" ] || return 0
  local parsed=""
  if command -v jq > /dev/null 2>&1; then
    parsed="$(jq -r '((.role_sessions? | objects | .permission_mode | strings) // "") + "\u001f" + (if ((.role_sessions? | objects | .allow_bypass_permissions) // null) == true then "true" else "false" end)' "$CONFIG_PATH" 2> /dev/null)" || parsed=""
  elif command -v python3 > /dev/null 2>&1; then
    parsed="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
r = d.get("role_sessions") if isinstance(d, dict) else None
r = r if isinstance(r, dict) else {}
pm = r.get("permission_mode")
pm = pm if isinstance(pm, str) else ""
print(pm + "\x1f" + ("true" if r.get("allow_bypass_permissions") is True else "false"))
' "$CONFIG_PATH" 2> /dev/null)" || parsed=""
  fi
  [ -n "$parsed" ] || die 10 "$SUB: cannot read config $CONFIG_PATH"
  IFS="$US" read -r CFG_PM CFG_OPT <<< "$parsed"
}

compute_expected() {
  EXP_FLAG="" EXP_MODE="none"
  case "$CFG_PM" in
    bypassPermissions)
      if [ "$CFG_OPT" = "true" ]; then EXP_FLAG="bypassPermissions"; EXP_MODE="bypassPermissions"; fi
      ;;
    acceptEdits) EXP_FLAG="acceptEdits"; EXP_MODE="acceptEdits" ;;
    plan)
      EXP_MODE="plan"
      case "$1" in reviewer | tester) EXP_FLAG="plan" ;; esac
      ;;
  esac
}

role_of_agent() {
  local r="${1##*:}"
  case "$r" in spec-writer | developer | reviewer | tester) printf '%s' "$r" ;; esac
}

check_permission_mode() {
  CFG_STORE=""
  local role given
  role="$(role_of_agent "${OPT_agent:-}")"
  [ -n "$role" ] || return 0
  config_read
  compute_expected "$role"
  given="${OPT_permission_mode:-}"
  [ "$given" = "$EXP_FLAG" ] || die 10 "$SUB: permission mode '${given:-none}' does not match config '${EXP_FLAG:-none}' for role $role (config: $CONFIG_PATH)"
  case "$OPT_name" in
    *-"$role"*)
      local prefix sessions sname stored
      prefix="${OPT_name%%-"$role"*}"
      sessions="$(tmux list-sessions -F "#{session_name}" 2> /dev/null)" || sessions=""
      while IFS= read -r sname; do
        sname="${sname%%"$US"*}"
        [ -n "$sname" ] || continue
        case "$sname" in "$prefix"-*) ;; *) continue ;; esac
        stored="$(stored_opt "$sname" @dwf_config_mode)"
        if [ -n "$stored" ] && [ "$stored" != "$EXP_MODE" ]; then
          die 10 "$SUB: config permission mode '$EXP_MODE' differs from '$stored' recorded by session '$sname'"
        fi
      done <<< "$sessions"
      ;;
  esac
  CFG_STORE="$EXP_MODE"
}

parse_opts() {
  local allowed="$1" key
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --user-approved)
        case " $allowed " in *" user-approved "*) ;; *) usage_die "unknown option $1" ;; esac
        OPT_user_approved=1
        shift
        ;;
      --force)
        case " $allowed " in *" force "*) ;; *) usage_die "unknown option $1" ;; esac
        OPT_force=1
        shift
        ;;
      --*)
        key="${1#--}"
        case " $allowed " in *" $key "*) ;; *) usage_die "unknown option $1" ;; esac
        [ $# -ge 2 ] || usage_die "option $1 needs a value"
        [ -n "$2" ] || usage_die "option $1 must not be empty"
        printf -v "OPT_${key//-/_}" '%s' "$2"
        shift 2
        ;;
      *) usage_die "unexpected argument '$1'" ;;
    esac
  done
}

require_opts() {
  local key var
  for key in "$@"; do
    var="OPT_${key//-/_}"
    [ -n "${!var:-}" ] || usage_die "missing required option --$key"
  done
}

check_name() {
  local re='^[A-Za-z0-9][A-Za-z0-9_-]*$'
  [[ "$1" =~ $re ]] || usage_die "invalid session name '$1' (allowed: letters, digits, _ and -, starting with a letter or digit)"
}

check_effort() {
  case "$1" in
    low | medium | high | xhigh | max) ;;
    *) usage_die "invalid effort '$1' (allowed: low, medium, high, xhigh, max)" ;;
  esac
}

need_tmux() {
  command -v tmux > /dev/null 2>&1 || die 3 "$SUB: tmux is not on PATH"
}

need_claude() {
  command -v claude > /dev/null 2>&1 || die 4 "$SUB: claude is not on PATH"
}

session_exists() {
  tmux has-session -t "=$1" > /dev/null 2>&1
}

need_session() {
  session_exists "$1" || die 6 "$SUB: no tmux session named '$1'"
}

pane_dead() {
  local dead
  dead="$(tmux display-message -p -t "=$1:" '#{pane_dead}' 2> /dev/null)" || die 8 "$SUB: could not read the pane of '$1'"
  [ "$dead" = "1" ]
}

stored_opt() {
  tmux show-options -v -t "=$1:" "$2" 2> /dev/null
}

new_uuid() {
  local id=""
  if command -v uuidgen > /dev/null 2>&1; then
    id="$(uuidgen 2> /dev/null)"
  elif command -v python3 > /dev/null 2>&1; then
    id="$(python3 -c 'import uuid; print(uuid.uuid4())' 2> /dev/null)"
  fi
  [ -n "$id" ] || return 1
  printf '%s' "$id" | tr 'A-Z' 'a-z'
}

cmd_launch() {
  parse_opts "name cwd agent model settings prompt permission-mode effort" "$@"
  require_opts name cwd agent model settings prompt
  check_name "$OPT_name"
  local effort="${OPT_effort:-medium}"
  check_effort "$effort"
  [ -d "$OPT_cwd" ] || usage_die "--cwd '$OPT_cwd' is not a directory"
  local cwd session_id
  cwd="$(cd "$OPT_cwd" && pwd)"
  session_id="$(new_uuid)" || usage_die "no UUID source: install uuidgen or python3"
  need_tmux
  need_claude
  session_exists "$OPT_name" && die 5 "launch: a tmux session named '$OPT_name' already exists"
  check_permission_mode

  local argv=(claude) v
  tmarg "$OPT_name"; v="$REPLY"; argv+=(--name "$v")
  tmarg "$OPT_agent"; v="$REPLY"; argv+=(--agent "$v")
  tmarg "$OPT_model"; v="$REPLY"; argv+=(--model "$v")
  argv+=(--effort "$effort")
  tmarg "$OPT_settings"; v="$REPLY"; argv+=(--settings "$v")
  if [ -n "${OPT_permission_mode:-}" ]; then
    tmarg "$OPT_permission_mode"; v="$REPLY"; argv+=(--permission-mode "$v")
  fi
  argv+=(--session-id "$session_id" --)
  tmarg "$OPT_prompt"; v="$REPLY"; argv+=("$v")

  tmux new-session -d -s "$OPT_name" -c "$cwd" "${argv[@]}" > /dev/null 2>&1 \
    || die 8 "launch: tmux could not create session '$OPT_name'"

  store_launch_opts "$session_id" "$effort" "$cwd"
  echo "launched $OPT_name session-id=$session_id"
}

store_launch_opts() {
  local session_id="$1" effort="$2" cwd="$3" resumed="${4:-}" pair ok=1
  tmux set-option -w -t "=$OPT_name:" remain-on-exit on > /dev/null 2>&1 || ok=0
  for pair in \
    "@dwf_session_id=$session_id" \
    "@dwf_name=$OPT_name" \
    "@dwf_agent=${OPT_agent:-}" \
    "@dwf_model=${OPT_model:-}" \
    "@dwf_effort=$effort" \
    "@dwf_permission_mode=${OPT_permission_mode:-}" \
    "@dwf_config_mode=${CFG_STORE:-}" \
    "@dwf_settings=${OPT_settings:-}" \
    "@dwf_resumed=$resumed" \
    "@dwf_cwd=$cwd"; do
    tmux set-option -t "=$OPT_name:" "${pair%%=*}" "${pair#*=}" > /dev/null 2>&1 || ok=0
  done
  if [ "$ok" -ne 1 ]; then
    session_exists "$OPT_name" || die 8 "$SUB: the session '$OPT_name' exited immediately (check the session id, the arguments and --cwd)"
    tmux kill-session -t "=$OPT_name" > /dev/null 2>&1
    die 8 "$SUB: tmux could not store the launch options for '$OPT_name'"
  fi
}

cmd_resume() {
  parse_opts "name session-id cwd agent model settings permission-mode effort" "$@"
  require_opts name session-id cwd
  check_name "$OPT_name"
  local uuid_re='^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
  [[ "$OPT_session_id" =~ $uuid_re ]] || usage_die "invalid --session-id '$OPT_session_id' (expected a UUID)"
  local effort="${OPT_effort:-medium}"
  check_effort "$effort"
  [ -d "$OPT_cwd" ] || usage_die "--cwd '$OPT_cwd' is not a directory"
  local cwd session_id v
  cwd="$(cd "$OPT_cwd" && pwd)"
  session_id="$(printf '%s' "$OPT_session_id" | tr 'A-Z' 'a-z')"
  need_tmux
  need_claude
  session_exists "$OPT_name" && die 5 "resume: a tmux session named '$OPT_name' already exists"
  check_permission_mode

  local argv=(claude)
  tmarg "$OPT_name"; v="$REPLY"; argv+=(--name "$v")
  if [ -n "${OPT_agent:-}" ]; then tmarg "$OPT_agent"; v="$REPLY"; argv+=(--agent "$v"); fi
  if [ -n "${OPT_model:-}" ]; then tmarg "$OPT_model"; v="$REPLY"; argv+=(--model "$v"); fi
  argv+=(--effort "$effort")
  if [ -n "${OPT_settings:-}" ]; then tmarg "$OPT_settings"; v="$REPLY"; argv+=(--settings "$v"); fi
  if [ -n "${OPT_permission_mode:-}" ]; then tmarg "$OPT_permission_mode"; v="$REPLY"; argv+=(--permission-mode "$v"); fi
  argv+=(--resume "$session_id")

  tmux new-session -d -s "$OPT_name" -c "$cwd" "${argv[@]}" > /dev/null 2>&1 \
    || die 8 "resume: tmux could not create session '$OPT_name'"
  store_launch_opts "$session_id" "$effort" "$cwd" 1
  echo "launched $OPT_name session-id=$session_id"
}

agents_tsv() {
  local json="$1"
  if command -v jq > /dev/null 2>&1; then
    printf '%s' "$json" | jq -r '.[] | select((.kind // "interactive") == "interactive" or .kind == "background") | [(.pid // "-" | tostring), (.status // .state // "unknown"), (.name // "-"), (.cwd // "-"), (.sessionId // "-")] | join("\u001f")' 2> /dev/null
  elif command -v python3 > /dev/null 2>&1; then
    printf '%s' "$json" | python3 -c '
import json, sys
for e in json.load(sys.stdin):
    if e.get("kind") not in (None, "interactive", "background"):
        continue
    f = [str(e.get("pid") or "-"), e.get("status") or e.get("state") or "unknown", e.get("name") or "-", e.get("cwd") or "-", e.get("sessionId") or "-"]
    print("\x1f".join(f))
' 2> /dev/null
  else
    return 1
  fi
}

cmd_list() {
  parse_opts "" "$@"
  need_tmux
  need_claude
  local panes rc
  panes="$(tmux list-sessions -F "#{session_name}${US}#{@dwf_session_id}${US}#{pane_dead}${US}#{pane_pid}${US}#{pane_current_path}${US}#{@dwf_cwd}" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$panes" in
      *"no server running"* | *"error connecting"* | *"No such file"*) panes="" ;;
      *) die 8 "list: tmux list-sessions failed: $panes" ;;
    esac
  fi

  local agents="" agents_ok=0 json
  if json="$(claude agents --json --all 2> /dev/null)" && agents="$(agents_tsv "$json")"; then
    agents_ok=1
  fi

  local pids=" " ids=" "
  local name sid dead pid path dcwd owner state status apid astatus aname acwd asid
  while IFS="$US" read -r name sid dead pid path dcwd; do
    [ -n "$name" ] || continue
    if [ -n "$sid" ]; then owner="launcher"; else owner="other"; fi
    if [ "$dead" = "1" ]; then state="dead"; else state="alive"; fi
    status="unknown"
    if [ "$agents_ok" -eq 1 ]; then
      while IFS="$US" read -r apid astatus aname acwd asid; do
        [ -n "$apid$asid" ] || continue
        if [ "$apid" = "$pid" ] || { [ -n "$sid" ] && [ "$asid" = "$sid" ]; }; then
          status="$astatus"
          break
        fi
      done <<< "$agents"
      case "$status" in busy | idle) ;; *) status="unknown" ;; esac
    fi
    pids="$pids$pid "
    [ -n "$sid" ] && ids="$ids$sid "
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$owner" "$state" "$status" "${path:-${dcwd:--}}" "${sid:--}"
  done <<< "$panes"

  if [ "$agents_ok" -eq 1 ]; then
    while IFS="$US" read -r apid astatus aname acwd asid; do
      [ -n "$aname$asid" ] || continue
      if [ "$apid" != "-" ]; then
        case "$pids" in *" $apid "*) continue ;; esac
      fi
      if [ "$asid" != "-" ]; then
        case "$ids" in *" $asid "*) continue ;; esac
      fi
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$aname" "foreign" "-" "$astatus" "$acwd" "$asid"
    done <<< "$agents"
  fi
  return 0
}

cmd_status() {
  parse_opts "name" "$@"
  require_opts name
  check_name "$OPT_name"
  need_tmux
  need_session "$OPT_name"
  if pane_dead "$OPT_name"; then
    echo dead
    exit 9
  fi
  echo alive
}

cmd_send() {
  parse_opts "name reply-to message" "$@"
  require_opts name reply-to message
  check_name "$OPT_name"
  need_tmux
  need_session "$OPT_name"
  if pane_dead "$OPT_name"; then
    die 9 "send: the pane of '$OPT_name' has exited"
  fi
  local buffer="dwf-send-$$"
  printf '%s\nReply to: %s' "$OPT_message" "$OPT_reply_to" | tmux load-buffer -b "$buffer" - > /dev/null 2>&1 \
    || die 8 "send: tmux could not load the message buffer"
  tmux paste-buffer -p -r -d -b "$buffer" -t "=$OPT_name:" > /dev/null 2>&1 \
    || die 8 "send: tmux could not paste into '$OPT_name'"
  sleep "${ROLE_SESSION_ENTER_DELAY:-0.3}"
  tmux send-keys -t "=$OPT_name:" Enter > /dev/null 2>&1 \
    || die 8 "send: tmux could not press Enter in '$OPT_name'"
  echo "sent $OPT_name"
}

cmd_respawn() {
  parse_opts "name force" "$@"
  require_opts name
  check_name "$OPT_name"
  need_tmux
  need_claude
  session_exists "$OPT_name" || die 6 "respawn: no tmux session named '$OPT_name' (the tmux server or session is gone); start it again with: $PROG resume --name $OPT_name --session-id <sessionId from the checkpoint sessions map> --cwd <dir>"
  local session_id agent model effort permission settings v
  session_id="$(stored_opt "$OPT_name" @dwf_session_id)"
  [ -n "$session_id" ] || die 8 "respawn: '$OPT_name' has no stored launch options"
  if ! pane_dead "$OPT_name" && [ "${OPT_force:-0}" != "1" ]; then
    echo "alive, not respawned"
    exit 0
  fi
  agent="$(stored_opt "$OPT_name" @dwf_agent)"
  model="$(stored_opt "$OPT_name" @dwf_model)"
  effort="$(stored_opt "$OPT_name" @dwf_effort)"
  permission="$(stored_opt "$OPT_name" @dwf_permission_mode)"
  settings="$(stored_opt "$OPT_name" @dwf_settings)"
  if [ -z "$effort" ] || { [ "$(stored_opt "$OPT_name" @dwf_resumed)" != "1" ] && { [ -z "$agent" ] || [ -z "$model" ] || [ -z "$settings" ]; }; }; then
    die 8 "respawn: '$OPT_name' has incomplete stored launch options"
  fi
  local new_config_mode=""
  if [ "${OPT_force:-0}" = "1" ]; then
    local frole; frole="$(role_of_agent "$agent")"
    if [ -n "$frole" ]; then
      config_read
      compute_expected "$frole"
      permission="$EXP_FLAG"
      new_config_mode="$EXP_MODE"
    fi
  fi
  if [ -n "$new_config_mode" ]; then
    tmux set-option -t "=$OPT_name:" @dwf_permission_mode "$permission" > /dev/null 2>&1 \
      && tmux set-option -t "=$OPT_name:" @dwf_config_mode "$new_config_mode" > /dev/null 2>&1 \
      || die 8 "respawn: tmux could not store the launch options for '$OPT_name'"
  fi
  local argv=(claude)
  tmarg "$OPT_name"; v="$REPLY"; argv+=(--name "$v")
  if [ -n "$agent" ]; then tmarg "$agent"; v="$REPLY"; argv+=(--agent "$v"); fi
  if [ -n "$model" ]; then tmarg "$model"; v="$REPLY"; argv+=(--model "$v"); fi
  argv+=(--effort "$effort")
  if [ -n "$settings" ]; then tmarg "$settings"; v="$REPLY"; argv+=(--settings "$v"); fi
  if [ -n "$permission" ]; then
    tmarg "$permission"; v="$REPLY"; argv+=(--permission-mode "$v")
  fi
  argv+=(--resume "$session_id")
  local rcwd; rcwd="$(stored_opt "$OPT_name" @dwf_cwd)"
  local rargs=(-k -t "=$OPT_name:"); [ -n "$rcwd" ] && rargs+=(-c "$rcwd")
  tmux respawn-pane "${rargs[@]}" "${argv[@]}" > /dev/null 2>&1 \
    || die 8 "respawn: tmux could not respawn the pane of '$OPT_name'"
  echo "respawned $OPT_name session-id=$session_id"
}

cmd_stop() {
  parse_opts "name user-approved" "$@"
  require_opts name
  check_name "$OPT_name"
  need_tmux
  need_session "$OPT_name"
  [ "${OPT_user_approved:-0}" = "1" ] || die 7 "stop: refusing to stop '$OPT_name' without --user-approved (the user must give permission first)"
  tmux kill-session -t "=$OPT_name" > /dev/null 2>&1 || die 8 "stop: tmux could not kill session '$OPT_name'"
  echo "stopped $OPT_name"
}

main() {
  [ $# -ge 1 ] || usage_die "missing subcommand (launch, resume, list, status, send, respawn, stop)"
  SUB="$1"
  shift
  case "$SUB" in
    launch) cmd_launch "$@" ;;
    resume) cmd_resume "$@" ;;
    list) cmd_list "$@" ;;
    status) cmd_status "$@" ;;
    send) cmd_send "$@" ;;
    respawn) cmd_respawn "$@" ;;
    stop) cmd_stop "$@" ;;
    *) die 2 "unknown subcommand '$SUB'" ;;
  esac
}

main "$@"
