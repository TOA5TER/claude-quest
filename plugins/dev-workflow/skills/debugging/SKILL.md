---
name: dev-workflow:debugging
description: "Three-mode unified skill for debugging bugs, standalone story implementation, and addressing PR review feedback (rework). Mode is auto-detected from arguments: no args = debug mode (investigate a reported bug), story-id only = development mode (implement a story), story-id + --rework = rework mode (address reviewer feedback). Use whenever a bug needs systematic investigation, review changes need addressing, or a story needs implementing without the full developer workflow."
---

# Debugging (Debug / Development / Rework)

**Role:** Unified skill for debugging, development, and rework — mode detected from arguments

**SCOPE BOUNDARY:** This skill **never** creates PM stories, tickets, issues, or subtasks — the Story Creation Gate in `skills/shared/standards.md` applies.

## Arguments: $ARGUMENTS

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Development Mode and Rework Mode.

## Mode Detection

Parse arguments:
- **Empty** → **Debugging mode** — wait for user to describe the bug
- **Story ID only** (e.g., `sc-12345`) → **Development mode** — implement the story
- **Story ID + `--rework`** (e.g., `sc-12345 --rework`) → **Rework mode** — address story comments

---

## Debugging Mode

*Triggered when: no arguments provided*

### Step 1: Gather Information
Wait for the user to describe the bug. Do not proactively ask questions — let them guide the session.

You need:
- What is broken
- Expected vs actual behavior
- Steps to reproduce
- Affected environment(s)

### Step 2: Reproduce the Bug
- Follow the reproduction steps provided
- Verify the issue occurs as described
- Document exact conditions that trigger it

### Step 3: Collect Evidence
- Gather logs, error messages, stack traces
- Check recent git history: `git log --oneline -20`
- Review test coverage for affected areas

### Step 4: Root Cause Analysis

Apply systematic debugging discipline before tracing code:

> Invoke Skill: `superpowers:systematic-debugging`
>
> Use the evidence from Step 3 as the input to Phase 1 (Root Cause Investigation).
> Complete all four phases before proceeding to Step 5. Do not propose any fix until
> hypothesis testing (Phase 3) is complete.

- Trace code execution to where behavior diverges
- Use Read tool to examine relevant files
- Use Grep to find related patterns
- Document findings with file:line references

### Step 5: Fix with TDD

Apply the full TDD cycle:

> Invoke Skill: `superpowers:test-driven-development`
>
> Write the failing test that reproduces the root cause from Step 4. Watch it fail (RED).
> Implement only the root-cause fix (GREEN). Run full suite and refactor if needed (REFACTOR).
> Do not implement changes beyond what is required to fix this specific root cause.

- **Write failing test first** that reproduces the bug
- Verify test fails before fixing
- Implement fix following existing patterns
- Verify test passes after fix
- Run full test suite to check for regressions

### Step 5.5: Verify Fix Before Committing

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh execution:
> - The bug-reproducing test now passes (run it now)
> - The full test suite passes (run it now)
> - No regressions in adjacent modules

