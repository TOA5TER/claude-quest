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

Read `skills/shared/pre-release-pin.md` → "Pre-Release Pin Disclosure" — apply it on every test round.

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

The developer has already deployed this change to dev and verified it against live data before hand-off. Your purpose is to catch the bugs the developer missed. You still verify everything independently and never take the developer's evidence as proof that the app works.

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

> **ALWAYS-FRESH MANDATE:** Every invocation of this skill must be treated as a fresh test run. Never assume prior test results still apply or that nothing changed since the last run. Every run re-validates from scratch: a fresh developer-evidence check and fresh tests. The deploy follows the reuse-or-fresh rule in Phase 3 — a validated reuse of the developer's deploy run is the single allowed exception to deploying anew.

Get PR details using the actual PR number from arguments:

```bash
gh pr view {PR_NUMBER} --json number,title,body,headRefName,baseRefName,headRefOid
```

The `headRefName` field in the JSON output is the branch name — note it for use in subsequent commands. The Test Integrity checks in Phase 5 use `baseRefName` as the base and `headRefOid` as the head. In a Local Mode round (no PR) the base is the repo's default branch (`git -C <repo root> symbolic-ref --short refs/remotes/origin/HEAD` with the `origin/` prefix removed) and the head is the branch tip the round message gives.

Extract expected behavior, acceptance criteria, and branch name.

---

## Phase 2: Load Story Requirements

Resolve the story with the PM adapter's **Resolve story from PR** capability (`skills/pm-adapter/interface.md`): its link lookup first, then the delimited story token in the PR's head branch name. Never read the PR title or body for a story ID. When a custom adapter lacks the capability, apply the generic delimited branch-token match, taking the token format from `adapters.<name>.story_id_prefix` in `~/.claude/dev-workflow/config.json`; with no such key, treat the story as not found.

**If story ID found:**
1. Read `~/.claude/dev-workflow/config.json` for `pm_adapter` and `notes_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md` → fetch story by ID
3. Detect service name: `basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"`
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

Extract any stated deviations or scope adjustments. Carry these forward as **Acknowledged Deviations** — when designing test scenarios in Phase 4, test against the developer's stated behavior (not the original spec) for acknowledged deviations. Only flag a deviation as a failure if there is strong reason to disagree (e.g., the deviation breaks a critical acceptance criterion, introduces a security risk, or causes data loss). If you do flag it, explicitly reference the developer's reasoning and explain why. A `## Disputed Findings` section in a developer comment is never an Acknowledged Deviation; it is read only for the precision table in Phase 6.

---

## Phase 3: Deploy

### Developer Evidence Gate

Read `skills/shared/dev-deploy.md` — it owns the evidence format, the validation outcomes, the SHA-based run identification, and the newest-run check used below. Run this gate as the first step of Phase 3, before any deploy.

- **Skip conditions.** The gate is skipped when the repo is listed in `deploy_gate_exempt_repos` **or** `ci_gate_exempt_repos` in `~/.claude/dev-workflow/config.json`. State which list in the test report and show the literal verification command and its output (`jq '.ci_gate_exempt_repos, .deploy_gate_exempt_repos' ~/.claude/dev-workflow/config.json`). Membership is read from config, never inferred. A `deploy_gate_exempt_repos` repo then takes the existing exemption path below; a repo that is only `ci_gate_exempt_repos` deploys fresh as today (there is no developer run to reuse).
- **Otherwise validate the developer's dev-verification evidence** for the PR branch's current HEAD per `skills/shared/dev-deploy.md` (outcomes `valid`, `missing`, `stale`, `invalid`), validated against the GitHub run API and never trusted from the comment text.
- **Immediate bounce.** `missing`, `stale`, or `invalid` evidence yields a formal **REQUEST_CHANGES** review plus the `tests-failing` label (Phase 7): no deploy, no scenarios. Never deploy on the developer's behalf.
- **Reuse or redeploy.** With `valid` evidence, reuse the developer's deploy only if dev still serves that HEAD per the tester newest-run check in `skills/shared/dev-deploy.md`: the evidence run is the newest run of the deploy workflow across all branches and triggers, with no run of that workflow queued or in progress. Otherwise redeploy fresh using the steps below. The test report states the deploy path: the reused run ID or the fresh run ID.

