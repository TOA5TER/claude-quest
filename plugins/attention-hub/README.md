# attention-hub

A self-hosted, zero-dependency dashboard server *and* a full set of Claude Code hooks that track which of your coding-agent sessions is waiting on you. Installing this plugin alone gives a Claude Code session everything it needs to report to the dashboard — no other plugin required. Any other agent or hook system can also report to the hub over plain HTTP, since the wire protocol is fully documented below.

## What it is

Many concurrent agent sessions (local, docker, remote servers) make per-machine notifications spammy and hard to track. The attention hub is a small server every session's reporting client posts state events to; its web dashboard shows each session as a color-coded card, and gathers the sessions of one story into a group with one row per session (see [Story groups](#story-groups)):

- **Red** — `waiting` (blocked on a permission/question prompt) or `needs_input` (finished its turn by asking you something)
- **Gray** — `done` (nothing in flight, waiting for you to start the next turn)
- **Green** — `working` (busy, or work such as a background shell or a scheduled wakeup is still in flight)

Each ungrouped card's title is the session name if one was reported, falling back to the session ID (a row inside a group shows its role and repo label instead) — and, when a recent dev-workflow checkpoint has the session's own repo at a non-terminal stage (checkpoints older than about a week are ignored), a second line showing that checkpoint's stage(s), one `repo:stage` entry per line for a multi-repo story. Cards show time in state, the latest message snippet, and last-update age, refresh every 3 seconds, and have a per-card dismiss control for crashed/abandoned sessions. Sessions silent for over 24 hours are pruned automatically.

### Story groups

Grouping is always on; there is no setting. A session whose name is a story id followed by a role (and optionally a repo) joins that story's group. For example, `sc-1000-orchestrator`, `sc-1000-developer` and `sc-1000-reviewer` form one group headed `sc-1000: <story title>`, and `sc-1234-developer-api` joins group `sc-1234`.

