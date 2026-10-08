---
name: dev-workflow:addressing-pr-comments
description: "Address PR review feedback in the current session — reads new comments, file-level inline comments, and review decisions since the last commit, then implements the required changes and replies to the PR with a summary of fixes. Use when a user asks to address PR comments, respond to review feedback, fix review notes, or iterate on a PR."
---

# Addressing PR Comments

**Role:** Address review feedback on the current PR — read, implement, and respond

**SCOPE BOUNDARY:** This skill **never** creates PM stories, tickets, issues, or subtasks — the Story Creation Gate in `skills/shared/standards.md` applies. Necessary extra work discovered mid-pipeline is governed by "Necessary Extra Work — No Follow-On Tickets" in `skills/shared/standards.md`: fold it into the current branch/PR by default; only a huge scope increase warrants stopping to ask — never a follow-on ticket.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/adapter-loading.md` — adapter loading procedures referenced in Step 1.

Read `skills/shared/checkpoint-seeding.md` — checkpoint seeding procedure referenced in Step 1.

Compact the conversation before continuing — you are about to iterate on existing work. Skip this when running inside a role session (the `DEV_WORKFLOW_ROLE` environment variable is set): a persistent session keeps its context across rounds and relies on the host's native auto-compaction (see `skills/shared/role-sessions.md`).

---

## Step 1: Identify the PR

If no PR number is provided in context:

```bash
gh pr status --json currentBranch
```

Use the current branch's open PR. If no PR is open, ask the user for the PR number.

Once the PR is resolved:

1. Read `~/.claude/dev-workflow/config.json` to get `pm_adapter`
2. Load PM adapter per procedure in `skills/shared/adapter-loading.md`
3. Resolve the story with the loaded adapter's **Resolve story from PR** capability (the same
   resolution `reviewing-prs`/`testing-prs` perform in their own Phase 2): its link lookup first,
   then the delimited story token in the PR's head branch name. Never read the PR title or body.
   When a custom adapter lacks the capability, apply the generic delimited branch-token match
   using `adapters.<name>.story_id_prefix` from `~/.claude/dev-workflow/config.json`; with no such
   key, treat the story as not found.
4. Detect service name: `git rev-parse --show-toplevel | xargs basename`
5. **If a story ID is found:** call `skills/shared/checkpoint-seeding.md`'s "Seed or Refresh
   Stage" with that story ID, the detected service name, stage `"reviewing-prs"`, and this
   PR's number — its fix work always happens inside an already-open review or test loop
   window, so it reuses that same stage value rather than introducing a new one.
6. **If no story ID is found:** skip the checkpoint call silently and continue exactly as
   today — this is best-effort telemetry and must not become a new hard requirement for a
   skill that currently tolerates no PM linkage.

---

## Step 2: Load New Feedback

Fetch all comment types since the last commit. Use the actual PR number in every command.

**Conversation comments:**
```bash
gh api repos/{owner}/{repo}/issues/{PR_NUMBER}/comments
```

**Inline file comments:**
```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/comments
```

**Reviews (state + body):**
```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/reviews
```

Filter to comments created or updated after the last commit timestamp:
```bash
git log -1 --format=%cI
```

Deduplicate: if an inline comment is also referenced in a review body, address it once.

---

## Step 3: Summarize Required Changes

Before writing any code, list every actionable item as a numbered checklist:

```
## Changes Required

1. [Author @foo, file.ts:42] — [what they asked for]
2. [Author @bar, review body] — [what they asked for]
```

If no actionable feedback is found, inform the user and stop.

---

## Step 4: Implement Changes

Address each item from the checklist:

- Apply the full RED-GREEN-REFACTOR cycle for code changes:
  > Invoke Skill: `superpowers:test-driven-development`
- Address items in checklist order — do not skip or defer
- Do not change anything outside the scope of reviewer feedback. Exception: the Code Comment Compliance Check in Step 5 requires rephrasing or removing any matched ticket/commit-hash/CI-run-ID citation with no suppression path, even when the offending comment predates this review/fix round and no reviewer flagged it — this is the one carve-out to this rule.
- Same-repo boundary: never clone, check out, or read another repository's files during a fix loop — not even to investigate a cross-repo constraint (e.g. a schema or config another repo defines). Investigate via documentation, the PR description, or by asking the user instead.
- Commit frequently with descriptive messages referencing the item being addressed

---

## Step 5: Verify

### Code Comment Compliance Check

Read and follow `skills/shared/code-comment-check.md` in full — the base-ref resolution, diff
commands, comment-marker table, regex patterns, and blocking policy all live there (shared with
`developing/SKILL.md`, which runs the identical check). The offending citation must be
rephrased or removed per the "Code Comments" rule in `skills/shared/standards.md` (see the Step 4
carve-out above).

This check runs once, from the current repo root, as part of Step 5 — `addressing-pr-comments` has no
multi-repo path, no repo discovery, and no per-repo sub-agent dispatch, so only the "Single-repo
path" bullet of that file's "Execution scope" section applies here.

> Invoke Skill: `superpowers:verification-before-completion`
>
> Verify with fresh execution:
> - All tests pass (run the full suite now)
> - Each checklist item from Step 3 has been addressed
> - Code is pushed to remote

---

## Step 5.5: Functional Verification in Dev Environment

After unit/integration tests pass, verify the fixes work in a running environment. Loop until certain.

**Deploy:**

Check `~/.claude/dev-workflow/config.json` for `deploy_command`.

- **If `deploy_command` is configured:** Deploy the branch to dev — follow the same deployment procedure as `dev-workflow:testing-prs` Phase 3 (GitHub Actions, shell command, or other pattern).
- **If not configured:** Run the service locally. Start it using whatever mechanism the project provides (Makefile, npm scripts, etc.).

**Test:**

For each item from the Step 3 checklist, design and execute a focused test scenario:

1. Document what you're doing and the expected outcome
2. Execute with fresh commands — no assumptions from prior runs
3. Collect evidence: logs, API responses, CLI output, screenshots
4. Record PASS or FAIL with specific details

Apply the accountability rules from `dev-workflow:testing-prs`: if you see an error, it is a failure — do not rationalize it away.

**Loop:**

- If any test fails → fix the issue, commit, push, re-deploy, re-test
- Continue until ALL addressed items pass functional testing
- Only proceed to Step 6 when you are certain every fix works correctly in the running environment

---

## Step 6: Reply to PR

### 6a: Reply to Inline Comments Directly

For each inline file comment addressed in Step 4, post a **direct reply** on that comment thread explaining what was changed:

```bash
gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/comments/{COMMENT_ID}/replies -f body="..."
```

Reply format: one concise sentence stating what was done (e.g., "Fixed — switched to `parseISO()` at `utils.ts:47`."). Do not repeat the reviewer's request back to them.

If you deviated from what the reviewer asked on a specific comment, explain why in the reply to that comment.

### 6b: Post Summary Comment

Post a summary comment on the PR covering all changes. Call out any deviations not already explained in inline replies.

Comment format:
```markdown
## Changes Addressed

1. [Item 1] — [what was changed and where: file.ts:42]
2. [Item 2] — [what was changed and where]

## Deviations
[Omit section entirely if none. Otherwise explain each deviation and reasoning.]
```

```bash
gh pr comment {PR_NUMBER} --body "..."
```
