---
name: dev-workflow:full-cycle
description: "Drives the entire dev-workflow lifecycle end to end in order — creating-stories → writing-specs → developing → reviewing-prs → testing-prs — looping back through review and testing until both pass. Use when a user wants to run the whole development pipeline for a feature, take a story 'all the way through', or says 'full cycle', 'end to end', 'run the whole workflow', or '/start full-cycle'. Resumable: re-invoke at any point and it detects current story/PR state and enters at the correct stage."
---

# Full Cycle

**Role:** Orchestrator — drive the entire dev-workflow lifecycle from idea to tested PR, in order, looping the stages that must repeat.

**SCOPE BOUNDARY:** This skill **sequences** the existing dev-workflow skills — it does not reimplement any stage. It never writes feature code, specs, or PRs directly; each stage's own skill does that. The orchestrator's only direct actions are: talking to the user during the interactive stages, launching role sessions, sending them messages, and stopping them (or dispatching subagents on the fresh-dispatch path and for the pr-state-reader and spec-writer), reading PM/GitHub state to decide what runs next, and producing the end-of-run summary. This restriction holds even when the user directly asks for a mid-session fix — such a fix must always be routed to the developer session as a `fix` message (or, on the fresh-dispatch fallback path, a fresh `dev-workflow-developer` Agent-tool dispatch in rework mode), never handled with a direct `Edit`/`Bash`/`git` call in the orchestrator's own context. And whenever any new commit lands on an open PR outside the formal Review Loop / Test Loop — including one made by a mid-session developer fix — a fresh review and test round against the new HEAD (per the Stage — reviewing-prs and Stage — testing-prs procedures below) is mandatory before the PR is reported done; if that fresh pass itself comes back changes-requested, it continues into the existing Review Loop / Test Loop machinery — including the Loop Safety Guard's cycle cap — rather than looping ad-hoc outside it (this closes the gap where a fix commit landed on an already-approved-and-tested PR with no re-verification before the pipeline reported it done). It **never merges the PR** — a human does that.

## Arguments: $ARGUMENTS

The skill accepts one of:

- **A PM story ID** (e.g., `sc-1043` or `1043`) — resume an existing story; detect its current state and enter the pipeline at the correct stage.
- **A free-form feature description** — begin a brand-new cycle at creating-stories.
- **No argument** — begin a brand-new cycle at creating-stories (creating-stories will prompt for the description).

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Setup.

Read `skills/shared/repo-discovery.md` — repo discovery procedure used by the stages this skill sequences.

Read `skills/shared/context-compaction.md` — checkpoint protocol, sentinel format, and non-tmux fallback wording used throughout this skill.

Read `skills/shared/role-sessions.md` — the role-session launch contract, message protocol, preflight, and liveness rules used by the developer, reviewer, and tester stages.

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
| writing-specs | **Main orchestrator** (interactive) | Must gate development on the user's explicit spec approval. |
| developing | **Role session** (developer; fresh subagent on the fallback path) | Non-interactive heavy implementation. |
| reviewing-prs | **Role session** (reviewer, per PR; fresh subagent on the fallback path) | Non-interactive review. |
| testing-prs | **Role session** (tester, per PR; fresh subagent on the fallback path) | Non-interactive functional testing. |
| loop-back fixes (addressing-pr-comments) | **Developer role session** in rework mode (fresh developer subagent on the fallback path) | The developer that wrote the code fixes it with context, on the same PR. |

A role session or dispatched subagent has no tool to ask the user. The implementation/review/test stages (developing, reviewing-prs, testing-prs) run their underlying skill in **autonomous mode** and return a single-line key/value result — as the body of a `result` message from a role session, or as the final response of a fresh dispatch (see "Autonomous mode" and "Output Mode Detection" in `standards.md`); the orchestrator reads that result only as a hint. addressing-pr-comments does **not** emit a key/value result — it implements fixes and posts PR replies — so a fix request's `result` is a short plain-text confirmation and the orchestrator never depends on it; the key/value rule applies only to `develop`, `review`, and `test` results. For every review/test outcome the orchestrator **re-confirms the authoritative state from GitHub and the PM story** (see Reading the Authoritative Review Decision) rather than trusting any subagent self-report.

