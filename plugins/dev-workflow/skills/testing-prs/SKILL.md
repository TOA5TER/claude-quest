---
name: dev-workflow:testing-prs
description: "Evidence-based functional testing of PRs in a dev/test environment — deploys the branch, brainstorms and designs test scenarios, executes them with evidence collection, and submits a formal GitHub review. Use whenever a user wants to functionally test a feature branch, validate a PR in a dev environment, or run QA on a pull request before merging."
---

# Testing PRs

**Role:** Testing PRs — functional testing with evidence-based validation

**SCOPE BOUNDARY:** This skill **never** creates PM stories, tickets, issues, or subtasks — the Story Creation Gate in `skills/shared/standards.md` applies. Necessary extra work discovered mid-pipeline is governed by "Necessary Extra Work — No Follow-On Tickets" in `skills/shared/standards.md`: for this role, require it as a change on the current PR's branch (it lands via the existing fix loop) by default; only a huge scope increase warrants stopping to ask — never a follow-on ticket.

## Arguments: $ARGUMENTS

Either a PR number (e.g., `42`) or a PM ticket ID (e.g., `sc-123`) may be passed as the argument.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Phase 0 and Phase 2.

Read `skills/shared/checkpoint-seeding.md` — checkpoint seeding procedure referenced in Phase 2 and Phase 7.

---

## Phase 0: Resolve Input to PR Number

Parse the argument from `$ARGUMENTS`.

**If the argument is purely numeric** → it is a PR number. Use it directly. Skip to Phase 1.

**If the argument contains non-numeric characters** (e.g., `sc-123`, `LIN-456`) → treat it as a PM ticket ID and resolve it:

1. Read `~/.claude/dev-workflow/config.json` to get `pm_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md`
3. Use the adapter's **"Finding PRs linked to a story"** instructions to look up linked PRs — adapters with native API support (Shortcut, Jira, GitHub Issues) will return authoritative results; others fall back to a branch-name match: `gh pr list --state all --limit 1000 --json number,url,state,headRefName`, keeping PRs whose head branch contains the delimited story token
4. **If exactly one PR is found** → extract its number. Use it as the PR number for all subsequent phases.
5. **If no PRs are found** → STOP: "No PR found referencing {story_id}. Ensure the PR is linked to the story through the adapter's linking mechanisms (its link lookup, or a story token in the PR's branch name), then try again." Never create a story, ticket, or issue to fill the gap.
6. **If multiple PRs are found** → list them (number, title, state) and ask the user: "Multiple PRs reference {story_id}. Which PR number should I test?"

---

## CRITICAL: Tester-Specific Rules
- Read and understand original requirements before testing
- **Deploy to test/dev environment only** — never staging or production
- Document every test step with clear pass/fail criteria
- Provide evidence for every assertion (logs, screenshots, API responses)
- **CRITICAL:** Never approve if any test fails
- **Before running any local verification command (build, lint, test, `terraform plan`) against the PR's code**, follow `skills/shared/standards.md` → "Workspace Isolation": look up a live linked worktree for the PR's branch (the first `worktree list` entry is the primary checkout and never counts), and if none exists, use a scratch worktree created once for this test run and removed at its end — never the primary checkout

### Your Accountability as Tester

**The app must work. That is your responsibility.**

You are not here to give the PR a soft pass — you are here to find out if it works. Be aggressive:

- **If you see an error, call it out.** Do not soften it, do not say "this might be environment-specific." An error is a failure.
- **Never assume something works in production that fails in dev.** Test environments exist to catch real problems. If it fails here, it is broken.
- **Do not rationalize failures away.** "Could work," "might be fine," "probably passes in prod" are not acceptable conclusions. Verify or mark as FAIL.
- **Your job is to surface problems, not to approve PRs.** If something is broken, your report must say so clearly.

**Rationalization Red Flags — if you think any of these, stop and investigate instead:**
| Thought | Required Action |
|---------|----------------|
| "This might work differently in production" | Verify it or mark FAIL |
| "The error is probably environment-related" | Investigate the root cause |
| "It mostly works" | Document what doesn't and mark those tests FAIL |
| "The failure seems minor" | Report it — severity is for the reviewer to decide |
| "I can't reproduce it consistently" | Document the intermittent failure as a FAIL |
| "The developer/orchestrator already explained this failure" | Verify it independently before accepting |