### Mandatory Dev Deploy CI Gate

> **NON-SKIPPABLE — DEV DEPLOY CI.** Every test run — including runs dispatched autonomously by `full-cycle` — MUST have a successful dev deploy CI run on the PR branch's current HEAD before executing any test scenario. That run is either the validated reused developer run (per the Developer Evidence Gate) or a fresh tester run of the **dev deploy CI**.
>
> - **Deploy fresh whenever reuse is not validated.** If the evidence is not reusable (dev no longer serves the HEAD, or the repo is only CI-exempt), trigger the configured deploy CI on the PR branch's current HEAD. Do not skip the deploy and test against whatever is already running.
> - **You may NOT skip this step for any of the following reasons** (each is an explicitly forbidden rationalization):
>   - the environment "looks deployed" or appears to already have this branch,
>   - a prior deploy ran earlier in the cycle and was not validated for reuse,
>   - the dev deploy CI is slow and you are under time pressure,
>   - you are running unattended / autonomously / inside an orchestrator.
> - **No test scenario may execute until a successful dev deploy run on current HEAD exists.** If a fresh deploy fails, stop and report the failure in the test report — do not test against a stale or partial environment.
> - **Only a dev deploy CI run counts.** The gate is satisfied **only** by a dev deploy CI run that completes with conclusion `success` on the PR branch's current HEAD. A local/Makefile/script/manual deploy NEVER satisfies this gate.
> - **Hard fail, never skip-and-proceed.** For a non-exempt repo, if the evidence bounces, no dev deploy CI can be run, a fresh deploy fails, the CI run concludes non-`success`, or only a local deploy path is available, the test verdict is **REQUEST_CHANGES** and the PR gets the `tests-failing` label (see Phase 7). Testing must never proceed to an APPROVE without a successful dev deploy run on current HEAD — either the validated reused developer run or a fresh tester run.
>
> The dev deploy CI is the `deploy_command` deployment described below. A verified deploy exemption leaves the exemption path unchanged.

#### Dev Deploy CI Exemption (opt-in, explicit, per repo)

Some repos legitimately have no dev deploy CI (e.g. documentation-only or plugin repos). The gate is skipped **only** when the current repo is explicitly listed in the dev-deploy-CI exemption.

- Read the exemption list from `~/.claude/dev-workflow/config.json` → `deploy_gate_exempt_repos` (an array of repo names). Detect the current repo name with `basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"`. The gate is skipped for the current repo **only if its name appears in this array**.
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
- There is no successful dev deploy CI run, so the dev deploy gate cannot be satisfied. **Submit a formal REQUEST_CHANGES review**, apply the `tests-failing` label (Phase 7), and stop. Never proceed to an APPROVE.
- The only case where a missing deploy is acceptable is a repo explicitly listed in `deploy_gate_exempt_repos` — in which case follow the exemption path above (skip the gate and announce it in the report).

**If `deploy_command` is configured**, interpret the instruction:

### GitHub Actions

Instructions like "Run the dev CI in Github Actions", "Trigger the deploy-dev workflow", "Run the CI pipeline":

1. Identify the target workflow:
   ```bash
   gh workflow list
   ```
   Match the instruction to the most relevant workflow name or file.

2. Trigger the workflow on the PR branch (use the actual branch name from Phase 1) and identify the run by SHA, following "Trigger and Identify the Run by SHA" in `skills/shared/dev-deploy.md`: the run is the newest run of that workflow whose `headSha` equals the PR's current HEAD SHA and that was created after the trigger. Never take the branch's latest run.
   ```bash
   gh workflow run <workflow-file-or-id> --ref "feature/branch-name"
   ```

