---
name: dev-workflow:epic
description: "Use when a large initiative must be decomposed into many coordinated tasks and driven to completion, when a user says 'epic', 'break this down', 'orchestrate this feature', 'run this whole initiative', or '/start epic', or provides a high-level multi-task feature summary that spans more than one PR or repo. Resumable: re-invoke against an existing epic slug to continue an interrupted run."
---

# Epic

**Role:** Orchestrator — take one high-level feature summary, reach consensus on a full task
breakdown, then autonomously drive every task to a review- and test-approved open PR, leaving the
merge to a human. The epic never merges.

The orchestrator inherits the main session model — it must stay an orchestrator (see the mandate
below). Every unit of real work (brainstorming, repo discovery, spec writing, development, review,
testing, fixing) is dispatched to a **subagent** via the **Agent tool** (never an inline `Skill`
call — see `skills/shared/standards.md` → "Subagent Dispatch"), whose model is resolved from config
per `skills/shared/standards.md` → "Subagent Model Selection".

Per-task isolation relies on subagent **nesting** (`skills/shared/standards.md` → "Subagent
Nesting"): the epic dispatches each task as a `dev-workflow-orchestrator` worker, which on Claude
Code **v2.1.172+** dispatches full-cycle's stages as further nested subagents (fresh context per
stage, within the fixed depth-5 cap). On older builds a task's stages run inline within that task's
worker — still isolated per task, just not per stage.

## Orchestration-Only Mandate

**The orchestrator performs orchestration only.** Its sole direct actions are:

- the discovery interview and the single consensus gate (user-facing),
- writing and updating `tasklist.md` (via the `tasklist` adapter),
- computing the ready set and scheduling,
- dispatching subagents,
- marking a dual-approved task `awaiting-merge`, tracking its open PR, and — on a later resume —
  detecting a human merge and advancing the task to `done`,
- the end-of-run report.

**The epic never merges a PR.** On dual approval it pauses the task at `awaiting-merge` and leaves
the PR open for a human; it never runs a merge command (`gh … pr merge` or any equivalent). The only path to `done` is a human merge detected on a subsequent resume.

It **never** writes feature code, specs, reviews, or tests itself, and **never** creates a task
on its own initiative outside the consensus gate or a subagent's bug report. If you find yourself
authoring a spec, editing source, reviewing a diff, or merging a PR in the main agent — stop; that
work belongs in a dispatched subagent or, for the merge, to a human. This includes reading source
files or running `repo-discovery.md` directly because a lookup "looks small" — dispatch it, every
time; "small enough to just do myself" is how research work leaks into the orchestrator's context.

## Arguments: $ARGUMENTS

One of:

- **A high-level feature summary** — begin a new epic at Phase 1 (discovery).
- **An existing epic slug** (matches a directory under `~/.claude/dev-workflow/epics/`) — resume:
  jump to Phase 6 (scheduler), reading the existing `tasklist.md` as the source of truth.
- **Empty** — ask the user for the feature summary (interactive), or, in autonomous mode, stop and
  report that a summary is required.

Read `skills/shared/standards.md` — mandatory rules for this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedure.

Read `skills/shared/repo-discovery.md` — repo discovery procedure used in Phase 1.

Read `skills/pm-adapter/tasklist.md` — the adapter that backs the tasklist; you operate it directly.

Read `skills/full-cycle/SKILL.md` — the per-task engine you drive; do not reimplement its loops.

Read the CLAUDE.md file in this repository before starting.

---

## Reuse, Do Not Reimplement

The per-task spec → development → review-loop → test-loop, resume/entry detection, and the loop-safety
guard all live in `full-cycle` already. **Epic drives `full-cycle` per task — it does not re-create
that logic.** The only net-new execution logic here is discovery, tasklist generation, and the
cross-task scheduler.

---

## Phase 0: Setup & Mode Detection

1. Determine output mode per `skills/shared/standards.md` → "Output Mode Detection". The discovery
   gate (Phase 5) requires user interaction; if running autonomously with no way to ask **and** this
   is a new epic (no existing slug), STOP and report that the consensus gate needs an interactive
   session. A **resume** against an existing slug needs no interaction and may run fully autonomously.
