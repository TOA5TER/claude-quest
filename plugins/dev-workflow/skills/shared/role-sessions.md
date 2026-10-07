# Role Sessions (persistent developer, reviewer, tester)

Standalone `full-cycle` runs the developer, reviewer, and tester as long-lived, named background Claude Code sessions instead of fresh one-shot subagents. Each repo's developer fixes every round of review and test feedback for that repo's PR; the same reviewer re-reviews and the same tester re-tests, each keeping its own context. The sessions talk through cross-session messaging in a **hub-and-spoke** topology: workers message only the orchestrator, and the orchestrator alone decides who receives what next.

**Scope.** Standalone `full-cycle` only. `epic` is descoped: a background session cannot launch its own background session (the platform's permission classifier denies a nested background launch), and a subagent's outbound messages carry its parent's address, so worker replies would land in the wrong conversation. When `full-cycle` runs as an epic per-task worker (a dispatched, autonomous subagent with no way to ask the user), it launches no role sessions, skips the preflight below, and runs the fresh-dispatch path described under "Fallback path".

**Other workers are unchanged.** `dev-workflow-spec-writer`, `dev-workflow-pr-state-reader`, and the reviewer's parallel perspective fan-out stay fresh one-shot subagents.

GitHub stays the authoritative source for every review and test decision.

---

## Roster, naming, lifecycle

- **Orchestrator.** In standalone `full-cycle` this is the user's own main session.
- **Developer.** One session per story and repo, launched lazily when that repo's first `develop` is sent (or when a `fix` is needed on resume), reused for all fix work on that repo's PR across the review loop and the test loop. It receives only that repo's `develop` and `fix` messages, so a multi-repo story never mixes repo contexts in one session.
- **Reviewer and tester.** One session per PR each, launched lazily at that PR's first review or first test. Per-PR sessions keep a multi-repo story's contexts separate and let different repos' rounds run concurrently.
- **Names.** Story ID plus role plus repo, for every role (for example `sc-1234-developer-api`, `sc-1234-reviewer-api`, `sc-1234-tester-api`). The host may rename on a collision, so record the name and short ID printed at launch and the session ID from `claude agents --json`, and never assume the requested name was granted.
- **Permission before teardown.** `shutdown`, `claude stop`, and `claude rm` never run without the user's explicit permission for that action. The user has the final say and may want the sessions to take on more work. Respawning a failed session is not teardown and stays allowed. Worktree reclamation rules are unchanged.
- **Teardown at Termination.** The orchestrator lists every live session of the story by name, says they remain available for more work, and asks once. The user may approve all, name a subset, or leave all running. Each repo's developer is offered together with that repo's reviewer and tester. On approval, send `shutdown`, wait for the `shutdown` result for one two-minute window, then stop and remove only the approved sessions regardless of whether it arrived; if removal is refused, report it and never force it. No answer, or a non-interactive run, means nothing is stopped and the report lists the session names and the manual commands (`claude stop` then `claude rm`).
- **Teardown on non-success paths.** Every path that ends or pauses a run without reaching Termination (a Loop Safety Guard loop stopped, the user declining more cycles, a per-round timeout, a second liveness failure, a blocked stop-and-report, an idle session with no result after one re-send, a failed preflight; the list is not exhaustive) follows the same report-and-ask: list live session names, say they remain available, and ask. There is no autonomous exemption. A user declining more cycles is not permission to tear down. While the user is still being asked, keep every session. Sessions left running are listed with the manual commands in the final report.

## Launch contract

Commands and tools:

- Launch: `claude --bg --name <name> --agent <plugin:agent> --model <model> --settings <json> "<prompt>"`
- Inspect: `claude agents --json` (add `--all` for stopped sessions)
- Recover: `claude respawn`
- Tear down (only with the user's permission): `claude stop` then `claude rm`
- Address sessions with the `ListAgents` tool (find a session, confirm it is reachable) and the `SendMessage` tool (send, ping, shutdown). The human-facing equivalent is `/list-agents`.

Each role launches as one background session with:

- a name;
- the plugin-qualified `--agent` (for example `dev-workflow:dev-workflow-reviewer`);
- the resolved model per "Subagent Model Selection" in `standards.md`; where the table default is `inherit`, pass the orchestrating session's own model name explicitly;
- a working directory: the developer launches with that repo's checkout as its working directory (resolved per the repo discovery procedure, whether the orchestrator started inside one repo or in a parent folder), using the simplest mechanism the host supports, for example changing into the checkout before running the launch command. Each repo checkout must pass the host's workspace-trust check; if the host offers no way to set the working directory, report that instead of inventing one. Reviewer and tester launch as before;
- inline `--settings` JSON that sets `crossSessionInbound` to `accept`, sets `worktree.bgIsolation` to `none` (Workspace Isolation in `standards.md` already owns worktree creation and the live lookup), and sets the env var `DEV_WORKFLOW_ROLE` to the role name (`developer`, `reviewer`, or `tester`).

Launch from a trusted workspace; a background launch from an untrusted directory fails, which the preflight treats as unavailable. If a later repo's developer fails to launch after the run's preflight already passed, do not fall back to fresh dispatch for that repo (modes are never mixed): stop, report the repo, and ask the user how to proceed.

**Launch-text caution.** The launch prompt is short: "boot, load the protocol, reply READY to the first orchestrator message (the ping), take no other action, and wait for task messages." It also tells the worker to answer every task message with an acknowledgement and a terminal reply sent through the messaging tool. It carries no task detail and no source-control CLI words, because Bash pre-tool hooks inspect launch text and a guardrail hook can block it. Task detail travels through the messaging tool, never through Bash. The first task arrives as a message.

**Optional config.** `role_sessions.permission_mode` in `~/.claude/dev-workflow/config.json`. When set it is passed as the sessions' permission mode; when unset no flag is passed and the host default for that directory applies. Permitted values are `default`, `acceptEdits`, and `plan`; `plan` applies only to the reviewer and tester, because a developer session in plan mode cannot edit files, so the developer launches with no flag. `bypassPermissions` is refused, because role sessions accept inbound messages from any local session; any other value (including the unconfirmed `auto`) is also refused. On a refused value, announce it once and launch with no flag.

## Preflight, handshake, fallback

Once per run, before the first launch, check all of:

1. the Claude Code version meets the floor (v2.1.224);
2. `claude agents --json` runs (agent view not disabled);
3. a background launch succeeds (workspace trust);
4. the first launched session, whatever its role, answers a `ping` within a bounded timeout.

**Per-session handshake.** Every newly launched session, whatever its role or repo, receives its own `ping` and must answer `ready` before its first task message is sent; a session replies `ready` to its first message and takes no other action, so a task sent first would be ignored. The preflight check above covers only the first launched session; later launches (including lazily launched developers, reviewers, and testers) get the same ping/ready handshake, and a failed later handshake follows the later-launch failure rule in the launch contract (stop, report, ask).

**`crossSessionInbound` prerequisite.** A worker's reply to a main session in a different permission-mode class is held for the user's approval, even when the worker accepts inbound messages itself. The orchestrator session must set `crossSessionInbound` to `accept` (or share the workers' permission class). A failed ping with a live, idle worker means its reply is almost certainly being held; the announcement names this remedy.

Any failed check ends the run's use of role sessions: announce the reason once, report any sessions already launched by name and ask before stopping them (per "Permission before teardown"), and run the entire run on fresh dispatch. Never mix modes within a run. Never wait silently for a reply that may be parked behind a dialog.

### Fallback path

The fallback loop body is a fresh `dev-workflow-developer` dispatch in rework mode (a PR number is supplied), followed by a fresh `dev-workflow-reviewer` or `dev-workflow-tester` dispatch. This is the path used when preflight fails and by every epic per-task worker.

## Message protocol

**Envelope.** The first line of every message is a one-line, machine-readable header: protocol marker and version, sender role, recipient role, message type, story or task ID, repo, PR number, round number. For example:

`DWF/1 orchestrator>reviewer review sc-1234 api PR#87 round 2`

The body is plain text. A human sees only the first line in a preview, so it must be self-explanatory.

**Types.**

| Direction | Types |
|-----------|-------|
| Orchestrator to worker | `develop` (one repo of the story), `fix` (feedback on a PR), `review` (a PR), `test` (a PR), `ping`, `shutdown` |
| Worker to orchestrator | `ready`, `ack` (receipt of a task message; one line, no body), `result`, `blocked` (needs a human decision) |

The `result` body for `develop`, `review`, and `test` is the existing flat key/value record from "Output Mode Detection" in `standards.md`, unchanged. The `result` for a `fix` is a short plain-text confirmation of what changed, and the `result` answering `shutdown` is the single word `shutdown`; neither is a key/value record, and the orchestrator never parses them.

**Develop scope.** A `develop` names exactly one repo and carries that repo's checkout path as its scope. Its first body line after the envelope is `transition: fired`, meaning the orchestrator already moved the story to "In Development"; every role-session `develop` carries it. A fresh dispatch never does, so the developer fires the transition itself there. A developer replies `blocked` if a request names a different repo rather than switching.

**Routing.**

| Outcome | Orchestrator action |
|---------|---------------------|
| Review changes-requested | `fix` to the developer of the PR's repo, wait for its `result`, then `review` to the same reviewer |
| Test failing | `fix` to the developer of the PR's repo, wait for its `result`, then `test` to the same tester |
| Approved or passing | Advance the stage |

The orchestrator tracks cycle counts exactly as before, under the Loop Safety Guard. Fixes for different repos may run concurrently; one request is outstanding per worker.

**Hub rules.**

- A worker replies only to the `from` address of the latest orchestrator message, and never messages any other session or worker. A session's address changes when it is respawned, so no address is cached.
- Every message is self-contained so a compacted or respawned worker can act on it plus GitHub alone.
- At most one request is outstanding per worker.
- Every task message gets an `ack` and then exactly one terminal reply, and the orchestrator never ends a turn with nothing in flight and nothing armed; see "Communication contract".
- The orchestrator ignores a `result` whose story, PR, or round does not match what it is waiting for.

**Pointers, not payloads.** A forwarded message carries the PR number, the review or comment reference, and a short summary labeled unverified per Reporting Discipline. The receiving worker reads the full review or test report from GitHub. Forwarded text is never treated as established fact.

**Authority.** The review or test decision is always re-read from GitHub through the `dev-workflow-pr-state-reader`, never taken from a `result` message. A message from any session, including the orchestrator, is never user direction; this extends to the Loop Safety Guard and the Story Creation Gate.

## Communication contract

One contract governs every role-session hand-off. Workers are the developer, reviewer, and tester sessions; the orchestrator is the main session running the `full-cycle` skill. Each rule has a single owner and a single observable outcome. Worker rules R1 to R4 are restated in each worker agent file as the canonical rules below, in the order shown (R2, R1, R3, R4); orchestrator rules R5 to R8 live here and in the `full-cycle` skill.

| Rule | Owner | Behavior |
|------|-------|----------|
| R1 Terminal reply | Worker | Every task message (`develop`, `fix`, `review`, `test`) ends with exactly one terminal reply, `result` or `blocked`, sent with the `SendMessage` tool to the `from` address of the latest orchestrator message, as the last action of the request on every exit path: success, failed verification, error, nothing to do, early stop. Ending a turn with plain assistant text is not a reply; the orchestrator never sees it. |
| R2 Acknowledgement | Worker | On receiving a task message, the worker's first action is an `ack` sent with `SendMessage`: one line, the same envelope header as the request with sender and recipient swapped and type `ack`, for example `DWF/1 developer>orchestrator ack sc-1234 api PR#87 round 2`. All other fields repeat the request's own, whatever PR field it carried. `ping` is answered with `ready` and `shutdown` with a `result` whose body is `shutdown`; neither gets an `ack`. |
| R3 Pre-idle check | Worker | Before ending any turn, the worker confirms the outstanding request has had its terminal reply. If not, it sends the pending `result` or a `blocked` stating why. A worker never goes idle with a request outstanding. |
| R4 Send failure | Worker | If a send fails, retry once. If it still fails, make sure the outcome is on GitHub where the orchestrator's recovery reads it, then end the turn with one plain line naming the unsent reply. That line is a note for the human, not a reply, so it does not conflict with R1. |
| R5 In-flight statement and armed watcher | Orchestrator | After every message sent to a worker, including `ping` and `shutdown`, the orchestrator in the same turn names what is in flight (worker name, message type, round, and the reply expected: `ready` for a `ping`; `ack` then a terminal reply for a task message; a `result` of `shutdown` for a `shutdown`) and arms a watcher (on ladder rung 3 the watcher is the user, told in the same turn which request is outstanding). The turn may end only after both. For a `shutdown` the wait is a single two-minute window: after it the orchestrator proceeds to stop the sessions the user approved, whether or not the `result` arrived, and names any session that did not answer. |
| R6 Same-turn hand-offs | Orchestrator | Each step of a multi-step hand-off completes in the turn the triggering event arrives: launch then `ping` in one turn; `ready` then the first task message in the turn the `ready` arrives (a "not reachable" error on the first `ping` after a launch is not treated as stopped; it is handled as "no `ready` yet" by the two-minute check below); on resume, `ping` of a reused session then the task message once `ready` arrives; a terminal reply then the authoritative GitHub read then the next send, or advancing the stage, in the turn the reply arrives; for a multi-repo story, every concurrent send in one turn. |
| R7 Missing ack or reply | Orchestrator | Two minutes after a send with no `ack` (or no `ready` for a `ping`), read the session's listed state. Stopped, failed, or no process: respawn once and re-send once, as in "Waiting and liveness". Blocked: apply the Blocked row there. Idle: apply the done-and-idle row there (read GitHub, check for commits newer than the request, then re-send once). Running or busy: do not re-send, as the "Still working" row already says (a `ping` that was not reachable at launch is re-sent once here); report to the user that the request has no `ack`, keep the watcher armed, and let the per-round limit keep running; the user's direction continues or ends the wait. A `ping` has a total limit of four minutes: a session still listed running or busy with no `ready` by then is reported to the user as a failed handshake, and the watcher is stopped. A missing terminal reply after an `ack` follows the "Waiting and liveness" table and per-round limits unchanged. The watcher is stopped when the terminal reply arrives or the request is abandoned. |
| R8 Prohibited turn endings | Orchestrator | A turn must not end (a) after a launch with no `ping` sent, (b) after `ready` with no task sent, (c) after any send with nothing named in flight or neither a watcher armed nor, on ladder rung 3, the user told which request is outstanding, or (d) after a terminal reply with the authoritative state unread and no next action taken. |

**The watcher ladder.** The orchestrator uses the first of these that its session offers.

1. A visible `Monitor`, preferred: it polls `claude agents --json` for the named session or sessions and prints a line when the two-minute mark passes, when a session's state changes to idle, failed, stopped, or blocked, every five minutes (the existing re-check interval) while running, and when the per-round limit is reached; it exits after that limit.
2. A scheduled wake-up tool, such as `ScheduleWakeup` (documented for dynamic loop mode) or a one-shot cron entry, set to the next check time.
3. If neither is available, the orchestrator tells the user in the same turn which request is outstanding and that no automatic wake-up is available, and asks them to message it to continue. This is the one case where a turn ends with no watcher armed, and it is allowed because the user has been told; the user's next message triggers the same checks a watcher would.

The `Monitor` cannot see messages, so the two-minute mark is a prompt for the orchestrator to check its own record of whether the `ack` arrived. One `Monitor` may cover every session sent a request in the same turn. A hidden background shell loop is never used.

**Hook reminder.** The SessionStart hook adds this exact sentence to the role-session context so the contract survives compaction and respawn: Role-session contract: answer every develop, fix, review, or test message with an ack, then end the request with a result or blocked message sent through the SendMessage tool; plain text is never a reply.

### Canonical worker rules

These rules apply only when you run as a role session. If you were dispatched as a one-shot subagent, your final response is your result and none of them applies.

1. **Acknowledge first.** On receiving a develop, fix, review, or test message, your first action is to send an `ack` with the SendMessage tool: one line, the same header as the request with sender and recipient swapped and type `ack`. All other header fields repeat the request's own, whatever PR field it carried. Answer `ping` with `ready` and `shutdown` with a `result` whose body is `shutdown`; neither gets an `ack`.
2. **Always send a terminal reply.** Every task message ends with exactly one `result` or `blocked`, sent with the SendMessage tool to the `from` address of the latest orchestrator message, as the last action of the request on every path: success, failed verification, error, nothing to do, or early stop. Ending a turn with plain text is not a reply and the orchestrator never sees it.
3. **Check before you idle.** Before ending any turn, confirm the outstanding request has had its terminal reply. If it has not, send the pending `result` or a `blocked` that says why. Never go idle with a request outstanding.
4. **If a send fails, retry once.** If it still fails, make sure the outcome is on GitHub where the orchestrator's recovery reads it, then end the turn with one plain line naming the unsent reply. That line is a note for the human, not a reply.

## Waiting and liveness

After every send, the orchestrator states what is in flight (role, target PR or repo, round, the replies it expects) and arms a watcher from the ladder in "Communication contract" (R5), per "Subagent Wait Discipline" in `standards.md`. A reply wakes the orchestrator with a new turn. Do not rely on idle notices or on a poll loop of `ListAgents`; the watcher reads the session's listed state through `claude agents --json`. Two minutes after a send with no `ack` (or no `ready` for a `ping`), the orchestrator runs the missing-ack check in R7, which refers to the rows below and does not override them. A missing terminal reply after an `ack` follows the rows below and the per-round limit.

- **Blocked** (waiting on a permission prompt): an interactive run asks the user; an autonomous run stops and reports the task as blocked.
- **Failed, stopped, or no process:** respawn once with `claude respawn`, then re-send the last request. A second failure ends role-session use for that PR with a stop-and-report, then report-and-ask per "Teardown on non-success paths".
- **Still working** (listed as running or busy): this is the normal state during CI and deploy waits. Re-arm the re-check and keep waiting. Do not respawn or re-send.
- **Done and idle with no result:** read the authoritative decision from GitHub; if present, proceed. Otherwise check the PR head for commits newer than the request (a `fix` may already have been applied); if there are none, re-send once, then stop and report (and ask about teardown per "Teardown on non-success paths").

**Per-round time limit.** Each request has an overall limit from the send: 60 minutes for `develop` and `fix`, 120 minutes for `review` and `test` (CI, terraform, and deploy waits can exceed 60), or `role_sessions.round_timeout_minutes` in `~/.claude/dev-workflow/config.json` when set to a positive integer, which applies to every request type. The limit holds regardless of how many re-checks returned "still working". At the limit, stop waiting, stop and report the task as timed out, and apply the report-and-ask in "Teardown on non-success paths" in "Roster, naming, lifecycle". Each re-check interval is 5 minutes unless `standards.md` "Subagent Wait Discipline" sets a different one.

Before every send, confirm the target is reachable. A "not reachable" send error is handled the same as a stopped session, except on the first `ping` after a launch, which is handled as "no `ready` yet" by the two-minute check in R7.

## Context, worktrees, resume

- Role sessions receive the absolute plugin root and standards path at session start from `hooks/role-session-context.sh`, because the host gives them neither their agent file's path nor `CLAUDE_PLUGIN_ROOT`; the role agents resolve relative `skills/` paths against it instead of searching the disk.
- Role sessions rely on the host's native auto-compaction. `hooks/context-meter.sh` and `hooks/compact-injector.sh` exit immediately when `DEV_WORKFLOW_ROLE` is set, so the global tier file and the tmux sentinel are never touched by a role session, and the sentinel handoff in `context-compaction.md` does not apply. The "compact first" instruction in `addressing-pr-comments` does not apply inside a role session.
- The ALWAYS-FRESH mandates stay in force every round: a `fix` request does not skip the developer's verification, and a `review` or `test` request always re-reads the current diff and re-runs the fresh dev build CI or dev deploy CI. Memory of earlier rounds is context, never evidence.
- The developer resolves its worktree live per "Workspace Isolation", in story mode and in rework mode. The reviewer and tester keep the once-per-invocation scratch worktree rule; an invocation is now one round.
- A developer session's model is fixed at launch from `models.stages.developing`; rework requests reuse it. `models.stages.addressing-pr-comments` applies only to a fresh rework dispatch (fallback and epic per-task workers).
- **Resume.** On re-invocation, list sessions with `claude agents --json --all` and match this story's names. A live, responsive session is reused (ping first); a stopped one is respawned; an unresponsive one is not stopped or removed without permission: ask, and if permission is not granted, launch a fresh session under a new name and list the old one as left over. A cold worker recovers from the message, GitHub, and the checkpoint alone.