### Independent Root-Cause Verification

Any root cause supplied by the developer, the PR description, or the orchestrator's dispatch prompt is a **claim, not a finding** — it must be independently verified before being accepted as explaining a test symptom. A stated explanation, however confident or authoritative its source, is itself a Rationalization Red Flag until you have checked it yourself (see the table row above).

For a migration-related symptom specifically, independent verification means querying `schema_migrations` in the target dev database and/or diffing the migrations directory against the base branch — not trusting the stated explanation. An edited-in-place migration file will show as modified against base while `schema_migrations` shows its version already applied, which means the edit never ran in that environment.

---

> **Note:** In all bash examples below, `{PR_NUMBER}` is a placeholder. Replace it with the actual PR number from `$ARGUMENTS` in every command you run.

## Phase 1: Load PR Details

> **ALWAYS-FRESH MANDATE:** Every invocation of this skill must be treated as a fresh test run. Never assume prior test results still apply or that nothing changed since the last run. Always deploy and test from scratch — there are always new changes to validate.

Get PR details using the actual PR number from arguments:

```bash
gh pr view {PR_NUMBER} --json number,title,body,headRefName
```

The `headRefName` field in the JSON output is the branch name — note it for use in subsequent commands.

Extract expected behavior, acceptance criteria, and branch name.

---

## Phase 2: Load Story Requirements

Resolve the story with the PM adapter's **Resolve story from PR** capability (`skills/pm-adapter/interface.md`): its link lookup first, then the delimited story token in the PR's head branch name. Never read the PR title or body for a story ID. When a custom adapter lacks the capability, apply the generic delimited branch-token match, taking the token format from `adapters.<name>.story_id_prefix` in `~/.claude/dev-workflow/config.json`; with no such key, treat the story as not found.

**If story ID found:**
1. Read `~/.claude/dev-workflow/config.json` for `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story by ID
3. Detect service name: `git rev-parse --show-toplevel | xargs basename`
4. Call `skills/shared/checkpoint-seeding.md`'s "Seed or Refresh Stage" with the resolved
   story ID, the detected service name, stage `"reviewing-prs"`, this PR's number, and the
   story title fetched in step 2 (attention-hub shows it in the story group header) — this
   is what makes a standalone `testing-prs` run visible to attention-hub immediately, without
   waiting for `full-cycle` to write anything. Must not touch `review_loop_count`/
   `test_loop_count` — that stays exclusively the orchestrator's bookkeeping.
5. Load notes adapter per procedure in `skills/shared/adapter-loading.md` → read Claude Instructions spec
6. Use acceptance criteria and Manual Testing section as test scenarios

**If story ID not found:**
- Note the limitation in the test report
- Design test scenarios based on PR description only
- Proceed without writing a checkpoint — this is best-effort telemetry, never a functional gate

---

## Phase 2.5: Re-Read PR Description and Comments

> **Deviation Awareness:** Before testing, re-read the PR description and all conversation comments to identify **explicit deviation decisions** — cases where the developer intentionally diverged from the spec or acceptance criteria and documented their reasoning.

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER} --jq '.body'
```

```bash
gh api repos/{owner}/{repo}/issues/{PR_NUMBER}/comments
```

Extract any stated deviations or scope adjustments. Carry these forward as **Acknowledged Deviations** — when designing test scenarios in Phase 4, test against the developer's stated behavior (not the original spec) for acknowledged deviations. Only flag a deviation as a failure if there is strong reason to disagree (e.g., the deviation breaks a critical acceptance criterion, introduces a security risk, or causes data loss). If you do flag it, explicitly reference the developer's reasoning and explain why.

---

## Phase 3: Deploy

### Mandatory Dev Deploy CI Gate

