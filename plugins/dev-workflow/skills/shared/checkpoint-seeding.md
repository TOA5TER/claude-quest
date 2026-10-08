# Checkpoint Seeding

Shared procedure for a stage skill to seed or refresh its own entry in the dev-workflow
checkpoint (`~/.claude/dev-workflow/state/{story-id}.json`, schema defined in
`context-compaction.md`) at its own stage boundary. Each stage's own self-seed is now the
primary writer of its `stage` field regardless of whether `full-cycle`/`epic` is driving the
pipeline: `full-cycle`'s own writes (see `context-compaction.md` → "Write points") no longer
duplicate a stage value at most boundaries — they cover the resume-bootstrap enrichment, the
spec-approval top-level fields, loop-count increments, and the terminal `"done"` advance.
(`next_action` remains reserved to full-cycle/epic's own bookkeeping in the schema below, but
no current write point populates it — a pre-existing gap, not something introduced or
resolved here.) Each stage skill that calls one of the two procedures below documents the
exact point in its own flow where that call happens — see that skill's own preamble/phase
text (`reviewing-prs` Phase 2, `testing-prs` Phase 2 and Phase 7, `writing-specs` Phase 3,
`developing` PM Context and PR Creation Requirements, `addressing-pr-comments`
Step 1, `creating-stories` Phase 0) rather than a single shared table — this
file only defines what the call does, not where each caller places it.

Both procedures are **best-effort telemetry, never a functional gate.** A failure at any
step — an unresolved story ID, an unwritable state directory, a malformed existing file —
is never surfaced as an error to the user and never blocks the caller's real work. Silently
skip (for an unresolved story ID) or surface-and-continue (for a write failure, per
`context-compaction.md` → "Checkpoint write failure") and proceed with the rest of the
skill exactly as if this procedure had not been called. Because `full-cycle` no longer
backfills a skipped mid-pipeline self-seed, a skipped or failed call now has a concrete
(non-functional) cost: that repo's checkpoint `stage` goes stale or absent for
attention-hub's display until the next stage's own self-seed succeeds. GitHub/PM state
remains the resume authority regardless (see `context-compaction.md` → "Checkpoint write
failure"), so this never blocks or misdirects the pipeline itself — only its telemetry
display.

---

## Seed or Refresh Stage

**Inputs:** a story ID (or none), a list of repo names, a stage value, optionally a PR
number, and optionally a story title (the title the caller already fetched from the PM
tool).

1. **No story ID resolved, or the story ID fails validation:** no-op silently. Do not write
   anything, do not warn, do not block.
   - No story ID resolved is the expected outcome whenever the caller cannot resolve one
     (e.g. `reviewing-prs`/`testing-prs`/`addressing-pr-comments` on a PR with no linked
     story) — proceed with the rest of the skill unchanged.
   - **Validate the story ID before it touches any path.** It must match
     `^[A-Za-z0-9_-]+$` (letters, digits, underscore, hyphen only — this also rejects any
     `/` or `..` path-traversal payload). This matters because in `reviewing-prs`,
     `testing-prs`, and `addressing-pr-comments` the story ID may come from a branch name or an
     external lookup that someone else controls, so the value is not re-checked before it becomes
     a path component in steps 2 and 5 below. A
     non-matching value is treated exactly like "no story ID resolved": a silent no-op,
     never an error surfaced to the user.
2. **Acquire an exclusive lock** on this story's checkpoint before reading it. Multiple
   callers can legitimately target the same `{story-id}.json` at once — most notably
   `developing`'s "one sub-agent per repo, concurrently" dispatch (see its Step 3), where
   every sub-agent in a level calls this procedure for its own repo entry in the same file
   at roughly the same time. Without a lock, two concurrent read-whole-file /
   modify / write-whole-file executions can each read the file before the other's `mv`
   lands, so the second `mv` silently discards the first writer's repo entry (a lost
   update) — the atomic `mv` in step 6 only guarantees a reader never sees a torn file, it
   does not serialize two writers.
   - Lock path: `~/.claude/dev-workflow/state/.lock-{story-id}` — a **directory**, not a
     file. `mkdir` is atomic on every POSIX filesystem (unlike creating or checking a plain
     file, which is not), so "the `mkdir` succeeded" is a reliable signal that this caller
     now holds the lock.
   - Attempt `mkdir ~/.claude/dev-workflow/state/.lock-{story-id}`. Success means the lock
     is held — proceed to step 3.
   - On failure (the directory already exists): check its mtime. If it is older than 30
     seconds, treat it as abandoned by a caller that crashed or was killed while holding
     it — remove it (`rmdir`) and retry the `mkdir` once. Otherwise, sleep briefly (around
     200ms) and retry, up to a 10-second total budget.
   - **If the lock cannot be acquired within the 10-second budget:** this is a step 2-6
     failure — handle it per step 7 below (surface-and-continue) rather than blocking the
     caller's real work waiting on a checkpoint lock.
