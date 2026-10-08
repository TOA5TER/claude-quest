# attention-hub

A self-hosted, zero-dependency dashboard server *and* a full set of Claude Code hooks that track which of your coding-agent sessions is waiting on you. Installing this plugin alone gives a Claude Code session everything it needs to report to the dashboard — no other plugin required. Any other agent or hook system can also report to the hub over plain HTTP, since the wire protocol is fully documented below.

## What it is

Many concurrent agent sessions (local, docker, remote servers) make per-machine notifications spammy and hard to track. The attention hub is a small server every session's reporting client posts state events to; its web dashboard shows each session as a color-coded card, and gathers the sessions of one story into a group with one row per session (see [Story groups](#story-groups)):

- **Red** — `waiting` (blocked on a permission/question prompt) or `needs_input` (finished its turn by asking you something)
- **Yellow** — `done` (task complete, awaiting your review)
- **Green** — `working` (busy)

Each ungrouped card's title is the session name if one was reported, falling back to the session ID (a row inside a group shows its role and repo label instead) — and, when a recent dev-workflow checkpoint has the session's own repo at a non-terminal stage (checkpoints older than about a week are ignored), a second line showing that checkpoint's stage(s), one `repo:stage` entry per line for a multi-repo story. Cards show time in state, the latest message snippet, and last-update age, refresh every 3 seconds, and have a per-card dismiss control for crashed/abandoned sessions. Sessions silent for over 24 hours are pruned automatically.

### Story groups

Grouping is always on; there is no setting. A session whose name is a story id followed by a role (and optionally a repo) joins that story's group. For example, `sc-1000-orchestrator`, `sc-1000-developer` and `sc-1000-reviewer` form one group headed `sc-1000: <story title>`, and `sc-1234-developer-api` joins group `sc-1234`.