3. Watch until completion:
   ```bash
   gh run watch <run-id>
   ```

4. Check the result:
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

### Test Integrity Scenarios
Plan these checks on every run: the revert-the-fix check, the mutation check and the property-based check. Phase 5's "Test Integrity" subsection defines each one and the single result it resolves to: `PASS`, `FAIL`, `N/A: <reason>` or `not possible: <reason>`.

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

### Test Integrity

A test that passes with or without the fix proves nothing. Run the checks planned in Phase 4. Each resolves to exactly one of `PASS`, `FAIL`, `N/A: <reason>` or `not possible: <reason>`.

**Changed files.** Take them from `git -C <verification worktree> diff --name-status --diff-filter=AMR <merge-base> <head>`, where the verification worktree is the PR-head worktree resolved per the Tester-Specific Rules (a reused linked worktree or the verification scratch worktree). Exclude a changed file from both classes when a directory segment is named `fixtures`, `testdata`, `__snapshots__` or `snapshots`, when its name is `conftest.py` or `__init__.py`, or when its extension is `.json`, `.jsonl`, `.yaml`, `.yml`, `.html`, `.md`, `.txt`, `.csv` or `.snap`. Any other changed file is a **test file** when a directory segment is named `test`, `tests`, `__tests__` or `spec`, or its name matches `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `*Test.*` or `*Tests.*`; the rest are **changed source files**. Deleted files are ignored; a renamed test file counts as changed.

**Merge-base.** Run `git -C <repo root> fetch origin <base>`, then `git -C <repo root> merge-base origin/<base> <head>`; the output is the merge-base. A failed command makes the revert-the-fix check `not possible`.

**Revert-the-fix check.** Run it in a revert-check worktree, created once per test run with `git -C <repo root> worktree add --detach <repo root>/.worktrees/revert-check-<first 12 characters of head> <merge-base>` (the detached form the Fallback bullet of Workspace Isolation uses; `superpowers:using-git-worktrees` cannot create a detached worktree). Inside it, run `superpowers:using-git-worktrees` Step 2 (Project Setup) and the Workspace Isolation dependency-folder rule, and run no baseline. Then check out the head's versions of the added or changed test files with `git -C <revert-check worktree> checkout <head> -- <test file paths>`. Run each added or changed test file with the documented test command (table TC-1) in the revert-check worktree (pre-change outcome) and in the verification worktree (head outcome), merge-base first. Any failure counts, including a load or import error from a module the PR adds. If a path already exists at the revert-check location before creation, do not reuse it and do not run anything in it: the check is `not possible: leftover revert-check path`. At the end of the run, only when this run's `worktree add` succeeded, discard the overlay with `git -C <revert-check worktree> reset --hard` and remove the worktree under the Removal rule in `skills/shared/standards.md`; never run `reset --hard` against a path this run did not create.

| Order | Source of the documented test command | Per-file form | Outcome of one test file (shared by the revert-the-fix and property-based checks) |
|-------|---------------------------------------|---------------|-------------------------------------------------------------------------------------|
| 1 | The nearest `CLAUDE.md` or `README.md` that names a command for running tests, walking up from the test file's directory to the repo root | The named command with the test file's path in place of the file path it names, or appended as its last argument when it names none | Fail when the command exits non-zero, unless the output ties the only failure to a coverage threshold; else pass |
| 2 | A `test` target or script in the root `Makefile`, `package.json` or `pyproject.toml`, or `cargo test` for a root `Cargo.toml`, or `go test ./...` for a root `go.mod` | The command with the test file's path appended; for `cargo test`, `go test ./...` and Makefile targets, the whole command once per worktree, with per-file outcomes read from the runner output | With the path appended: as row 1. With the whole command: fail when the runner output reports a counterexample or failing case in the file, or a build or load failure of the package, crate or module directory that contains it; else pass. A non-zero exit, failing case or counterexample that the output ties to other files has no effect on this file. A run that stops before it reports a result for the file gives row 3 |
| 3 | None of the above | The check is `not possible: no documented test command`; the same holds when per-file outcomes cannot be read from the runner output | None; the check is not possible |

Revert-the-fix result (first matching row):

| # | Condition | Result |
|---|-----------|--------|
| 1 | The PR adds or changes no test file (this includes a bug fix with no tests, which Code Quality reports) | `N/A: no added or changed test files` |
| 2 | The merge-base cannot be found, the revert-check worktree cannot be created, its Project Setup fails, or TC-1 row 3 applies | `not possible: <reason>` |
| 3 | Any added or changed test file fails at the head | `FAIL: <files> fail at the PR head` |
| 4 | At least one added or changed test file fails at the merge-base, and every merge-base failure is a missing third-party package that a manifest at the merge-base declares (an environment failure, per the red-baseline rule of Workspace Isolation) | `not possible: dependencies missing at the merge-base: <package>` |
| 5 | At least one added or changed test file fails at the merge-base | `PASS`, listing the files that also pass pre-change, for information only |
| 6 | Otherwise: every added or changed test file passes at the merge-base | `FAIL: no added or changed test file fails against pre-change code` |

**Existing tooling only.** A mutation or property-based tool counts as existing only when a file tracked at the PR head (`git -C <verification worktree> ls-files`) declares a tool in the table below. The tester never installs a tool, never adds a package and never edits a manifest; the only installs that run are the Project Setup step of `superpowers:using-git-worktrees` and the dependency-folder rule, both working from the repo's own manifests. A declared tool that still fails to start is `not possible: <first error line>`.

| Tool | Kind | Declared by (tracked file at PR head) |
|------|------|---------------------------------------|
| Stryker | mutation | `@stryker-mutator/core` in `package.json`, or a `stryker.conf.*` or `stryker.config.*` file |
| mutmut | mutation | `mutmut` in a Python manifest or lockfile, or a `[mutmut]` or `[tool.mutmut]` section |
| cosmic-ray | mutation | `cosmic-ray` in a Python manifest or lockfile |
| PIT | mutation | `pitest-maven` in `pom.xml` or `info.solidsoft.pitest` in a Gradle build file |
| cargo-mutants | mutation | a `.cargo/mutants.toml` file |
| Infection | mutation | `infection/infection` in `composer.json`, or an `infection.json` or `infection.json5` file |
| go-mutesting | mutation | `go-mutesting` in `go.mod` or a tracked `tools.go` |
| `fast-check` | property | `fast-check` in `package.json` |
| Hypothesis | property | `hypothesis` in a Python manifest or lockfile |
| proptest, quickcheck | property | the crate name in `Cargo.toml` |
| jqwik | property | `net.jqwik` in `pom.xml` or a Gradle build file |
| gopter, rapid | property | `github.com/leanovate/gopter` or `pgregory.net/rapid` in `go.mod` |

**Mutation check.** Run the declared tool in the verification worktree under the repo's own configuration, scoped to the changed source files through the tool's own command-line option for naming the files or paths to mutate. A tool with no such option is `not possible: <tool> cannot be scoped to the changed files`, so code the PR never touched cannot fail it. Never pass a threshold option on the command line. A PASS lists the mutation score and the surviving mutants.

**Property-based check.** Run, with the per-file form of the documented test command in the verification worktree, every test file that names a declared property tool and either is added or changed by the PR or contains the file name, without extension, of a changed source file (the in-scope property test files). A FAIL counts only a failure that TC-1's outcome column ties to an in-scope property test file; failures the output ties to other files have no effect on this check.

Mutation and property-based result (each evaluated alone, first matching row):

| # | Condition | Result |
|---|-----------|--------|
| 1 | No tool of this kind is declared at the PR head | `N/A: no mutation tooling declared` or `N/A: no property-based tooling declared` |
| 2 | Nothing is in scope (no changed source file for mutation; no in-scope property test file for property) | `N/A: nothing in scope` |
| 3 | The tool fails to start, cannot be scoped, or exits non-zero without reporting a mutation result, a counterexample, a failing case, or a build or load failure | `not possible: <tool> cannot be scoped to the changed files` for a tool that cannot be scoped; otherwise `not possible: <first error line>` |
| 4 | A property run reports a counterexample, a failing case, or a build or load failure that TC-1's outcome column ties to an in-scope property test file | `FAIL`, quoting the counterexample or failure |
| 5 | The repo's own config for the mutation tool sets a minimum score (Stryker `thresholds.break`, PIT `mutationThreshold`, Infection `minMsi` or `minCoveredMsi`, or a threshold option in the documented mutation command) and the run exits non-zero reporting a score below that configured threshold | `FAIL`, with the score, the threshold and the survivors |
| 6 | Otherwise, including a tool that exits non-zero only because mutants survived when the repo sets no minimum score (for example cargo-mutants or mutmut with no threshold), and a property run whose only failures the output ties to files outside the in-scope property test files | `PASS`, with the score and survivors (mutation) or the case count (property) |

### Review Standards Checklist

Run the "Review Standards Checklist" in `skills/shared/standards.md` against the PR diff. Each violation is a failure listed in the test report's `## Failures` section, and the verdict is REQUEST_CHANGES, the same as a failed scenario. Its failures take the `[Test: Review Standards Checklist]` tag.

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
> test report under "Failures". A merged failure keeps its `[Test: <section>]` tag.
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
**Deploy path:** [reused developer deploy run {RUN_ID} | fresh tester deploy run {RUN_ID} | skipped by exemption]
**Test round:** {R}

