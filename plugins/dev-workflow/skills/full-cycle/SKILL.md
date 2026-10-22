---
name: dev-workflow:full-cycle
description: "Drives the entire dev-workflow lifecycle end to end in order — creating-stories → writing-specs → developing → reviewing-prs → testing-prs — looping back through review and testing until both pass. Use when a user wants to run the whole development pipeline for a feature, take a story 'all the way through', or says 'full cycle', 'end to end', 'run the whole workflow', or '/start full-cycle'. Resumable: re-invoke at any point and it detects current story/PR state and enters at the correct stage."
---

# Full Cycle

**Role:** Orchestrator — drive the entire dev-workflow lifecycle from idea to tested PR, in order, looping the stages that must repeat.

**SCOPE BOUNDARY:** This skill **sequences** the existing dev-workflow skills — it does not reimplement any stage. It never writes feature code, specs, or PRs directly; each stage's own skill does that. The orchestrator's only direct actions are: talking to the user during the interactive stages, launching role sessions, sending them messages, relaying the spec-writer's questions, answers, specs, and revisions between it and the user (it never writes or edits a spec itself), stopping them only with the user's explicit permission, and (in role-session mode) firing the single in-development transition (the state set by `story_states.in_development`, default "In Development") (or dispatching subagents on the fresh-dispatch path and for the pr-state-reader and spec-writer), reading PM/GitHub state to decide what runs next, and producing the end-of-run summary. This restriction holds even when the user directly asks for a mid-session fix — all implementation and fix work on the story's repos, including a request the user makes mid-session, goes to that repo's developer role session as a `fix` or `develop` message, never to the orchestrator itself or a fresh subagent while that session exists, and never handled with a direct `Edit`/`Bash`/`git` call in the orchestrator's own context. The fresh `dev-workflow:developer` Agent-tool dispatch in rework mode is limited to the fresh-dispatch fallback path. When the repo has no developer session yet, the by-name lookup then launch rule (`role-sessions.md` → "Lookup before every launch") supplies one. And whenever any new commit lands on an open PR outside the formal Review Loop / Test Loop — including one made by a mid-session developer fix — a fresh review and test round against the new HEAD (per the Stage — reviewing-prs and Stage — testing-prs procedures below) is mandatory before the PR is reported done; if that fresh pass itself comes back changes-requested, it continues into the existing Review Loop / Test Loop machinery — including the Loop Safety Guard's cycle cap — rather than looping ad-hoc outside it (this closes the gap where a fix commit landed on an already-approved-and-tested PR with no re-verification before the pipeline reported it done). It **never merges the PR** — a human does that.

## Arguments: $ARGUMENTS

The skill accepts one of:

- **A PM story ID** (e.g., `sc-1043` or `1043`) — resume an existing story; detect its current state and enter the pipeline at the correct stage.
- **A free-form feature description** — begin a brand-new cycle at creating-stories.
- **No argument** — begin a brand-new cycle at creating-stories (creating-stories will prompt for the description).

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Setup.

Read `skills/shared/repo-discovery.md` — repo discovery procedure used by the stages this skill sequences.

Read `skills/shared/context-compaction.md` — checkpoint protocol, sentinel format, and non-tmux fallback wording used throughout this skill.

Read `skills/shared/role-sessions.md` — the role-session launch contract, message protocol, preflight, and liveness rules used by the spec-writer, developer, reviewer, and tester stages.

Read the CLAUDE.md file in this repository before starting.

---

## Setup: Load Adapters

1. Read `~/.claude/dev-workflow/config.json`
2. Note `pm_adapter` and `notes_adapter` values
3. Note the `models` section (if present) — used for stage-dispatch model resolution throughout this skill (see "Subagent model selection" below and "Subagent Model Selection" in `standards.md`)
4. Load PM adapter per procedure in `skills/shared/adapter-loading.md`
5. Load notes adapter per procedure in `skills/shared/adapter-loading.md`

Parse `$ARGUMENTS`:

- Matches a story ID pattern (`sc-NNNNN` or bare `NNNNN`) → treat as a **story ID**; go to Resume / Entry Detection.
- Non-empty and not a story ID → treat as a **feature description**; there is no story yet, so the entry stage is creating-stories (carry the description into that stage).
- Empty → no story yet; the entry stage is creating-stories.

---

## Interactive vs. Subagent Stages

This is the central design decision for this skill (resolved with the user — see the sc-1043 spec):

| Stage | Where it runs | Why |
|-------|---------------|-----|
| creating-stories | **Main orchestrator** (interactive) | Interviews the user; needs to ask questions. |
| writing-specs | **Role session** (spec-writer, one per story; fresh relay dispatch on the fallback path) | Writes the specs and relays its questions and finished specs to the user through the orchestrator, which gates development on the user's explicit spec approval. |
| developing | **Role session** (developer; fresh subagent on the fallback path) | Non-interactive heavy implementation. |
| reviewing-prs | **Role session** (reviewer, per PR; fresh subagent on the fallback path) | Non-interactive review. |
| testing-prs | **Role session** (tester, per PR; fresh subagent on the fallback path) | Non-interactive functional testing. |
| loop-back fixes (addressing-pr-comments) | **Developer role session** in rework mode (fresh developer subagent on the fallback path) | The developer that wrote the code fixes it with context, on the same PR. |

A role session or dispatched subagent has no tool to ask the user. The implementation/review/test stages (developing, reviewing-prs, testing-prs) run their underlying skill in **autonomous mode** and return a single-line key/value result — as the body of a `result` message from a role session, or as the final response of a fresh dispatch (see "Autonomous mode" and "Output Mode Detection" in `standards.md`); the orchestrator reads that result only as a hint. addressing-pr-comments does **not** emit a key/value result — it implements fixes and posts PR replies — so a fix request's `result` is a short plain-text confirmation and the orchestrator never depends on it; the key/value rule applies only to `develop`, `review`, and `test` results. For every review/test outcome the orchestrator **re-confirms the authoritative state from GitHub and the PM story** (see Reading the Authoritative Review Decision) rather than trusting any subagent self-report.

**PR-branch checkout for developer rework.** reviewing-prs and testing-prs take a PR number argument, but `dev-workflow:addressing-pr-comments` resolves the PR from the *current branch* (`gh pr status`) — a developer session or fresh dispatch handling a fix request is not necessarily checked out on that branch. Because implementation/fix work runs inside an isolated worktree (`skills/shared/standards.md` → "Workspace Isolation"), that branch may already be checked out in a worktree elsewhere — a plain `gh pr checkout {PR_NUMBER}` fails outright when it is. The `dev-workflow:developer` agent's rework mode handles this: it locates the branch's worktree via `git worktree list --porcelain` and `cd`s there, falling back to `gh pr checkout {PR_NUMBER}` only when no worktree holds the branch. Pass the explicit PR number in the request — the developer resolves its own worktree live, so it never has to guess or ask.

## Role Sessions

In **standalone** full-cycle, the spec-writer, developer, reviewer, and tester run as persistent role sessions per `skills/shared/role-sessions.md`, which owns the launch contract, naming, message envelope, routing, liveness, teardown, and resume rules. This skill owns *when* each of those happens:

- **Background jobs.** A standalone run, including one inside a background job, always runs the preflight.
- **Preflight (once per run).** Before the first role-session launch, wherever in the run that is, execute the preflight in `role-sessions.md` (version floor, agent view, background launch, ping handshake); each check is run, never inferred, and a check fails only on an observed failure. The handshake is the `ping` of whichever session is launched first, whatever its role (the spec-writer on a normal run, the developer when resuming at entry row 3 or 4, the reviewer or tester when resuming at entry rows 8-9). Any failed check: announce the failed check with the observed output and the remedy once, report any sessions already launched by name and ask before stopping them, and run the remaining work on the fresh-dispatch path; no stage runs on both paths at once. A run that moves from fresh dispatch to role sessions part-way follows `role-sessions.md` → "Mid-run mode change".
- **Lazy launch.** The story's spec-writer session (named `{story-id}-spec-writer`, one per story) launches when the story enters writing-specs, after creating-stories. Each repo's developer session (named `{story-id}-developer-{repo}`) launches when that repo's first `develop` is sent, or, when a `fix` request is needed and that repo has no developer session (resume at entry rows 6-7), at that point. Every launch, at first use, on resume, or mid-run, is preceded by the by-name lookup in `role-sessions.md` → "Lookup before every launch", and the lookup's outcome (reuse, respawn, report, or launch) replaces the launch. A PR's reviewer session launches at that PR's first review, and its tester session at that PR's first test. Every newly launched session (spec-writer, developer, reviewer, tester) is pinged and must answer `ready` before its first task message. Names, model, and settings follow the launch contract.
- **Fresh-dispatch path (fallback).** The rest of this skill describes both paths; where a step says "send a message to the session", the fresh-dispatch path instead dispatches the same worker with the Agent tool, as described under "How to dispatch".
- **Hand-off discipline.** Every message sent to a role session follows the Communication contract in `role-sessions.md` (R5 to R8). In the same turn as the send, name what is in flight (worker, message type, round, and the replies expected: `ready`, or `ack` then a terminal reply, or a `result` of `shutdown`) and arm a watcher: a visible `Monitor` first, then a scheduled wake-up, then, if neither is available, tell the user which request is outstanding. Complete each step of a multi-step hand-off in the turn its trigger arrives (launch then `ping`; `ready` then the task message; a terminal reply then the GitHub read then the next send). Never end a turn after a launch with no `ping` sent, after `ready` with no task sent, after a send with nothing in flight or nothing armed, or after a terminal reply with no next action. Two minutes with no `ack` triggers the missing-ack check in R7.
- **Cost.** Each role session consumes subscription usage like an interactive session; announce the sessions launched (one spec-writer for the story, one developer per repo, and a reviewer and tester per PR). They are stopped only with the user's permission.

**How to dispatch on the fresh-dispatch path, and for the always-fresh workers (mandatory — per `standards.md` → "Subagent Dispatch"):**

Every subagent stage dispatched below runs in a **fresh, isolated context**. To get that, you MUST
dispatch it with the **Agent tool**, passing the stage's dedicated `subagent_type`. Do
**not** invoke the `Skill` tool yourself for a downstream stage — `Skill` loads content
into *your* context (no subagent), which is exactly the "everything runs in one agent"
failure this design prevents. Each worker's body invokes the matching
`dev-workflow:{stage}` skill autonomously, so stage behavior is unchanged.

**Every stage dispatch below is sequential — you cannot proceed until it returns.**
Dispatch it to block for its result per `standards.md` → "Subagent Wait Discipline"; do
not background it and end your turn to wait on a notification. This applies to every
"Dispatch the Agent tool" instruction in this skill, including inside the Review Loop and
Test Loop.

**Base root for wrapper agents.** The plugin root is the parent of the parent of this skill's base directory (the "Base directory for this skill" line shown when the skill loads). End every Agent-tool dispatch prompt with the line `base-root: <that absolute path>`. A wrapper plugin's agents replace the base agents by `subagent_type` and read base agent sections from `<root>/agents/<role>.md`; base agents ignore the line. A fresh dispatch gets no other source for the root, because the session-start context line that carries it speaks only to role sessions. See `skills/shared/local-mode.md` → "Base Root for Wrapper Agents".