3. **Read the existing checkpoint**, if any, at `~/.claude/dev-workflow/state/{story-id}.json`.
   - If the file exists and parses as a JSON object, use it as the base.
   - If the file does not exist, or exists but fails to parse as a JSON object, start from
     a fresh `{"story_id": "{story-id}", "repos": {}}` shape — a malformed existing file is
     never a reason to stop; treat it the same as absent.
4. **Upsert each named repo's entry** in the `repos` map:
   - If the repo has no existing entry, create one: `{"stage": "{stage}", "pr_number":
     null, "review_loop_count": 0, "test_loop_count": 0}`, then set `stage` to the given
     value and `pr_number` to the given PR number if one was supplied.
   - If the repo already has an entry, set only its `stage` field to the given value.
     When a PR number is supplied AND the entry's existing `pr_number` is `null`/absent,
     set it too. Never overwrite an already-set `pr_number` with a different value, and
     never touch `review_loop_count`, `test_loop_count`, or `next_action` — those fields
     are full-cycle/epic's own bookkeeping and this procedure is a merge-upsert, not a
     replace.
5. **Update the top-level fields.** Set `updated_at` to the current ISO-8601 UTC timestamp.
   When a non-empty story title was supplied, set the top-level `story_title` to it. When
   the title is omitted or empty, leave any existing `story_title` exactly as it is — this
   procedure never clears it. The title is written under the same lock and in the same
   atomic write as the rest of this step.
6. **Write atomically.** Write the full updated JSON to a temp file in the same directory
   (e.g. `~/.claude/dev-workflow/state/.tmp-{story-id}-{unix-timestamp}-{pid}.json`), then
   `mv` it onto the real path — a plain rename on the same filesystem, so a concurrent
   reader (e.g. attention-hub's `get_dev_workflow_stage`) never observes a partially
   written file.
7. **Release the lock:** `rmdir ~/.claude/dev-workflow/state/.lock-{story-id}`. Always run
   this — including when any of steps 3-6 fails — so a failure mid-write doesn't wedge
   every later caller beyond step 2's own 30-second staleness backstop.
8. **On any failure in steps 2-6** (lock timeout, permission error, disk full, or any other
   write failure): surface the error to the user per `context-compaction.md` → "Checkpoint
   write failure", and continue — never block or abort the caller's real work over a
   checkpoint write failure.

This procedure never touches `review_loop_count`, `test_loop_count`, `approval_text`, or
`approval_timestamp` — those remain exclusively full-cycle/epic's own writes (see
`context-compaction.md` → "Write points"). Nor does it touch `story_title`, beyond setting a
supplied non-empty title in step 5: it never clears or blanks one. A dispatched subagent's own standalone self-seed
(this procedure) and full-cycle's post-return write to the same repo entry can therefore
never clobber each other's fields, regardless of which one runs first or last within the
same pipeline execution. The lock in step 2 additionally protects the case field
disjointness alone does not cover: two sibling subagents each writing a *different* repo
entry in the same file, where each write is a full read-modify-write of the whole JSON
document — the lock, not field disjointness, is what prevents one subagent's `mv` from
silently discarding another's.

