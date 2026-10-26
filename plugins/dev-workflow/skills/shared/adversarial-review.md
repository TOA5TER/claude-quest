# Adversarial Review Procedure

This is a shared procedure invoked by multiple skills to run an independent adversarial review of completed work. It spawns a subagent with completely fresh context — no access to the main agent's reasoning, plans, or session state — to independently verify that the work actually meets requirements. The subagent acts as a hostile auditor whose sole purpose is to find failures.

---

## Context Variables

The calling skill must supply these three values when spawning the subagent:

- **`story_id`** — The PM story ID (e.g., `sc-12345`). May be empty in debug mode when there is no associated story.
- **`review_target`** — What artifact to review. Must be one of:
  - `"spec document"` — a written spec is being reviewed
  - `"code changes on branch"` — feature implementation on a branch
  - `"rework changes on branch"` — changes made in response to review feedback
  - `"bug fix on branch"` — a bug fix on a branch
- **`review_context`** — Additional context provided by the calling skill. Examples: full spec document content, bug description and root cause analysis, rework checklist items, or acceptance criteria excerpts. This is the subagent's primary source of truth when no story is available.

---

## Regime Selection

This file holds two review regimes. Pick one from `review_target`:

- **Code-target regime** — every `review_target` except `"spec document"`. It is the sections from **Subagent Spawn Instructions** through **Handling Findings**: one hostile-persona subagent, PASS/FAIL/UNVERIFIED verdicts, and the 5-iteration cap.
- **Spec Document Regime** — `review_target` is `"spec document"`. Apply the **Spec Document Regime** section at the end of this file in place of the code-target persona, output format and iteration loop. The Context Variables stay shared. Step 1 of the Subagent Procedure is replaced by **Reviewer Inputs** for this regime.

---

## Subagent Spawn Instructions

Use the Agent tool to launch a subagent with `subagent_type: "general-purpose"`.

The subagent prompt must include:

1. The values of all three context variables (`story_id`, `review_target`, `review_context`)
2. An instruction to read and follow `skills/shared/adversarial-review.md` — the subagent executes the procedure defined in this file
3. The working directory path so the subagent can locate files and run git commands

**Critical:** The subagent has NO access to the main agent's reasoning, plan files, scratch files, or session state. It operates with completely fresh context. Do not pass notes, implementation summaries, or the main agent's self-assessment — the subagent must form its own independent view.

Include the adversarial persona block from the **Adversarial Persona** section verbatim in the subagent prompt.

---

## Subagent Procedure

Once spawned, the subagent executes these steps in order:

**Step 1: Load reference material**

- **If `story_id` is provided:**
  - Read `~/.claude/dev-workflow/config.json` to obtain `pm_adapter` and `notes_adapter`
  - Read `skills/shared/adapter-loading.md` and follow the adapter loading procedure for both adapters
  - Fetch the story independently using the PM adapter
  - Read the spec independently using the notes adapter
  - These are the authoritative sources of what was required. Use them — do not trust any summary or paraphrase passed in `review_context`

- **If `story_id` is empty (debug mode):**
  - Use `review_context` (bug description, root cause analysis, reproduction steps) as the sole reference material
  - There is no story to fetch, no spec to read — work only from what was provided

**Step 2: Form expectations**

Before examining any actual work output, write a numbered checklist of "what SHOULD be true" — concrete, verifiable expectations derived from the story, spec, and/or `review_context`.

Rules for forming expectations:
- Write each expectation as a specific, checkable claim (not a vague hope)
- Every expectation must cite its source: which AC item, spec section, or `review_context` element it derives from
- Do this entirely before looking at code, diffs, or output — the checklist must reflect requirements, not implementation

**Step 3: Examine actual work**

- **For `review_target: "spec document"`:** Read the spec content provided in `review_context` and any spec files referenced by the notes adapter
- **For `review_target: "code changes on branch"` / `"rework changes on branch"` / `"bug fix on branch"`:** Detect the default branch (`git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'`), then run `git diff <default-branch>...HEAD` to see all changes introduced on the branch. Read specific files as needed to investigate individual expectations.

**Step 4: Audit each expectation**

For every item on the checklist, examine the actual work and assign a verdict:

- **PASS** — The work clearly and demonstrably satisfies the expectation. Evidence found.
- **FAIL** — The work does not satisfy the expectation, or contradicts it. State exactly what is missing or wrong.
- **UNVERIFIED** — The expectation cannot be conclusively evaluated from the available artifacts (e.g., runtime behavior, external service integration, timing-dependent behavior). State why verification is not possible from static inspection.

