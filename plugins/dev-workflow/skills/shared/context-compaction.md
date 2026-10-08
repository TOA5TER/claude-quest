# Context Compaction Protocol

Shared reference for all full-cycle checkpoint, sentinel, and compaction-fallback behavior.
Read this doc at skill startup when invoking full-cycle with a story ID.

---

## Checkpoint State File

Path: `~/.claude/dev-workflow/state/{story-id}.json`

Schema:
```json
{
  "story_id": "sc-1043",
  "story_title": "Group sessions by story id",
  "repos": {
    "api": {
      "pr_number": 42,
      "stage": "reviewing-prs",
      "review_loop_count": 1,
      "test_loop_count": 0,
      "next_action": "re-dispatch reviewing-prs subagent for PR 42"
    },
    "web": {
      "pr_number": 43,
      "stage": "reviewing-prs",
      "review_loop_count": 0,
      "test_loop_count": 1,
      "next_action": "re-dispatch testing-prs subagent for PR 43"
    }
  },
  "approval_text": "Approved — proceed with the spec as written.",
  "approval_timestamp": "2026-06-10T16:05:00Z",
  "updated_at": "2026-06-10T17:30:00Z"
}
```

Each key under `repos` is a service/repo name, matching `repo-discovery.md`'s "service
name" convention. A single-repo story's `repos` map has exactly one entry and behaves
identically to the old single-valued fields, with no special-casing required by consuming
code paths.

`story_title` is optional and display-only: the story's title as fetched from the PM tool,
read by attention-hub's reporting client for its story group header. It is never a resume
signal and nothing in the pipeline branches on it. Only "Seed or Refresh Stage" writes it,
when a caller passes a non-empty title (see `checkpoint-seeding.md`); a checkpoint without it
is valid, and the hub then shows the bare story id.

**No worktree path is ever stored in the checkpoint.** A stage or subagent that needs a
repo's worktree resolves it live via `git worktree list --porcelain` (matching the entry
whose branch equals that repo's feature branch), exactly as the developer agent's rework mode
does — see `skills/shared/standards.md` → "Workspace Isolation". Nothing here
caches it, so there is no staleness or cross-repo-mixup class of bug to guard against.

**Stage vocabulary.** The write points below produce four values: `"writing-specs"`,
`"developing"`, `"reviewing-prs"`, and `"done"`. This is not necessarily every value a
`repos[].stage` field can hold — checkpoints from other or older code paths (e.g. a
`"finished"` or `"blocked-environment"` entry) may exist on disk; a reader must not
assume the field is limited to these four and should treat any other value as opaque
rather than erroring. PR creation records `pr_number` on that repo's entry while `stage`
stays `"developing"` — developing's own PR Creation Requirements self-seed retimes only the
PR number at that point, not the stage. `pr_number` populated together with
`stage: "developing"` is therefore a valid, expected combination, not a bug signal — it is
the state between PR creation and `reviewing-prs` actually starting. `stage` advances to
`"reviewing-prs"` only when `reviewing-prs`' own Phase 2 self-seed runs, and stays there
through *both* the review loop and the test loop that follow it — `review_loop_count` and
`test_loop_count` are what distinguish which loop a repo is currently in while `stage` reads
`"reviewing-prs"`. `stage` advances to its terminal `"done"` only when that repo's
testing-prs passes. This is a distinct, smaller vocabulary from the
entry-detection `prs=` tuple's `stage` field (`finished` / `testing-prs` / `reviewing-prs`, see
`full-cycle/SKILL.md`'s Resume / Entry Detection); when initializing a checkpoint entry
from a parsed tuple, map the tuple's `stage` to the checkpoint's: `finished` → `"done"`,
`testing-prs` or `reviewing-prs` → `"reviewing-prs"`.

**Legacy stage values (pre-rename checkpoints).** A checkpoint written before the
dev-workflow skill rename may still hold the old stage vocabulary:
`"write-spec"`, `"start-development"`, or `"review-pr"`. `stage` is a persisted, on-disk
identifier, not a skill name — it does not get a hard cutover. Whenever a checkpoint's
`stage` field is read (at "Checkpoint initialization on resume" in `full-cycle/SKILL.md`
and anywhere else a checkpoint entry's `stage` is consulted), treat these as aliases and
translate on read: `"write-spec"` → `"writing-specs"`, `"start-development"` →
`"developing"`, `"review-pr"` → `"reviewing-prs"`. Never write a legacy value back —
the next checkpoint write for that entry always uses the current vocabulary.

### Write points

full-cycle writes the checkpoint at select stage boundaries and loop iterations — the
resume-bootstrap enrichment, the spec-approval gate, loop-count increments, and the terminal
`"done"` advance. Every other stage-to-stage transition is each stage's own self-seed (see
"Self-seeding" below). Every write below updates the correct repo's entry in the `repos`
map, except where noted as a top-level field:

- **During entry-detection resume, before running any stage:** initialize or enrich the
  `repos` map from the entry-detection subagent's result — the case on every cold resume by
  a bare story ID, since creating-stories never runs on that path. First, if the checkpoint has
  no `repos` map yet, or it is missing an entry for a repo named in the story's "Repos to
  modify" field, seed one entry per such repo from that field (per the note above, this
  field is available immediately at story creation, independent of `prs=`): `pr_number:
  null`, `stage` set from `story_state` per the Resume / Entry Detection table —
  `"writing-specs"` for row 2 (no spec / "In Spec" or earlier), `"developing"` for row 3
  (spec present / "Ready for Dev", no linked PR) — and `review_loop_count: 0,
  test_loop_count: 0`. This is what covers rows 2 and 3, where `prs=none` because no PR is
  linked yet and there is therefore no tuple to source from. Then, whether or not that
  seeding ran, use the parsed `prs=` tuples to enrich/update the entry for any repo that
  does have a linked PR: `pr_number` from the tuple's `pr`, `stage` mapped per the Stage
  vocabulary note above, and `review_loop_count: 0, test_loop_count: 0` if that repo had no
  prior entry (loop counts are not recoverable from GitHub, so a cold resume restarts them
  at 0). Both steps fill gaps only — neither overwrites an entry the checkpoint already has
  counts for.
- **After the user approves the spec in writing-specs** (before developing begins) —
  record the top-level `approval_text` (the user's literal approval message, verbatim)
  and `approval_timestamp` (ISO-8601 time the approval was given). These two fields are
  the mechanical evidence that the spec-approval gate actually fired; full-cycle refuses
  to dispatch developing without them (see full-cycle's "Hard gate — recorded
  approval"). The repo entry itself is untouched by this write — writing-specs' own Phase 3
  self-seed already recorded `stage: "writing-specs"` when writing-specs started, and
  developing's own PM Context self-seed is what advances `stage` to `"developing"` once
  developing actually starts.
- **After each review-loop / test-loop iteration:** increment that PR's repo entry's
  `review_loop_count` / `test_loop_count`.
- **After a given PR's testing-prs passes:** advance that repo's entry's `stage` to `"done"`.
  Other repos' entries are untouched and continue independently — this is the terminal
  state a fully finished repo reaches while a sibling repo can still be mid-loop.

None of these write points (the resume bootstrap, the approval write, loop-count increments,
or the terminal `"done"` advance) touches the top-level `story_title`; it is preserved as-is.

full-cycle's own mid-pipeline writes no longer anticipate a stage that hasn't started yet.
Each stage's own self-seed (per `checkpoint-seeding.md`) is the sole writer of *its own
stage's boundary-start value* under normal, non-resume operation — not the sole writer of
`stage` overall. Counterexamples elsewhere in the pipeline: `testing-prs` and
`addressing-pr-comments` write `"reviewing-prs"` on a repo's entry (the loop-back value from
`context-compaction.md`'s Stage vocabulary note, not anticipation — that repo's review loop
is already underway when either calls it), `testing-prs`' own Phase 7 self-seed writes the
terminal `"done"`, and full-cycle itself still writes stage values above at the cold-resume
bootstrap row (multiple values, including `"developing"` for a not-yet-started stage —
legitimate there, since a cold resume by bare story ID has no self-seed to defer to) and at
its own terminal `"done"` advance above, which fires only after that outcome has actually
occurred.

The checkpoint **complements** GitHub/PM state — it stores what GitHub cannot: loop counts and
the orchestrator's next intended action. GitHub/PM remain authoritative for resume detection:
a repo's checkpoint `stage` field is a display/telemetry mirror for attention-hub, not a
second resume-decision source. For example, a session that dies between PR creation and
`reviewing-prs`' own Phase 2 self-seed leaves that repo's checkpoint `stage` reading
`"developing"` with `pr_number` already set (see the Stage vocabulary note above) —
`full-cycle`'s Resume / Entry Detection table (rows 4-8, evaluated from the PR's actual
GitHub review/label state), not this checkpoint field, is what correctly resumes that repo
at `reviewing-prs`, not `developing`.