> **NON-SKIPPABLE — DEV DEPLOY CI.** Every test run — including runs dispatched autonomously by `epic` or `full-cycle` — MUST run the **dev deploy CI** to deploy the PR branch fresh to the dev/test environment and wait for it to succeed before executing any test scenario. This gate is mandatory and has no exceptions.
>
> - **Always deploy the branch fresh.** Trigger the configured deploy CI on the PR branch's current HEAD on every run. Do not skip the deploy and test against whatever is already running.
> - **You may NOT skip this step for any of the following reasons** (each is an explicitly forbidden rationalization):
>   - the environment "looks deployed" or appears to already have this branch,
>   - a prior deploy ran earlier in the cycle,
>   - the dev deploy CI is slow and you are under time pressure,
>   - you are running unattended / autonomously / inside an orchestrator.
> - **No test scenario may execute until the dev deploy CI completes successfully.** If the deploy fails, stop and report the failure in the test report — do not test against a stale or partial environment.
> - **Only a dev deploy CI run counts.** The gate is satisfied **only** by a dev deploy CI run that completes with conclusion `success` on the PR branch's current HEAD. A local/Makefile/script/manual deploy NEVER satisfies this gate.
> - **Hard fail, never skip-and-proceed.** For a non-exempt repo, if no dev deploy CI can be run, the deploy fails, the CI run concludes non-`success`, or only a local deploy path is available, the test verdict is **REQUEST_CHANGES** and the PR gets the `tests-failing` label (see Phase 7). Testing must never proceed to an APPROVE without a successful fresh dev deploy CI run.
>
> The dev deploy CI is the `deploy_command` deployment described below; this gate makes running it fresh and waiting for success non-negotiable. Run it as the first action of Phase 3.

#### Dev Deploy CI Exemption (opt-in, explicit, per repo)

Some repos legitimately have no dev deploy CI (e.g. documentation-only or plugin repos). The gate is skipped **only** when the current repo is explicitly listed in the dev-deploy-CI exemption.

- Read the exemption list from `~/.claude/dev-workflow/config.json` → `deploy_gate_exempt_repos` (an array of repo names). Detect the current repo name with `git rev-parse --show-toplevel | xargs basename`. The gate is skipped for the current repo **only if its name appears in this array**.
- **Default is gated.** A repo absent from `deploy_gate_exempt_repos` is gated. The `deploy_command` `fallback` entry is NOT an exemption — falling back to the fallback deploy instruction still requires a successful dev deploy CI run.
- **Absence of a deploy CI is not an exemption.** For a non-exempt repo with no runnable dev deploy CI, the verdict is **REQUEST_CHANGES + `tests-failing`**, never a skip.
- **Announce every skip.** When the gate is skipped by exemption, the test report MUST state that functional dev testing was skipped by explicit exemption (naming the repo and `deploy_gate_exempt_repos`) and describe how the change was otherwise validated. Silent skipping is forbidden.
- **Prove it — the exemption claim must show its work.** Immediately after the skip statement, the test report MUST include the literal verification command and its output, e.g. `jq '.deploy_gate_exempt_repos' ~/.claude/dev-workflow/config.json` and the resulting array. A prose skip statement with no verification command and output next to it is not a valid exemption claim.
- **A missing verification line invalidates the exemption claim.** If the test report asserts an exemption but does not show the verification command and its output, treat the claim as unverified — this is a gate failure (**REQUEST_CHANGES**), never a pass, and never something to silently correct or wave through.
- **Process Fidelity applies here (see `skills/shared/standards.md` → "Process Fidelity", hard-fail carve-out) — and it does not soften this gate.** Silently treating a non-exempt repo as if it were listed in `deploy_gate_exempt_repos`, silently overriding a failing or missing dev deploy CI result, or asserting an exemption without the required verification line must STOP and be flagged to the user. This creates no user-grantable bypass of the deploy gate itself — its hard-fail default and config-driven exemption list stay exactly as strict as documented above.

Read `~/.claude/dev-workflow/config.json` for `deploy_command`.

`deploy_command` is a natural language instruction describing how to deploy the branch to the test/dev environment. Interpret it and take the appropriate action.

**If `deploy_command` is not configured (or yields no runnable dev deploy CI) for a non-exempt repo:**
- Do **not** skip the deploy and do **not** test against the currently running environment — that loophole is removed.
- There is no successful fresh dev deploy CI run, so the dev deploy gate cannot be satisfied. **Submit a formal REQUEST_CHANGES review**, apply the `tests-failing` label (Phase 7), and stop. Never proceed to an APPROVE.
- The only case where a missing deploy is acceptable is a repo explicitly listed in `deploy_gate_exempt_repos` — in which case follow the exemption path above (skip the gate and announce it in the report).

**If `deploy_command` is configured**, interpret the instruction:

### GitHub Actions

Instructions like "Run the dev CI in Github Actions", "Trigger the deploy-dev workflow", "Run the CI pipeline":