2. Read `~/.claude/dev-workflow/config.json`; note the `models` section for subagent dispatch.
3. The PM adapter for this skill is **always `tasklist`** (read `skills/pm-adapter/tasklist.md`) — do
   **not** use the configured `pm_adapter`, and do **not** rewrite `config.json`. The notes adapter
   is the configured one (used by `writing-specs` inside each task's `full-cycle`).
4. If `$ARGUMENTS` is an existing epic slug → go to **Phase 6** (resume).

---

## Phase 1: Deep Discovery

Goal: understand the initiative well enough to propose a complete, correctly-ordered task breakdown.

1. **Brainstorm the initiative.** Dispatch the Agent tool (`subagent_type: general-purpose`, task
   type `reasoning`) instructing the worker to invoke `superpowers:brainstorming` against the feature
   summary and return the clarified intent, requirements, edge cases, and risks. (Or, in interactive
   mode, run brainstorming in the main agent so it can ask the user — brainstorming is a discovery
   activity, not implementation work, so this does not violate the orchestration mandate.)
2. **Investigate the target repos.** Per `skills/shared/repo-discovery.md`, discover the repos in the
   workspace and their purposes. **Always** dispatch repo-investigation via the Agent tool
   (`subagent_type: general-purpose`, task type `reasoning`) — the orchestrator never runs
   `repo-discovery.md`'s procedure, reads source files, or greps the codebase directly, regardless of
   how small the read looks. The subagent returns a summary of repo purposes/structure; only that
   summary enters the orchestrator's context.
3. **Propose a decomposition** into small tasks. For each proposed task, mirror `creating-stories`'s
   field structure: `title`, `description`, `acceptanceCriteria`, `testingInstructions`, `repo`
   (exactly one per task), `story_type`, and `depends_on` (task IDs). A dependency exists when one
   task consumes an artifact another task produces (endpoint, contract, package, schema, file).
4. **Order and group.** Topologically sort tasks. Tasks in distinct repos with no dependency between
   them may run concurrently; tasks in the same repo are serialized (one in-flight PR per repo).

Do **not** write `tasklist.md` yet — nothing is created before the gate.

---

## Phase 5: Consensus Gate (single point of full agreement)

Present the **full proposed tasklist** to the user — the Mermaid ordering plus every task's
description, AC, testing, repo, and dependencies — and iterate until they **explicitly approve**.

- This is the **one** consensus gate. After approval the run is autonomous, but it stops short of
  merging — each task pauses at an open, dual-approved PR for a human to merge.
- Render the proposal per `skills/shared/standards.md` Output Format rules (HTML if written to a file
  for the user to read; otherwise the Mermaid + task list inline).
- In autonomous mode there is no gate to run — a new epic cannot reach this phase autonomously
  (Phase 0 stopped it). A resume skips this phase entirely.

Only on explicit approval, proceed to Phase 5b.

---

## Phase 5b: Write the Tasklist

Derive `[epic-slug]` from the feature summary (kebab-case, short). Create the directory
`~/.claude/dev-workflow/epics/[epic-slug]/`.

Write `tasklist.md` using the `tasklist` adapter's **Create story** capability for **each** approved
task — one Create call per task, in dependency order. The metadata header and Mermaid graph are
written per the file format in `skills/pm-adapter/tasklist.md`.

The Story Creation Gate is satisfied: the user explicitly invoked `epic` and approved this exact
task set. All creation happens here, in the orchestrator.

---

## Phase 6: Scheduler

The scheduler decides what runs next. It reads `tasklist.md` as the source of truth.

1. **Compute the ready set.** A task is *ready* when its status is `pending` and every task in its
   `depends_on` is `done` (i.e. its dependency's PR has been **merged by a human**). An
   `awaiting-merge` dependency does **not** satisfy a dependent — because epic never merges, dependents
   naturally pause until a human merges the dependency and a later resume advances it to `done`.
2. **Apply the parallelism rule:**
   - **One in-flight task per repo.** A repo is "in flight" if any of its tasks is `in-progress`,
     `in-review`, `in-test`, or `awaiting-merge`. An `awaiting-merge` task's PR is still open, so it
     keeps its repo in flight and blocks the next same-repo task until a human merges it. Do not
     schedule a second task for a repo that already has one in flight.
   - **Cross-repo concurrency.** Ready tasks in distinct repos run **concurrently**, each as its own
     dispatched subagent.
3. **Isolated worktree required, before dispatching a task.** This is a workspace-isolation
   requirement, not a parallelism property — under the one-in-flight-task-per-repo rule above it adds
   no concurrency of its own. See `skills/shared/standards.md` → "Workspace Isolation" for the
   mechanism; pass that requirement into every dispatch.
4. **Bug priority.** When an open bug task exists for a repo, schedule it as the **next** task for
   that repo — ahead of any pending feature task — subject to the same one-in-flight-PR-per-repo rule.
5. For every task selected this round, go to **Phase 7** (drive). Dispatch the concurrent ones in a
   single batch.
6. When the ready set is empty:
   - If any task is still **actively** in flight (`in-progress`, `in-review`, `in-test`), wait for it
     to complete (Phase 8) per `standards.md` → "Subagent Wait Discipline" — state which task(s) are
     in flight and how completion will be detected before yielding the turn; do not go silent — then
     recompute from step 1.
   - An `awaiting-merge` task is **never waited on** — only a human can advance it, which cannot
     happen inside this run. Do not block on it.
   - If nothing is actively in flight and nothing is ready — i.e. every remaining task is
     `awaiting-merge` and/or `blocked` — go to **Phase 10** (terminate) rather than hanging on a
     merge the epic cannot perform.

---

## Phase 7: Per-Task Drive

For each scheduled task, **dispatch the Agent tool** with
`subagent_type: dev-workflow-orchestrator` — **one** per task — to run `full-cycle` for
that task in **autonomous mode**, pinned to the `tasklist` adapter. Resolve the subagent
model per `standards.md` and pass it as the `model` parameter (the worker inherits
otherwise). Do **not** invoke the `Skill` tool yourself for full-cycle — that would run the
whole task in *this* orchestrator's context, defeating per-task isolation.

The `dev-workflow-orchestrator` worker retains the `Agent` tool, so on Claude Code
v2.1.172+ it dispatches each of full-cycle's stages as its own nested subagent (depth-3,
under the depth-5 cap) — every stage gets a fresh context even under the epic. On older
builds those stages run inline within the task's worker context (still isolated per task).
Dispatch the concurrent tasks of a round in a **single message with multiple Agent calls**.

This is epic's one genuinely concurrent, backgrounded dispatch point — apply
`standards.md` → "Subagent Wait Discipline" here: immediately after dispatching the round,
state every task dispatched (task ID, repo) and that you are waiting on their completion
notifications, then arm a bounded, visible fallback re-check rather than trusting
notification delivery alone for a run that may take a long time. Never let the turn end
with an in-flight round and no statement of what's running.

The dispatch prompt must contain, explicitly:

> Invoke Skill: `dev-workflow:full-cycle` with task ID `{task_id}`, running autonomously.
>
> **PM adapter override:** Use the `tasklist` PM adapter (the plugin's built-in
> `skills/pm-adapter/tasklist.md`). The PM "story" is task `{task_id}` in the tasklist file
> `{tasklist_path}`. Do **not** read `pm_adapter` from config; do **not** contact
> Shortcut/Jira/Linear/GitHub Issues. This instruction overrides the configured adapter.
> Branch name: `{epic-slug}-{task_id}`. The PR carries **no** `sc-` ID.
>
> **Isolated worktree required before starting** — see `skills/shared/standards.md` →
> "Workspace Isolation" for the mechanism. When this task's work reaches a nested
> `finishing-a-development-branch` invocation, pass it the cleanup instruction that skill
> actually needs: keep the worktree until a human merges this PR (epic never merges — see
> Phase 8's `awaiting-merge → done` reclamation), not the creation requirement above, which
> that teardown-only skill has no use for.
>
> full-cycle enters at writing-specs (the task exists with no spec) and proceeds through development,
> the review loop, and the test loop autonomously. **Do not merge.** Neither you nor the orchestrator
> merges this PR — a human merges it later. Report the PR's final review and test decisions back; on
> dual approval the orchestrator marks the task `awaiting-merge` and leaves the PR open.
>
> **Bug reporting:** If you discover a defect attributable to a previously-completed task, include in
> your result a `bug-report` describing the defect, the affected repo, and the suspected source task
> ID. **Do not create a task** — only report it.

The subagent returns its autonomous-mode key/value result. The orchestrator does **not** trust a
self-reported pass/fail — it re-confirms PR state authoritatively from GitHub (per full-cycle's
"Reading the Authoritative Review Decision") before marking the task `awaiting-merge`.

---

## Phase 8: Completion → Awaiting Merge (no merge)

When a task's `full-cycle` run reports review-approved **and** test-approved:

1. Re-read the PR's authoritative review and test decisions from GitHub (dispatch the decision-read
   via the Agent tool with `subagent_type: dev-workflow-pr-state-reader` so raw JSON stays out of the
   orchestrator). Both must be `APPROVED`.
2. **Do not merge.** The epic never merges. Via the `tasklist` adapter's **Update story**, set the
   task `Status` to `awaiting-merge` (which also updates its Mermaid node), and confirm the
   `PR` field records the open PR URL so it can be tracked and reported.
3. Leave the PR **open** for a human to merge. Pause this line of work: the task's repo stays in
   flight (its PR is open), so the next same-repo task waits until a human merges it. Dependent tasks
   stay paused because they require `done` (a human merge), not `awaiting-merge`.
4. Do **not** wait on the `awaiting-merge` task — a human must act, which cannot happen inside this
   run. Recompute the ready set (Phase 6); only tasks unblocked by already-`done` dependencies become
   schedulable.

### Detecting a human merge (on resume)

The `awaiting-merge → done` transition happens **only** on a later resume, never inline during a run.
When the epic is re-invoked against the slug, it reconciles each `awaiting-merge` task against GitHub
(see Resumability): for each such task, read the PR's state (`gh pr view {PR} …`)
— a **read**, never a merge.

- **PR merged** (a human merged it) → set `Status` to `done` via the adapter; its dependents become
  schedulable. **Reclaim the worktree now** — one of this pipeline's designated cleanup points (see
  `skills/shared/standards.md` → "Workspace Isolation"). Resolve the worktree live, the same way
  the developer agent's rework mode does: run `git -C <that repo's root> worktree list --porcelain` and
  match the entry whose `branch refs/heads/<name>` equals the merged PR's branch — never guess a path
  or reuse one from a sibling repo. If no matching entry is found, skip removal and note it in the
  end-of-run report rather than guessing. Otherwise remove it per `standards.md`'s "Removal must
  actually succeed against ordinary gitignored build output" procedure — a plain `git -C <that repo's
  root> worktree remove <path>` when the worktree is clean, `--force` only when everything untracked
  is confirmed gitignored, and a skip-and-report otherwise — followed by `git -C <that repo's root>
  worktree prune`. Scoping every command to that specific repo's root (not a bare `git worktree
  remove`/`prune` from an ambiguous cwd) is what keeps a multi-repo epic cleanup from ever targeting
  the wrong repo; removing a sibling repo's still-unmerged worktree is a data-loss bug, not an
  acceptable fallback.
- **PR closed without merging** → set `Status` to `blocked`, record the reason as a comment. **Reclaim
  the worktree now too** — this task's line of work is over either way, merged or not, so there's no
  reason to leave it around. Same procedure as the "PR merged" cleanup above: resolve the worktree live
  via `git -C <that repo's root> worktree list --porcelain` matched against the closed PR's branch, then
  remove it per `standards.md`'s gitignored-build-output procedure, followed by `git -C <that repo's
  root> worktree prune`.
- **PR still open** → leave it `awaiting-merge`; report it again at end of run.

---

## Phase 9: Bug Intake

When a task subagent's result contains a `bug-report`:

1. The **orchestrator** creates a new task via the `tasklist` adapter's **Create story** capability —
   `story_type: bug`, the reported `repo`, the reported description, and `depends_on` including the
   suspected **source task** (so the link to the originating task is recorded).
2. The bug task is scheduled with **priority** per Phase 6 step 4 — next for its repo, ahead of
   pending features — subject to one-in-flight-PR-per-repo.
3. When picked up, it flows through `full-cycle` identically to any other task, **including getting
   its own spec at writing-specs time**.

The orchestrator creates the bug task; the subagent only reported it. This preserves "subagents do
work, they don't create tasks" and keeps creation inside the orchestrator's consensus authority.

---

## Phase 10: Blocked-Task Handling & Termination

**Blocked tasks.** A task is `blocked` when it exhausts full-cycle's loop-safety guard (review/test
never converges), has an `awaiting-merge` PR that a human closed without merging, or otherwise cannot
proceed autonomously. Mark it `blocked` via the adapter with the outstanding feedback recorded as a
comment, then **continue with other unblocked tasks**. A block on one task never halts the whole
epic. Note that `awaiting-merge` is **not** a blocked state — it is a healthy pause waiting on a human
merge, reported separately below.

**Termination.** Stop when no task is runnable — i.e. every remaining task is `done`,
`awaiting-merge`, and/or `blocked` (nothing is actively in flight or ready). The epic terminates here
rather than hanging on merges it cannot perform. Emit an end-of-run report:

- epic slug
- per-task final status
- **Open PRs awaiting merge** — prominently list every `awaiting-merge` task with its PR URL, and
  a clear instruction: **"Merge these PRs, then re-invoke `epic` against this slug to resume —
  merged tasks advance to done and unblock their dependents."**
- PR URLs for all tasks
- any blocked tasks, each with its reason

In autonomous mode, emit the flat key/value summary per `standards.md` (`service-name`, `pm-key`,
`pr-number`, `status`, `message`, plus highest-signal extras). For an epic the `pm-key` is the epic
slug and `pr-number` may be a count or the list of open PRs. When tasks remain at `awaiting-merge`,
`status` reflects the pause (the run did not fully complete because PRs await a human merge), and
include a `message` directing the human to merge the listed PRs and re-invoke to resume — for example
an extra key such as `awaiting-merge` listing those PR URLs.

---

## Resumability

`tasklist.md` **is** the durable epic state. On re-invocation against an existing slug:

1. Read `tasklist.md`; treat every task `Status` as authoritative.
2. **Reconcile every `awaiting-merge` task against GitHub first** (per Phase 8 → "Detecting a human
   merge"). For each, read the PR state (a read, never a merge):
   - **merged** → set `Status` to `done`;
   - **closed without merging** → set `Status` to `blocked` with a recorded reason;
   - **still open** → leave it `awaiting-merge`.
   Do this before computing the ready set so a human merge performed between runs unblocks dependents.
3. Recompute the ready set from statuses (Phase 6). Do **not** recreate `done` tasks, and never merge
   any PR — only humans merge.
4. Resume scheduling. full-cycle's own per-task checkpoints under `~/.claude/dev-workflow/state/`
   remain in place for in-task resume — the tasklist is the cross-task source of truth, those
   checkpoints are the within-task one.

---

## Completion Criteria

- A new epic ran deep discovery and reached an explicit user-consensus gate before creating anything.
- `tasklist.md` was written at the configured path: a Mermaid graph ordering tasks with
  parallelization, each task's full description embedded.
- Each task was driven through `full-cycle` on the `tasklist` adapter with zero external PM calls;
  on dual approval its PR was **left open** and the task marked `awaiting-merge`. The epic merged
  nothing — a task reaches `done` only after a human merge is detected on a later resume.
- Every open PR awaiting merge is tracked in `tasklist.md` and reported in the end-of-run report with
  a merge-then-resume instruction.
- Parallelism held: cross-repo concurrent, same-repo sequential, one in-flight PR per repo (an
  awaiting-merge PR counts as in flight).
- Each task ran inside its own isolated worktree (a workspace-isolation property, not a
  parallelism one — see "Workspace Isolation" in `skills/shared/standards.md`), and every merged
  task's worktree was reclaimed at its `awaiting-merge → done` transition (Phase 8) — this is
  asserted only for tasks that actually reached `done` in this run; an epic still holding
  `awaiting-merge` tasks has not yet verified their cleanup.
- The orchestrator only orchestrated — no feature code, spec, review, or test authored in the main
  agent.
- Bug reports from subagents became orchestrator-created, priority-scheduled bug tasks.
- The run is resumable from `tasklist.md`, and a clear end-of-run report was produced.
