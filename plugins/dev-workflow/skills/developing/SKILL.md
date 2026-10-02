---
name: dev-workflow:developing
description: "Full-stack development workflow with story context loading, TDD, automated planning, subagent execution, and PR creation. Use this skill whenever implementing features, fixing bugs, or any hands-on coding task. Always use this when a user says 'implement', 'build', 'code up', 'add feature', 'start dev', or provides a story ID to work from. Works with or without a PM story."
---

# Developing

**Role:** Developer — implement features, fix bugs, and maintain code quality

**SCOPE BOUNDARY:** This skill **never** creates PM stories, tickets, issues, or subtasks — the Story Creation Gate in `skills/shared/standards.md` applies. Necessary extra work discovered mid-pipeline is governed by "Necessary Extra Work — No Follow-On Tickets" in `skills/shared/standards.md`: fold it into the current branch/PR by default; only a huge scope increase warrants stopping to ask — never a follow-on ticket.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in PM Context.

Read `skills/shared/repo-discovery.md` — repo discovery procedure referenced in Repo Discovery.

Read `skills/shared/checkpoint-seeding.md` — checkpoint seeding procedure referenced in
PM Context and PR Creation Requirements.

Read the CLAUDE.md file in this repository before starting.

---

## Branch Management

- **ALWAYS** start by creating a new branch with prefix `feature/`, `fix/`, or `chore/`. On the story-ID path ("Implementation Planning" below), that branch is created inside the isolated worktree required by `skills/shared/standards.md` → "Workspace Isolation" — never checked out in the primary checkout first. The No Story ID path (no PM story, ad hoc interactive work) checks it out directly, as today.
- Branch names should be descriptive: `feature/slack-monitoring`, `fix/docker-permissions`, `chore/update-dependencies`
- Check which branch you're on first. If not on `main`, you may already be on the correct branch — check if a PR is already open.

---

## No Story ID Path

If **no story ID** was provided, work directly with the user to define and scope the task:

1. **Understand the task** — If the request is vague or missing, use `AskUserQuestion` to clarify what needs to be built or changed.
2. **Brainstorm requirements** — Before writing any code, invoke brainstorming to clarify scope boundaries and identify risks within the stated requirements:
   > Invoke Skill: `superpowers:brainstorming`
   >
   > OVERRIDE: After brainstorming completes, do NOT invoke `superpowers:writing-plans` yet.
   > Return here and proceed to step 3.
3. **Plan implementation** — Invoke the planning skill:
   > Invoke Skill: `superpowers:writing-plans`
   >
   > OVERRIDE: The plan is a human-readable artifact saved to a local file — render it as a
   > standalone HTML document per the Output Format rules in `skills/shared/standards.md`, and
   > save to `./.scratch/tmp/YYYY-MM-DD-plan.html`.
   > NEVER save to `docs/` or any subdirectory (including `docs/superpowers/plans/`).
   > `.scratch/` is gitignored — this file must never be committed.
   > Use the brainstorming output and user's description as the feature description input.
4. **Implement** — Use the Development Standards below. Apply TDD for each distinct behavior.
5. **Commit and PR** — Follow the Commit and PR Process below. Include a clear description of what was built and why.

After planning, skip the "PM Context" and "Implementation Planning" sections — continue from **Development Standards**.

---

## PM Context (if story ID provided)

If you have a story ID:

1. Read `~/.claude/dev-workflow/config.json` to determine `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story via PM adapter instructions
3. Load notes adapter per procedure in `skills/shared/adapter-loading.md`
4. Read the **"Repos to modify"** field from the story (a comma-joined list of repo/service names), then load the Claude Instructions spec(s) via the notes adapter:
   - **Single repo (or field absent):** follow today's single-repo flow unchanged — load one spec and continue as before.
   - **Multiple repos:** load the spec for EACH named repo. If any spec is missing, STOP and ask the user to run `dev-workflow:writing-specs` for the story first.
5. **If a required spec is not found:** STOP and ask user to invoke the Writer skill (`dev-workflow:writing-specs`) with this story ID first. Never create a story, ticket, or issue to fill the gap
6. Use spec(s) as the primary implementation guide

### Repo Discovery

If the dispatch prompt supplied an explicit repo path (a `Repo path:` field), use it directly
as the resolved single repo root and skip re-running `skills/shared/repo-discovery.md`'s
two-path detection — the orchestrator already resolved it. Only fall back to running the full
procedure below (including its multi-repo per-repo loop) when no repo path was supplied.

Otherwise, determine which checkout(s) to operate on per `skills/shared/repo-discovery.md` (two-path detection, the "Repos to modify" precedence rules, per-item repo tags, and the single-repo shortcut). Each Path-2 repo is its own checkout in its own sibling folder with its own feature branch.

Once the repo(s) are resolved, call `skills/shared/checkpoint-seeding.md`'s "Seed or Refresh
Stage" with this story's ID, every resolved repo, and stage `"developing"` — this is what
makes a standalone `developing` run visible to attention-hub immediately, without waiting
for `full-cycle` to write anything. (The "No Story ID Path" above makes no call — there is
no story ID to key a checkpoint by.)

**The worktree root supersedes the repo root once a worktree exists.** Whichever of the above
resolved the "repo root" (a supplied `Repo path:`, or this section's own discovery), that value
is only the starting point — per `skills/shared/standards.md` → "Workspace Isolation", the next
step in "Implementation Planning" below creates or verifies an isolated worktree for that repo,
and from that point forward the **worktree root**, not the plain repo root, is "the resolved repo
root" for every remaining step (implementation, commits, PR creation). Before creating a new
worktree, always check live for an existing one: run `git -C <repo root> worktree list
--porcelain` and match the entry whose branch equals this story's feature branch name — per
`skills/shared/standards.md` → "Workspace Isolation" and mirroring the developer agent's rework-mode
lookup. If found, treat it as authoritative immediately and `cd` there — do not create a second
worktree. If not found, create one. The multi-repo path below performs this same lookup
independently per repo, in its own per-repo sub-agent, as described in "Multi-repo path" → Step 3.

---

## Implementation Planning (when story ID and spec are loaded)

### Single-repo path (one repo named or field absent)

After loading the Claude Instructions spec, invoke the planning skill:

> Invoke Skill: `superpowers:writing-plans`
>
> OVERRIDE: The plan is a human-readable artifact saved to a local file — render it as a
> standalone HTML document per the Output Format rules in `skills/shared/standards.md`, and
> save to `./.scratch/tmp/YYYY-MM-DD-<story-id>-plan.html`.
> NEVER save to `docs/` or any subdirectory (including `docs/superpowers/plans/`).
> `.scratch/` is gitignored — this file must never be committed.
> Use the Claude Instructions spec as the feature description input.

Then invoke subagent-driven execution:

> Invoke Skill: `superpowers:subagent-driven-development`
>
> IMPORTANT OVERRIDE: Proceed automatically with subagents without asking the user for
> confirmation. Dispatch subagents and proceed.
>
> REQUIRED: Set up workspace isolation before implementation starts, per
> `skills/shared/standards.md` → "Workspace Isolation" — the worktree carries the feature
> branch (see "Repo Discovery" above and "Branch Management" for why it is never checked
> out in the primary checkout first). Worktree isolation is required for this task —
> proceed without asking; if baseline tests fail, report the failure and stop rather than
> asking whether to proceed. When this reaches a nested `finishing-a-development-branch`
> invocation, pass it the cleanup instruction that skill needs (preserve the worktree; a
> human iterates on PR feedback there), not the creation requirement above — that skill
> only tears down, it never creates a workspace.

### Multi-repo path (two or more repos named in "Repos to modify")

Move the story to **"In Development" exactly once** — at the start of the entire run, before any per-repo work begins. Do NOT repeat this transition per repo.

**State ownership:** developing owns the "In Development" transition; writing-specs owns "Ready for Dev". Each skill fires only its own transition — never the other's.

#### Step 1 — Infer the cross-repo dependency graph

Examine each repo's Claude Instructions spec for inter-repo dependencies. A dependency exists when one repo's spec consumes an artifact that another repo's spec introduces — for example: an HTTP endpoint, a shared data contract, a published package, an event schema, or an output file. Identify all such producer → consumer edges.

Topologically sort the repos into **dependency levels**:

- **Level 0:** repos with no dependency on any other repo in the set (they can start immediately).
- **Level 1:** repos whose only dependencies are on Level 0 repos.
- **Level N:** repos whose dependencies are fully satisfied by levels 0 … N-1.
- Repos with no detected dependency between them sit at the same level and run concurrently.

Display the computed dependency graph and level groupings to the user before proceeding.

#### Step 2 — Enqueue per-repo work in dependency order

For each level, enqueue one planning + implementation task per repo in that level. The task list must reflect the computed order so the execution sequence is visible. Example structure:

```
Level 0 (concurrent): [repo-a, repo-b]
Level 1 (concurrent, after Level 0): [repo-c]
Level 2 (concurrent, after Level 1): [repo-d, repo-e]
```

#### Step 3 — Execute level by level

**This agent dispatches one sub-agent per repo for the level.** Each per-repo sub-agent performs its own implementation directly. As of Claude Code **v2.1.172** a sub-agent *may* itself nest further sub-agents (fixed depth-5 cap, when it has the `Agent` tool — see `skills/shared/standards.md` → "Subagent Nesting"), so this stage may run as a nested subagent under `full-cycle`/`epic` and still dispatch its per-repo workers. On builds older than v2.1.172, nesting is unavailable and the per-repo sub-agents run inline within whichever context dispatched this stage. Either way, this preserves cross-repo concurrency across a level; per-task parallelism *inside* a single repo is not pursued here.

Process each level as follows:

1. **Before dispatching the level,** invoke `superpowers:writing-plans` once per repo in that level (using that repo's spec), saving each plan to `./.scratch/tmp/YYYY-MM-DD-<story-id>-<repo-name>-plan.html`.

2. **Within a level — dispatch one sub-agent per repo, concurrently** (up to Claude Code's concurrent sub-agent cap). Each sub-agent receives:
   - The repo's checkout path and feature branch name.
   - That repo's Claude Instructions spec and the plan from step 1 as the implementation guide.
   - The full Development Standards below (TDD, no placeholder code, etc.).
   - The instruction: **Implement the plan directly — for each task write a failing test, make it pass, refactor, and commit. Do NOT invoke `superpowers:subagent-driven-development`, `superpowers:executing-plans`, or any skill that spawns sub-agents; you are a leaf sub-agent and cannot dispatch further sub-agents.**
   - Workspace isolation per `skills/shared/standards.md` → "Workspace Isolation": before creating a
     new workspace, this sub-agent checks live for an existing one for its own repo — `git -C <repo
     root> worktree list --porcelain`, matching the entry whose branch equals this story's feature
     branch — and `cd`s there and reuses it if found; otherwise it creates one. Worktree isolation is
     required for this task — proceed without asking; if baseline tests fail, report the failure and
     stop rather than asking whether to proceed. When this reaches a nested
     `finishing-a-development-branch` invocation, pass it the cleanup instruction that skill needs
     (preserve the worktree for PR-feedback iteration), not the creation requirement above — that
     skill only tears down.
   - Per-repo internal code review instructions (see "Internal Code Review" below). The sub-agent
     implements and self-reviews, then **opens this repo's PR itself, from inside its own worktree,
     before returning** (see Step 4) — it is the workspace that has the implementation, so PR
     creation stays there instead of being handed back to the main agent's own, different working
     directory.
   - The Code Comment Compliance Check instructions: reference `skills/shared/code-comment-check.md` by path in this sub-agent's dispatch prompt (mirroring how `adversarial-review.md` is dispatched by reference) — do not inline the full regex table into every per-repo prompt.

3. **A later level does not start until every sub-agent in the prior level has finished successfully.** If any sub-agent in a level fails, stop, diagnose, fix, and retry that repo before advancing.

#### Step 4 — Open one PR per repo

Each per-repo sub-agent opens its own PR itself, from inside its own worktree, once it completes and
passes its internal code review — this keeps `gh pr create` running in the same workspace that has
the implementation checked out. Every PR must:

- Reference the single shared story using the PM adapter's "Story Reference in PRs" format.
- Follow all PR Creation Requirements below.

Each sub-agent also attaches its own PR to the story as an external link via the PM adapter before
returning, then reports the PR number back in its result. Nothing about the worktree path is
reported or aggregated — a later reader resolves each repo's worktree live, per
`skills/shared/standards.md` → "Workspace Isolation".

---

## Development Standards

1. **Test Driven Development** — Write failing tests first. Tests should fail until implementation is correct, then pass.

   Apply the full RED-GREEN-REFACTOR cycle:
   > Invoke Skill: `superpowers:test-driven-development`
   > Use this for each distinct behavior being implemented.

   **Process Fidelity applies here (see `skills/shared/standards.md` → "Process Fidelity").** Skipping RED-GREEN-REFACTOR for a given behavior, or deviating from the per-repo implementation plan, requires asking the user for explicit permission first — it is never a silent judgment call.
2. **Respect existing architecture patterns** — Study the codebase structure before making changes
3. **No placeholder code** — Always implement full functionality. If unable, stop and ask for help
4. **For database changes** — Update appropriate DAO, Entity classes, and Migrations
5. **Test before completion** — Run tests and fix all errors before considering work done

---

## Debugging and Problem Solving

- Never give up when debugging. If stuck, ask for help — see `skills/shared/standards.md` → "Problem Solving" for the full principles (goal vs. mechanical exit condition, investigate before accepting a gap, verify subagent claims)
- If unable to access a screenshot, mockup, or attachment referenced in requirements — STOP and ask the user. Do not proceed with incomplete data.
- Use `gh api` instead of `gh pr` when reading PR comments and file comments
- Always run `git status` after committing to ensure nothing was missed

---

## Commit and PR Process

- **Commit frequently** with descriptive messages explaining what was accomplished
- **NEVER** commit to main
- **NEVER** skip commit hooks
- **NO boilerplate** — Never include "Co-Authored by Claude", "Generated with Claude Code", or any AI attribution in commits or PRs
- **ALWAYS commit and push** after completing work — never leave work uncommitted
- **MANDATORY: Create PR after successful implementation** using `gh pr create`
- **Clean PR descriptions** — focus on what was changed and why

---

## PR Creation Requirements

When creating the PR:
- Title should be concise and descriptive
- Body must include:
  - **Summary**: Brief description of changes
  - **Story Reference**: Link using PM adapter's "Story Reference in PRs" format (omit this section if there is no story ID)
  - **How to Test**: Testing steps from Claude Instructions if available, otherwise based on changes made
- NO AI-generated boilerplate or mentions of AI tools

Once the PR is created and a story ID is in scope, call `skills/shared/checkpoint-seeding.md`'s
"Seed or Refresh Stage" again for that repo, passing stage `"developing"` — developing's own
current stage — and supplying the new PR number. This records the PR number as soon as it's
known, without asserting review has begun; reviewing-prs' own Phase 2 self-seed is what advances
the checkpoint to `stage: "reviewing-prs"` once reviewing-prs actually starts. The repeated
`"developing"` value on this second call is intentional — the new PR number is the payload, and
the stage write is a no-op refresh, not a stage the PR-creation step is asserting has begun; do
not change it back to `"reviewing-prs"`. In the multi-repo path (Step 4 below), each per-repo
sub-agent makes this call itself for its own repo/PR before returning.

---

## Internal Code Review (when story ID provided)

After the subagent-driven implementation completes for a repo, invoke a code review before creating that repo's PR:

> Invoke Skill: `superpowers:requesting-code-review`
>
> Provide the code-reviewer subagent with:
> - The Claude Instructions spec for this repo as the expected-functionality reference
> - The story acceptance criteria
> - The diff of all changes made during implementation in this repo
>
> Address any required changes before proceeding to PR creation.

**Multi-repo note:** this review runs once per repo, inside that repo's sub-agent as part of its self-review, before the sub-agent opens that repo's PR itself and reports back (Step 4) — do not wait for all repos to finish before reviewing each one.

Note (single-repo path only): `superpowers:subagent-driven-development` includes per-task spec
and quality reviews internally. This step adds a final whole-implementation review before the PR
is opened. In the multi-repo path the per-repo sub-agents implement directly (no nested
subagent-driven-development), so this review is their first independent review — run it per repo.

---

## Pre-Completion Verification

Before declaring work complete, run the steps below in order.

### Code Comment Compliance Check

Read and follow `skills/shared/code-comment-check.md` in full — the base-ref resolution, diff
commands, comment-marker table, regex patterns, blocking policy, and multi-repo execution scope
all live there (shared with `addressing-pr-comments/SKILL.md`, which runs the identical check).

### Terraform Plan Check (if applicable)

Detect whether terraform files were changed in this branch. First, fetch the remote to ensure the default branch ref is up to date:

```bash
git fetch origin
```

Then diff against the remote default branch:

```bash
git diff origin/HEAD --name-only
```

Look for any files ending in `.tf` or located inside a `tf/` path.

**If no terraform files changed:** Skip this section and proceed to the verification skill below.

**If terraform files changed:** Check whether a CI terraform plan workflow ran for this branch. First get the current branch name:

```bash
git branch --show-current
```

Then check CI runs:

```bash
gh run list --branch <current-branch> --json conclusion,status,name,createdAt,workflowName --limit 25
```

Look for any workflow whose name contains "terraform" (case-insensitive).

- **CI terraform run found and passed:** No additional action needed — CI has already validated the plan. Continue to the verification skill below.
- **CI terraform run found and failed:** The CI terraform plan failed. Do not declare work complete — fix the plan failure and re-run CI before proceeding.
- **CI terraform workflow is still `in_progress`:** Wait for it to complete. Re-run the `gh run list` command every 2 minutes until the run reaches a terminal conclusion (success, failure, or cancelled). Cap the wait at 30 minutes total. If the run has not completed after 30 minutes, note a warning and continue to the verification skill below.
- **No CI terraform run found:** Run `terraform plan` directly. Use the same directory detection as reviewing-prs Phase 3: check for `tf/` first, then `terraform/`, then fall back to the directory of the changed `.tf` files. For example, if `tf/` exists:

  ```bash
  terraform -chdir=tf/ plan
  ```

  - **`terraform` is not installed on the machine:** Note that terraform validation was not possible due to the missing CLI. Continue to the verification skill below with a warning.
  - **Plan exits with a non-zero exit code:** Do not declare work complete — fix the plan failure before proceeding.
  - **Plan exits with exit code 0:**

    Capture the full plan output. Before including it in the PR description, consider omitting any sensitive attribute values (ARNs, account IDs, IP ranges, secret resource references) from the output.

    If a PR already exists for this branch, append the plan output to the PR description using `--body-file` to avoid shell injection from plan output that may contain backticks or `$()`:

    1. Read the current PR body:

       ```bash
       gh pr view --json body -q .body
       ```

    2. Write the combined body (existing content + the Terraform Plan section) to `.scratch/pr-body-updated.txt` using the Write tool.

    3. Update the PR description:

       ```bash
       gh-as-app.sh developer pr edit --body-file .scratch/pr-body-updated.txt
       ```

    4. Delete the scratch file:

       ```bash
       rm .scratch/pr-body-updated.txt
       ```

    If no PR exists yet, save the plan output to `.scratch/terraform-plan.txt` — you will include it in the PR description when you create the PR.

  Then continue to the verification skill below.

### Final Verification

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh command execution (not memory of previous runs):
> - All tests pass (run the full test suite now)
> - Code is pushed to remote
> - PR exists and is not draft

---

## Adversarial Review (when story ID provided)

If the "No Story ID Path" was used (no PM story), skip this section — there is no independent story/spec to form expectations against.

Read and follow the adversarial review procedure in `skills/shared/adversarial-review.md` with these context variables:

- `story_id`: the story ID from PM Context
- `review_target`: "code changes on branch"
- `review_context`: the Claude Instructions spec loaded during PM Context

---

## Completion Criteria

- All changes are committed with clean messages
- All tests pass
- Code is pushed to remote branch
- PR is created with clean, professional description linking the story
- **Worktree cleanup is manual when this skill runs standalone.** When dispatched by `full-cycle`/`epic` those orchestrators own reclamation (see `full-cycle/SKILL.md`'s Termination section / `epic/SKILL.md`'s `awaiting-merge → done` transition). A human invoking `developing <story-id>` directly has no such reclamation point — nothing here removes the worktree this run created. Look it up live via `git -C <repo root> worktree list --porcelain` (matching the entry whose branch is this story's feature branch), note the path, and once the PR is merged the human should run `git -C <repo root> worktree remove <path>` followed by `git -C <repo root> worktree prune` (or delegate to `superpowers:finishing-a-development-branch`).

---

## Debugging and Problem Solving

- Never give up when debugging. If stuck, ask for help — see `skills/shared/standards.md` → "Problem Solving" for the full principles (goal vs. mechanical exit condition, investigate before accepting a gap, verify subagent claims)
- If unable to access a screenshot, mockup, or attachment referenced in requirements — STOP and ask the user. Do not proceed with incomplete data.
- Use `gh api` instead of `gh pr` when reading PR comments and file comments
- Always run `git status` after committing to ensure nothing was missed