1. Identify the target workflow:
   ```bash
   gh workflow list
   ```
   Match the instruction to the most relevant workflow name or file.

2. Trigger the workflow on the PR branch (use the actual branch name from Phase 1):
   ```bash
   gh workflow run <workflow-file-or-id> --ref "feature/branch-name"
   ```

3. Get the run ID (allow a few seconds for the run to register):
   ```bash
   sleep 5
   ```
   ```bash
   gh run list --workflow=<workflow> --branch="feature/branch-name" --limit=1 --json databaseId -q '.[0].databaseId'
   ```

4. Watch until completion:
   ```bash
   gh run watch <run-id>
   ```

5. Check the result:
   ```bash
   gh run view <run-id> --json conclusion -q .conclusion
   ```
   - `success` → deployment succeeded, proceed to testing
   - Any other value → deployment failed, stop and report failure in test report

### Other patterns

> **A local deploy does NOT satisfy the dev deploy CI gate.** Only a dev deploy **CI run** that completes with conclusion `success` on the PR branch's current HEAD satisfies the gate. A Makefile target, a local script, a `kubectl`/`docker`/`helm` command run from this machine, or any manual/local deployment is never sufficient on its own. If only a local path is available for a non-exempt repo, treat it as a deploy-gate **failure**: submit REQUEST_CHANGES, apply the `tests-failing` label, and do not APPROVE.

- If the instruction maps to a dev deploy **CI** run (a workflow/pipeline), run it per the GitHub Actions steps above and require conclusion `success`.
- If the instruction describes only a local shell command (e.g., starts with `kubectl`, `docker`, `helm`, a script path, a Makefile target) and there is **no** corresponding dev deploy CI for a non-exempt repo, the dev deploy gate cannot be satisfied — submit REQUEST_CHANGES + `tests-failing` and stop. Do not let the local command stand in for the gate.
- If the instruction is ambiguous, prefer the dev deploy CI workflow (check CI config files first); a Makefile/script is not an acceptable substitute for the gate.
- If you cannot determine how to run the dev deploy CI for a non-exempt repo, that is a gate failure — submit REQUEST_CHANGES + `tests-failing`. (For an exempt repo, follow the exemption path.)

---

## Phase 3.5: Brainstorm Test Scenarios

Before designing formal scenarios, invoke brainstorming to surface non-obvious test cases:

> Invoke Skill: `superpowers:brainstorming`
>
> Focus on: edge cases not in acceptance criteria, error conditions, concurrent operations,
> and UX failure modes.
>
> OVERRIDE: After brainstorming completes, do NOT invoke `superpowers:writing-plans`.
> Return to Phase 4 with brainstorming output as additional scenario candidates.

---

## Phase 4: Design Test Scenarios

From story acceptance criteria and Claude Instructions (or PR description if no story):

### Happy Path Scenarios
- Normal usage flows that should succeed
- Each acceptance criterion gets at least one test

### Error/Edge Case Scenarios
- Invalid inputs, boundary conditions
- Concurrent operations if relevant
- Missing required data

### UX Verification
- User-facing messages and behaviors
- Accessibility (if applicable)

---

## Phase 5: Execute Tests

Apply the verification-before-completion discipline to every test assertion:

> Invoke Skill: `superpowers:verification-before-completion`
>
> Apply the five-phase gate (Identify → Execute → Read → Confirm → Assert) to each test
> scenario individually. Do not record a PASS without executing fresh commands and reading
> their complete output in this session.