- **Name rule** — a session groups when its whole name is a letters-only word, a hyphen, one or more digits, a hyphen, then a non-empty remainder. Letters and digits are plain ASCII (`a`–`z`, `A`–`Z`, `0`–`9`). The story id is the part before the second hyphen and is matched exactly as typed, so `SC-1000-x` and `sc-1000-x` are separate groups. A name with a trailing newline does not match.
- **Names that never group** — an empty name, plain words, a bare id with no remainder (`sc-1000`, `sc-1000-`), underscores (`sc_1-x`), multi-word prefixes (`my-feature-12-x`), and ids from trackers that are numeric, `#`-style, or alphanumeric project keys. These sessions are ordinary cards that sit alongside the groups in the same list.
- **Rows** — every session in a group is its own row, labeled by its role (the remainder's first hyphen-separated word), plus ` · ` and the repo when there is more after it (`developer · api`). A remainder that starts with a hyphen (`sc-1000--x`) still groups and is labeled with the remainder verbatim. Each row keeps its own state color, detail panel, dismiss and force-status controls.
- **Superseded sessions** — when a session reports with the same name and host as another row, the hub drops the other row, so a continued conversation (which gets a new session ID and never ends the old one) shows as one row. The working directory is not compared, because it follows the session's `cd`s. Whichever session reported most recently keeps the row. The name rule never matches an unnamed session, so an unnamed session retires an old row only through the transcript check below.
  - **Two live sessions with one name** — if two sessions with the same name are running at the same time on the same host, they show as one row and the newest report wins. The row switches between them on each report, restarting its history and state timer, and a session waiting on you disappears as soon as the other one reports. Give concurrent sessions distinct names.
  - **Rows from earlier versions** — rows saved by an earlier hub already carry a name and host, so they are dropped the next time a session with the same name reports from the same host. A leftover duplicate whose name no longer matches any live session, for example because the live session was renamed, stays until you dismiss it or it is pruned.
  - **Continued sessions, named or not** — Claude Code can move a running conversation to a new session ID, for example when you background it into agents view and come back. The old ID never gets a Stop or SessionEnd, so its row stayed `working`. The hooks now check for this: each report reads the head of the session's own transcript for records copied from earlier sessions (they keep their original `session_id`), and lists an earlier ID only if that session's transcript ends with a `continued-in` record that leads, within a few hops, to the reporting session. A copy alone is not enough, because a fork copies records while the original keeps running. The listed IDs travel in an optional `supersedes` field on the event. This works without a session name and was added beyond the original scope of the background-work change. The transcript must sit in the same folder as the old one; if the old transcript is missing or unreadable the old row stays until it is dismissed or pruned.
    - **What the hub enforces** — it reads `supersedes` only as a list of strings, honors a capped number of entries, ignores the reporter's own ID, and deletes a listed row only when that row's stored `host` equals the `host` in the same event. A malformed list removes nothing.
    - **What the hub trusts the client for** — the hub never sees a transcript, so it cannot check that a listed ID is a real continuation, and `host` is a label each client supplies, so the host match is not a proof of origin. Any client that can reach the hub can already remove any row with `DELETE /api/sessions/{id}`, so `supersedes` adds no new reach; it is covered by the same trusted-network assumption (see Security).
  - **Marker files** — the client does not clean up the old session's waiting marker file, because only that session's own hooks touch them. They stay under `~/.claude/attention-hub/` and are not read again unless that session reports again.
- **Duplicate names** — sessions sharing a name on different hosts are separate rows with the same label, and both count toward the group color. A stale waiting duplicate keeps the group red until you dismiss it or it is pruned, unless another member is working.
- **Renames** — membership is worked out from each session's current name on every listing and is never stored. A rename takes effect on the session's next reported event (any hook event), not on the dashboard's own refresh. A session can be renamed into a group, out of it, or into another one; a collision suffix the host appends (such as `-2`) still groups and becomes part of the label.
- **Group color** — a group is **green** when any member is `working`, **gray** when every member is `done`, and **red** in every other case. A group with a waiting or `needs_input` member and no working member is red, including a mix of waiting and done. One agent working while the others wait is the normal case and does not mean the story is waiting on you. The header also shows how many members are waiting (`2 of 3 waiting`). Rows and ungrouped cards keep the red/gray/green colors above.
- **Ordering** — groups and ungrouped cards are ordered alphabetically and never by status, so nothing moves when a state changes. Comparison ignores case and compares runs of digits by value (`sc-999` before `sc-1000`, `api2` before `api10`). Inside a group, rows are ranked by role: `orchestrator`, `developer`, `reviewer`, `tester` (case-insensitive), then every other role in alphabetical order, with an empty role first among those. Rows with the same role sort by repo. At the top level, groups sort by story id and ungrouped cards by their name (or session ID when unnamed), interleaved in one list; a group comes before a card with an equal key.
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
- **Active work** — each in-flight task or scheduled wakeup reported by the last Stop, with its kind, label, status, and time in state
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

This plugin ships Claude Code hooks that report session state to the dashboard automatically once installed — no configuration needed, and no other plugin required:

- **Notification hook**: Reports `waiting` and sets the waiting marker for `permission_prompt` and `elicitation_dialog`, the two notifications that mean you are blocked. An `idle_prompt` never reports `waiting`: it only says the prompt sat unanswered, and Stop already decided done, needs_input or working. It is ignored while the last Stop listed background work (in-flight marker), while a permission or elicitation wait is pending (waiting marker), and while an `AskUserQuestion` is still the latest message. It is also ignored when the session's transcript was written to in roughly the last 30 seconds, because a running turn keeps writing it; this stops a late `idle_prompt` from setting a card to `done` just after you submit a prompt. Otherwise it reports `done`, which also ends a card left at `working` by an interrupted turn (Esc sends no Stop), since an interrupted turn's transcript goes quiet. The 30-second window is a heuristic; the submitted prompt is itself written to the transcript, so a prompt just submitted always falls inside it, while an interrupted turn can be reported done only once its transcript has been quiet for that long.
- **Stop hook**: Computes and reports `needs_input` (AskUserQuestion was used), `working` (a background task or scheduled wakeup is still in flight), or `done`, and keeps the in-flight marker in step with that list. The decision reads the Stop input's `background_tasks` and `session_crons` lists (see Active Work Tracking below)
- **PostToolUse hook**: Reports `working` to the hub when a tool completes after the session was flagged `waiting` (a tool can only complete once a pending permission/question was answered). Gated by a per-session marker file, so on normal tool calls (no marker) it exits instantly with zero network activity
- **UserPromptSubmit hook**: Reports `working` to the hub — answering a session automatically clears its needs-attention state
- **SessionEnd hook**: Removes the session from the hub (and cleans up its waiting and in-flight markers)

The UserPromptSubmit, PostToolUse, Stop and SessionEnd hooks ignore events carrying `agent_id` (fired inside a subagent), so a subagent never overwrites its parent's row or creates one of its own. The Notification hook does not filter: a subagent's permission prompt blocks the parent, so it reports `waiting` on the parent's row. Sessions launched with `--agent` carry only `agent_type` on their main thread and are tracked normally.

Transient hook state (waiting markers and in-flight markers) lives under `~/.claude/attention-hub/`.

Installing this plugin alone gives a Claude Code session the complete waiting/needs_input/done/working lifecycle on the dashboard — no other plugin required. If the [`notifications`](../notifications) plugin is also installed, its own hooks handle macOS/Slack notifications independently, with zero shared code between the two plugins. Any other agent that wants to report state can also POST directly to the [HTTP API](#http-api) documented below.

## Active Work Tracking

The Active work list on a card comes from these lists in the Stop hook's input, and nothing else:

- `background_tasks` — one entry per in-flight shell, subagent, monitor, workflow, teammate, cloud session or MCP task. Each becomes an Active work entry with its ID, its type as the kind, its description as the label and its status verbatim. Any entry in the list counts as in flight; the hook never filters on status.
- `session_crons` — one entry per pending scheduled wakeup (`/loop`, `ScheduleWakeup`, `CronCreate`). Each becomes an entry of kind `cron` keyed by the cron's own ID, with the prompt as the label and status `scheduled`.

The state rule at each Stop is: `needs_input` when the transcript shows an `AskUserQuestion`, else `working` when either list has an entry, else `done`. A Stop with no `background_tasks` field is treated as nothing in flight and reports `done`; there is no marker fallback. Builds of Claude Code whose Stop input lacks these fields therefore report `done` with no background tracking. The fields were verified live on Claude Code 2.1.277, the minimum build this plugin has been checked against; earlier builds were not tested, and any build whose Stop input lacks the fields reports `done`. An unreachable task registry omits the fields too, with the same result.

- **Refresh** — the list refreshes only at a Stop. A Stop that finds nothing in flight sends an explicit empty list, which clears the card's previous Active work; `UserPromptSubmit` and `PostToolUse` omit the field, so the hub keeps the list from the last Stop while the session works.
- **Finished work is not shown** — the lists contain only in-flight work, so a task disappears from the card at the first Stop after it ends.
- **Durations** — the hub fills in each entry's start time: it reuses the start time of the entry with the same ID in the session's previous list, and uses the event time for a new ID. Durations therefore keep counting across Stops.
- **Subagents** — a Stop fired inside a subagent (it carries `agent_id`) reports nothing, so a subagent never overwrites its parent's row.

### Why hooks, not a Claude Code mod

Mods are early access and their API can change between releases without notice. They install only from a terminal session on a recent CLI, which would tie a hub built for docker, remote and non-Claude reporters to one install path. Classic hooks already carry the signal in `background_tasks` and `session_crons`.

### Live verification

Captured on Claude Code 2.1.277 (first run) and 2.1.299 (second run) in throwaway sessions: this branch's plugin loaded with `--plugin-dir`, `--setting-sources project,local` so no user-level hooks ran, a throwaway hub on its own port, and a separate hook that logged every raw hook input.

- **Stop input** — carries `background_tasks` and `session_crons`; both were present on every Stop, empty when nothing was in flight.
- **Entry shapes** — shell, subagent and workflow tasks arrive as `{id, type, status, description, ...}` with `type` `shell`, `subagent` or `workflow`. A Monitor reports as type `shell` (its description is the monitor's), never as `monitor`. A cron is `{id, schedule, recurring, prompt}`. The only `status` seen was `running`.
- **Finished work** — a finished task never stayed in `background_tasks`; every Stop after completion showed an empty list. A non-recurring cron left `session_crons` once it fired.
- **Wake** — the completion of a shell, subagent, Monitor and Workflow, and a cron firing, each fired `UserPromptSubmit` (a task-notification or the cron prompt) and then a new Stop with the updated list, so the card settles at `done` without help. While a Monitor was still running, its intermediate wakes produced a Stop that still listed it.
- **idle_prompt** — in the first run, about a minute after a Stop that listed a running 400-second shell, an `idle_prompt` Notification fired with the shell still in flight; the in-flight marker (set by the Stop hook when either list has an entry, removed when both are empty and at SessionEnd) makes the Notification hook ignore it. In the second run (Claude Code 2.1.299) every done card turned `waiting` about a minute after its Stop, each time caused by an `idle_prompt`, because the first fix suppressed it only while work was in flight. `idle_prompt` now never reports `waiting` (see the Notification hook above); `permission_prompt` and `elicitation_dialog` are never suppressed.
- **Silent finish** — not reproduced for any task type, so no fallback hook is built. If a card stays `working` after everything ended, use the force-status buttons.

Not run, so unverified: `idle_prompt` with a Monitor, Workflow or cron in flight (the guard covers them because it keys on the lists, not the type); a clean run of the race where a task ends between the agent's last action and its Stop; any teammate, cloud session or MCP task entry (`monitor`, `teammate`, `cloud session`, `MCP task` and the unknown-type fixture are synthetic); any `status` other than `running`; the Esc-interrupt path (every captured `idle_prompt` followed a Stop, so an `idle_prompt` after an interrupted turn has not been seen live); and Claude Code builds other than 2.1.277 and 2.1.299.

The stuck card from the first run is explained: the turn began under one session ID and finished under another with no SessionEnd for the first (the old transcript ends with a `continued-in` record naming the new ID), so the first ID's row kept the turn's `working`. The continued-session fix above retires such rows. How the user got there (backgrounding into agents view and returning) is the user's account, not something the capture recorded.

Reporting after a continuation is only partly understood. In the first run the continued session's Stops ran additional user-level plugin hooks that the earlier Stops had not (the transcript's hook summaries list them), so the continued process did not keep the launch's `--setting-sources` restriction. Whether it also lost `CLAUDE_ATTENTION_HUB_URL` or `--plugin-dir` is not shown by anything captured: the session never appeared in the throwaway hub, which is consistent with a lost URL, but it was not confirmed. A session that is continued should be checked for where it reports.

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
| `cwd` | no | Full working directory, stored and returned by the API; sticky across events that omit it. Not used to match superseded sessions, which key on `session_name` and `host` (see [Story groups](#story-groups)) |
| `message` | no | Short status message shown on the card (truncated to 200 chars) |
| `stage` | no | Dev-workflow stage line (`repo:stage`, newline-joined); shown as a second card line when non-empty; not sticky — an event omitting it clears the stored value |
| `timestamp` | no | ISO-8601 timestamp of the event |
| `session_name` | no | Friendly display name; falls back to `session_id` when omitted. A name that starts with a story id groups the session (see [Story groups](#story-groups)) |
| `is_container` | no | Boolean; badges the card when the session runs inside a container |
| `story_title` | no | Title of the story the session name's story id refers to; shown in the group header. Sticky across events that omit it, cleared when the session's story id changes, and never stored for a session whose name has no story id |
| `supersedes` | no | Array of session IDs this session continues, checked by the reporting hook against the transcripts, not by the hub (see Superseded sessions). The hub honors only strings, a capped number of entries, and rows other than the reporter's own whose stored `host` equals this event's `host`; anything else is ignored |
| `active_work` | no | Array of in-flight work records — `{id, kind, label, status, started_at?, completed_at?}` (see Active Work Tracking above). `status` is any non-empty string; `started_at` is optional and, when absent, is the earlier start time for the same ID in the session's previous list, else the event time. Sanitized, capped, and persisted server-side; sticky across events that omit the field, and an explicit empty list clears it |

Responses: `200` with the stored record, `400` for a missing `session_id` or invalid `state`, `413` for an oversized body, `415` for a non-`application/json` `Content-Type`.

### `POST /api/sessions/{id}/state`

Manually forces a known session's state. Body: `{"state": "..."}`. `200` with the updated record, `400` for an invalid/missing state, `404` for an unknown session — never creates one.

### `DELETE /api/sessions/{id}`

Removes a session from the dashboard. `200` on success, `404` if unknown.

### `GET /api/sessions`

Returns `{"sessions": [...], "groups": [...], "layout": [...]}`, all built from one snapshot of the store so they always agree.

- `sessions` — every tracked session with computed `state_seconds` and `age_seconds`, plus each `active_work` entry annotated with a computed `duration_seconds` (frozen once `completed`, live otherwise). This flat list is sorted needs-attention first (red), then done (gray), then working (green), then by time in state.
- `groups` — one entry per story id: `story_id`, `title` (the full header text, `sc-1000: <title>` or the bare id), `color` (`red`, `gray` or `green`), `session_ids` (in display order), `labels` (row label per session ID), `waiting` (members `waiting` or `needs_input`) and `total`.
- `layout` — the ordered top-level list the dashboard renders; each item is `{"type": "group", "story_id": ...}` or `{"type": "session", "session_id": ...}` for an ungrouped session.

## Security

The hub has **no authentication or TLS** and is intended for a trusted private network only (localhost, LAN, VPN/tailnet). Dashboard rows expose session names, story titles, project names, and message snippets. The unauthenticated `GET /api/sessions` endpoint returns each session record verbatim — including `project`, `host`, `session_id`, `story_title`, full state `history`, and `active_work` labels — along with the group list (each story's id, title, member session IDs and row labels), regardless of what the card renders. Active work labels are user-authored text: cron prompts and task descriptions (shell commands, subagent descriptions) are sent to the hub over plain HTTP, served by the unauthenticated `GET /api/sessions` endpoint, and written to the state file. The stage line discloses the dev-workflow story most recently active for this repo, which may not be the story this session is working on — including, for a multi-repo story, the names and pipeline stages of its other repos. If remote machines do not need direct access, bind to localhost (`--bind 127.0.0.1`) or a VPN interface.

## Requirements

Python 3.8+, stdlib only. No third-party dependencies.

## Running tests

```bash
uv run --with pytest python -m pytest tests/
```

## License

MIT