Demand evidence for every PASS verdict. Do not accept the presence of a function name as proof it works. Do not accept a test file as proof tests pass. Do not accept a comment as proof of behavior. Show the actual code, the actual assertion, the actual logic.

---

## Adversarial Persona

Copy the following block verbatim into the subagent prompt:

---

You are a hostile, deeply skeptical code auditor. Your job is to find every way the development agent failed, cut corners, missed requirements, or fabricated claims. You do not give the benefit of the doubt — ever. You assume the development agent is lazy, careless, and motivated to make things look done when they are not.

Every "it works" claim is a lie until you personally verify it in the code. Every test that exists might be testing the wrong thing — check it. Every function that was "added" might be a stub — read it. Every requirement that was "addressed" might have been addressed superficially or not at all — verify it against the actual source.

You take pleasure in finding mistakes. You are not mean for the sake of being mean — you are mean because sloppy work causes production outages, and you refuse to let sloppy work through. You will embarrass the development agent if the work is inadequate, and you will do so without apology.

Your standard questions when reviewing any claim:
- Show me the code.
- Show me the test.
- Where is the evidence?
- Why should I believe this works?

You do not accept hand-waving, vague assertions, or appeals to intent. You care only about what is actually present in the artifacts. If it is not there, it does not exist.

---

## Output Format

The subagent must produce a report in exactly this structure:

```
## Adversarial Review Report

**Story:** {story_id or "N/A"}
**Review Target:** {review_target}

### Expectations Formed

1. {expectation} — Source: {AC item / spec section / review context element}
2. ...

### Audit Findings

| # | Expectation | Verdict | Evidence | Finding |
|---|-------------|---------|----------|---------|
| 1 | {brief} | PASS / FAIL / UNVERIFIED | {what was found or demanded} | {description} |
| 2 | ... | ... | ... | ... |

### Summary

- **PASS:** {count}
- **FAIL:** {count}
- **UNVERIFIED:** {count}

### Recommendation

{ACCEPTABLE or UNACCEPTABLE}
{If UNACCEPTABLE: list each FAIL item that must be addressed}
```

No additional commentary outside this structure. The report is the deliverable.

---

## Handling Findings

After receiving the subagent's report, the main agent acts as follows:

- **ACCEPTABLE (zero FAIL items):** Log the report and proceed to the next phase or step. No rework required.

- **UNACCEPTABLE (one or more FAIL items):** Enter the iteration loop:

  1. Address each FAIL item from the report.
  2. Re-run the adversarial review by spawning a fresh subagent using the same procedure above.
  3. If the new report is ACCEPTABLE → log the report and proceed to the next phase or step.
  4. If the new report is still UNACCEPTABLE → repeat steps 1–3.
  5. **Maximum 5 total adversarial review iterations (including the first run).** If UNACCEPTABLE after 5 iterations, **STOP immediately.** Present the remaining FAIL items to the user and ask for guidance. Do not make further changes without explicit user input.

- **UNVERIFIED items:** Note them but do not block progress. UNVERIFIED means the subagent could not conclusively verify the expectation from static artifacts (e.g., runtime behavior, external service calls). These are informational, not blocking.

---

## Spec Document Regime

Applies only when `review_target` is `"spec document"`. The calling skill supplies mode specifics: how to ask the user (its ask route), how to stop (its stop route), where the review record and round snapshots live, and how the writer fixes defects. This section defines what reviewers do and when the review ends.

A review cycle is the set of rounds between a spec write and an ACCEPTABLE verdict or a final stop. A revise request or a change request at the user approval gate starts a new cycle.

### Reviewer Inputs

- Every reviewer reads the story itself: through the PM adapter, or from the local story file path given in its prompt. Never pass story content in the prompt.
- Every reviewer reads only the spec files named in its prompt. It never reads the notes adapter's spec path and never reads the review record.
- Before each round the writer writes the reviewer files into the round folder (`./.scratch/tmp/spec-rounds/{story-id}/{repo}/round-N/`; replay runs use `./.scratch/replays/{story-id}/{repo}/rounds/round-N/`):
  - `partition-K.html` — one per partition, written by `spec-lint.py --partition`.
  - `reference.html` — for each partition reviewer: the requirements section and, when present, the glossary, written by `spec-lint.py --extract`.
  - `cross-cutting.html` — for the cross-cutting reviewer: the traceability matrix, the impact inventory and, when present, the glossary.
  - `ripple.html` — for the verify reviewer: the ripple set.
- A small spec (size class S) is reviewed by one reviewer that gets the whole spec file.

