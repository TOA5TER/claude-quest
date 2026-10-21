---
name: dev-workflow:dev-verify
description: "Run the developer's dev deploy and verification gate against an existing PR on its current HEAD: exemption check, trigger the dev deploy CI, wait, verify each acceptance criterion in dev, fix loop, post the evidence comment, mark the PR ready. Invoked by the developer agent's verify mode and by wrappers that defer developing's gate. Argument: the PR number."
---

# Dev Verify

**Role:** Developer — prove a PR works in dev before hand-off

**SCOPE BOUNDARY:** This skill never creates PM stories, tickets, issues, or subtasks, and never touches production. It runs autonomously.

Read `skills/shared/standards.md` — these mandatory rules govern this entire session.

Read `skills/shared/dev-deploy.md` — the procedure this skill runs, end to end.

Read `skills/shared/adapter-loading.md` — adapter loading procedure, used to load the PM adapter and fetch the linked story.

## Input

The PR number. Resolve the PR's head branch and current head SHA, and land on the branch in its worktree exactly as rework mode does (look up the live worktree with `git -C <repo root> worktree list --porcelain`, else check the PR out).

The acceptance criteria to verify come from the linked story (resolve it with the PM adapter's **Resolve story from PR** capability, `skills/pm-adapter/interface.md`: its link lookup first, then the delimited story token in the PR's head branch name, never the PR title or body; load the PM adapter per `skills/shared/adapter-loading.md`), or from the PR description when no story is linked.

## Local Mode

A Local Mode repo (see `skills/shared/local-mode.md`) has no PR: there is no PR number to resolve, no evidence comment to post and no PR to mark ready. The repo is on both exempt lists, so the exempt-form gate result (the literal `jq` exemption command and its output) goes into the develop note, or the fix note for a fix round, instead of a PR comment, and the outcome is `exempt`.

## Procedure

Run the developer half of `skills/shared/dev-deploy.md` on the PR's current HEAD:

1. Exemption check. For a repo in `ci_gate_exempt_repos` or `deploy_gate_exempt_repos`, post the exempt-form evidence comment with the proof line and mark the PR ready.
2. Otherwise resolve the deploy workflow, trigger it, identify the run by SHA, and wait for conclusion `success`.
3. Verify every acceptance criterion against live dev (apply the nothing-observable rule when applicable).
4. On any failure: fix, commit, push, redeploy, reverify, looping until everything passes. Apply the infrastructure-failure retry rule.
5. Post the dev-verification evidence comment for the final HEAD, then mark the PR ready.

## Outcome

End with exactly one outcome line:

- `verified`: evidence posted for the final HEAD and the PR is ready.
- `exempt`: exempt-form evidence posted and the PR is ready.
- `dev-verification blocked: <reason>`: the gate could not pass. The PR stays a draft.