## Summary
**{X}/{Y} tests passed**

## Test Results

### Happy Path
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

### Error Scenarios
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

### UX Verification
- [PASS/FAIL] [test description] — evidence: [brief evidence summary]

### Test Integrity
- [PASS/FAIL/N/A/not possible] revert-the-fix — command: [command], exit code: [n], evidence: [one line per test file: `<file> — merge-base: pass|fail, head: pass|fail`, then the first failing line at the merge-base]
- [PASS/FAIL/N/A/not possible] mutation — command: [command], exit code: [n], evidence: [score and surviving mutants, or the N/A or not possible reason]
- [PASS/FAIL/N/A/not possible] property-based — command: [command], exit code: [n], evidence: [case count, counterexample, or the N/A or not possible reason]

## Failures
[If any: specific details of each failure with reproduction steps. Start every item with `[Test: <section>]`, the section being Happy Path, Error Scenarios, UX Verification, Test Integrity or Review Standards Checklist.]

## Recommendation
[APPROVE / REQUEST CHANGES with reasoning]

## Review Precision

| Perspective | Raised | Verifier-dropped | Developer-disputed |
|-------------|--------|------------------|--------------------|
| Happy Path | {n} | n/a | {n} |
| Error Scenarios | {n} | n/a | {n} |
| UX Verification | {n} | n/a | {n} |
| Test Integrity | {n} | n/a | {n} |
| Review Standards Checklist | {n} | n/a | {n} |
```

**Test Integrity counting.** `PASS` and `FAIL` bullets count toward `{X}/{Y} tests passed`; `N/A` and `not possible` do not. A `FAIL` is also listed under `## Failures` with the `[Test: Test Integrity]` tag and makes the verdict REQUEST_CHANGES; `N/A` and `not possible` never block an APPROVE.