- **Name rule** — a session groups when its whole name is a letters-only word, a hyphen, one or more digits, a hyphen, then a non-empty remainder. Letters and digits are plain ASCII (`a`–`z`, `A`–`Z`, `0`–`9`). The story id is the part before the second hyphen and is matched exactly as typed, so `SC-1000-x` and `sc-1000-x` are separate groups. A name with a trailing newline does not match.
- **Names that never group** — an empty name, plain words, a bare id with no remainder (`sc-1000`, `sc-1000-`), underscores (`sc_1-x`), multi-word prefixes (`my-feature-12-x`), and ids from trackers that are numeric, `#`-style, or alphanumeric project keys. These sessions are ordinary cards that sit alongside the groups in the same list.
- **Rows** — every session in a group is its own row, labeled by its role (the remainder's first hyphen-separated word), plus ` · ` and the repo when there is more after it (`developer · api`). A remainder that starts with a hyphen (`sc-1000--x`) still groups and is labeled with the remainder verbatim. Each row keeps its own state color, detail panel, dismiss and force-status controls.
- **Duplicate names** — two sessions reporting the same name (for example a restarted session while the old record awaits pruning) are two rows with the same label, and both count toward the group color. A stale waiting duplicate keeps the group red until you dismiss it or it is pruned.
- **Renames** — membership is worked out from each session's current name on every listing and is never stored. A rename takes effect on the session's next reported event (any hook event), not on the dashboard's own refresh. A session can be renamed into a group, out of it, or into another one; a collision suffix the host appends (such as `-2`) still groups and becomes part of the label.
- **Group color** — a group is **red** only when every member is `waiting` or `needs_input`; otherwise it is **green**, including when every member is `done`. One agent working while the others wait is the normal case and does not mean the story is waiting on you. The header also shows how many members are waiting (`2 of 3 waiting`). Rows and ungrouped cards keep the red/yellow/green colors above.
- **Ordering** — groups, rows and ungrouped cards are ordered alphabetically and never by status, so nothing moves when a state changes. Comparison ignores case and compares runs of digits by value (`sc-999` before `sc-1000`, `api2` before `api10`). Inside a group, rows sort by role, then repo (an empty role first), so they read `developer`, `orchestrator`, `reviewer`, `tester`. At the top level, groups sort by story id and ungrouped cards by their name (or session ID when unnamed), interleaved in one list; a group comes before a card with an equal key.
- **Display** — headers, titles and labels are always shown as plain text, never as markup. Group headers have no dismiss or force-status controls; those stay on each row.
- **Group title** — the header reads `<story id>: <story title>`, or just the story id when no title is known. The title comes from the optional `story_title` the reporting client reads from the story's dev-workflow checkpoint. When members carry different titles, the most recently updated member's title is shown.

Known limitations:

- **Orchestrator naming** — in a standalone dev-workflow pipeline run the orchestrator is your own main session, which is not named automatically. It joins its group only once you name it `<story id>-orchestrator` (for example with `/rename sc-1000-orchestrator`); until then it is an ungrouped card, and the group can turn red while the orchestrator is working.
- **Title gaps** — only the `writing-specs`, `developing`, `reviewing-prs` and `testing-prs` stages record the title in the checkpoint. A rework-only story, or a checkpoint created some other way, shows just the story id until one of those stages runs.

### Expanded card view

Clicking a card or group row expands it; any number may be open at once, and open cards stay open across the 3-second refresh. The detail panel shows:

- **Host** — with a `container` badge when the session runs inside a container
- **Full session ID** and the **full last message**
- **Last update** age
- **Active work** — each tracked subagent entry with its kind, label, status, and time in state
- **Status history** — the last 20 state transitions (most recent first), each with the time it was entered, how long it lasted, and a `manual` badge when the change came from a manual override rather than a reported event

### Forcing a status

If the hub falls out of sync with reality, the **force status** buttons in the expanded panel set the state by hand — the current state's button is disabled. The override applies immediately and shows up in the history as `manual`, but it is not pinned: the next genuine reported event for the session overwrites it, so the hub always self-heals toward reality.

The buttons call a small API you can also script against:

```bash
curl -X POST http://localhost:8765/api/sessions/<session-id>/state \
  -H 'Content-Type: application/json' -d '{"state": "working"}'
```

Responses: `200` with the updated record, `400` for a missing/invalid state or malformed body, `404` for an unknown session — forcing never creates a session.

## Reporting hooks

This plugin ships seven Claude Code hooks that report session state to the dashboard automatically once installed — no configuration needed, and no other plugin required:

- **Notification hook**: Reports `waiting` to the hub for actionable notification types (permission prompts, idle prompts, elicitation dialogs) and sets the waiting marker
- **Stop hook**: Computes and reports `needs_input` (AskUserQuestion was used), `working` (a background subagent is still active), or `done` — ported from the same decision logic the notifications plugin used before this split
- **PreToolUse hook**: Pure observer — marks the session as having active work when an `Agent` dispatch (a subagent call — see Subagent Tracking below) or a backgrounded `Bash` call is seen; every other tool is a no-op. Always exits `0` and never emits a permission-decision payload, so it can never block a tool call
- **PostToolUse hook**: Reports `working` to the hub when a tool completes after the session was flagged `waiting` (a tool can only complete once a pending permission/question was answered). Gated by a per-session marker file, so on normal tool calls (no marker) it exits instantly with zero network activity
- **UserPromptSubmit hook**: Reports `working` to the hub — answering a session automatically clears its needs-attention state
- **SessionEnd hook**: Removes the session from the hub (and cleans up its waiting marker and any active-subagent markers)
- **SubagentStop hook**: Flips one active task-kind marker for the session to `completed` (see Subagent Tracking and Retention below) — it stays visible as recently-finished work for a short retention window rather than disappearing immediately

Transient hook state (waiting markers, active-subagent markers) lives under `~/.claude/attention-hub/`.

Installing this plugin alone gives a Claude Code session the complete waiting/needs_input/done/working lifecycle on the dashboard — no other plugin required. If the [`notifications`](../notifications) plugin is also installed, its own hooks handle macOS/Slack notifications independently, with zero shared code between the two plugins. Any other agent that wants to report state can also POST directly to the [HTTP API](#http-api) documented below.

## Subagent Tracking and Retention

When a session reports an active subagent (via the PreToolUse hook), the hub tracks markers for each dispatched Agent (subagent) call and each backgrounded Bash command. Each marker carries:

- **Kind**: one of `task` (Agent/subagent dispatch — the marker kind is named `task` for historical reasons, but detection keys on the real `tool_name` value `Agent`) or `bash` (backgrounded Bash)
- **Label**: friendly identifier for the work (e.g., agent name, script name)
- **Status**: `active` or `completed`
- **Duration**: elapsed time since the subagent started, computed from its start time (and, if completed, its finish time) — live while active, frozen once completed

The hub prunes markers based on three retention policies:

- **Active task-kind markers** (ACTIVE_SUBAGENT_TTL_SECONDS): pruned after 2 hours of inactivity, so long-running dispatches are tracked across your session
- **Active background-kind markers** (bash; BACKGROUND_SUBAGENT_TTL_SECONDS): pruned after 15 minutes, since these tend to finish faster
- **Completed task-kind markers** (COMPLETED_RETENTION_SECONDS): stay visible in the expanded card view for 5 minutes before being pruned, so you can see recent work that finished

Tracking Workflow or Monitor dispatches was investigated and found not achievable via this hook — Claude Code's PreToolUse event only fires for a fixed set of tools (`Bash`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `Agent`, `WebFetch`, `WebSearch`, `AskUserQuestion`, `ExitPlanMode`, and MCP tools), which does not include Workflow or Monitor. This is tracked as a follow-up (a different mechanism would be needed), not shipped as non-functional code.

This tracking appears in the expanded card view's **Active work** section, showing each entry's kind, label, status, and elapsed time.

## Starting the hub

The hub is a manual-start, zero-dependency Python script (run it in tmux/screen; it does not survive reboot):

```bash
uv run hub/attention_hub.py
```

The server itself has no environment variables — it is configured entirely via CLI flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--port` | `8765` | Port to listen on |
| `--bind` | `0.0.0.0` | Bind address (`127.0.0.1` for localhost only) |
| `--state-file` | `~/.claude/attention_hub_state.json` | JSON persistence file (sessions survive hub restarts) |
| `--prune-hours` | `24` | Drop sessions silent for this many hours |

Open `http://localhost:8765/` for the dashboard.

## HTTP API

Any reporting client can integrate with the hub over plain HTTP.

### `POST /api/events`

Reports (creates or updates) a session's state. Body is a JSON object:

| Field | Required | Meaning |
|-------|----------|---------|
| `session_id` | yes | Stable identifier for the session; keys the dashboard row |
| `state` | yes | One of `waiting`, `needs_input`, `done`, `working` |
| `project` | no | Project/repo name; stored, returned by the API, and shown in the card's expanded detail panel — not on the collapsed row |
| `host` | no | Machine/host name |
| `message` | no | Short status message shown on the card (truncated to 200 chars) |
| `stage` | no | Dev-workflow stage line (`repo:stage`, newline-joined); shown as a second card line when non-empty; not sticky — an event omitting it clears the stored value |
| `timestamp` | no | ISO-8601 timestamp of the event |
| `session_name` | no | Friendly display name; falls back to `session_id` when omitted. A name that starts with a story id groups the session (see [Story groups](#story-groups)) |
| `is_container` | no | Boolean; badges the card when the session runs inside a container |
| `story_title` | no | Title of the story the session name's story id refers to; shown in the group header. Sticky across events that omit it, cleared when the session's story id changes, and never stored for a session whose name has no story id |
| `active_work` | no | Array of per-subagent records — `{id, kind, label, status, started_at, completed_at?}` (see Subagent Tracking and Retention above); sanitized, capped, and persisted server-side; sticky across events that omit the field |

Responses: `200` with the stored record, `400` for a missing `session_id` or invalid `state`, `413` for an oversized body, `415` for a non-`application/json` `Content-Type`.

### `POST /api/sessions/{id}/state`

Manually forces a known session's state. Body: `{"state": "..."}`. `200` with the updated record, `400` for an invalid/missing state, `404` for an unknown session — never creates one.

### `DELETE /api/sessions/{id}`

Removes a session from the dashboard. `200` on success, `404` if unknown.

### `GET /api/sessions`

Returns `{"sessions": [...], "groups": [...], "layout": [...]}`, all built from one snapshot of the store so they always agree.

- `sessions` — every tracked session with computed `state_seconds` and `age_seconds`, plus each `active_work` entry annotated with a computed `duration_seconds` (live while `active`, frozen once `completed`). This flat list is sorted needs-attention first (red, yellow, green), then by time in state.
- `groups` — one entry per story id: `story_id`, `title` (the full header text, `sc-1000: <title>` or the bare id), `color` (`red` or `green`), `session_ids` (in display order), `labels` (row label per session ID), `waiting` (members `waiting` or `needs_input`) and `total`.
- `layout` — the ordered top-level list the dashboard renders; each item is `{"type": "group", "story_id": ...}` or `{"type": "session", "session_id": ...}` for an ungrouped session.

## Security

The hub has **no authentication or TLS** and is intended for a trusted private network only (localhost, LAN, VPN/tailnet). Dashboard rows expose session names, story titles, project names, and message snippets. The unauthenticated `GET /api/sessions` endpoint returns each session record verbatim — including `project`, `host`, `session_id`, `story_title`, full state `history`, and `active_work` labels — along with the group list (each story's id, title, member session IDs and row labels), regardless of what the card renders. The stage line discloses the dev-workflow story most recently active for this repo, which may not be the story this session is working on — including, for a multi-repo story, the names and pipeline stages of its other repos. If remote machines do not need direct access, bind to localhost (`--bind 127.0.0.1`) or a VPN interface.

## Requirements

Python 3.8+, stdlib only. No third-party dependencies.

## Running tests

```bash
uv run --with pytest python -m pytest tests/
```

## License

MIT