The optional story title is the one input added beyond a story ID, repo names, a stage and
an optional PR number (the procedure still returns nothing). It is display-only, read by
attention-hub for its story group header. The callers that already hold the fetched story
pass it: `writing-specs` (Phase 3), `developing`'s first call (PM Context → Repo Discovery),
and `reviewing-prs` and `testing-prs` (Phase 2). Every other call omits it, which leaves any
recorded title in place. The lock in step 2 is internal to this procedure and changes nothing
for callers.

---

## Seed Pending Pre-Story Placeholder

**Input:** a list of candidate repo names, discovered before any story exists.

Used only by `creating-stories` Phase 0, for the window between repo discovery and story
creation where no story ID exists yet to key a checkpoint by.

`"init"` is never terminal (`STAGE_TERMINAL_STAGES` is `{"done", "finished"}`), so a
placeholder that survives past the interview itself — which, since a Phase 6 creation success
now deliberately leaves it in place rather than superseding it immediately (see "Cleanup is
the caller's responsibility" below), is the normal post-success case and not only an
abandoned/interrupted one — would otherwise keep shadowing real checkpoints for the same repo
indefinitely: attention-hub would keep displaying that placeholder's `"init"` stage for the
repo instead of its actual current checkpoint entry. The placeholder's actual required
lifetime is therefore the interview plus story creation plus however long until
`writing-specs` runs and its Phase 3 self-seed supersedes it — seconds, in an interactive
`full-cycle` run where `writing-specs` starts immediately after; unbounded, on the standalone
`creating-stories` path, where a created story can sit in the backlog for any length of time
before someone runs `writing-specs` against it. Two defenses, mirroring the `.compact-request`
sentinel's own staleness handling (`hooks/compact-injector.sh`'s `STALE_SECONDS=600`):

- **Reader-side staleness (primary defense):** `get_dev_workflow_stage` applies a stricter
  age cutoff to `.pending-*.json` files specifically —
  `PENDING_PLACEHOLDER_MAX_AGE_SECONDS = 3600` (one hour) in `attention_hub_client.py`,
  versus the general `STAGE_MAX_AGE_SECONDS` (7 days) applied to every other checkpoint. This
  bound was sized for a live interactive interview (these typically finish in minutes); it is
  not fully protective against the longer lifetime named above, since a standalone
  `creating-stories` run can leave the gap to `writing-specs` open far past an hour. Past the
  one-hour mark, the `.pending-*.json` file is skipped by the lookup exactly as if it did not
  exist, and `get_dev_workflow_stage` falls through to whatever prior checkpoint is freshest
  for that repo — genuinely stale-shadowing again, if a non-terminal entry from an unrelated
  prior story exists within the general 7-day `STAGE_MAX_AGE_SECONDS` window. This is
  genuinely better than the pre-fix unbounded case (bounded to at most 7 days rather than
  indefinitely), but it is not a fully bounded exposure, and this file's job is to say so
  rather than imply otherwise. The constant stays at 3600 rather than being raised: a longer
  cutoff would trade away its interview-abandonment protection without closing the gap for a
  backlogged story of any length.
