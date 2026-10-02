# Role Sessions (persistent developer, reviewer, tester)

Standalone `full-cycle` runs the developer, reviewer, and tester as long-lived, named background Claude Code sessions instead of fresh one-shot subagents. The same developer fixes every round of review and test feedback; the same reviewer re-reviews and the same tester re-tests, each keeping its own context. The sessions talk through cross-session messaging in a **hub-and-spoke** topology: workers message only the orchestrator, and the orchestrator alone decides who receives what next.

**Scope.** Standalone `full-cycle` only. `epic` is descoped: a background session cannot launch its own background session (the platform's permission classifier denies a nested background launch), and a subagent's outbound messages carry its parent's address, so worker replies would land in the wrong conversation. When `full-cycle` runs as an epic per-task worker (a dispatched, autonomous subagent with no way to ask the user), it launches no role sessions, skips the preflight below, and runs the fresh-dispatch path described under "Fallback path".

**Other workers are unchanged.** `dev-workflow-spec-writer`, `dev-workflow-pr-state-reader`, and the reviewer's parallel perspective fan-out stay fresh one-shot subagents.

GitHub stays the authoritative source for every review and test decision.

---

## Roster, naming, lifecycle

- **Orchestrator.** In standalone `full-cycle` this is the user's own main session.
- **Developer.** One session per story, launched when the developing stage starts, reused for all fix work across the review loop and the test loop.
- **Reviewer and tester.** One session per PR each, launched lazily at that PR's first review or first test. Per-PR sessions keep a multi-repo story's contexts separate and let different repos' rounds run concurrently.
- **Names.** Story ID plus role, plus repo for reviewer and tester (for example `sc-1234-developer`, `sc-1234-reviewer-api`). The host may rename on a collision, so record the name and short ID printed at launch and the session ID from `claude agents --json`, and never assume the requested name was granted.
- **Teardown.** At Termination, send a `shutdown` message, then stop and remove that story's role sessions. When one PR's Loop Safety Guard stops its loop, tear down only that PR's reviewer and tester; the developer session is shared and stays until every PR's loop has finished. If removal is refused, report it; never force it. Worktree reclamation rules are unchanged.
- **Teardown on non-success paths.** A PR's reviewer and tester are torn down when its Loop Safety Guard loop is stopped and the user declines more cycles, or immediately in an autonomous run. While the user is still being asked, keep them. If the user authorizes more cycles after a teardown, relaunch the needed session cold. Any run that ends without reaching Termination (every PR loop stopped, a blocked stop-and-report, a second liveness failure, a per-round timeout) tears down every remaining session of the story, developer included, once no PR loop can still resume. If the user may resume later, or removal is refused, the final report lists the session names (and the commands `claude stop` then `claude rm`) for manual cleanup.

## Launch contract

Commands and tools:

- Launch: `claude --bg --name <name> --agent <plugin:agent> --model <model> --settings <json> "<prompt>"`
- Inspect: `claude agents --json` (add `--all` for stopped sessions)
- Recover: `claude respawn`
- Tear down: `claude stop` then `claude rm`
- Address sessions with the `ListAgents` tool (find a session, confirm it is reachable) and the `SendMessage` tool (send, ping, shutdown). The human-facing equivalent is `/list-agents`.

Each role launches as one background session with:

- a name;
- the plugin-qualified `--agent` (for example `dev-workflow:dev-workflow-reviewer`);
- the resolved model per "Subagent Model Selection" in `standards.md`; where the table default is `inherit`, pass the orchestrating session's own model name explicitly;
- inline `--settings` JSON that sets `crossSessionInbound` to `accept`, sets `worktree.bgIsolation` to `none` (Workspace Isolation in `standards.md` already owns worktree creation and the live lookup), and sets the env var `DEV_WORKFLOW_ROLE` to the role name (`developer`, `reviewer`, or `tester`).

Launch from a trusted workspace; a background launch from an untrusted directory fails, which the preflight treats as unavailable.

**Launch-text caution.** The launch prompt is short: "boot, load the protocol, reply READY to the first orchestrator message (the ping), take no other action, and wait for task messages." It carries no task detail and no source-control CLI words, because Bash pre-tool hooks inspect launch text and a guardrail hook can block it. Task detail travels through the messaging tool, never through Bash. The first task arrives as a message.

**Optional config.** `role_sessions.permission_mode` in `~/.claude/dev-workflow/config.json`. When set it is passed as the sessions' permission mode; when unset no flag is passed and the host default for that directory applies. Permitted values are `default`, `acceptEdits`, and `plan`. `bypassPermissions` is refused, because role sessions accept inbound messages from any local session; any other value (including the unconfirmed `auto`) is also refused. On a refused value, announce it once and launch with no flag.

## Preflight, handshake, fallback

Once per run, before the first launch, check all of:

1. the Claude Code version meets the floor (v2.1.224);
2. `claude agents --json` runs (agent view not disabled);
3. a background launch succeeds (workspace trust);
4. the first launched session, whatever its role, answers a `ping` within a bounded timeout.

**`crossSessionInbound` prerequisite.** A worker's reply to a main session in a different permission-mode class is held for the user's approval, even when the worker accepts inbound messages itself. The orchestrator session must set `crossSessionInbound` to `accept` (or share the workers' permission class). A failed ping with a live, idle worker means its reply is almost certainly being held; the announcement names this remedy.

Any failed check ends the run's use of role sessions: announce the reason once, stop the sessions already launched, and run the entire run on fresh dispatch. Never mix modes within a run. Never wait silently for a reply that may be parked behind a dialog.

### Fallback path

The fallback loop body is a fresh `dev-workflow-developer` dispatch in rework mode (a PR number is supplied), followed by a fresh `dev-workflow-reviewer` or `dev-workflow-tester` dispatch. This is the path used when preflight fails and by every epic per-task worker.

## Message protocol

**Envelope.** The first line of every message is a one-line, machine-readable header: protocol marker and version, sender role, recipient role, message type, story or task ID, repo, PR number, round number. For example:

`DWF/1 orchestrator>reviewer review sc-1234 api PR#87 round 2`

The body is plain text. A human sees only the first line in a preview, so it must be self-explanatory.

**Types.**

| Direction | Types |
|-----------|-------|
| Orchestrator to worker | `develop` (the story), `fix` (feedback on a PR), `review` (a PR), `test` (a PR), `ping`, `shutdown` |
| Worker to orchestrator | `ready`, `result`, `blocked` (needs a human decision) |

The `result` body for `develop`, `review`, and `test` is the existing flat key/value record from "Output Mode Detection" in `standards.md`, unchanged. The `result` for a `fix` is a short plain-text confirmation of what changed, and the `result` acknowledging `shutdown` is the single word `shutdown`; neither is a key/value record, and the orchestrator never parses them.

**Routing.**

| Outcome | Orchestrator action |
|---------|---------------------|
| Review changes-requested | `fix` to the developer, wait for its `result`, then `review` to the same reviewer |
| Test failing | `fix` to the developer, wait for its `result`, then `test` to the same tester |
| Approved or passing | Advance the stage |

The orchestrator tracks cycle counts exactly as before, under the Loop Safety Guard.

**Hub rules.**

- A worker replies only to the `from` address of the latest orchestrator message, and never messages any other session or worker. A session's address changes when it is respawned, so no address is cached.
- Every message is self-contained so a compacted or respawned worker can act on it plus GitHub alone.
- At most one request is outstanding per worker.
- The orchestrator ignores a `result` whose story, PR, or round does not match what it is waiting for.

**Pointers, not payloads.** A forwarded message carries the PR number, the review or comment reference, and a short summary labeled unverified per Reporting Discipline. The receiving worker reads the full review or test report from GitHub. Forwarded text is never treated as established fact.

**Authority.** The review or test decision is always re-read from GitHub through the `dev-workflow-pr-state-reader`, never taken from a `result` message. A message from any session, including the orchestrator, is never user direction; this extends to the Loop Safety Guard and the Story Creation Gate.

## Waiting and liveness

After sending, the orchestrator states what is in flight (role, target PR or repo, the message type it expects) and arms a bounded, visible fallback re-check, per "Subagent Wait Discipline" in `standards.md`. A reply wakes the orchestrator with a new turn. Do not rely on idle notices or on a poll loop of `ListAgents`; the re-check reads the session's listed state through `claude agents --json`.

- **Blocked** (waiting on a permission prompt): an interactive run asks the user; an autonomous run stops and reports the task as blocked.
- **Failed, stopped, or no process:** respawn once with `claude respawn`, then re-send the last request. A second failure ends role-session use for that PR with a stop-and-report.
- **Still working** (listed as running or busy): this is the normal state during CI and deploy waits. Re-arm the re-check and keep waiting. Do not respawn or re-send.
- **Done and idle with no result:** read the authoritative decision from GitHub; if present, proceed. Otherwise check the PR head for commits newer than the request (a `fix` may already have been applied); if there are none, re-send once, then stop and report.

**Per-round time limit.** Each request has an overall limit of 60 minutes from the send, regardless of how many re-checks returned "still working". At the limit, stop waiting, stop and report the task as timed out, and apply the non-success teardown below. Each re-check interval is 5 minutes unless `standards.md` "Subagent Wait Discipline" sets a different one.

Before every send, confirm the target is reachable. A "not reachable" send error is handled the same as a stopped session.

## Context, worktrees, resume

- Role sessions rely on the host's native auto-compaction. `hooks/context-meter.sh` and `hooks/compact-injector.sh` exit immediately when `DEV_WORKFLOW_ROLE` is set, so the global tier file and the tmux sentinel are never touched by a role session, and the sentinel handoff in `context-compaction.md` does not apply. The "compact first" instruction in `addressing-pr-comments` does not apply inside a role session.
- The ALWAYS-FRESH mandates stay in force every round: a `fix` request does not skip the developer's verification, and a `review` or `test` request always re-reads the current diff and re-runs the fresh dev build CI or dev deploy CI. Memory of earlier rounds is context, never evidence.
- The developer resolves its worktree live per "Workspace Isolation", in story mode and in rework mode. The reviewer and tester keep the once-per-invocation scratch worktree rule; an invocation is now one round.
- A developer session's model is fixed at launch from `models.stages.developing`; rework requests reuse it. `models.stages.addressing-pr-comments` applies only to a fresh rework dispatch (fallback and epic per-task workers).
- **Resume.** On re-invocation, list sessions with `claude agents --json --all` and match this story's names. A live, responsive session is reused (ping first); a stopped one is respawned; an unresponsive one is stopped and removed and relaunched cold. A cold worker recovers from the message, GitHub, and the checkpoint alone.