**Self-seeding.** Every stage skill also writes/refreshes this same checkpoint at its own
stage boundary, independent of whether `full-cycle`/`epic` is driving the pipeline — see
"Write points" above for what full-cycle's own writes still cover; every other stage
boundary's `stage` value is written solely by that stage's own self-seed, per
`checkpoint-seeding.md`'s "Seed or Refresh Stage" procedure. Most stages call it once, at
their own start: `writing-specs` (Phase 3), `reviewing-prs` (Phase 2), and
`addressing-pr-comments` (Step 1). `developing` and `testing-prs` each call it twice, at two
distinct points within their own execution: `developing` at PM Context (before implementation
starts) and again at PR Creation Requirements (once the PR exists, carrying the PR number);
`testing-prs` at Phase 2 (before testing starts) and again at Phase 7 (its own terminal
`"done"` write).
`creating-stories` writes only once, and not into a real `{story-id}.json` entry at all — a
pre-story placeholder at Phase 0, via "Seed Pending Pre-Story Placeholder", before a story ID
exists. It deliberately does not write a real checkpoint entry at Phase 6 on story-creation
success; `writing-specs`' own Phase 3 self-seed is what eventually supersedes the placeholder,
once a real entry exists to replace it. "Seed or Refresh Stage" shares the exact merge-upsert
semantics described above (upsert only the fields it's given; never touch
`review_loop_count`, `test_loop_count`, `approval_text`, or `approval_timestamp`; and never
clear `story_title`, which it sets only when given a non-empty title), so a
stage's own self-seed and full-cycle's own writes to the same repo entry can never clobber
each other's fields, regardless of which one runs first or last within the same pipeline
execution.

### Checkpoint write failure

If a write fails (disk full, permissions), surface the error to the user and continue.
Do NOT abort the pipeline. GitHub/PM state remains the resume authority.

---

## Role sessions

Role sessions (see `skills/shared/role-sessions.md`) do not use the sentinel handoff or the context meter: both hooks exit immediately when `DEV_WORKFLOW_ROLE` is set, and a role session relies on the host's native auto-compaction. The sections below describe the single tmux-hosted orchestrator only.

---

## Sentinel File

Path: `~/.claude/dev-workflow/state/.compact-request`

Content: the exact resume command, e.g.:
```
/start full-cycle sc-1043
```

The sentinel is **only written inside tmux** (when `$TMUX` is set). Outside tmux the manual
fallback is used instead (see below). The compact-injector Stop hook consumes (deletes) the
sentinel after reading it — a sentinel is single-use.

### Stale sentinel

A sentinel older than 10 minutes is stale. The Stop hook deletes it without acting.

---

## High-Context Handoff Procedure

When the context meter has reported ≥75% and full-cycle reaches a stage boundary:

### Inside tmux (`$TMUX` is set)

1. Confirm the checkpoint is current — the stage now in progress already self-seeded its
   own `stage` value at its own start (per `checkpoint-seeding.md`), and any
   full-cycle-owned field due at this exact boundary (approval evidence, a loop counter)
   was already written by its own entry in "Write points" above. No separate write happens
   here.
2. Write the sentinel file with the resume command.
3. Announce to the user:

   > **Compacting at stage handoff.** Context has reached a high-usage threshold. Confirming checkpoint state and requesting /compact — the pipeline will resume automatically after compaction.

4. End the turn. The compact-injector Stop hook fires next.

### Outside tmux (`$TMUX` is not set)

1. Confirm the checkpoint is current — the stage now in progress already self-seeded its
   own `stage` value at its own start (per `checkpoint-seeding.md`), and any
   full-cycle-owned field due at this exact boundary (approval evidence, a loop counter)
   was already written by its own entry in "Write points" above. No separate write happens
   here.
2. Do **not** write the sentinel.
3. End the turn with this exact message (substitute the actual story ID):

   > **Good compaction point.** Context is high. To keep state clean, please run:
   >
   > 1. `/compact`
   > 2. `/start full-cycle {story-id}`
   >
   > The checkpoint is current — the pipeline will re-enter at the correct stage.

---

## Compact-Injector Behavior Summary

`hooks/compact-injector.sh` (Stop hook):

1. If no sentinel exists → exit 0 (nothing to do).
2. If sentinel is older than 10 minutes → delete it, exit 0 (stale).
3. If `$TMUX` is not set → exit 0 (tmux required for injection).
4. Read the resume command from the sentinel, delete the sentinel, spawn detached injector.

Detached injector (runs after a ~2-second delay, out-of-band):

1. Confirm Claude Code pane shows idle prompt via `tmux capture-pane`.
2. Send `/compact` to the pane.
3. Poll pane until compaction completes (prompt returns).
4. Send the resume command.
5. Max 3 retries on any step. On final failure, write `~/.claude/dev-workflow/state/.compact-request.failed` and stop.