For each scenario:
1. Document the test step: what you're doing and expected outcome
2. Execute the test (any local command against the PR's code runs in the worktree resolved per the Tester-Specific Rules above — never the primary checkout)
3. Collect evidence: logs, API responses, screenshots, output
4. Record: PASS or FAIL with specific details

**During execution:** If you encounter any error — unexpected output, exception, wrong behavior, missing data — **stop and investigate it immediately.** Do not move on. Do not assume it is noise. Capture it as a failure with full evidence. The presence of an error means the test fails, even if the "main flow" looked okay.

---

## Phase 5.5: Parallel Failure Investigation (conditional)

If Phase 5 produced **3 or more independent test failures** across different subsystems:

> Invoke Skill: `superpowers:dispatching-parallel-agents`
>
> IMPORTANT OVERRIDE: Proceed automatically through all agent dispatch steps without
> asking the user for confirmation. Dispatch agents and proceed.
>
> Group failures by domain (API failures, UI failures, data-layer failures, etc.).
> Each agent investigates one domain. After completion, integrate findings into the Phase 6
> test report under "Failures".
>
> Every dispatched agent's prompt must carry this workspace rule: any local command against
> the PR's code runs only inside a git worktree already checked out on the PR's branch —
> locate it with `git -C <repo root> worktree list --porcelain`, skipping the first entry,
> which is the primary checkout. If no entry's `branch` line matches the PR's branch, also
> check for an entry whose `HEAD <sha>` equals the PR's head commit SHA — a detached scratch
> worktree the parent created has no `branch` line at all (it reports `detached` instead), so
> this is the only way to find it. Never check out or switch branches in the primary checkout,
> and do not create a worktree; if neither match succeeds, report that and investigate
> from logs and evidence instead. Leave the repository exactly as found.
>
> If fewer than 3 independent failures exist, skip this step and proceed to Phase 6.

---

## Phase 6: Write Test Report

```markdown
## Test Report — PR #{PR_NUMBER}

**Environment:** [where tests ran]
**Branch:** [branch name]
**Story:** [story ID or "no story linked"]

## Summary
**{X}/{Y} tests passed**

## Test Results

### Happy Path
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

### Error Scenarios
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

### UX Verification
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

## Failures
[If any: specific details of each failure with reproduction steps]

## Recommendation
[APPROVE / REQUEST CHANGES with reasoning]
```

**A successful fresh dev deploy CI run is a necessary precondition for an APPROVE recommendation.** The recommendation may be APPROVE only if the dev deploy CI reached conclusion `success` on the PR branch's current HEAD (or the repo is dev-deploy-CI-exempt and the report shows the verification command and output per the Dev Deploy CI Exemption section above) **and** all test scenarios passed. A deploy failure, a missing/unrunnable deploy CI on a non-exempt repo, a non-`success` deploy CI conclusion, a local-only deploy, or an exemption claim missing its verification line yields **REQUEST CHANGES** regardless of any local validation.

---

## Phase 7: Submit Review

### Inline Comments

Where a test failure or concern is tied to a specific line of code, post it as an **inline review comment** on that line. Use inline comments for:
- Test failures traceable to a specific code path
- Observed bugs with a clear `file:line` origin
- Questions about specific implementation behavior discovered during testing

The review body should contain the full test report; inline comments supplement it with precise code-level context.

To post inline comments as part of the review, use `gh api` to create a review with comments:

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews -f event="REQUEST_CHANGES" -f body="..." -f 'comments[][path]="file.ts"' -f 'comments[][position]=42' -f 'comments[][body]="..."'
```

### Submit

Submit formal GitHub review using the actual PR number:
- **APPROVE** (`gh pr review {PR_NUMBER} --approve`) only if a fresh dev deploy CI run concluded `success` on current HEAD (or the repo is dev-deploy-CI-exempt with a verified exemption claim) **and** all tests pass
- **REQUEST_CHANGES** (`gh pr review {PR_NUMBER} --request-changes`) if the dev deploy gate was not satisfied on a non-exempt repo (deploy failed, no runnable deploy CI, non-`success` conclusion, or local-only deploy), if an exemption claim is missing its verification line, **or** any test fails

A successful dev deploy CI run is a precondition for both APPROVE and the `tested-in-dev` label below. Never APPROVE + `tested-in-dev` without it.

Include full test report in the review body.

**Always** label the testing outcome so downstream automation (e.g. `dev-workflow:full-cycle`'s resume detection) can distinguish a test result from a review result on the same PR. Apply exactly one label per run, matching the review decision, and clear the opposite label so the two are never both present (use the actual PR number):

On a passing run (`APPROVE`):
```bash
gh pr edit {PR_NUMBER} --add-label "tested-in-dev" --remove-label "tests-failing"
```

On a failing run (`REQUEST_CHANGES`):
```bash
gh pr edit {PR_NUMBER} --add-label "tests-failing" --remove-label "tested-in-dev"
```

**On a PASS verdict** (the `APPROVE` branch above), and a story ID was resolved in Phase 2:
call `skills/shared/checkpoint-seeding.md`'s "Seed or Refresh Stage" once more, advancing
this repo's entry to stage `"done"`. On a FAIL verdict, make no additional call — stage
stays at `"reviewing-prs"` from the Phase 2 seed.
