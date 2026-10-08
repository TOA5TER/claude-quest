---
name: dev-workflow:reviewing-prs
description: "Comprehensive multi-perspective PR review (Product Manager, Developer, QA, and Architect lenses) that compares implementation against story requirements and CI/CD results. Includes automatic first-review vs re-review mode detection. Always use this when a user asks to review a PR, check a pull request, or validate an implementation against requirements."
---

# Reviewing PRs

**Role:** Reviewing PRs — comprehensive PR review comparing implementation against story requirements

**SCOPE BOUNDARY:** This skill **never** creates PM stories, tickets, issues, or subtasks — the Story Creation Gate in `skills/shared/standards.md` applies. Necessary extra work discovered mid-pipeline is governed by "Necessary Extra Work — No Follow-On Tickets" in `skills/shared/standards.md`: for this role, require it as a change on the current PR's branch (it lands via the existing fix loop) by default; only a huge scope increase warrants stopping to ask — never a follow-on ticket.

## Arguments: $ARGUMENTS

Either a PR number (e.g., `42`) or a PM ticket ID (e.g., `sc-123`) may be passed as the argument.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Phase 0 and Phase 2.

Read `skills/shared/checkpoint-seeding.md` — checkpoint seeding procedure referenced in Phase 2.

---

## Phase 0: Resolve Input to PR Number

Parse the argument from `$ARGUMENTS`.

**If the argument is purely numeric** → it is a PR number. Use it directly. Skip to Phase 1.

**If the argument contains non-numeric characters** (e.g., `sc-123`, `LIN-456`) → treat it as a PM ticket ID and resolve it:

1. Read `~/.claude/dev-workflow/config.json` to get `pm_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md`
3. Use the adapter's **"Finding PRs linked to a story"** instructions to look up linked PRs — adapters with native API support (Shortcut, Jira, GitHub Issues) will return authoritative results; others fall back to a branch-name match: `gh pr list --state all --limit 1000 --json number,url,state,headRefName`, keeping PRs whose head branch contains the delimited story token
4. **If exactly one PR is found** → extract its number. Use it as the PR number for all subsequent phases.
5. **If no PRs are found** → STOP: "No PR found referencing {story_id}. Ensure the PR is linked to the story through the adapter's linking mechanisms (its link lookup, or a story token in the PR's branch name), then try again."
6. **If multiple PRs are found** → list them (number, title, state) and ask the user: "Multiple PRs reference {story_id}. Which PR number should I review?"

---

## CRITICAL: Reviewer-Specific Rules
- Load original story requirements first
- **Trigger and monitor CI/CD checks** — it is the reviewer's job to ensure all checks run
- **NEVER run terraform apply** — only `terraform plan` is allowed for validation
- **Before running any local verification command (build, lint, test, `terraform plan`) against the PR's code**, follow `skills/shared/standards.md` → "Workspace Isolation": look up a live linked worktree for the PR's branch (the first `worktree list` entry is the primary checkout and never counts), and if none exists, use a scratch worktree created once for this review and removed at its end — never the primary checkout
- **When the repo under review is this plugin's own marketplace repo (its checkout contains `plugins/dev-workflow/CLAUDE.md`) and the PR bumps a plugin version, check that file's version-bump rule** (a behavior change bumps `.claude-plugin/plugin.json` and the matching `.claude-plugin/marketplace.json` entry in the same PR) before flagging placement as a convention violation — read the written rule rather than inferring a "separate PR" convention from git history. Other repos with a `plugin.json` bump have no such file; do not go looking for it there
- Be critical but constructive with specific examples and file:line references
- Score objectively (1-10) with clear justification
- Verify no tests have been disabled, commented out, or mocked to always pass
- Check for debug statements (println, console.log, dbg!, etc.)

---

> **Note:** In all bash examples below, `{PR_NUMBER}` is a placeholder. Replace it with the actual PR number from `$ARGUMENTS` in every command you run.

## Phase 1: Load PR Details

Get PR details using the actual PR number from arguments:

```bash
gh pr view {PR_NUMBER} --json body,headRefName,number,title,isDraft
```

- Verify PR exists and is not draft
- **STOP if PR is draft** — respond: "This PR is marked as draft. Please mark it ready for review first."

---