**Test round.** Set `{R}` to 1 plus the number of earlier reviews on the PR whose body starts with `## Test Report`. In a Local Mode or approval-only round, `{R}` is the N of the `test-round-N.md` note being written.

**Review Precision.** Raised is the number of `## Failures` items carrying that row's tag in this report. Verifier-dropped is `n/a` in every row. Developer-disputed counts the items tagged `[Test: <section>]` in `## Disputed Findings` sections posted after the latest earlier test report by the trusted authors defined in `skills/reviewing-prs/SKILL.md` Re-Review Mode "Disputes" (PR conversation comments; in Local Mode and approval-only mode, the fix note for the previous test round), in the row of that section. Every row is always printed, with `0` where nothing was counted. The round line and the table live only in the report text; add no checkpoint field, file, label or PM field.

**A successful dev deploy run on current HEAD is a necessary precondition for an APPROVE recommendation.** The recommendation may be APPROVE only if a dev deploy CI run reached conclusion `success` on the PR branch's current HEAD — either the validated reused developer run or a fresh tester run (or the repo is dev-deploy-CI-exempt and the report shows the verification command and output per the Dev Deploy CI Exemption section above) **and** all test scenarios passed. Missing, stale, or invalid developer evidence on a non-exempt repo, a deploy failure, a missing/unrunnable deploy CI on a non-exempt repo, a non-`success` deploy CI conclusion, a local-only deploy, or an exemption claim missing its verification line yields **REQUEST CHANGES** regardless of any local validation.

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
- **APPROVE** (`gh pr review {PR_NUMBER} --approve`) only if a dev deploy CI run concluded `success` on current HEAD — the validated reused developer run or a fresh tester run (or the repo is dev-deploy-CI-exempt with a verified exemption claim) **and** all tests pass
- **REQUEST_CHANGES** (`gh pr review {PR_NUMBER} --request-changes`) if the dev deploy gate was not satisfied on a non-exempt repo (developer evidence missing, stale, or invalid, deploy failed, no runnable deploy CI, non-`success` conclusion, or local-only deploy), if an exemption claim is missing its verification line, **or** any test fails