**PR-branch checkout for developer rework.** reviewing-prs and testing-prs take a PR number argument, but `dev-workflow:addressing-pr-comments` resolves the PR from the *current branch* (`gh pr status`) — a developer session or fresh dispatch handling a fix request is not necessarily checked out on that branch. Because implementation/fix work runs inside an isolated worktree (`skills/shared/standards.md` → "Workspace Isolation"), that branch may already be checked out in a worktree elsewhere — a plain `gh pr checkout {PR_NUMBER}` fails outright when it is. The `dev-workflow-developer` agent's rework mode handles this: it locates the branch's worktree via `git worktree list --porcelain` and `cd`s there, falling back to `gh pr checkout {PR_NUMBER}` only when no worktree holds the branch. Pass the explicit PR number in the request — the developer resolves its own worktree live, so it never has to guess or ask.

## Role Sessions

In **standalone** full-cycle, the developer, reviewer, and tester run as persistent role sessions per `skills/shared/role-sessions.md`, which owns the launch contract, naming, message envelope, routing, liveness, teardown, and resume rules. This skill owns *when* each of those happens:

- **Epic worker rule.** When full-cycle runs as an epic per-task worker — a dispatched, autonomous subagent with no way to ask the user, the same autonomous-mode detection the rest of this skill uses — it skips the preflight and launches **no** role sessions, because subagent messaging addresses the parent session. It runs the fresh-dispatch path below: every stage is an Agent-tool dispatch, and each fix loop body is a fresh `dev-workflow-developer` dispatch in rework mode followed by a fresh `dev-workflow-reviewer` or `dev-workflow-tester` dispatch.
- **Preflight (once per run).** Before the first launch, run the preflight in `role-sessions.md` (version floor, agent view, background launch, ping handshake). The handshake is the `ping` of whichever session is launched first, whatever its role (the developer on a normal run, the reviewer or tester when resuming at entry rows 7-8). Any failed check: announce the reason and remedy once, stop sessions already launched, and run the **entire run** on the fresh-dispatch path. Never mix modes within a run.
- **Lazy launch.** The developer session launches when the developing stage starts, or, when a `fix` request is needed and no developer session exists (resume at entry rows 5-6), at that point. A PR's reviewer session launches at that PR's first review, and its tester session at that PR's first test. Names, model, and settings follow the launch contract.
- **Fresh-dispatch path (fallback and epic workers).** The rest of this skill describes both paths; where a step says "send a message to the session", the fresh-dispatch path instead dispatches the same worker with the Agent tool, as described under "How to dispatch".
- **Cost.** Each role session consumes subscription usage like an interactive session; announce the sessions launched.

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