## Phase 1.5: Detect Review Mode

> **ALWAYS-FRESH MANDATE:** Every invocation of this skill must be treated as if new changes have been pushed. Never assume there is nothing to review because this is a re-run. Always load the current diff in Phase 4 and read it in full — this is non-negotiable regardless of review mode.

Fetch all existing reviews on this PR. Use the actual PR number from arguments.

```bash
gh pr reviews {PR_NUMBER} --json author,state,body,submittedAt
```

**Decision logic:**

- If the reviews array is empty (no reviews at all) → **First Review Mode**
- If reviews exist but none have `state: CHANGES_REQUESTED` → **First Review Mode**
- If at least one review has `state: CHANGES_REQUESTED` → **Re-Review Mode**

Output a mode banner before proceeding:

- `[FIRST REVIEW]` — you are doing the initial review; exhaustiveness is mandatory
- `[RE-REVIEW — verifying N previously requested changes]` (where N is the count of bullet items extracted from the `## Required Changes` section) — you are checking whether prior requests were addressed

**For Re-Review Mode only:**

Parse the body of the most recent `CHANGES_REQUESTED` review. Extract the bullet list under the `## Required Changes` section verbatim. This is the **Previous Required Changes** list. Carry it forward to Phase 5.

**Fallback:** If the most recent `CHANGES_REQUESTED` review body is empty, null, or does not contain a `## Required Changes` section with bullet points, log a warning: "Previous required changes not found — falling back to First Review Mode." Then treat this invocation as First Review Mode.

---

## Phase 2: Extract Story ID and Load Requirements

Resolve the story with the PM adapter's **Resolve story from PR** capability (`skills/pm-adapter/interface.md`): its link lookup first, then the delimited story token in the PR's head branch name. Never read the PR title or body for a story ID. When a custom adapter lacks the capability, apply the generic delimited branch-token match, taking the token format from `adapters.<name>.story_id_prefix` in `~/.claude/dev-workflow/config.json`; with no such key, treat the story as not found.