### Step 6: Commit and Create PR
- Branch naming: `fix/[brief-description]`, chosen per `skills/developing/SKILL.md` → Branch Management (caller-supplied branch first, then the repo's branch-name rule looked up before creating the branch, then the story token default). A debugging run with no story keeps its token-free branch name.
- PR body: follow `skills/developing/SKILL.md` → PR Creation Requirements; the bug, root cause (with file:line refs), and fix go in whichever template section describes the changes
- Include test results as evidence
- Attach the PR to the story as described in PR Creation Requirements; a run with no story skips the attach

### Step 7: Adversarial Review

Read and follow the adversarial review procedure in `skills/shared/adversarial-review.md` with these context variables:

- `story_id`: (empty — no story in debug mode)
- `review_target`: "bug fix on branch"
- `review_context`: the bug description from Step 1, root cause analysis from Step 4, and the reproduction test name from Step 5

The adversarial agent verifies the fix actually addresses the identified root cause, the test genuinely reproduces the original bug (not a different scenario), and no regressions were introduced.

---

## Development Mode

*Triggered when: story ID provided (no --rework flag)*

### Step 1: Load Adapters and Story
1. Read `~/.claude/dev-workflow/config.json` for `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story by ID
3. Load notes adapter per procedure in `skills/shared/adapter-loading.md` → read Claude Instructions spec
4. **If spec not found:** STOP and ask user to invoke the Writer skill (`dev-workflow:writing-specs`) with this story ID first. Never create a story, ticket, or issue to fill the gap

### Step 1.5: Write Implementation Plan

After loading the Claude Instructions spec:

> Invoke Skill: `superpowers:writing-plans`
>
> OVERRIDE: Save plan to `./.scratch/tmp/YYYY-MM-DD-<story-id>-plan.md`.
> Use the Claude Instructions spec as the feature description.

### Step 2: Branch
- Check current branch — if on `main`, the new branch (`feature/`, `fix/`, or `chore/`) is created inside the isolated worktree set up in Step 2.5, not checked out here first
- If already on a feature branch, check if a PR exists
- Name the branch per `skills/developing/SKILL.md` → Branch Management: look up the repo's branch-name rule before creating it (`gh api repos/OWNER/REPO/rules/branches/<candidate-branch>`), and default to the story token after the prefix. A run with no story keeps a token-free branch name.

### Step 2.5: Subagent-Driven Implementation

With the plan written, and before creating the branch:

> Invoke Skill: `superpowers:subagent-driven-development`
>
> IMPORTANT OVERRIDE: Proceed automatically with subagents without asking the user for
> confirmation. Dispatch subagents and proceed.
>
> REQUIRED: Set up workspace isolation before starting, per `skills/shared/standards.md` →
> "Workspace Isolation" — the worktree carries the feature branch (see Step 2 above).
> Worktree isolation is required for this task — proceed without asking; if baseline tests
> fail, report the failure and stop rather than asking whether to proceed. When this
> reaches a nested `finishing-a-development-branch` invocation, pass it the cleanup
> instruction that skill needs (preserve the worktree for PR-feedback iteration), not the
> creation requirement above — that skill only tears down.

### Step 3: Implement with TDD
- Write failing tests first based on acceptance criteria

   Apply the full RED-GREEN-REFACTOR cycle:
   > Invoke Skill: `superpowers:test-driven-development`

- Implement following Claude Instructions step-by-step
- Run tests after each significant change
- Follow existing architecture patterns

### Step 3.5: Verify Before Pushing

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh execution:
> - All tests pass (run the full suite now)
> - No files left unstaged

### Step 4: Commit and Push
- Commit frequently with descriptive messages
- Run all tests and fix any failures before pushing
- Push to remote branch

### Step 5: Create PR
- Title: concise and descriptive
- Body: follow `skills/developing/SKILL.md` → PR Creation Requirements (the repo's pull request template)
- Attach the PR to the story as described there
- NO AI-generated boilerplate

### Step 6: Adversarial Review

Read and follow the adversarial review procedure in `skills/shared/adversarial-review.md` with these context variables:

- `story_id`: the story ID from arguments
- `review_target`: "code changes on branch"
- `review_context`: the Claude Instructions spec loaded in Step 1

The adversarial agent verifies the implementation satisfies all spec requirements and story acceptance criteria.

**Worktree cleanup is manual when Development Mode runs standalone.** When dispatched by `full-cycle`/`epic`, those orchestrators own reclamation (see `full-cycle/SKILL.md`'s Termination section / `epic/SKILL.md`'s `awaiting-merge → done` transition). A human running `debugging <story-id>` directly has no such reclamation point — look up the worktree live via `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is this story's feature branch), and once the PR is merged the human should run `git -C <repo root> worktree remove <path>` followed by `git -C <repo root> worktree prune` (or delegate to `superpowers:finishing-a-development-branch`).

---

## Rework Mode

*Triggered when: story ID + `--rework` provided*

### Step 1: Load Context
1. Read `~/.claude/dev-workflow/config.json` for `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story by ID (title, description, acceptance criteria, **all comments**)
3. Load notes adapter per procedure in `skills/shared/adapter-loading.md` → read Claude Instructions spec (best-effort — continue even if missing; rework requirements from comments take precedence)

### Step 2: Extract Rework Requirements

Parse all story comments chronologically (oldest to newest). Identify:
- Reviewer change requests
- Rejection notes or reasons
- Bug reports or edge cases found
- Requests to update logic, naming, structure, or tests

**Summarize as a numbered checklist before writing any code:**

```
## Rework Items Identified

1. [Summary of item from comment dated YYYY-MM-DD by @author]
2. [Summary of another item]
```

**If no comments indicate rework:**
- STOP and ask: "I don't see any comments describing rework requirements. Can you clarify what needs to be changed?"

### Step 2.5: Process Review Feedback

Apply structured reception of the code review feedback before implementing:

> Invoke Skill: `superpowers:receiving-code-review`
>
> Use the rework checklist from Step 2 as the "feedback" input. Complete the RECEPTION
> (restate items), VERIFICATION (check against actual codebase), EVALUATION (identify
> any valid pushback), and RESPONSE phases before creating the branch in Step 3. This
> ensures each rework item is understood in codebase context before implementation.

### Step 3: Create New Branch

**ALWAYS** create a new `fix/` branch — never reuse an existing rework branch. Set up
workspace isolation first, per `skills/shared/standards.md` → "Workspace Isolation": use
`superpowers:using-git-worktrees` to create an isolated workspace, then create the branch
(`fix/<story-id>-rework`, or more descriptively `fix/<story-id>-address-review-feedback`) inside
it — not in the primary checkout, the way an unqualified `git checkout -b` would here. These
names embedding the story ID are the default suggestion; look up the repo's branch-name rule per
`skills/developing/SKILL.md` → Branch Management before creating the branch, and use a different
shape only when that rule requires one. Work
from that worktree for every remaining step. Worktree isolation is required for this task —
proceed without asking; if baseline tests fail, report the failure and stop rather than
asking whether to proceed.

### Step 4: Implement with TDD

- Address ONLY the items from story comments — no scope creep
- Write failing tests for each rework item, then fix

   Apply the full RED-GREEN-REFACTOR cycle:
   > Invoke Skill: `superpowers:test-driven-development`

- Commit frequently with descriptive messages

### Step 4.5: Verify Before Creating PR

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh execution:
> - Each rework item from the Step 2 checklist has a passing test
> - Full test suite passes
> - Code is pushed to remote

### Step 5: Create PR

Build the PR body per `skills/developing/SKILL.md` → PR Creation Requirements (the repo's pull request template). List the reworked items, and what changed for each, in whichever template section describes the changes made. Attach the PR to the story as described there.

### Step 6: Adversarial Review

Read and follow the adversarial review procedure in `skills/shared/adversarial-review.md` with these context variables:

- `story_id`: the story ID from arguments
- `review_target`: "rework changes on branch"
- `review_context`: the rework checklist from Step 2 and the Claude Instructions spec from Step 1

The adversarial agent verifies each rework item from the checklist was properly addressed.

**Worktree cleanup is manual when Rework Mode runs standalone.** When dispatched by `full-cycle`/`epic`'s review or test fix loop, those orchestrators own reclamation (see `full-cycle/SKILL.md`'s Termination section / `epic/SKILL.md`'s `awaiting-merge → done` transition). A human running `debugging <story-id> --rework` directly has no such reclamation point — look up the worktree live via `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is this story's feature branch), and once the PR is merged the human should run `git -C <repo root> worktree remove <path>` followed by `git -C <repo root> worktree prune` (or delegate to `superpowers:finishing-a-development-branch`).

---