**Subagent type + model per dispatch.** Resolve the model with the resolution order
`models.stages.<stage-key>` → `models.<task-type>` → built-in default, then pass it as the
`model` parameter on the Agent call (it overrides the worker's frontmatter default):

| Stage / dispatch | `subagent_type` | `stages` key | Task type | Default model |
|-----------------|-----------------|--------------|-----------|---------------|
| entry-detection | `dev-workflow-pr-state-reader` | `entry-detection` | `implementation` | `sonnet` |
| writing-specs (autonomous path only) | `dev-workflow-spec-writer` | `writing-specs` | `implementation` | `sonnet` |
| developing | `dev-workflow-developer` | `developing` | `implementation` | `sonnet` |
| developer rework (fresh dispatch) | `dev-workflow-developer` | `addressing-pr-comments` | `implementation` | `sonnet` |
| pr-number-read | `dev-workflow-pr-state-reader` | `pr-number-read` | `implementation` | `sonnet` |
| reviewing-prs | `dev-workflow-reviewer` | `reviewing-prs` | `review` | `opus` |
| testing-prs | `dev-workflow-tester` | `testing-prs` | `review` | `opus` |
| decision-read | `dev-workflow-pr-state-reader` | `decision-read` | `implementation` | `sonnet` |

In role-session mode the developer session's model is fixed at launch from `models.stages.developing`; fix requests reuse it. The `models.stages.addressing-pr-comments` key applies only to a fresh developer rework dispatch (fallback path and epic per-task workers) and is ignored in role-session mode. A fresh rework dispatch resolves its model from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`.

**Output mode (per `standards.md` → "Output Mode Detection").** Determine the mode at startup. The orchestrator is **interactive by nature** when it must run creating-stories or writing-specs, because those stages require user input (the spec-approval gate especially). If the skill is running non-interactively (no way to ask the user) AND the detected entry stage is creating-stories or writing-specs, STOP and surface that the pipeline needs an interactive session to define/approve the spec. When resuming at developing or later, no further interaction is required and the run may complete autonomously, emitting the flat key/value summary at Termination.

---

## Resume / Entry Detection

The skill is resumable: re-invoking it at any time must enter the pipeline at the correct stage. For a multi-repo story, each linked PR is evaluated independently per repo — determine each repo's entry stage from PM story state, whether that repo's PR exists, its review decision, and testing-prs's tracking labels. Evaluate top to bottom and enter each repo at the **first** matching stage. The orchestrator resumes each unfinished repo at its own detected stage and skips any repo already at `finished`.

**Process Fidelity applies here (see `standards.md` → "Process Fidelity").** Entry detection selects where the pipeline resumes — it never skips or reorders work: entering at a non-default stage, skipping a stage (including writing-specs or its User Approval Gate), or running stages out of the documented order beyond what the table below dictates requires asking the user for explicit permission first, never a silent inference from PR/story state.

**Why the row order matters:** when testing-prs fails it submits a `REQUEST_CHANGES` review, which drives the PR's aggregate `reviewDecision` to `CHANGES_REQUESTED` — the *same* value reviewing-prs produces when review fails. The PR review decision alone therefore cannot tell a failed review from a failed test. The `tests-failing` / `tested-in-dev` labels (set by testing-prs — see the testing-prs label requirement) are the disambiguator, so the label rows are evaluated **before** the generic `reviewDecision == CHANGES_REQUESTED` row.

| # | Observed state | Entry stage |
|---|----------------|-------------|
| 1 | No story yet (no story ID; feature description or empty argument) | **creating-stories** |
| 2 | Story exists but no spec is linked, or story state is "In Spec" / earlier | **writing-specs** |
| 3 | Spec present / story "Ready for Dev" and **no** linked PR | **developing** |
| 4 | A repo's linked PR carries the `tested-in-dev` label and no `tests-failing` label | **finished** — testing passed for that repo; report and skip it |
| 5 | A repo's linked PR carries the `tests-failing` label | **developer fix → testing-prs** (test loop) for that repo |
| 6 | A repo's linked PR `reviewDecision` is `CHANGES_REQUESTED` (and no `tests-failing` label) | **developer fix → reviewing-prs** (review loop) for that repo |
| 7 | A repo's linked PR is review-approved with no `tested-in-dev`/`tests-failing` label | **testing-prs** for that repo |
| 8 | A repo's linked PR exists but has no review decision yet (`REVIEW_REQUIRED`/null) | **reviewing-prs** for that repo |

How to gather each signal, evaluated independently per repo:

1. **Story state:** fetch the story via the PM adapter; read its workflow state. Treat it as a coarse, informational signal only — the plugin's built-in stages do **not** set a "Dev Complete" (or equivalent terminal) state, so resume detection must not depend on one. (A particular PM adapter may add such a transition; if present it corroborates the label, but the label is authoritative.)
2. **Linked PR (per repo):** use the PM adapter's "Finding PRs linked to a story" instructions to find every linked PR, then resolve each PR's repo/service name from its GitHub owner/repo, matching `repo-discovery.md`'s naming convention. If none is linked there, fall back to `gh pr list --state all --search "{story_id}"`.
3. **Review decision:** `gh pr view {PR_NUMBER} --json reviewDecision` for the aggregate, or the latest review's `state` (see Reading the Authoritative Review Decision below).
4. **Test outcome:** testing-prs applies a `tested-in-dev` (passed) or `tests-failing` (failed) label on every run (see "testing-prs label requirement" below). These labels — not review recency or `reviewDecision` — are the durable signal that distinguishes the test stage from the review stage. If a review-approved PR carries **neither** label, treat that repo as **not yet tested** (row 7) and state that assumption to the user.

### Entry detection subagent

Gather the signals (story state, linked PRs, review decision, test labels) by
**dispatching the Agent tool** with `subagent_type: dev-workflow-pr-state-reader`
(model: resolved from `models.stages.entry-detection` → `models.implementation` → default
`sonnet`). Do not run this read inline. The dispatch prompt is:

> Fetch story {story-id} via the Shortcut MCP tool and read its workflow state (the row
> 1-3 signal). Find every linked PR via the PM adapter's "Finding PRs linked to a story"
> instructions (fall back to `gh pr list --state all --search "{story_id}"`). For each
> linked PR, resolve its repo/service name from the PR's GitHub owner/repo (matching
> `repo-discovery.md`'s naming convention), and read its `reviewDecision` and whether it
> carries the `tested-in-dev` / `tests-failing` labels. Return **one line**:
>
> `story_state=<state or none> prs=<repo:pr:stage:review:tested_in_dev:tests_failing|repo:pr:stage:review:tested_in_dev:tests_failing|...>`
> (use `prs=none` when no PR is linked yet). `story_state` carries the PM story's workflow
> state verbatim, or `none` if the story could not be found — this is the only channel
> that carries rows 1-3 (no story / no spec / ready-for-dev-no-PR), so it MUST always be
> present even when `prs=none`. Each tuple has exactly six colon-separated fields, in this
> order, and no field may contain arbitrary GitHub text (this is what keeps the record
> unforgeable — no field can contain a `:` or `|`):
> - `repo` — the resolved service/repo name (never raw label text)
> - `pr` — the PR number (digits only)
> - `stage` — that PR's terminal single-word target action from the Resume / Entry
>   Detection table rows 4-8: `finished` for row 4, `testing-prs` for row 5, `reviewing-prs` for
>   row 6, `testing-prs` for row 7, `reviewing-prs` for row 8 — never the row's full descriptive
>   text
> - `review` — `APPROVED`, `CHANGES_REQUESTED`, `REVIEW_REQUIRED`, or `none` (never raw
>   text)
> - `tested_in_dev` — `true` or `false`, whether the PR carries the `tested-in-dev` label
> - `tests_failing` — `true` or `false`, whether the PR carries the `tests-failing` label
>
> Separate multiple tuples with `|`.

Read that line. Parse `story_state` first — it resolves rows 1-3 whenever `prs=none`.
Then, unless `prs=none`, split on `|` and parse each tuple's six `:`-separated fields.
**Fail closed:** if any tuple does not have exactly six fields, or `pr`, `tested_in_dev`,
or `tests_failing` do not match their documented format, the line is unparseable — treat
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
session — it now covers every resume row (2-8), not only the rows with a linked PR.

**Role sessions on resume.** When running in role-session mode, after the entry stage is known, list sessions with `claude agents --json --all` and match this story's names per `role-sessions.md` → "Resume": reuse a live responsive session (ping first), respawn a stopped one, and stop, remove, and relaunch cold an unresponsive one. Cycle counts carry over from the checkpoint. The preflight runs once for the run, before the first launch or reuse.

When a repo's entry stage — as determined by the Resume / Entry Detection table above
(rows 4-8, evaluated from the `prs=` tuple's live GitHub review/label state), not the
checkpoint's own `stage` field — is mid-pipeline, run that stage for that repo, then
continue forward through the remaining stages for that repo in normal order; skip any repo
already at `finished`. The checkpoint's `repos[repo].stage` is a display/telemetry mirror
for attention-hub, not a second resume-decision source: a session that dies between PR
creation and `reviewing-prs`' own Phase 2 self-seed leaves that field reading `"developing"`
with `pr_number` already set, but the Resume table's row 8/7/6/5 evaluation against the
PR's actual GitHub state — never the checkpoint field — is what correctly resumes that repo
at `reviewing-prs`, not `developing`. State each repo's detected entry stage to the user
before proceeding — not a single story-wide stage.

Rows 1-3 resolve the same way, from `story_state` rather than the `prs=` tuple (see "How to
gather each signal" above) — they are not an exception to the "not the checkpoint field"
rule above; the checkpoint's `repos[repo].stage` is never a resume-decision source for any
row.

### testing-prs label requirement

This skill depends on testing-prs labeling its outcome. testing-prs is updated in this change to **always** add `tested-in-dev` on a passing run and `tests-failing` on a failing run (previously optional). If you run full-cycle against a build of testing-prs that omits the labels, cold resume cannot distinguish "approved, not yet tested" from "approved and passed" — in that case it defaults to row 7 (re-test) and announces the re-test, which is safe but may repeat a passing test.

---

## Stage — creating-stories (interactive, main orchestrator)

Run only when there is no story yet.

This stage satisfies the Story Creation Gate in `skills/shared/standards.md` only because the user explicitly invoked full-cycle. If full-cycle itself was auto-triggered, creating-stories's invocation-provenance pre-gate must still fire its permission ask.

> Invoke Skill: `dev-workflow:creating-stories`
>
> Pass the feature description from `$ARGUMENTS` if one was provided.

Drive the interview to completion in the main agent so it can ask the user questions. Capture the resulting **story ID** and carry it forward. Proceed to writing-specs.

---

## Stage — writing-specs (interactive, main orchestrator)

> Invoke Skill: `dev-workflow:writing-specs`
>
> Pass the story ID as the argument.

writing-specs already writes one spec per repo named in the story (satisfying the "once per repo" requirement) and has its own User Approval Gate. Run it in the main agent so that gate is interactive.

**Autonomous path (e.g. an epic task):** when running autonomously there is no human to satisfy the approval gate, so do **not** run writing-specs in this orchestrator's context. Instead **dispatch the Agent tool** with `subagent_type: dev-workflow-spec-writer` (model: resolved from `models.stages.writing-specs` → `models.implementation` → default `sonnet`), passing the story/task ID and any PM-adapter override. The worker produces the spec(s) and proceeds — there is no interactive gate to honor in this mode.

**Mandatory confirmation gate (interactive path only):** Do NOT advance to developing until the user has explicitly approved the spec(s) through writing-specs's approval gate. If the user requests changes, let writing-specs revise and re-present until approved. Only on explicit approval do you proceed.

**Hard gate — recorded approval (interactive path only):** when the user approves, write
the checkpoint immediately with `approval_text` (the user's literal approval message,
verbatim) and `approval_timestamp` (ISO-8601) per `skills/shared/context-compaction.md`.
Then, immediately before dispatching the developing subagent, read
`~/.claude/dev-workflow/state/{story-id}.json` back and verify both fields are present and
non-empty. If either is missing or empty, do NOT dispatch developing — stop,
re-request explicit approval from the user, record it in the checkpoint, and re-run this
check. Prose approval that was never recorded does not satisfy the gate. This check fires
only at this writing-specs → developing boundary — no other stage or resume path reads
these fields.

Once this gate is satisfied, no further user confirmation is required or expected through developing, reviewing-prs, testing-prs, or the fix loops — proceeding through those stages is documented pipeline behavior, not a Process Fidelity deviation.

**State ownership:** writing-specs owns the "Ready for Dev" transition and the `claude-written` label. The orchestrator does not duplicate them.

---

## Stage — developing (developer role session)

Before dispatching, resolve the target repo's path per `shared/repo-discovery.md`'s two-path
detection. If that procedure's single-repo shortcut applies (inside one git repo, or the
story names exactly one repo in "Repos to modify"), carry the resolved path forward into the
dispatch prompt below as `{resolved-repo-path}`. For a multi-repo story, do not resolve a
single path here — omit the `Repo path:` field entirely and let the dispatched subagent's own
per-repo discovery-and-loop run unmodified.

**In role-session mode, launch the developer session** (`dev-workflow:dev-workflow-developer`, role `developer`, model resolved from `models.stages.developing` → `models.implementation` → default `sonnet`) per `role-sessions.md`, run the preflight handshake with its `ping`, then send it a `develop` message carrying the input below. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow-developer` (same model resolution). The worker's body already invokes `dev-workflow:developing` autonomously; your request supplies only the variable inputs. Worktree ISOLATION is unconditional for this developer dispatch — it always works inside an isolated worktree, never the primary checkout (see `skills/shared/standards.md` → "Workspace Isolation") — it is not an epic-specific instruction, only the PM-adapter override and branch name below are. Worktree CREATION, though, is conditional: per standards.md's live-lookup rule, the dispatched subagent creates a worktree only when `git worktree list --porcelain` finds no existing match for the target branch; if a match exists (e.g. a resumed run), it reuses that one and does not create a second. This does not extend to every stage of a full-cycle run: developer rework in the Review Loop below *locates* an existing worktree or falls back to a plain checkout (see "PR-branch checkout for developer rework" above), it does not create one unconditionally.

> Story/task ID: `{story-id}`. Repo path: `{resolved-repo-path}`. Worktree isolation is required for this task — proceed without asking; if baseline tests fail, report the failure in your result and stop rather than asking whether to proceed. Run autonomously. [For an epic task, also pass the PM-adapter override and branch name.]

Single-repo case only: include the `Repo path:` field when the single-repo shortcut applies, as
resolved above. For a multi-repo story, omit `Repo path:` entirely — there is no single repo to
name — and let the dispatched subagent's own per-repo discovery-and-loop resolve each repo. Do not
drop the epic-task bracketed clause in either case.

After the developer's `result` message arrives (or the dispatched subagent returns), apply the wait discipline in `role-sessions.md` → "Waiting and liveness" while waiting. The developer branches, implements with TDD, and opens the PR (one PR per repo for a multi-repo
story). It resolves its own worktree per repo, live, per `skills/shared/standards.md` →
"Workspace Isolation" — reusing a matching one found via `git worktree list --porcelain` or
creating a new one — so nothing about the worktree path is passed in the dispatch prompt, recorded
in the checkpoint, or returned in the subagent's result.

After it reports, **dispatch the Agent tool** with `subagent_type: dev-workflow-pr-state-reader` (model: resolved from `models.stages.pr-number-read` → `models.implementation` → default `sonnet`) to resolve the **PR number(s)** authoritatively — do not resolve this inline. Dispatch prompt:

> Find any linked PRs for story `{story_id}` via the PM adapter's "Finding PRs linked to a story"
> instructions (the subagent attaches the PR to the story on creation), falling back to
> `gh pr list --state all --search "{story_id}"`. For each linked PR, resolve its
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

**State ownership:** developing owns the "In Development" transition. The orchestrator does not duplicate it.

Then proceed to reviewing-prs for each resulting PR.

---

## Stage — reviewing-prs (reviewer role session)

For each PR produced by developing:

**In role-session mode, launch that PR's reviewer session at its first review** (`dev-workflow:dev-workflow-reviewer`, role `reviewer`, model resolved from `models.stages.reviewing-prs` → `models.review` → default `opus`) and send it a `review` message; later rounds go to the same session. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow-reviewer` (same model resolution). The worker's body already invokes `dev-workflow:reviewing-prs` autonomously and already carries the fresh-dev-build-CI mandate; your request supplies only:

> PR number: `{PR_NUMBER}`. Run autonomously.
>
> (Reminder, also enforced by the worker: you MUST trigger the **dev build CI** fresh on the PR's current HEAD and wait for it to reach a terminal state before reviewing. An approval without a fresh dev build CI run on current HEAD is invalid — the orchestrator treats it as a failed review. A non-passing CI result (failed/cancelled/timed-out) must yield REQUEST_CHANGES, never APPROVE. The sole exception is a repo explicitly listed in `ci_gate_exempt_repos`: there a CI-free APPROVE is **valid** and must NOT be treated as a failed stage or looped — the review body will say the gate was skipped by exemption.)

After its `result` arrives (or it returns), read the PR's latest **review** decision authoritatively from GitHub (see below). Use that — not the worker's self-report — to decide the next step. The reviewer's `result` is a hint only.

---

## Review Loop

While the latest review decision for the PR is **changes requested**:

1. **Send a `fix` message to the developer session** (wait for its `result`). On the fresh-dispatch path, **dispatch the Agent tool** with `subagent_type: dev-workflow-developer` in rework mode (model: resolved from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`). The developer's rework mode already locates and lands on the PR's branch (its own worktree if one holds it, else `gh pr checkout {PR_NUMBER}` — see "PR-branch checkout" above) and invokes `dev-workflow:addressing-pr-comments`; your request supplies only:
   > PR number: `{PR_NUMBER}`. Rework mode: address the review feedback. [Short summary of the feedback, labeled unverified; the full review is on GitHub.]

   It implements the requested changes on the **same branch and PR** and replies to the review.
2. Send a `review` message to the **same reviewer session** for the same PR (fresh-dispatch path: re-dispatch the reviewer via the Agent tool, `subagent_type: dev-workflow-reviewer`, model: resolved from `models.stages.reviewing-prs` → `models.review` → default `opus`).
3. Re-read the authoritative review decision (the newest review submitted since this re-request).

Repeat until the review decision is **approved**, subject to the Loop Safety Guard below. Then proceed to testing-prs.

---

## Stage — testing-prs (tester role session)

Once the PR is review-approved:

**In role-session mode, launch that PR's tester session at its first test** (`dev-workflow:dev-workflow-tester`, role `tester`, model resolved from `models.stages.testing-prs` → `models.review` → default `opus`) and send it a `test` message; later rounds go to the same session. **On the fresh-dispatch path, dispatch the Agent tool** with `subagent_type: dev-workflow-tester` (same model resolution). The worker's body already invokes `dev-workflow:testing-prs` autonomously and already carries the fresh-dev-deploy mandate; your request supplies only:

> PR number: `{PR_NUMBER}`. Run autonomously.
>
> (Reminder, also enforced by the worker: you MUST run the **dev deploy CI** to deploy the branch fresh and wait for it to succeed before executing any test scenario. A test result without a fresh dev deploy is invalid — the orchestrator treats it as a failed test. A local/Makefile/script deploy never counts; without a successful dev deploy CI run the verdict is REQUEST_CHANGES + `tests-failing`, never APPROVE. The sole exception is a repo explicitly listed in `deploy_gate_exempt_repos`: there a deploy-CI-free APPROVE is **valid** and must NOT be treated as a failed stage or looped — the test report will say functional dev testing was skipped by exemption.)

After its `result` arrives (or it returns), read the PR's latest **test** decision authoritatively from GitHub.

**State ownership:** testing-prs owns its outcome labels — it submits the `APPROVE`/`REQUEST_CHANGES` review and applies `tested-in-dev` (pass) or `tests-failing` (fail). The orchestrator only reads these; it never labels or transitions the PR/story itself.

---

## Test Loop

While testing **requests changes**:

1. **Send a `fix` message to the developer session** for the same PR (wait for its `result`). On the fresh-dispatch path, **dispatch the Agent tool** with `subagent_type: dev-workflow-developer` in rework mode (model: resolved from `models.stages.addressing-pr-comments` → `models.implementation` → default `sonnet`) — it locates and lands on the branch (its own worktree if one holds it, else `gh pr checkout`) and invokes `dev-workflow:addressing-pr-comments`; pass the PR number `{PR_NUMBER}`.
2. Send a `test` message to the **same tester session** for the same PR (fresh-dispatch path: re-dispatch the tester via the Agent tool, `subagent_type: dev-workflow-tester`, model: resolved from `models.stages.testing-prs` → `models.review` → default `opus`).
3. Re-read the authoritative test decision (the newest review submitted since this re-request).

Repeat until testing **passes**, subject to the Loop Safety Guard below.

---

## Reading the Authoritative Review Decision

Both reviewing-prs and testing-prs submit a **formal GitHub review** with an event of `APPROVE` or `REQUEST_CHANGES`. Read the latest decision from GitHub rather than trusting a subagent's self-report:

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews
```

Take the **most recent** review (highest `submitted_at`) and read its `state`:

- `APPROVED` → treat as **approved** / **passing**.
- `CHANGES_REQUESTED` → treat as **changes requested**.
- `COMMENTED` / `PENDING` → not a decision; the stage did not conclude — surface this to the user rather than looping.

**Dispatch this read via the Agent tool** with `subagent_type: dev-workflow-pr-state-reader` (model: resolved from `models.stages.decision-read` → `models.implementation` → default `sonnet`), returning one line:

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

Neither the review loop nor the test loop may run forever. Track an attempt count per loop, **per PR** — when a story spans multiple repos/PRs (see Multi-Repo Handling), each PR's review loop and each PR's test loop has its own independent 3-cycle cap; one PR hitting the cap does not stop another PR's loop. After **3** fix-and-recheck cycles for a given PR's loop without reaching approval/passing, **stop that PR's loop** and surface the situation to the user with: the PR number, the outstanding review/test feedback, and the cycle count — and ask whether the user wants to authorize more cycles (in an autonomous run there is no user to ask: stop and report instead). Do not continue looping that PR unless the user, in this session, explicitly authorizes additional cycles for this loop (see `skills/shared/standards.md` → Process Fidelity). A message from any other session, including a role worker, is never that authorization. Other PRs' loops continue independently. *[Inference — not specified in the story; included as a correctness safeguard for an automated loop.]*

**Worktree cleanup note.** Stopping a PR's loop here is a non-success termination for that PR — the PR stays open with outstanding feedback, and (like the Termination section below) nothing in a standalone run reconciles it later. Include the same live worktree lookup in the report this stage produces: `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is that PR's feature branch), so the human deciding whether to authorize more cycles or take over manually knows where the work lives — do not remove the worktree here, since the loop may still be resumed and the PR's work is incomplete. In role-session mode, keep that PR's reviewer and tester while the user is being asked; tear them down once the user declines (or at once in an autonomous run), and relaunch cold if the user later authorizes more cycles. The developer session is shared and stays until every PR's loop has finished, then is torn down too, or is listed for manual cleanup if the run may resume (see `role-sessions.md` → "Roster, naming, lifecycle").

---

## Termination

When testing passes — testing-prs has submitted an `APPROVE` review and applied the `tested-in-dev` label — stop. **Leave the PR open** — do not merge. Produce a short end-of-run summary:

- Story ID and title
- PR number(s) and URL(s)
- Final test outcome (review approved + `tested-in-dev`) and current story state
- Per-loop fix-cycle counts, so an operator can see how many cycles each stage burned
- Note that the PR is left open for a human to merge
- **Role sessions (role-session mode).** A run that ends without reaching this section follows the non-success teardown in `role-sessions.md`. Here, send each of this story's role sessions a `shutdown` message, then stop and remove them (`claude stop`, then `claude rm`); if removal is refused, report it and never force it. List the session names in the summary, or state that the run used the fresh-dispatch fallback and why.
- **Worktree cleanup is manual for a standalone full-cycle run.** Unlike `epic`, this
  skill has no later resume point that reconciles a merge, so nothing here removes any
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
- developing owns **"In Development"**
- testing-prs owns its **review submission and `tested-in-dev`/`tests-failing` labels**

The orchestrator never duplicates these transitions or labels. Its job between stages is to read state, not to write it.

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

**Role-session mode, multiple repos.** Reviewer and tester requests for distinct PRs go to their distinct per-PR sessions in a single turn; name what was sent, then wait for all results or liveness verdicts. Fix requests all go to the single developer session, so serialize them: one outstanding `fix` request at a time, in repo order.

**Batch concurrent dispatches within the review → test cycle (fresh-dispatch path).** When a round of this cycle's per-PR Agent-tool dispatches spans multiple distinct repos, issue the concurrent ones as multiple `Agent` tool calls within a single message — mirroring `epic/SKILL.md` Phase 6's "dispatch the concurrent ones in a single batch" rule — rather than dispatching one repo at a time. **This is batching, not backgrounding:** every dispatch in the batch is still blocked on within the same turn per the "sequential — you cannot proceed until it returns" rule above — the turn does not proceed until all of the batch's results are in hand, and none of them is fired into the background to be picked up on a later wake event. Before blocking on the batch, name what was launched — each subagent's role and target PR/repo — per `shared/standards.md`'s "Subagent Wait Discipline" → "State what's in flight before you stop." This applies only to the review → test cycle's repeated per-PR dispatches; it does not extend to developing, which is always a single Agent-tool dispatch per story — that stage's own per-repo looping happens entirely inside the dispatched `dev-workflow-developer` subagent.

---

## Completion Criteria

- The pipeline advanced through every required stage in order, entering at the correct stage on resume.
- writing-specs's user-approval gate was honored before development began.
- In standalone runs, the developer, reviewer, and tester ran as named role sessions (developer per story; reviewer and tester per PR) reached through messages, and were torn down at Termination or loop stop; when the preflight failed, and for every epic per-task worker, they ran as **Agent-tool dispatches** to their dedicated `subagent_type` (a fresh, isolated context per stage) on the resolved model. Neither path ever used an inline `Skill` call for a downstream stage. In the interactive path, creating-stories and writing-specs ran in the main agent for their gates; in the autonomous path, writing-specs ran as the `dev-workflow-spec-writer` worker.
- The review loop and test loop each ran until approval/passing or until the Loop Safety Guard stopped them.
- Testing passed (review approved + `tested-in-dev` label) and the PR is left open (not merged).
- A clear end-of-run summary was produced.