**If story ID not found:**
- Post review comment requesting the PR be linked to a story (through the adapter's link or a story token in the branch name)
- **STOP with REQUEST_CHANGES** — do not proceed without story linkage. Never create a story, ticket, or issue to fill the gap

**Once ID found:**

1. Read `~/.claude/dev-workflow/config.json` to get `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story by ID
3. Detect service name: `git rev-parse --show-toplevel | xargs basename`
4. Call `skills/shared/checkpoint-seeding.md`'s "Seed or Refresh Stage" with the resolved
   story ID, the detected service name, stage `"reviewing-prs"`, and this PR's number — this
   self-seed is the primary writer of this repo's `stage` value regardless of whether
   `full-cycle` is driving the pipeline (see `checkpoint-seeding.md`'s opening paragraph).
   Must not touch `review_loop_count`/`test_loop_count` — that stays exclusively the
   orchestrator's bookkeeping.
5. Load notes adapter per procedure in `skills/shared/adapter-loading.md` → read Claude Instructions spec
6. **If spec not found:** ERROR and ask user to invoke the Writer skill (`dev-workflow:writing-specs`) with this story ID first

---

## Phase 3: Trigger and Monitor CI/CD Jobs

**CRITICAL:** Ensure all CI/CD checks run before reviewing code.

**NEVER run terraform apply.** Only run terraform plan for validation.

### Mandatory Dev Build CI Gate

> **NON-SKIPPABLE — DEV BUILD CI.** Every review — including reviews dispatched autonomously by `epic` or `full-cycle` — MUST trigger the **dev build CI** and wait for it to reach a terminal state before any code review begins. This gate is mandatory and has no exceptions.
>
> - **Always run it fresh on the PR's current HEAD.** Trigger a new dev build CI run against the PR branch's latest commit. Do not reuse, point at, or "count" a pre-existing run — a prior run does not satisfy this gate.
> - **You may NOT skip this step for any of the following reasons** (each is an explicitly forbidden rationalization):
>   - a build run already exists on the branch,
>   - the dev build CI is slow and you are under time pressure,
>   - you are running unattended / autonomously / inside an orchestrator,
>   - the diff "looks safe" or the code "obviously builds."
> - **Code review must not begin until the dev build CI reaches a terminal state** (success, failure, or cancelled). If it fails, stop and report per the failure handling below — do not proceed to the multi-perspective review.
> - **An approval produced without a fresh dev build CI run on current HEAD is invalid.** If you reach Phase 6 and the gate was not satisfied, return to this gate and run it.
> - **Hard fail, never silent fall-through.** If the dev build CI does not reach a `success` conclusion on current HEAD — it failed, was cancelled, or timed out without confirming success — the review verdict is a formal **REQUEST_CHANGES** (see the failure handling below). A non-passing CI state may NEVER result in APPROVE, and may never end in only a bare comment or STOP with no recorded review decision.

#### Dev Build CI Exemption (opt-in, explicit, per repo)

Some repos legitimately have no dev build CI (e.g. documentation-only or plugin repos). The gate is skipped **only** when the current repo is explicitly listed in the dev-build-CI exemption.

- Read the exemption list from `~/.claude/dev-workflow/config.json` → `ci_gate_exempt_repos` (an array of repo names). The gate is skipped for the current repo **only if its name appears in this array**.
- **Default is gated.** A repo that is absent from `ci_gate_exempt_repos` is gated. The `review_ci_command` `fallback` entry is NOT an exemption — falling back to the fallback CI instruction still requires the gate to run and pass.
- **Absence of a CI workflow is not an exemption.** If a non-exempt repo has no dev build CI workflow at all, that is a **REQUEST_CHANGES**, not a skip. Never treat "no CI found" as auto-exempt.
- **Announce every skip.** When the gate is skipped by exemption, the review body MUST state it explicitly, e.g. "Dev build CI gate skipped: repo `<name>` is explicitly exempt in `ci_gate_exempt_repos`." Silent skipping is forbidden.
- **Prove it — the exemption claim must show its work.** Immediately after the skip sentence, the review body MUST include the literal verification command and its output, e.g. `jq '.ci_gate_exempt_repos' ~/.claude/dev-workflow/config.json` and the resulting array. A prose skip sentence with no verification command and output next to it is not a valid exemption claim.
- **A missing verification line invalidates the exemption claim.** If the review body asserts an exemption but does not show the verification command and its output, treat the claim as unverified — this is a gate failure (**REQUEST_CHANGES**), never a pass, and never something to silently correct or wave through.
- **Process Fidelity applies here (see `skills/shared/standards.md` → "Process Fidelity", hard-fail carve-out) — and it does not soften this gate.** Silently treating a non-exempt repo as if it were listed in `ci_gate_exempt_repos`, silently overriding a failing, missing, or cancelled CI result, silently expanding who counts as exempt, or asserting an exemption without the required verification line must STOP and be flagged to the user. No user answer converts any of those into a passing verdict the CI did not produce — the gate's pass/fail logic and its config-driven exemption list stay exactly as strict as documented above.

The dev build CI is the build workflow described under "Load CI Configuration" and "Required Workflows" below; this gate makes triggering and waiting on it non-negotiable. Run it as the first action of Phase 3.

### Load CI Configuration

Read `~/.claude/dev-workflow/config.json` for `review_ci_command`.

`review_ci_command` is a natural language instruction describing how to trigger CI for the PR branch, keyed by repository name with a `fallback` entry for unlisted repos.

Detect the current repository name:

```bash
git rev-parse --show-toplevel | xargs basename
```

Look up the repo name in `review_ci_command`. If not found, use the `fallback` entry. If `review_ci_command` is not configured at all, fall through to the default behavior below.

**If `review_ci_command` is configured:** Interpret the natural language instruction and execute it against the PR branch. Then proceed to monitoring below.

**If `review_ci_command` is not configured:** Use the default behavior — list existing CI runs and trigger any missing or failed workflows.

### Get Branch and List CI Runs

First get the branch name for this PR:

```bash
gh pr view {PR_NUMBER} --json headRefName -q .headRefName
```

Then list CI runs using the branch name from the output above:

```bash
gh run list --branch "branch-name-from-output" --json conclusion,status,name,createdAt,workflowName --limit 10
```

#### Required Workflows

- **Always:** build workflow (if exists), test workflow (if exists)

Trigger any missing or failed non-terraform workflows. Wait up to 15 minutes for completion.

After waiting, check results:
- If any non-terraform workflow is still `in_progress` (conclusion is null): wait an additional 5 minutes and re-check once. If still running after the re-check, the CI gate has not passed — **submit a formal REQUEST_CHANGES review** whose body names each timed-out workflow and states that CI did not reach a passing state on current HEAD, then **STOP** the multi-perspective review. Do not proceed until all non-terraform workflows reach a terminal `success` state. Never APPROVE on an unconfirmed (timed-out) CI result.
- If any non-terraform workflow has failed: the CI gate has not passed — **submit a formal REQUEST_CHANGES review** whose body names each failed workflow and its conclusion (e.g., "Build workflow failed with conclusion: failure") and states that CI did not pass on current HEAD, then **STOP** the multi-perspective review. Do not proceed to Terraform Plan Validation or code review until all non-terraform checks pass. Never APPROVE on a failed CI result.

#### Terraform Plan Validation

Fetch changed files and most recent commit timestamp in a single call:

```bash
gh pr view {PR_NUMBER} --json files,commits
```

Use `.files` to check whether any `.tf` files were modified or any files inside a `tf/` path were changed. Use `.commits[-1].committedDate` to get the PR's most recent commit timestamp.

**If no terraform files changed:** Skip this section entirely.

**If terraform files changed:** Re-run the CI runs list command now to get a fresh snapshot:

```bash
gh run list --branch "branch-name-from-earlier" --json conclusion,status,name,createdAt,workflowName --limit 10
```

Check the results for any workflow whose name contains the word "terraform" (case-insensitive).

- **CI terraform workflow is still `in_progress` (conclusion is null):** Re-run `gh run list` every 2 minutes until the run reaches a terminal conclusion (success, failure, or cancelled). Cap the wait at 30 minutes total. If the run has not completed after 30 minutes, the CI gate has not passed — **submit a formal REQUEST_CHANGES review** noting that the terraform CI run did not complete within the timeout on current HEAD, then **STOP**. Do not proceed until the terraform CI run reaches a terminal `success` state. Never APPROVE on an unconfirmed terraform CI result.
- **CI terraform run found and passed:** Verify the run's `createdAt` timestamp is more recent than `.commits[-1].committedDate` (obtained above). If it is, note the result and continue. If the passing run predates the most recent commit, treat it as "No CI terraform run found" and fall through to the manual plan fallback.

- **CI terraform run found and failed:** The CI gate has not passed — **submit a formal REQUEST_CHANGES review** naming the failed terraform workflow and stating that CI did not pass on current HEAD, then **STOP**. Do not proceed with the code review until the CI check passes. Never APPROVE on a failed terraform CI result.

- **No CI terraform run found (fallback):** Run `terraform plan` directly — this is a local verification command, so it runs from the PR-branch worktree resolved per `skills/shared/standards.md` → "Workspace Isolation" (see the Reviewer-Specific Rules above), never from the primary checkout; `-chdir` paths below are relative to that worktree. Check for `tf/` first, then `terraform/`, then fall back to the directory of the changed `.tf` files. For example, if `tf/` exists:

  ```bash
  terraform -chdir=tf/ plan
  ```

  - **`terraform` is not installed on the machine:** Post a note in the review comment that terraform validation was not possible due to missing CLI. Do **not** halt the review — continue with a warning.
  - **Plan exits with a non-zero exit code:** Include the full plan output in the review comment, request changes, and **STOP**.
  - **Plan exits with exit code 0:** Continue to the sub-cases below.
    - **Plan shows infrastructure changes (any resources to add, change, or destroy):** Include the plan output in the review comment, omitting any sensitive attribute values (ARNs, account IDs, IP ranges, secret resource references). Evaluate whether the changes align with the PR's stated intent.
      - Changes appear **unrelated** to the PR's stated purpose → flag as unexpected drift, request changes, and **STOP**.
      - Changes **clearly match** what the PR description says it will do → note the plan output and proceed.
    - **Plan exits cleanly with no changes:** Add a brief note ("No infrastructure drift detected — terraform plan clean") and proceed.

#### Migration Immutability Check

Reuse the `gh pr view {PR_NUMBER} --json files,commits` call from Terraform Plan Validation above — only the `.files` array is needed for this check. Each entry carries a `changeType` field (GitHub's `PatchStatus` enum: `ADDED` / `CHANGED` / `COPIED` / `DELETED` / `MODIFIED` / `RENAMED`), so no additional API call is required. Note the payload exposes only `path`, `additions`, `deletions`, and `changeType` per file — there is no previous-filename or base-tree signal, so this check only evaluates conditions computable from those fields.

Filter `.files` to entries whose `path` contains a `migrations/` directory segment.

**If no migration files changed, or every changed migration file has `changeType: ADDED`:** Skip this section entirely — adding new migration files is the normal, expected pattern.

**If any such file has `changeType: MODIFIED` or `changeType: RENAMED`:** record a **blocking finding** naming each offending file path and its change type. (`RENAMED` is blocked unconditionally: the payload carries no previous-filename or base-tree data to distinguish a rename of an existing migration from a rename of a file added in this same PR, and renaming an already-applied migration is itself a divergence — the runner's ledger records the old filename/version — so the conservative flag is correct. If the rename is genuinely PR-internal, the developer can say so and the reviewer verifies it from the commit list already in hand.) Migration files are immutable once applied: migration runners (e.g. `golang-migrate`) never re-run an applied migration, so editing an existing migration file silently diverges every environment that already ran it — the schema change must ship as a new migration file instead. Carry the finding forward to Phase 6, where it is force-included in Required Changes and blocks APPROVE (see the Migration Immutability gate there).

---

## Phase 3.5: Re-Read PR Description and Comments

> **Deviation Awareness:** Before reviewing code, re-read the PR description and all conversation comments to identify **explicit deviation decisions** — cases where the developer intentionally diverged from the spec or standard approach and documented their reasoning.

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER} --jq '.body'
```

```bash
gh api repos/{owner}/{repo}/issues/{PR_NUMBER}/comments
```

Extract any stated deviations, trade-off decisions, or "I chose X instead of Y because Z" notes. Carry these forward as **Acknowledged Deviations** — do not flag these as issues in Phase 5 unless there is strong reason to disagree (e.g., security risk, data loss, correctness failure). If you do disagree, explicitly reference the developer's stated reasoning and explain why the concern overrides it.

---

## Phase 4: Load PR Changes

> **Never skip this phase.** Regardless of review mode, always fetch a fresh diff. Re-review mode changes how findings are classified — it does not eliminate the requirement to read the current diff.

Get the list of changed files:

```bash
gh pr view {PR_NUMBER} --json files
```

Get the full diff:

```bash
gh pr diff {PR_NUMBER}
```

Analyze all file changes and understand the scope of modifications.

---

## Phase 5: Multi-Perspective Code Review

> **MODE CHECK:** Apply the correct review mode determined in Phase 1.5.
> - If the mode banner was `[FIRST REVIEW]` → follow **First Review Mode** below.
> - If the mode banner was `[RE-REVIEW]` → follow **Re-Review Mode** below.

### First Review Mode

> **EXHAUSTIVENESS MANDATE — FIRST REVIEW**
> This is the only opportunity to identify issues. A re-review will not accept new findings unless they meet the Critical Exception Threshold (defined below). You must find everything now:
> - Read the entire diff — every file, every line
> - Apply all six specialist perspectives with maximum scrutiny
> - Do not defer marginal issues thinking you can raise them next time — there is no next time
> - If unsure whether something is worth flagging, flag it

**Step 1:** Read all six subagent instruction files **in parallel**:
- `skills/subagents/code-quality-engineer.md`
- `skills/subagents/simple-architecture-engineer.md`
- `skills/subagents/security-engineer.md`
- `skills/subagents/performance-engineer.md`
- `skills/subagents/product-manager.md`
- `skills/subagents/budget-manager.md`

**Step 2:** Launch all 6 review subagents **in parallel** (all in a single message with multiple Agent tool calls).

For each agent, craft a prompt that embeds:
1. The full content of its instruction file (read in Step 1)
2. The following shared context block:

```
---
WORKSPACE RULE:
This review is read-only. If you run any local command against the PR's code (build, lint, test, plan), run it only inside a git worktree already checked out on the PR's branch — locate it with `git -C <repo root> worktree list --porcelain`, skipping the first entry, which is the primary checkout. If no entry's `branch` line matches the PR's branch, also check for an entry whose `HEAD <sha>` equals the PR's head commit SHA — a detached scratch worktree the parent created has no `branch` line at all (it reports `detached` instead), so this is the only way to find it. Never check out a branch in, switch branches in, or modify the primary checkout, and do not create a worktree yourself; if neither match succeeds, report that local verification was not possible and review from the diff instead. Leave the repository exactly as you found it.

STORY REQUIREMENTS:
[Story title, description, and all acceptance criteria from Phase 2]

SPECIFICATION (Claude Instructions):
[Full spec content from Phase 2]

CI/CD STATUS:
[Build/test/terraform results from Phase 3]

PR DIFF:
[Full diff from Phase 4]
---
```

Launch the following 6 agents in parallel:

1. **Code Quality Engineer** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/code-quality-engineer.md` + shared context
   - Focus: correctness, naming, test quality, debug artifacts, consistency

2. **Simple Architecture Engineer** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/simple-architecture-engineer.md` + shared context
   - Focus: DRY, YAGNI, over-engineering, unnecessary abstractions, new dependencies

3. **Security Engineer** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/security-engineer.md` + shared context
   - Focus: OWASP Top 10, injection, auth, sensitive data exposure, input validation

4. **Performance Engineer** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/performance-engineer.md` + shared context
   - Focus: N+1 queries, algorithm complexity, memory, caching, async blocking

5. **Product Manager** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/product-manager.md` + shared context
   - Focus: acceptance criteria verification, user problem fit, regressions, readiness

6. **Budget Manager** (subagent_type: general-purpose)
   - Instructions: content of `skills/subagents/budget-manager.md` + shared context
   - Focus: external API costs, infrastructure, storage, scaling cost profile

This round is a genuinely concurrent, backgrounded dispatch — apply `skills/shared/standards.md`
→ "Subagent Wait Discipline": state that all 6 are in flight before yielding the turn, and
arm a bounded, visible fallback re-check rather than trusting notification delivery alone.
Wait for all 6 agents to complete, then collect all findings before proceeding to Phase 5.5.

---

### Re-Review Mode

**Do not run the parallel subagents. Replace them entirely with the following:**

Work through each item in the **Previous Required Changes** list extracted in Phase 1.5. For each item:

- **Status:** Addressed / Not Addressed / Partially Addressed
- **Evidence:** Specific `file:line` reference from the current diff showing the change (or its absence)

Items with **Status: Not Addressed** or **Partially Addressed** must be carried forward verbatim into the Required Changes section of the Phase 6 review body. If a required change was addressed by deleting the problematic code entirely, mark it **Addressed** with Evidence citing the deletion.

After verifying all previous items, evaluate whether any **new** findings meet the Critical Exception Threshold:

> **Critical Exception Threshold** — A new finding qualifies ONLY if ALL of the following are true:
> 1. It is a security vulnerability, data loss risk, or correctness failure that would cause incorrect behavior in production
> 2. It was introduced by code added **after** the original review — it does not appear in the original diff
> 3. It is not present in the original diff in any form — not as the same code, a functionally equivalent pattern, or a related code path. If it appears anywhere in the original diff, it does not qualify regardless of how subtle or easy to overlook it was.
>
> If a finding does not satisfy all three criteria, it must be omitted. Do not rationalize marginal findings into this threshold.

List qualifying new findings under a **New Critical Findings** section with an explicit per-item justification of why the threshold is met.

Once the verification checklist is complete, skip to Phase 5.5.

---

## Phase 5.5: Verify Before Submitting Review

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh command execution:
> - All CI/CD checks still pass (re-run status check from Phase 3)
> - The PR diff reflects current HEAD (no new commits since loading in Phase 4)
> - **First Review Mode:** No required changes identified in Phase 5 have been overlooked
> - **Re-Review Mode:** All unresolved items from the Phase 5 verification checklist have been carried forward to Phase 6. No New Critical Findings that meet the exception threshold have been omitted.

---

## Phase 6: Synthesize and Submit Review

**Scoring (1-10):**
- 9-10: Excellent, approve with minor optional suggestions
- 7-8: Good, approve with minor required fixes noted
- 5-6: Adequate but has issues that should be addressed
- 3-4: Significant problems, request changes
- 1-2: Fundamental issues, request changes with priority concerns

*In Re-Review Mode: score is based primarily on how completely previous required changes were addressed. New critical findings that meet the exception threshold lower the score as in first review.*

**For Re-Review Mode**, use this body format instead:

~~~markdown
## Review Score: X/10

## Summary
[2-3 sentence overview of whether previous requests were addressed]

## Previous Changes Verification
- [original required change item]: Addressed — `file.ts:42` resolves the issue
- [original required change item]: Not Addressed — no relevant change found in diff
- [original required change item]: Partially Addressed — `file.ts:88` handles the happy path but the error case remains

## New Critical Findings (Exception Threshold Met)
[Omit this section entirely if no new findings qualify. If present, include per-item threshold justification for each finding.]

## Required Changes
[Unresolved items from Previous Changes Verification only, plus any qualifying New Critical Findings. No other new items are permitted in re-review.]

## Suggestions (Optional)
[Omit entirely in re-review unless directly related to an unresolved previous request]
~~~

**For First Review Mode**, synthesize all six subagent reports into this format. De-duplicate findings that appear across multiple reports — each issue should appear once, in the section most relevant to it. Prioritize findings by severity when populating Required Changes.

**Review body format:**
```markdown
## Review Score: X/10

## Summary
[2-3 sentence overview of the implementation quality]

## Code Quality
[Key findings from Code Quality Engineer — correctness, naming, test coverage]

## Architecture
[Key findings from Simple Architecture Engineer — simplicity, DRY, design]

## Security
[Key findings from Security Engineer — omit section if no findings]

## Performance
[Key findings from Performance Engineer — omit section if no findings]

## Product Fit
[Acceptance criteria verification and product findings from Product Manager]

## Budget Impact
[Cost impact rating and findings from Budget Manager — omit section if no new costs]

## Required Changes
[Consolidated must-fix items from all reviewers, bulleted with file:line references]

## Suggestions (Optional)
[Consolidated nice-to-have improvements from all reviewers]
```

### Inline Comments

Where a finding is tied to a specific line of code, post it as an **inline review comment** on that line rather than burying it in the review body. Use inline comments for:
- Required changes with a clear `file:line` target
- Suggestions tied to a specific code pattern
- Questions about a specific implementation choice

The review body should summarize and reference these inline comments, not duplicate their full content.

To post inline comments as part of the review, use `gh api` to create a review with comments:

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews -f event="REQUEST_CHANGES" -f body="..." -f 'comments[][path]="file.ts"' -f 'comments[][position]=42' -f 'comments[][body]="..."'
```

**Dev build CI is a necessary precondition for APPROVE — independent of the 1–10 score.** A review may be **APPROVE only if all** of the following hold:
1. The dev build CI (and every required CI check) reached a `success` conclusion on the PR's current HEAD — **or** the repo is dev-build-CI-exempt (listed in `ci_gate_exempt_repos`), in which case the review body states the gate was skipped by exemption **and** includes the verification command and its output per the Dev Build CI Exemption section above; **and**
2. score ≥ 8; **and**
3. no required changes exist.

If the CI gate did not pass (failed, cancelled, or timed-out/unconfirmed) on a non-exempt repo, the verdict is **REQUEST_CHANGES** regardless of score — a high score never overrides a non-passing CI gate. An exemption claim missing its verification line is likewise **REQUEST_CHANGES**, not an APPROVE.

**Migration immutability is likewise a necessary precondition for APPROVE — independent of the 1–10 score.** Any finding recorded by the Migration Immutability Check (Phase 3) must be force-included in the review's Required Changes section, explicitly naming the check, the offending file path(s), and each file's change type. An approval produced while a Migration Immutability Check finding exists is invalid — the verdict is **REQUEST_CHANGES** regardless of score.

Submit formal GitHub review with decision:
- **APPROVE** only if the CI gate passed (or the repo is dev-build-CI-exempt with a verified exemption claim) **and** score ≥ 8 **and** no required changes
- **REQUEST_CHANGES** if the CI gate did not pass on a non-exempt repo, if an exemption claim is missing its verification line, or if required changes exist
- **COMMENT** if neutral/informational

Use the actual PR number:

```bash
gh pr review {PR_NUMBER} --approve --body "..."
```