- **Writer-side sweep (defense-in-depth):** before writing its own placeholder (step 2
  below), this procedure lists the state directory for existing `.pending-*.json` files and
  deletes any whose mtime is older than the same `PENDING_PLACEHOLDER_MAX_AGE_SECONDS`
  threshold — cleaning up a genuinely abandoned prior run's leftover file proactively,
  rather than waiting on the reader-side cutoff above. A pending file younger than the
  threshold is left alone here (it may belong to a concurrently running interview).
  `writing-specs`' own Phase 3 sweep (see that file's "Repo Discovery" section) is a separate,
  later deletion path with no age guard and no story-ID scoping (the placeholder carries no
  story ID to scope by), and it deliberately does not apply this same "may belong to a
  concurrent interview" caution: this is best-effort telemetry, never a functional gate, so
  the collateral cost of deleting a concurrent, unrelated `creating-stories` interview's
  placeholder is that interview's `init` display disappearing until it reaches its own Phase 6
  (or, since Phase 6 success no longer deletes it, until `writing-specs` runs for it) — never a
  functional break. Two concurrent `creating-stories` interviews for the exact same repo is
  rare enough that adding scoping machinery (an age guard or path-matching) to that sweep is
  not justified.

1. **Sweep stale pending files.** List `~/.claude/dev-workflow/state/.pending-*.json`; for
   each whose mtime is older than `PENDING_PLACEHOLDER_MAX_AGE_SECONDS` (3600 seconds),
   delete it. Failures here are non-fatal — skip a file that can't be removed and continue.
2. Build a `repos` map with one entry per candidate repo, each `{"stage": "init",
   "pr_number": null, "review_loop_count": 0, "test_loop_count": 0}`.
3. Write this to a uniquely named file in the checkpoint state directory:
   `~/.claude/dev-workflow/state/.pending-{unix-timestamp}-{pid}.json` — the leading dot
   matches the existing `.compact-request` sentinel's dot-prefixed convention for
   non-story runtime state in this directory. The `stage: "init"` entries are the freshest
   file on disk as soon as they're written, so they shadow any stale prior-story checkpoint
   for the same repo in `get_dev_workflow_stage`'s freshest-mtime-wins lookup — see
   `context-compaction.md`'s Legacy stage values note for how a reader treats an unrecognized
   stage value as opaque; `"init"` is simply never terminal, so it always matches (subject to
   the reader-side staleness cutoff documented above).
4. Return the placeholder file's path to the caller. `creating-stories` uses this returned
   path to delete the file immediately on its three abandonment paths (the Phase 2
   adapter-lacks-Create-Story stop, a Phase 5 user cancel, or a Phase 6 creation failure — see
   "Cleanup is the caller's responsibility, not this procedure's" below). On the story-creation
   success path, `creating-stories` does not use the returned path at all: it leaves the
   placeholder in place, and `writing-specs`' Phase 3 self-seed is what later discovers and
   deletes it, via its own independent glob sweep over `.pending-*.json` — not via this
   returned path, which by then belongs to a different (already-exited) caller.
5. **On any write failure:** surface the error to the user and continue — same
   best-effort posture as "Seed or Refresh Stage" above. Proceed with the rest of
   `creating-stories` unchanged; the interview and story creation do not depend on this
   placeholder existing.

**Cleanup is the caller's responsibility, not this procedure's.** This procedure only
creates the placeholder file and reports its path. Deletion happens on one of two paths,
neither of which is this procedure:

- **`creating-stories` deletes it immediately** on any of its three abandonment paths — the
  Phase 2 adapter-lacks-Create-Story stop, a Phase 5 user cancel, or a Phase 6 creation
  failure — since none of these ever produces a story that could reach `writing-specs` to
  supersede the placeholder.
- **`writing-specs` deletes it on normal completion.** On a Phase 6 creation success,
  `creating-stories` deliberately leaves the placeholder in place (see its own Phase 6
  instructions) rather than deleting it or writing a real checkpoint entry itself.
  `writing-specs`' Phase 3 self-seed is what supersedes it: once that self-seed writes the
  real `{story-id}.json` entry, Phase 3 sweeps in a single pass over every `.pending-*.json`
  file and deletes any whose `repos` list is now superseded by the repos it just seeded — not
  a per-repo deletion, since the placeholder is one file holding a `repos` map that can cover
  several repos.