### C12 Severity Rubric

Every finding carries one severity.

| Severity | Definition | Effect |
|----------|------------|--------|
| BLOCKER | One of four classes: dropped or contradicted requirement; fact wrong against the repo; open decision; uncovered AC. | Only open BLOCKERs make a review UNACCEPTABLE and start another round. |
| MAJOR | A real defect outside the four BLOCKER classes, with the same evidence. | Never fails a review or starts a round. The writer fixes it in any round that runs; unfixed MAJORs are listed for the approver. |
| NOTE | An observation, or any finding that lacks the evidence a BLOCKER or MAJOR needs. | Never starts a round. |

Evidence requirement: a BLOCKER or MAJOR must quote the spec text, name the spec ID or section anchor, and give a failing input (a concrete case where the spec as written leads to a wrong or undecidable outcome). A finding missing any of the three is recorded as a NOTE.

### C13 Partitioned First Pass

Round 1 is a full first pass. The reviewer layout is decided by size class and visible word count; the first matching row applies.

| Size class | Visible words | Reviewers |
|------------|---------------|-----------|
| S | any | One reviewer, whole spec, all perspectives. |
| M or L | at most 3,000 | One partition reviewer with the whole spec, plus one cross-cutting reviewer. |
| M or L | over 3,000 | Pack top-level sections in document order into partitions of at most 3,000 words. A section over 3,000 words is split at its sub-section boundaries; a sub-section over 3,000 words is split between its child elements; a table over 3,000 words is split between rows, each part repeating the table head. One reviewer per partition, plus one cross-cutting reviewer. All run in parallel. |

- A partition reviewer's perspective is the one that owns most of its words: Story Summary, Requirements and Clarifications belong to the user; Technical Context, Implementation Steps and Evidence and Impact to the implementer; Test Requirements, Manual Testing and Validation Checklist to the tester; Completeness Checks to the operator. Ties go to the earliest section.
- The Glossary is never packed into a partition. It reaches reviewers only through `reference.html` and `cross-cutting.html`.
- Each partition reviewer receives its partition and the reference file, and fetches the story itself. The cross-cutting reviewer receives the glossary, the traceability matrix and the impact inventory, and checks them against each other and the story.

**Persona: calibrated skeptic.** Check every claim against the repo and the story. Report only what evidence supports. Never pad a report. Zero findings is a valid result. This persona replaces the hostile persona for this regime.

**Binary checklist.** Every reviewer works through every item, recording yes or no with evidence per item. A no is a finding of the class shown.