A successful dev deploy CI run is a precondition for both APPROVE and the `tested-in-dev` label below. Never APPROVE + `tested-in-dev` without it.

Include full test report in the review body.

**Disputes.** The tester never rules on a dispute. Every run re-runs every scenario under the ALWAYS-FRESH mandate, so a disputed failure that recurs is listed again under `## Failures`; the tester only counts disputes in its Review Precision table.

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

### Story State Transition (after test approval)

Runs only after this run submitted `APPROVE`, and only when `story_states.after_test_approval` is set in `~/.claude/dev-workflow/config.json`. With the key unset, fire no transition. A non-APPROVE verdict fires nothing. In Local Mode (no PR) the transition is skipped. In approval-only mode the review decision is read from the latest `review-round-N.md` note (highest N) instead of the reviews API: `decision=APPROVED` counts as review approval, anything else, including no note, skips the transition.

1. Read the PR's reviews: `gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews`.
2. Never count a review whose body starts with `## Test Report` (the heading this skill puts at the top of every test review) as the reviewer's. Of the remaining reviews, take the reviewer's as follows: when `reviewer_login` is set and that login has at least one such review (an `APPROVED` or `CHANGES_REQUESTED` review that is not a `## Test Report`), the reviewer's reviews are those by that login; otherwise (unset, or set but absent from this PR, as on a repo where a person reviews) they are all of the remaining reviews, so a shared reviewer/tester login still works.
3. Take the reviewer's latest review (by `submitted_at`) whose state is `APPROVED` or `CHANGES_REQUESTED`. No review ID is needed. Never use the PR-level `reviewDecision`.
4. If that state is exactly `APPROVED`, resolve the story (from the branch's story token, else the PM adapter's Resolve story from PR) and move it to the `story_states.after_test_approval` state through the PM adapter's Update Story operation. Skip when the story is already in that state.
5. Otherwise skip, and report the story left unchanged pending review approval.