**Subagent type + model per dispatch.** Resolve the model with the resolution order
`models.stages.<stage-key>` → `models.<task-type>` → built-in default, then pass it as the
`model` parameter on the Agent call (it overrides the worker's frontmatter default):

| Stage / dispatch | `subagent_type` | `stages` key | Task type | Default model |
|-----------------|-----------------|--------------|-----------|---------------|
| entry-detection | `dev-workflow:pr-state-reader` | `entry-detection` | `implementation` | `sonnet` |
| writing-specs (both paths) | `dev-workflow:spec-writer` | `writing-specs` | `reasoning` | `opus` |
| developing | `dev-workflow:developer` | `developing` | `implementation` | `sonnet` |
| developer rework (fresh dispatch) | `dev-workflow:developer` | `addressing-pr-comments` | `implementation` | `sonnet` |
| pr-number-read | `dev-workflow:pr-state-reader` | `pr-number-read` | `implementation` | `sonnet` |
| reviewing-prs | `dev-workflow:reviewer` | `reviewing-prs` | `review` | `opus` |
| testing-prs | `dev-workflow:tester` | `testing-prs` | `review` | `opus` |
| decision-read | `dev-workflow:pr-state-reader` | `decision-read` | `implementation` | `sonnet` |
| handoff-check | `dev-workflow:pr-state-reader` | `handoff-check` | `implementation` | `sonnet` |
| verify (fresh dispatch) | `dev-workflow:developer` | `verify` | `implementation` | `sonnet` |

In role-session mode the spec-writer session's model is fixed at launch from `models.stages.writing-specs` → `models.reasoning` → default `opus`, and each developer session's model is fixed at launch from `models.stages.developing`; fix requests reuse it. The `models.stages.addressing-pr-comments` key applies only to a fresh developer rework dispatch (fallback path) and is ignored in role-session mode. A fresh rework dispatch resolves its model from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`. A fresh verify dispatch resolves from `models.stages.verify` → `models.implementation` → default `sonnet`; in role-session mode a verify request reuses the developer session. The `handoff-check` dispatch resolves its model from `models.stages.handoff-check` → `models.implementation` → default `sonnet`.

**Output mode (per `standards.md` → "Output Mode Detection").** Determine the mode at startup. The orchestrator is **interactive by nature** when it must run creating-stories or writing-specs, because creating-stories interviews the user and writing-specs relays the spec-writer's questions and gates development on the user's spec approval. If the skill is running non-interactively (no way to ask the user) AND the detected entry stage is creating-stories or writing-specs, STOP and surface that the pipeline needs an interactive session to define/approve the spec. When resuming at developing or later, no further interaction is required and the run may complete autonomously, emitting the flat key/value summary at Termination.

---

## Resume / Entry Detection

The skill is resumable: re-invoking it at any time must enter the pipeline at the correct stage. For a multi-repo story, each linked PR is evaluated independently per repo — determine each repo's entry stage from PM story state, whether that repo's PR exists, its review decision, and testing-prs's tracking labels. Evaluate top to bottom and enter each repo at the **first** matching stage. The orchestrator resumes each unfinished repo at its own detected stage and skips any repo already at `finished`.

**Process Fidelity applies here (see `standards.md` → "Process Fidelity").** Entry detection selects where the pipeline resumes — it never skips or reorders work: entering at a non-default stage, skipping a stage (including writing-specs or its User Approval Gate), or running stages out of the documented order beyond what the table below dictates requires asking the user for explicit permission first, never a silent inference from PR/story state.

**Why the row order matters:** when testing-prs fails it submits a `REQUEST_CHANGES` review, which drives the PR's aggregate `reviewDecision` to `CHANGES_REQUESTED` — the *same* value reviewing-prs produces when review fails. The PR review decision alone therefore cannot tell a failed review from a failed test. The `tests-failing` / `tested-in-dev` labels (set by testing-prs — see the testing-prs label requirement) are the disambiguator, so the label rows are evaluated **before** the generic `reviewDecision == CHANGES_REQUESTED` row.

| # | Observed state | Entry stage |
|---|----------------|-------------|
| 1 | No story yet (no story ID; feature description or empty argument) | **creating-stories** |
| 2 | Story exists but no spec is linked, or story state is "In Spec" / earlier | **writing-specs** (reuse or respawn the story's spec-writer by name, then pick the next message from the checkpoint per "Stage — writing-specs") |
| 3 | Spec present / story "Ready for Dev" and **no** linked PR | **developing** |
| 4 | A repo's linked PR is open and a draft | **verify** — a developer `mode: verify` request for that repo (evaluated ahead of every label and review-decision row) |
| 5 | A repo's linked PR carries the `tested-in-dev` label and no `tests-failing` label | **finished** — testing passed for that repo; report and skip it |
| 6 | A repo's linked PR carries the `tests-failing` label | **developer fix → testing-prs** (test loop) for that repo |
| 7 | A repo's linked PR `reviewDecision` is `CHANGES_REQUESTED` (and no `tests-failing` label) | **developer fix → reviewing-prs** (review loop) for that repo |
| 8 | A repo's linked PR is review-approved with no `tested-in-dev`/`tests-failing` label | **testing-prs** for that repo |
| 9 | A repo's linked PR exists but has no review decision yet (`REVIEW_REQUIRED`/null) | **reviewing-prs** for that repo |

**Why the draft row is row 4:** a PR returned to draft (an interrupted deploy wait, a failed re-pin gate; `skills/shared/pre-release-pin.md` → "Draft-PR Resume Routing" decides whether a draft PR that carries a pre-release pin marker goes back as a re-pin or a verify request) can still carry stale labels or a stale review, so the draft row precedes the `tested-in-dev` row. A draft PR goes back to the developer, not the reviewer: reviewing-prs stops on drafts without recording a decision.

**Local Mode and approval-only repos.** A repo with no push access or no way to approve its own PR does not follow the GitHub-only signals below. Read "Local Mode and Approval-Only Repos" for the push read, the notes-based resume order and the entry stop-and-ask before evaluating the table for such a repo.

How to gather each signal, evaluated independently per repo:

1. **Story state:** fetch the story via the PM adapter; read its workflow state. Treat it as a coarse, informational signal only — the plugin's built-in stages do **not** set a "Dev Complete" (or equivalent terminal) state, so resume detection must not depend on one. (A particular PM adapter may add such a transition; if present it corroborates the label, but the label is authoritative.)
2. **Linked PR (per repo):** use the PM adapter's "Finding PRs linked to a story" instructions to find every linked PR, then resolve each PR's repo/service name from its GitHub owner/repo, matching `repo-discovery.md`'s naming convention. If none is linked there, fall back to a branch-name match: `gh pr list --state all --limit 1000 --json number,url,state,headRefName`, keeping PRs whose head branch contains the delimited story token.
3. **Review decision:** `gh pr view {PR_NUMBER} --json reviewDecision` for the aggregate, or the latest review's `state` (see Reading the Authoritative Review Decision below).
4. **Test outcome:** testing-prs applies a `tested-in-dev` (passed) or `tests-failing` (failed) label on every run (see "testing-prs label requirement" below). These labels — not review recency or `reviewDecision` — are the durable signal that distinguishes the test stage from the review stage. If a review-approved PR carries **neither** label, treat that repo as **not yet tested** (row 8) and state that assumption to the user.

### Entry detection subagent

Gather the signals (story state, linked PRs, review decision, test labels) by
**dispatching the Agent tool** with `subagent_type: dev-workflow:pr-state-reader`
(model: resolved from `models.stages.entry-detection` → `models.implementation` → default
`sonnet`). Do not run this read inline. The dispatch prompt is:

> Fetch story {story-id} via the Shortcut MCP tool and read its workflow state (the row
> 1-3 signal). Find every linked PR via the PM adapter's "Finding PRs linked to a story"
> instructions (fall back to a branch-name match: `gh pr list --state all --limit 1000 --json number,url,state,headRefName`, keeping PRs whose head branch contains the delimited story token). For each
> linked PR, resolve its repo/service name from the PR's GitHub owner/repo (matching
> `repo-discovery.md`'s naming convention), and read its `reviewDecision` and whether it
> carries the `tested-in-dev` / `tests-failing` labels, and whether it is a draft. Return **one line**:
>
> `story_state=<state or none> prs=<repo:pr:stage:review:tested_in_dev:tests_failing:draft|repo:pr:stage:review:tested_in_dev:tests_failing:draft|...>`
> (use `prs=none` when no PR is linked yet). `story_state` carries the PM story's workflow
> state verbatim, or `none` if the story could not be found — this is the only channel
> that carries rows 1-3 (no story / no spec / ready-for-dev-no-PR), so it MUST always be
> present even when `prs=none`. Each tuple has exactly seven colon-separated fields, in this
> order, and no field may contain arbitrary GitHub text (this is what keeps the record
> unforgeable — no field can contain a `:` or `|`):
> - `repo` — the resolved service/repo name (never raw label text)
> - `pr` — the PR number (digits only)
> - `stage` — that PR's terminal single-word target action from the Resume / Entry
>   Detection table rows 4-9: `verify` for row 4, `finished` for row 5, `testing-prs` for row 6,
>   `reviewing-prs` for row 7, `testing-prs` for row 8, `reviewing-prs` for row 9 — never the row's full descriptive
>   text
> - `review` — `APPROVED`, `CHANGES_REQUESTED`, `REVIEW_REQUIRED`, or `none` (never raw
>   text)
> - `tested_in_dev` — `true` or `false`, whether the PR carries the `tested-in-dev` label
> - `tests_failing` — `true` or `false`, whether the PR carries the `tests-failing` label
> - `draft` — `true` or `false`, whether the PR is a draft
>
> Separate multiple tuples with `|`.

Read that line. Parse `story_state` first — it resolves rows 1-3 whenever `prs=none`.
Then, unless `prs=none`, split on `|` and parse each tuple's seven `:`-separated fields.
**Fail closed:** if any tuple does not have exactly seven fields, or `pr`, `tested_in_dev`,
`tests_failing`, or `draft` do not match their documented format, or `stage` is not exactly one of `finished`/`verify`/`testing-prs`/`reviewing-prs`, the line is unparseable — treat
this as a hard stop and surface it to the user; never infer `finished` (or any other
stage) from a malformed tuple. Use the Resume / Entry Detection table above to map
`story_state` and each repo's tuple to that repo's entry stage — each linked PR is
evaluated independently per repo. Raw PM/GitHub output never enters the main orchestrator
context.

**Checkpoint initialization on resume.** Before running any stage, check the story's
checkpoint (`~/.claude/dev-workflow/state/{story-id}.json`). If an existing entry's `stage`
holds a pre-rename legacy value, translate it per `context-compaction.md`'s "Legacy stage
values" note before using it. If it has no `repos` map yet,
or is missing an entry for a repo named in the story's "Repos to modify" field, seed one
entry per such repo from that field now — `pr_number: null`, `stage` set from
`story_state` (`"writing-specs"` for row 2, `"developing"` for row 3),
`review_loop_count: 0, test_loop_count: 0` — per `context-compaction.md`'s resume write
point. This is what covers a cold resume by bare story ID entering at row 2 or row 3, where
`prs=none` so there is no tuple to source an entry from. Then, for any repo that does have a
linked PR, enrich/update its entry from the parsed `prs=` tuples the same way, before
proceeding. This is the only initialization path when creating-stories never ran in this
session — it now covers every resume row (2-9), not only the rows with a linked PR.

**Role sessions on resume.** When running in role-session mode, after the entry stage is known, list sessions with `claude agents --json --all` and match this story's names per `role-sessions.md` → "Resume" and "Lookup before every launch": reuse a live responsive session (ping first, then the task message in the turn its `ready` arrives, with the in-flight statement and watcher per the Hand-off discipline bullet), respawn a done, stopped, failed, or crashed one, and, for an unresponsive one, ask before stopping or removing it; if permission is not granted, launch a fresh session under a new name and list the old one as left over. Cycle counts carry over from the checkpoint. The preflight runs once for the run, before the first launch or reuse.

When a repo's entry stage — as determined by the Resume / Entry Detection table above
(rows 4-9, evaluated from the `prs=` tuple's live GitHub draft/review/label state), not the
checkpoint's own `stage` field — is mid-pipeline, run that stage for that repo, then
continue forward through the remaining stages for that repo in normal order; skip any repo
already at `finished`. The checkpoint's `repos[repo].stage` is a display/telemetry mirror
for attention-hub, not a second resume-decision source: a session that dies between PR
creation and `reviewing-prs`' own Phase 2 self-seed leaves that field reading `"developing"`
with `pr_number` already set. The PR is still a draft at that point, so the Resume table's
row 4 evaluation against the PR's actual GitHub state — never the checkpoint field — resumes
that repo at `verify`, a developer `mode: verify` request, not `reviewing-prs`. Once the
developer's gate has marked the PR ready, the same evaluation resumes it at `reviewing-prs`. State each repo's detected entry stage to the user
before proceeding — not a single story-wide stage.

Rows 1-3 resolve the same way, from `story_state` rather than the `prs=` tuple (see "How to
gather each signal" above) — they are not an exception to the "not the checkpoint field"
rule above; the checkpoint's `repos[repo].stage` is never a resume-decision source for any
row.

### testing-prs label requirement

This skill depends on testing-prs labeling its outcome. testing-prs is updated in this change to **always** add `tested-in-dev` on a passing run and `tests-failing` on a failing run (previously optional). If you run full-cycle against a build of testing-prs that omits the labels, cold resume cannot distinguish "approved, not yet tested" from "approved and passed" — in that case it defaults to row 8 (re-test) and announces the re-test, which is safe but may repeat a passing test.

---

## Stage — creating-stories (interactive, main orchestrator)

Run only when there is no story yet.

This stage satisfies the Story Creation Gate in `skills/shared/standards.md` only because the user explicitly invoked full-cycle. If full-cycle itself was auto-triggered, creating-stories's invocation-provenance pre-gate must still fire its permission ask.

> Invoke Skill: `dev-workflow:creating-stories`
>
> Pass the feature description from `$ARGUMENTS` if one was provided.

Drive the interview to completion in the main agent so it can ask the user questions. Capture the resulting **story ID** and carry it forward. Proceed to writing-specs.

---

## Stage — writing-specs (spec-writer role session, user-gated)

The orchestrator never runs `dev-workflow:writing-specs` itself and never writes or edits a spec. Once the story exists, the spec stage runs in the story's **spec-writer** worker; the orchestrator launches it, messages it, monitors it, and relays between it and the user. The user's approval gate is unchanged: nothing advances to developing until the user explicitly approves the spec(s).

**Role-session path.** First run the by-name lookup (`role-sessions.md` → "Lookup before every launch"), then reuse, respawn, or launch the story's spec-writer (`dev-workflow:spec-writer`, role `spec-writer`, named `{story-id}-spec-writer`, model resolved from `models.stages.writing-specs` → `models.reasoning` → default `opus`) per `role-sessions.md`, with the orchestrator's own working directory (the workspace root that writing-specs' repo discovery needs). If this is the run's first launch, run the preflight first. `ping` it, and send a `spec` message (repo field `all`, `PR#none`, carrying the story ID) in the turn its `ready` arrives. Every send follows R5 to R8 (in-flight statement, watcher, same-turn hand-offs). Then loop on its replies:

1. **`blocked` with `kind: questions`.** Route on the first body line of every spec-writer `blocked`. Ask the user the questions verbatim, and send their verbatim reply as an `answer` message, labelled as the user's words. Add every path on the `written:` line to the running log's "written this run" list.
2. **Specs `result`.** Add the listed spec paths to the "written this run" list. Present the design summary and a table of repo and spec path, and ask the same two options writing-specs uses: approve all, or request changes to specific specs.
3. **Change requests.** Send a `revise` message to the **same** spec-writer session with the user's words verbatim, then handle its next reply as above.
4. **Approval.** On the user's explicit approval, write the checkpoint immediately with `approval_text` (the user's literal approval message, verbatim) and `approval_timestamp` (UTC ISO-8601 with a `Z` suffix, for example `2026-10-16T18:04:11Z`, so the spec-writer's finalize check can compare it against `spec_written_at`) per `skills/shared/context-compaction.md`, then send `finalize` carrying the spec paths.
5. **`finalize` `result`.** Run the recorded-approval hard gate below, then move to developing. A `blocked` with `kind: stop` (from any request, including `finalize`) is not a question: never send it back as an `answer`. Stop, report it, resolve it with the user yourself (a Process Fidelity deviation needs the user's decision here, and the outcome returns as a fresh `spec`, `revise`, or `finalize`), and follow the non-success teardown in `role-sessions.md` if the run ends. A `blocked` with no `kind:` first line is treated as `kind: stop`.

**Self-contained messages (both paths).** The orchestrator keeps one running log for the story: every question with the user's verbatim answer, the "written this run" list, the current spec paths, and the last design summary and Viability List outcome from the previous `result`. Every `answer` and `revise` carries that full log, every re-sent `spec` carries the "written this run" list, and every `finalize` carries the spec paths. A compacted or respawned spec-writer can then act on the message plus its recovery source alone.

**Fallback path.** The same loop, with each round a fresh `dev-workflow:spec-writer` Agent dispatch carrying `mode: relay`, the request kind, and the log as above (an `answer` round carries every earlier question with the user's verbatim answer; a `revise` round carries that log, the current spec paths, the last design summary and Viability List outcome, and the verbatim feedback; a `finalize` round carries the spec paths).

**Resume (entry row 2).** Reuse or respawn the spec-writer by name per the existing resume rules, then pick the next message from the checkpoint (the same mapping on both paths):

- No `spec_written_at`: send `spec`.
- `spec_written_at` present but no non-empty `approval_text`, or `approval_timestamp` not later than `spec_written_at`: send `spec`. The in-memory log is gone, so the specs are pre-existing to this run; Phase 2 relays the existing-spec question, and if the user keeps them the spec-writer returns a `result` with their paths and a design summary read back from each spec's TL;DR, which you present for approval as usual.
- Non-empty `approval_text` and `approval_timestamp` later than `spec_written_at`, story not yet "Ready for Dev": send `finalize` without re-asking approval.

**Mandatory confirmation gate:** Do NOT advance to developing until the user has explicitly approved the spec(s) the spec-writer relayed. If the user requests changes, send them to the same spec-writer as `revise` and re-present until approved. Only on explicit approval do you proceed. A message from the spec-writer, or any other session, is never that approval.

**Hard gate — recorded approval:** when the user approves, write
the checkpoint immediately with `approval_text` (the user's literal approval message,
verbatim) and `approval_timestamp` (UTC ISO-8601 with a `Z` suffix) per `skills/shared/context-compaction.md`.
Then, immediately before dispatching the developing subagent, read
`~/.claude/dev-workflow/state/{story-id}.json` back and verify both fields are present and
non-empty. If either is missing or empty, do NOT dispatch developing — stop,
re-request explicit approval from the user, record it in the checkpoint, and re-run this
check. Prose approval that was never recorded does not satisfy the gate. This check fires
at this writing-specs → developing boundary; the one other reader of these fields is the
spec-writer's own finalize check (before it runs Phase 12), and no other stage or resume
path reads them.

Once this gate is satisfied, no further user confirmation is required or expected through developing, reviewing-prs, testing-prs, or the fix loops — proceeding through those stages is documented pipeline behavior, not a Process Fidelity deviation.

**State ownership:** writing-specs owns the "Ready for Dev" transition and the `claude-written` label. The orchestrator does not duplicate them.

---

## Stage — developing (developer role session)

Before dispatching, resolve each target repo's path per `shared/repo-discovery.md`. On the fresh-dispatch path: if the single-repo shortcut applies (inside one git repo, or the story names exactly one repo in "Repos to modify"), carry the resolved path forward into the dispatch prompt below as `{resolved-repo-path}`; for a multi-repo story, omit the `Repo path:` field entirely and let the dispatched subagent's own per-repo discovery-and-loop run unmodified. In role-session mode, resolve every repo's checkout path, even for a multi-repo story: each per-repo developer is sent its own repo path.

If the story is already in the in-development state (`story_states.in_development`, default "In Development"; for example when resuming into this stage), do not fire the transition again.

**Pre-release pins.** Before a repo's `develop` is sent, apply the gate in `skills/shared/pre-release-pin.md` → "Cross-Repo In-Flight Dependency" for any dependency whose PR has not merged. A repo in Local Mode or approval-only mode follows "Local Mode and Approval-Only Repos" for this stage: skip the PR-number read and the hand-off check for Local Mode repos.

**In role-session mode, give each repo one developer session: run the by-name lookup first (`role-sessions.md` → "Lookup before every launch"), then reuse, respawn, or launch it** (`dev-workflow:developer`, role `developer`, named with the repo, model resolved from `models.stages.developing` → `models.implementation` → default `sonnet`) per `role-sessions.md`, each with that repo's checkout as its working directory. Compute dependency levels from each repo's spec using the same producer-to-consumer rule `developing` uses for multi-repo stories. Launch the first repo's session and `ping` it (the run's preflight normally already passed at the spec-writer launch; if this is the run's first launch, the preflight runs here); once the first developer's handshake succeeds, fire the PM adapter's in-development transition (`story_states.in_development`, default "In Development") exactly once for the story (a failed preflight keeps the run on fresh dispatch, so it never fires twice; after a mid-run switch the story is already in the in-development state and the transition is not fired again), then send `develop` messages. Every other repo's developer session gets its own `ping` and answers `ready` before its first `develop` (see `role-sessions.md` "Per-session handshake"); send the `develop` in the turn the `ready` arrives. After each send, name what is in flight and arm the watcher per the Hand-off discipline bullet, one `Monitor` covering every `develop` sent in the same turn. Each `develop` names exactly one repo, carries that repo's path, and has `transition: fired` as its first line after the envelope. Send level-0 repos' `develop` concurrently; send each later level's `develop` only after every PR of the prior level is open (confirmed through the PR-number read below). If a later repo's developer fails to launch, stop and report; never fall back to fresh dispatch for it. The message carries the input below. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow:developer` (same model resolution). The worker's body already invokes `dev-workflow:developing` autonomously; your request supplies only the variable inputs. Worktree ISOLATION is unconditional for this developer dispatch — it always works inside an isolated worktree, never the primary checkout (see `skills/shared/standards.md` → "Workspace Isolation"). Worktree CREATION, though, is conditional: per standards.md's live-lookup rule, the dispatched subagent creates a worktree only when `git worktree list --porcelain` finds no existing match for the target branch; if a match exists (e.g. a resumed run), it reuses that one and does not create a second. This does not extend to every stage of a full-cycle run: developer rework in the Review Loop below *locates* an existing worktree or falls back to a plain checkout (see "PR-branch checkout for developer rework" above), it does not create one unconditionally.

> Story ID: `{story-id}`. Repo path: `{resolved-repo-path}`. Worktree isolation is required for this task — proceed without asking; if baseline tests still fail after the dependency-folder step in Workspace Isolation, report the failure in your result and stop rather than asking whether to proceed. Run autonomously.

Fresh-dispatch path: include the `Repo path:` field only when the single-repo shortcut applies, as
resolved above. For a multi-repo story, omit `Repo path:` entirely and let the dispatched subagent's own per-repo discovery-and-loop resolve each repo. In role-session mode, every `develop` carries its own repo's `Repo path:`.

After each developer's `result` message arrives (or the dispatched subagent returns), apply the wait discipline in `role-sessions.md` → "Waiting and liveness" while waiting; the `result` is the terminal reply, so in the same turn it arrives, read the PR number and state from GitHub and take the next action. The developer branches, implements with TDD, and opens the PR (one PR per repo for a multi-repo
story). It resolves its own worktree per repo, live, per `skills/shared/standards.md` →
"Workspace Isolation" — reusing a matching one found via `git worktree list --porcelain` or
creating a new one — so nothing about the worktree path is passed in the dispatch prompt, recorded
in the checkpoint, or returned in the subagent's result.

After it reports, **dispatch the Agent tool** with `subagent_type: dev-workflow:pr-state-reader` (model: resolved from `models.stages.pr-number-read` → `models.implementation` → default `sonnet`) to resolve the **PR number(s)** authoritatively — do not resolve this inline. Dispatch prompt:

> Find any linked PRs for story `{story_id}` via the PM adapter's "Finding PRs linked to a story"
> instructions (the subagent attaches the PR to the story on creation), falling back to
> a branch-name match: `gh pr list --state all --limit 1000 --json number,url,state,headRefName`, keeping PRs whose head branch contains the delimited story token. For each linked PR, resolve its
> repo/service name from the PR's GitHub owner/repo (matching `repo-discovery.md`'s naming
> convention). Return **one line**:
>
> `pr_numbers=<repo:pr,repo:pr,...>` (or `pr_numbers=none` when no PR was found)

Read that one line and parse each `repo:pr` pair — the repo name is what identifies each PR
to dispatch reviewing-prs against next (see below), not a checkpoint write: developing's own
PR Creation Requirements self-seed already recorded that repo's `pr_number` in the
checkpoint before this subagent was even dispatched. Do not rely solely on the subagent's
self-reported PR number, and never let raw PM/GitHub JSON enter the main orchestrator
context.

**State ownership:** developing owns the in-development transition (`story_states.in_development`, default "In Development"), except in role-session mode, where the orchestrator fires it once per story as described above.

Then run the **hand-off check** below for each resulting PR, and proceed to reviewing-prs only for a PR that passes it.

### Hand-off check (before every review or test send)

The developer's gate (`skills/shared/dev-deploy.md`) leaves the PR a draft until dev-verification evidence for the current HEAD is posted. A fix round's `result` is plain text that this skill never depends on, except a result starting `head mismatch:` or `push rejected:`, the only fix results this skill reads, which route per "Local Mode and Approval-Only Repos". A blocked fix would otherwise go to the reviewer, bounce as stale, and burn loop cycles. After **every** developer `develop` or `fix` round, including inside the Review Loop and Test Loop, and on resume, and before sending any `review` or `test` message, **dispatch the Agent tool** with `subagent_type: dev-workflow:pr-state-reader` (model: resolved from `models.stages.handoff-check` → `models.implementation` → default `sonnet`). Dispatch prompt:

> For repo `{owner}/{repo}` PR `{PR_NUMBER}` (use exactly this repo for the exemption lookup and pass `-R {owner}/{repo}` on every `gh` call, never the working directory's repo), read whether the PR is a draft and run the `skills/shared/dev-deploy.md` evidence validation for the PR's current HEAD (read-only API calls only). Return **one line**:
>
> `draft=<true|false> evidence=<valid|missing|stale|invalid|exempt|not-applicable>`

Read that one line; never let raw PM/GitHub JSON enter the main orchestrator context.

- `draft=true`, or `evidence` of `missing`, `stale`, or `invalid`: **do not** send to review or test. Surface it to the user as a developer block (naming the PR, the draft state, and the evidence outcome) and stop that repo's pipeline until the user decides (an autonomous run stops and reports the block in its result instead of asking). The round does **not** count against the Loop Safety Guard.
- `evidence` of `valid`, `exempt`, or `not-applicable` with `draft=false`: proceed. `exempt` means the repo is in `ci_gate_exempt_repos` or `deploy_gate_exempt_repos`. `not-applicable` is returned only by a wrapper's own pr-state-reader for a repo where its rules make evidence impossible; such a PR is opened or owned by the user, who leaves it ready (not a draft), because no evidence can ever mark it ready. A draft PR still blocks and is surfaced to the user.

A developer `blocked` reply, or a fresh-dispatch `status: error` result whose `message` starts `dev-verification blocked:`, is handled the same way: stop that repo's pipeline and ask the user (an autonomous run stops and reports).

**Verify request.** To send a draft PR back to the developer (entry row 4, or after the user resolves a block), send a `develop` message to that repo's developer session whose first body line is `mode: verify`, then the PR number; it carries no `transition: fired` line and fires no transition. On the fresh-dispatch path, dispatch `subagent_type: dev-workflow:developer` (model: resolved from `models.stages.verify` → `models.implementation` → default `sonnet`) with the same body. The result is the same flat key/value `develop` result, parsed with the existing develop rules; run the hand-off check again afterwards.

---

## Stage — reviewing-prs (reviewer role session)

For each PR produced by developing:

**In role-session mode, at that PR's first review run the by-name lookup (`role-sessions.md` → "Lookup before every launch"), then reuse, respawn, or launch that PR's reviewer session** (`dev-workflow:reviewer`, role `reviewer`, model resolved from `models.stages.reviewing-prs` → `models.review` → default `opus`) and send it a `review` message in the same turn as its `ready` (then name what is in flight and arm the watcher per the Hand-off discipline bullet); later rounds go to the same session. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow:reviewer` (same model resolution). The worker's body already invokes `dev-workflow:reviewing-prs` autonomously and already carries the evidence gate and the reuse-or-fresh dev build CI rule; your request supplies only:

> PR number: `{PR_NUMBER}`. Run autonomously.
>
> (Reminder, also enforced by the worker: for a non-exempt repo you MUST first validate the developer's dev-verification evidence for the PR's current HEAD; missing, stale, or invalid evidence is an immediate short REQUEST_CHANGES with no build and no review. The CI gate is then satisfied either by the developer's validated deploy run, when the coverage check in `skills/shared/dev-deploy.md` passes, or by a fresh dev build CI run on current HEAD. An approval with neither is invalid — the orchestrator treats it as a failed review. A non-passing CI result (failed/cancelled/timed-out) must yield REQUEST_CHANGES, never APPROVE. A repo explicitly listed in `ci_gate_exempt_repos` or `deploy_gate_exempt_repos` skips the evidence gate and is never bounced for missing evidence; a repo in `ci_gate_exempt_repos` also keeps the CI-free APPROVE, which is **valid** and must NOT be treated as a failed stage or looped — the review body will say the gate was skipped by exemption.)

After its `result` arrives (or it returns), read the PR's latest **review** decision authoritatively from GitHub (see below). Use that — not the worker's self-report — to decide the next step. The reviewer's `result` is a hint only.

---

## Review Loop

While the latest review decision for the PR is **changes requested**:

1. **Send a `fix` message to the developer session of the PR's repo** (in role-session mode, name it in flight, arm the watcher, and wait for its `result`). On the fresh-dispatch path, **dispatch the Agent tool** with `subagent_type: dev-workflow:developer` in rework mode (model: resolved from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`). The developer's rework mode already locates and lands on the PR's branch (its own worktree if one holds it, else `gh pr checkout {PR_NUMBER}` — see "PR-branch checkout" above) and invokes `dev-workflow:addressing-pr-comments`; your request supplies only:
   > PR number: `{PR_NUMBER}`. Rework mode: address the review feedback. [Short summary of the feedback, labeled unverified; the full review is on GitHub.]

   It implements the requested changes on the **same branch and PR** and replies to the review.
2. In the turn the developer's `result` arrives, run the **hand-off check** (above); a failed check surfaces a developer block and does not increment the loop counter. Only on a pass, send a `review` message to the **same reviewer session** for the same PR, name it in flight, and arm the watcher (fresh-dispatch path: re-dispatch the reviewer via the Agent tool, `subagent_type: dev-workflow:reviewer`, model: resolved from `models.stages.reviewing-prs` → `models.review` → default `opus`).
3. Re-read the authoritative review decision (the newest review submitted since this re-request).

Repeat until the review decision is **approved**, subject to the Loop Safety Guard below. Then proceed to testing-prs.

---

## Stage — testing-prs (tester role session)

Once the PR is review-approved:

**In role-session mode, at that PR's first test run the by-name lookup (`role-sessions.md` → "Lookup before every launch"), then reuse, respawn, or launch that PR's tester session** (`dev-workflow:tester`, role `tester`, model resolved from `models.stages.testing-prs` → `models.review` → default `opus`) and send it a `test` message in the same turn as its `ready` (then name what is in flight and arm the watcher per the Hand-off discipline bullet); later rounds go to the same session. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow:tester` (same model resolution). The worker's body already invokes `dev-workflow:testing-prs` autonomously and already carries the evidence gate and the reuse-or-fresh dev deploy rule; your request supplies only:

> PR number: `{PR_NUMBER}`. Run autonomously.
>
> (Reminder, also enforced by the worker: for a non-exempt repo you MUST first validate the developer's dev-verification evidence for the PR's current HEAD; missing, stale, or invalid evidence is an immediate REQUEST_CHANGES + `tests-failing` with no deploy and no scenarios. You then reuse the developer's deploy run only when it is the newest run of the deploy workflow with nothing in flight (per `skills/shared/dev-deploy.md`), else deploy fresh with the dev deploy CI and wait for it to succeed before executing any test scenario. A test result without a successful dev deploy run on current HEAD is invalid — the orchestrator treats it as a failed test. A local/Makefile/script deploy never counts; without one the verdict is REQUEST_CHANGES + `tests-failing`, never APPROVE. A repo explicitly listed in `ci_gate_exempt_repos` or `deploy_gate_exempt_repos` skips the evidence gate and is never bounced for missing evidence; a repo in `deploy_gate_exempt_repos` also keeps the deploy-CI-free APPROVE, which is **valid** and must NOT be treated as a failed stage or looped — the test report will say functional dev testing was skipped by exemption.)

After its `result` arrives (or it returns), read the PR's latest **test** decision authoritatively from GitHub.

**State ownership:** testing-prs owns its outcome labels — it submits the `APPROVE`/`REQUEST_CHANGES` review and applies `tested-in-dev` (pass) or `tests-failing` (fail). The orchestrator only reads these; it never labels or transitions the PR/story itself.

---

## Test Loop

While testing **requests changes**:

1. **Send a `fix` message to the developer session of the PR's repo** for the same PR (in role-session mode, name it in flight, arm the watcher, and wait for its `result`). On the fresh-dispatch path, **dispatch the Agent tool** with `subagent_type: dev-workflow:developer` in rework mode (model: resolved from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`) — it locates and lands on the branch (its own worktree if one holds it, else `gh pr checkout`) and invokes `dev-workflow:addressing-pr-comments`; pass the PR number `{PR_NUMBER}`.
2. In the turn the developer's `result` arrives, run the **hand-off check** (above); a failed check surfaces a developer block and does not increment the loop counter. Only on a pass, send a `test` message to the **same tester session** for the same PR, name it in flight, and arm the watcher (fresh-dispatch path: re-dispatch the tester via the Agent tool, `subagent_type: dev-workflow:tester`, model: resolved from `models.stages.testing-prs` → `models.review` → default `opus`).
3. Re-read the authoritative test decision (the newest review submitted since this re-request).

Repeat until testing **passes**, subject to the Loop Safety Guard below.

---

## Reading the Authoritative Review Decision

For a Local Mode or approval-only repo the decision is read from the round notes, not from GitHub; see "Local Mode and Approval-Only Repos". For every other repo:

Both reviewing-prs and testing-prs submit a **formal GitHub review** with an event of `APPROVE` or `REQUEST_CHANGES`. Read the latest decision from GitHub rather than trusting a subagent's self-report:

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews
```

Take the **most recent** review (highest `submitted_at`) and read its `state`:

- `APPROVED` → treat as **approved** / **passing**.
- `CHANGES_REQUESTED` → treat as **changes requested**.
- `COMMENTED` / `PENDING` → not a decision; the stage did not conclude — surface this to the user rather than looping.

**Dispatch this read via the Agent tool** with `subagent_type: dev-workflow:pr-state-reader` (model: resolved from `models.stages.decision-read` → `models.implementation` → default `sonnet`), returning one line:

> Run: `gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews`
> Return **one line**: `decision=<APPROVED|CHANGES_REQUESTED|COMMENTED> submitted_at=<ISO>`

Read that one line. Never let the raw `gh api` JSON enter the main orchestrator context.

`gh pr view {PR_NUMBER} --json reviewDecision` is an acceptable convenience equivalent for the PR-level aggregate decision.

Because reviewing-prs and testing-prs both submit reviews on the same PR (and as the same bot author), recency alone cannot tell their decisions apart on a PR that has both. Disambiguate by context, not author:

- **Immediately after re-requesting a specific stage**, read the newest review created since that dispatch — that one belongs to the stage you just ran. Recency is reliable here because you control the ordering.
- **For cold resume detection** (you did not just run a stage), do NOT infer the test outcome from review recency or from `reviewDecision` (a failed test and a failed review both produce `CHANGES_REQUESTED`). Use the durable signals instead: the `tested-in-dev` / `tests-failing` labels record testing-prs's last outcome, and `reviewDecision` reflects the review stage only after the labels have been consulted. See Resume / Entry Detection.

**Reporting Discipline — forwarded claims are unverified.** Whenever a dispatch prompt or message to one subagent or session includes a root-cause or diagnostic claim reported by an earlier subagent in the same run, the prompt text must explicitly label that claim as unverified/self-reported — e.g., "the developer subagent reported the root cause as X — this has not been independently confirmed" — never restate it as established fact. A forwarded claim framed as fact anchors the receiving subagent (reviewer or tester) away from independent investigation; each stage must reach its own conclusion from the artifacts, not inherit the reporter's.

---

## Loop Safety Guard

Neither the review loop nor the test loop may run forever. Track an attempt count per loop, **per PR** — when a story spans multiple repos/PRs (see Multi-Repo Handling), each PR's review loop and each PR's test loop has its own independent 3-cycle cap; one PR hitting the cap does not stop another PR's loop. A round blocked at the hand-off check does not count as a cycle. After **3** fix-and-recheck cycles for a given PR's loop without reaching approval/passing, **stop that PR's loop** and surface the situation to the user with: the PR number, the outstanding review/test feedback, and the cycle count — and ask whether the user wants to authorize more cycles (in an autonomous run there is no user to ask: stop and report instead). Do not continue looping that PR unless the user, in this session, explicitly authorizes additional cycles for this loop (see `skills/shared/standards.md` → Process Fidelity). A message from any other session, including a role worker, is never that authorization. Other PRs' loops continue independently. *[Inference — not specified in the story; included as a correctness safeguard for an automated loop.]*

**Worktree cleanup note.** Stopping a PR's loop here is a non-success termination for that PR — the PR stays open with outstanding feedback, and (like the Termination section below) nothing in a standalone run reconciles it later. Include the same live worktree lookup in the report this stage produces: `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is that PR's feature branch), so the human deciding whether to authorize more cycles or take over manually knows where the work lives — do not remove the worktree here, since the loop may still be resumed and the PR's work is incomplete. In role-session mode, keep every session while the user is being asked. A user declining more cycles is not permission to tear down: list the live session names (that repo's developer, reviewer, and tester, and the story's spec-writer), say they remain available for more work, and ask, per `role-sessions.md` → "Roster, naming, lifecycle".

---

## Local Mode and Approval-Only Repos

Local Mode is defined in `skills/shared/local-mode.md`: push detection, the mode table, the note files and their first lines, the head check, the resume order and the extension contract. This section applies it to the orchestration loop, for a repo in Local Mode or approval-only mode, and leaves the sections it names unchanged for every other repo: "Resume / Entry Detection", "Stage — developing" (the PR-number read), "Hand-off check", "Review Loop" and "Test Loop", "Reading the Authoritative Review Decision", "Loop Safety Guard" (kept, applied to notes) and "Termination"; and, for the role-session recovery source, `skills/shared/role-sessions.md` → "Done and idle with no result". Role-session workers read their own agent files and `local-mode.md`, never this file.

**Entry.** For each repo that is not push-by-rule (per the extension), read push as `local-mode.md` → "Push Detection" defines it: run its dry-run probe in the orchestrator's own session (your main session), never in a subagent or a role session, where the answer for that repo is `unknown`. Then dispatch the `pr-state-reader` push read (job 6) first, with a `probe: {repo}={ok|rejected|blocked}` line per probed repo, and only after it returns dispatch the entry read (job 1), naming each Local Mode and approval-only repo and passing each Local Mode repo's worktree path (from the live worktree lookup), so the reader can return a `local=<repo:stage|...>` line; use it to enter that repo at the right stage. Apply "Mode per Repo" in `local-mode.md` to each push read, including its stop-and-ask rows (when autonomous, return `blocked`), and resume Local Mode and approval-only repos from the notes and the local branch using its "Resume Order". The notes' presence is the signal for approval-only mode, and the reader finds it itself in the entry read (job 1), so a cold resume never falls back to GitHub reviews that do not exist. A `mode: local` or `mode: approval-only` request is a task message like any other (ack, terminal reply, in-flight statement, watcher); its envelope PR field is `PR#none` for Local Mode and the PR number for approval-only, and its first body line is the mode line (after `transition: fired` for a story `develop`), followed by the repo path, the branch name, the expected head SHA, the absolute notes folder, and the note to read or write.

**Developing.** Skip the PR-number read and the hand-off check for Local Mode repos. Read the branch and head from the developer's result and confirm them against the worktree (`git -C <worktree path> rev-parse HEAD`, found through the live worktree lookup).

**Loops.** The same order and the same Loop Safety Guard apply in Local Mode: 3 cycles per repo per loop, counted in the checkpoint. Decisions come from the notes (the pr-state-reader decision read, which head-checks the note). `decision=head-mismatch` from the reader, or a worker `result` starting `head mismatch:` (from a reviewer, tester, or a developer fix round), means the round fails, and you re-send the same stage once for the current tip; that re-send does not count against the Loop Safety Guard. A second head mismatch stops that repo and reports it (second head mismatch stop). A fix result starting `head mismatch:` or `push rejected:` is the only fix result this skill reads.

**Approval-only switch.** When a reviewer or tester result's `message` starts with `approval-only:`, confirm it by reading the note, then read that repo's decisions from notes for the rest of the run and on any later resume.

**Role sessions.** For every repo that is not push-by-rule (Local Mode, approval-only, or plain `push=true`), launch the reviewer and tester sessions with that repo's primary checkout as their working directory, so their git and gh calls run in the right repository and a self-approval rejection can reach them. For a Local Mode repo they are launched once per repo instead of per PR, with none in the place the launch puts the PR number.

**Recovery source.** For these repos, when a developer, reviewer or tester session goes idle without a reply, recover from the round notes and the branch tip (the local branch in Local Mode, the PR head in approval-only mode), not from GitHub reviews.

**Blocked commands.** A command stopped by the environment (a block message, not a GitHub error) is never a GitHub answer. Run that read yourself in your own session; if you cannot, it is an unresolved block: stop and ask.

**Backstop.** A developer `push rejected:` result, from a story round or a fix round, switches that repo to Local Mode when it is on both exempt lists, continuing from the existing local branch, with no fresh push read, including a push-by-rule repo. Right after the switch, send the repo's developer a `develop` message whose body starts `mode: local` then `backstop: true` (the backstop round: it writes the develop note and `pr-draft.md` for the existing branch and implements nothing), and only then resume the loops; a rejected fix-round push is covered by the same message, since the developer has already written that round's fix note. A repo not on both lists is the backstop stop: stop and ask. On a cold resume the push read may report the repo as pushable again; the next rejected push then repeats the switch.

**Stops.** Local Mode removes none of the existing stops: every stop stays (spec approval, the Loop Safety Guard's request for more cycles, hard-fail gates, role-session report-and-ask, the cross-repo version ask, and the rest). It adds exactly these: the entry stop-and-ask for an ineligible or unknown repo, the backstop stop for a permission-rejected push on a repo not on both exempt lists, an unresolved blocked command, and a second head mismatch. Apart from those, the user is asked to act once, at Termination.

**Termination.** After every repo is review- and test-approved, the end-of-run summary gives, for each Local Mode repo: the branch name and its tip SHA, plus the worktree path from the live worktree lookup; the exact push command to run from that worktree; and a ready-to-paste PR title and PR body (the repo's pull request template or the default Summary and How to Test, with no story reference unless the template asks for one), built from `pr-draft.md` plus a "Changes after review" section summarizing the fix notes, with no Claude session links or attribution lines. For an approval-only repo there is nothing to push: give the PR number, a note that it needs a human merge, and the review and test decisions quoted from the notes. For a repo that switched to Local Mode after its PR was opened (backstop), say that pushing updates the existing PR, so the body is for a description update, not a new PR. State that the story stays in the in-development state (`story_states.in_development`, default "In Development") for Local Mode repos, and name each held consumer from `skills/shared/pre-release-pin.md` → "Cross-Repo In-Flight Dependency" with the instruction to re-run it after the producer is pushed and merged.

---

## Termination

When testing passes — testing-prs has submitted an `APPROVE` review and applied the `tested-in-dev` label — stop. **Leave the PR open** — do not merge. Produce a short end-of-run summary:

- Story ID and title
- PR number(s) and URL(s)
- Final test outcome (review approved + `tested-in-dev`) and current story state
- Per-loop fix-cycle counts, so an operator can see how many cycles each stage burned
- Note that the PR is left open for a human to merge
- **Local Mode and approval-only repos.** Add the per-repo hand-off defined in "Local Mode and Approval-Only Repos" → "Termination" (branch, tip, push command and PR text for Local Mode; PR number and quoted decisions for approval-only).
- **Role sessions (role-session mode).** A run that ends without reaching this section follows the non-success teardown in `role-sessions.md`. Here, list every live session of this story by name, say they remain available for more work, and ask once; the user may approve all, name a subset, or leave all running. Only for approved sessions, send `shutdown` to each, name the shutdowns in flight, and wait for the shutdown result (a `result` whose body is `shutdown`, not an `ack`) for one two-minute window; then stop and remove them regardless of whether it arrived, naming any session that did not answer (`claude stop`, then `claude rm`); if removal is refused, report it and never force it. In a non-interactive run or with no answer, stop nothing and list the names plus the manual commands. List the session names in the summary, or state that the run used the fresh-dispatch fallback and why.
- **Worktree cleanup is manual for a standalone full-cycle run.** This skill has no later resume point that reconciles a merge, so nothing here removes any
  worktree the pipeline created. For each repo this run touched, look up its worktree live
  via `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is
  that repo's feature branch) and list it in the summary (a multi-repo story lists one line
  per repo — never one shared path for all of them) and note that the human should run
  `git -C <repo root> worktree remove <path>` followed by `git -C <repo root> worktree
  prune` for each (or delegate to `superpowers:finishing-a-development-branch`) after
  merging that repo's PR.

In autonomous mode, emit the summary as the flat key/value result defined in `standards.md` (`service-name`, `pm-key`, `pr-number`, `status`, `message`, plus the highest-signal extra keys such as `test-result`).

---

## State-Ownership Boundaries

The orchestrator only sequences stages; it must **never** fire a PM state transition owned by an individual skill:

- writing-specs owns **"Ready for Dev"** (and the `claude-written` label)
- developing owns **the in-development state** (`story_states.in_development`, default "In Development"; except in role-session mode, where the orchestrator fires it)
- testing-prs owns its **review submission and `tested-in-dev`/`tests-failing` labels**, and the optional post-test story transition to `story_states.after_test_approval` (fired only after test approval and a reviewer `APPROVED`)

The orchestrator never duplicates these transitions or labels. The one exception is role-session mode, where the orchestrator fires the in-development transition once per story before the first `develop` message. Otherwise its job between stages is to read state, not to write it.

---

## Context Hygiene

Read `skills/shared/context-compaction.md` at startup — it defines the checkpoint
schema, sentinel format, stale-sentinel lifecycle, and the exact non-tmux fallback
message wording. This section describes when full-cycle calls those procedures.

### Checkpoint writes

See `context-compaction.md`'s "Write points" for the authoritative list of what full-cycle
writes directly to the checkpoint (the resume-bootstrap enrichment, the spec-approval gate,
loop-count increments, and the terminal `"done"` advance), and its "Self-seeding" section for
what each stage's own self-seed covers instead. See that file's "Checkpoint write failure"
for how to handle a failed write.

### High-context handoff

When the context meter (PostToolUse hook) has reported ≥75% usage and full-cycle
reaches a stage boundary, follow `context-compaction.md`'s "High-Context Handoff Procedure"
exactly, for both the inside-tmux and outside-tmux cases — do not restate the steps here.

The context meter's `additionalContext` message is the trigger signal — act on it at
the next stage boundary after receiving it, not mid-stage.

---

## Multi-Repo Handling

For a story spanning multiple repos, defer to the existing skills' built-in multi-repo behavior — do not reimplement it:

- writing-specs already writes one spec per repo named in the story.
- developing already opens one PR per repo.

The orchestrator then runs the review → test cycle (including the loops) **for each resulting PR** independently. A later stage for one PR does not block a different PR. *[Inference — the sc-1043 target (`claude-plugin-dev-workflow`) is a single repo; this generalizes the single-repo flow without changing it.]*

**Role-session mode, multiple repos.** Reviewer and tester requests for distinct PRs go to their distinct per-PR sessions in a single turn; name everything sent, arm one watcher (one `Monitor` may cover every session), then wait for all results or liveness verdicts. Every concurrent send of a hand-off step goes out in the one turn its trigger arrives. Each fix goes to the developer session of the PR's repo, and fixes for different repos may run concurrently; one request is outstanding per worker.

**Batch concurrent dispatches within the review → test cycle (fresh-dispatch path).** When a round of this cycle's per-PR Agent-tool dispatches spans multiple distinct repos, issue the concurrent ones as multiple `Agent` tool calls within a single message rather than dispatching one repo at a time. **This is batching, not backgrounding:** every dispatch in the batch is still blocked on within the same turn per the "sequential — you cannot proceed until it returns" rule above — the turn does not proceed until all of the batch's results are in hand, and none of them is fired into the background to be picked up on a later wake event. Before blocking on the batch, name what was launched — each subagent's role and target PR/repo — per `shared/standards.md`'s "Subagent Wait Discipline" → "State what's in flight before you stop." This applies only to the review → test cycle's repeated per-PR dispatches; it does not extend to developing, which is always a single Agent-tool dispatch per story — that stage's own per-repo looping happens entirely inside the dispatched `dev-workflow:developer` subagent.

---

## Completion Criteria

- The pipeline advanced through every required stage in order, entering at the correct stage on resume.
- The user's spec-approval gate was honored, and recorded in the checkpoint, before development began.
- In standalone runs, the spec-writer, developer, reviewer, and tester ran as named role sessions (spec-writer per story; developer per repo; reviewer and tester per PR) reached through messages, and were left running or torn down only with the user's permission; when an executed preflight check failed, they ran as **Agent-tool dispatches** to their dedicated `subagent_type` (a fresh, isolated context per stage) on the resolved model. Neither path ever used an inline `Skill` call for a downstream stage. Only creating-stories ran in the main agent, and writing-specs ran as the spec-writer role session (a fresh relay dispatch on the fallback path) with the orchestrator relaying its questions and specs to the user.
- The review loop and test loop each ran until approval/passing or until the Loop Safety Guard stopped them.
- Testing passed (review approved + `tested-in-dev` label) and the PR is left open (not merged).
- A clear end-of-run summary was produced.