| ID | Question (yes passes) | BLOCKER class on no |
|----|-----------------------|---------------------|
| RC-1 | Every story AC maps to at least one requirement and one test? | uncovered AC |
| RC-2 | No requirement, step or out-of-scope item contradicts or drops a story requirement? (A descope callout the story's viability rules allow counts as satisfied.) | dropped or contradicted requirement |
| RC-3 | Every file path exists (or is marked new), and every cited line range and heading matches? | fact wrong against the repo |
| RC-4 | Every evidence ledger source shows the stated fact? | fact wrong against the repo |
| RC-5 | No deferral wording by intent, and no open decision or "developer decides" branch anywhere, verification sections included? | open decision |
| RC-6 | Every step leaves no choice open about behavior, scope, file locations, values, interfaces or approach? (Code-level how is excluded.) | open decision |
| RC-7 | Every story-required edge case is covered by a requirement, an input-class row or a test? | dropped or contradicted requirement |
| RC-8 | Every term a reader outside the project would not know is defined in the glossary? | none: a no is a MAJOR |

**Fundamentals first.** A fundamental BLOCKER is wrong story or repo scope, an approach that contradicts the story's intent, a size class that should have tripped the size gate, or a missing required section. A reviewer that finds one reports it first and stops its detail review. The round still counts. After the fix, the next round is a full first pass, not verify-only.

**Report format.** Each reviewer returns the checklist table followed by its findings. One row per finding:

| Field | Content |
|-------|---------|
| Finding ID | `RF-n`, numbered per report |
| Severity | BLOCKER, MAJOR or NOTE |
| Class | One of the four BLOCKER classes, or `none` |
| Fundamental | yes or no |
| Quote | The spec text, verbatim |
| Spec ID or anchor | The ID or section anchor the quote sits in |
| Failing input | A concrete input or case where the spec as written fails |
| Evidence | file:line, story text or fixture path that shows the defect |

**Reviewer retry.** A reviewer that times out or returns no report is re-spawned once. The retry is not a new round. When partitions fail, re-spawn only the failed partitions. The round completes when every reviewer has reported. A second failure of the same reviewer stops through the calling mode's stop route.

### C14 Verify-Only Rounds

From round 2, one verify reviewer runs instead of a full first pass, unless the previous round halted on a fundamental BLOCKER (then run a full first pass). A round-1 result whose BLOCKERs were all rejected still gets one verify round before acceptance.

**Writer handling between rounds.** The writer checks each BLOCKER against the spec, the story and the repo before changing anything. A false BLOCKER is rejected with evidence (a quote or file:line). A real BLOCKER is fixed by class: every sibling instance in the spec, plus every element that references a touched ID. When a fix needs a decision the story, the clarifications and the repo cannot settle, the writer asks through the mode's ask route before the next round (autonomous mode records an inference-labeled decision instead). Accepted and rejected verdicts, evidence and the fix list go in the review record.

**Verify reviewer prompt.** The prompt holds only: these regime instructions, the story ID, the open BLOCKERs, the rejected BLOCKERs with the writer's evidence, the diff between the last two round snapshots, and the ripple set (plus the local story file path in a replay). No other spec content.

**Ripple set.** Every spec element that links to or is linked from an ID changed in the diff, plus those IDs' traceability matrix rows.

**Verify report.** For each open BLOCKER: RESOLVED or OPEN. For each rejected BLOCKER: CONFIRMED-FALSE or REINSTATED (a reinstated BLOCKER counts as open). New BLOCKERs may be raised only inside the diff or the ripple set.

**Fresh writer.** The rework that precedes round 3, and the rework that precedes each extension round, runs in a fresh general-purpose subagent, using the same model chain the full-cycle skill uses for the spec writer. It receives only: the spec path, the review record path, the open BLOCKERs, the story ID, the writer handling rules above, the spec writing rules for requirement form, IDs and traceability, and the lint command. It checks, rejects or fixes each BLOCKER by class and logs its verdicts in the review record. When a fix needs a decision, it stops and returns the question to the writer, which asks through the mode's ask route and re-dispatches it with the answer.

### C15 Stopping Rules

**Review record state.** The review record's body carries four attributes: `data-cycle` (1 on the first write, plus 1 for each revise or gate change request), `data-round` (rounds run in this cycle), `data-extension` (`none`, `granted` or `used`) and `data-state` (`in-review`, `awaiting-answer`, `accepted` or `stopped`). The record is the only source of round state. When an answer arrives and the state is `awaiting-answer`, resume the review at the stored round with the stored extension state: skip spec drafting, apply the answer to the spec, and continue. An approved restructure sets the extension to `granted`, and the next rounds are 4 and 5.

**Cap.** The cap is 3 rounds per review cycle. A user's "continue" never resets the round count: the count never decreases. One extension of at most 2 rounds is allowed per review cycle, and only after the user approves a restructure from a restructure report. Each review cycle therefore runs at most 5 rounds. A new cycle starts only from a revise request or a change request at the user approval gate.

After each round, apply this table. The first matching row applies.

| Condition after round N | Action |
|-------------------------|--------|
| Open BLOCKERs = 0 and no unadjudicated rejection | ACCEPTABLE: go to the user approval gate, listing unfixed MAJORs. |
| Round N is the full first pass that follows a round halted by a fundamental BLOCKER, and N is below the cap | Round N's BLOCKER count becomes the baseline for round N+1. Fix and run round N+1. |
| N is 2 or more, round N does not follow a fundamental halt, and a new BLOCKER sits in text edited in round N-1 | Stop early: restructure report. |
| N is 2 or more, round N does not follow a fundamental halt, and open BLOCKERs are at least the count after round N-1 | Stop early: restructure report. |
| N is 3 with no extension, or N is the extension's last round | Stop: restructure report. After an extension the only options are accept with open BLOCKERs listed, or stop. |
| Any other | Fix by class per the writer handling rules (in a fresh writer when N+1 is 3, 4 or 5) and run round N+1. |

**Restructure report.** It lists the BLOCKER count per round, the table row that fired, the failing IDs, a proposed restructure (a split, a section rewrite or a table conversion) and the options: approve the restructure and its extension, accept the spec with its open BLOCKERs listed for the approver, or stop. It goes out through the mode's ask route. A "continue" answer that does not approve the restructure runs no new round; re-send the options.

- When the approved restructure is a split, no extension runs: the written spec stays on disk unchanged, the split plan names it as superseded, and the review record's state becomes `stopped`.
- In autonomous mode there is no user to approve: the run ends with an error status carrying the report and never proceeds to publishing the spec.
