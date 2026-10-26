# dev-workflow

Role-based development workflow subagents with pluggable PM and notes adapters.

## Prerequisites

This plugin requires the [superpowers plugin](https://github.com/obra/superpowers) to be installed:

```
/plugin install superpowers@superpowers-marketplace
```

The superpowers plugin provides core methodology skills (TDD, systematic debugging, brainstorming, verification gates, subagent orchestration) that are invoked throughout the dev-workflow skill phases.

## Roles

| Command | Skill | Purpose |
|---------|-------|---------|
| `/start developing [story-id]` | developing | Branch, implement with TDD, commit, create PR |
| `/start writing-specs story-id` | writing-specs | Fetch story → analyze codebase → write Claude Instructions spec |
| `/start reviewing-prs PR` | reviewing-prs | Multi-perspective PR review against story requirements |
| `/start testing-prs PR` | testing-prs | Functional testing with evidence gathering |
| `/start debugging` | debugging | Debug-first workflow (describe bug → investigate → TDD fix) |
| `/start debugging story-id --rework` | debugging | Read story comments as rework items → fix → new PR |
| `/start creating-stories` | creating-stories | Interview user → draft story → submit to PM tool |
| `/start toiling [story-id]` | toiling | Operations work with no branch or PR: unless already told, asks whether the agent carries out the task or writes a runbook under .scratch/ |
| `dev-workflow:dev-verify PR` | dev-verify | Run the developer's dev deploy and verification gate on an existing PR: deploy to dev, verify each criterion, post evidence, mark ready |
| `/start full-cycle [story-id\|description]` | full-cycle | Drive the whole lifecycle end to end: creating-stories → writing-specs → developing → reviewing-prs → testing-prs, looping until tests pass |

## Configuration

Create `~/.claude/dev-workflow/config.json`:

```json
{
  "pm_adapter": "shortcut",
  "notes_adapter": "obsidian",
  "adapters": {
    "obsidian": {
      "vault_path": "/path/to/your/vault",
      "prompts_dir": "Engineering/Prompts"
    },
    "local": {
      "specs_path": "docs/specs"
    },
    "shortcut": {
      "story_id_prefix": "sc-"
    }
  },
  "deploy_command": "Run the dev CI workflow in GitHub Actions",
  "ci_gate_exempt_repos": [],
  "deploy_gate_exempt_repos": [],
  "story_states": {
    "in_development": "In Development",
    "after_test_approval": "Ready for Release"
  },
  "reviewer_login": "your-reviewer-login",
  "models": {
    "implementation": "sonnet",
    "reasoning": "opus",
    "review": "opus",
    "stages": {
      "developing": "sonnet",
      "reviewing-prs": "opus",
      "testing-prs": "opus",
      "addressing-pr-comments": "sonnet",
      "entry-detection": "sonnet",
      "pr-number-read": "sonnet",
      "decision-read": "sonnet",
      "handoff-check": "sonnet",
      "verify": "sonnet"
    }
  }
}
```

The `local` notes adapter's `specs_path` is optional. When omitted, specs default to `docs/specs/` relative to the repo root. Set it to a relative path (resolved against the repo root) or an absolute path to store specs elsewhere.

The `models` section is optional. When absent, all dispatches use the built-in defaults shown above. When present, any key you set overrides the default for that task type or stage; unspecified keys fall through to defaults automatically.

**Model key reference:**

| Key | Default | Governs |
|-----|---------|---------|
| `models.implementation` | `sonnet` | All coding/implementation subagents (implementers, TDD cycles) |
| `models.reasoning` | `opus` | All reasoning/planning subagents (brainstorming, architecture, spec writing) |
| `models.review` | `opus` | All review/testing subagents (review board, adversarial review, test agents) |
| `models.stages.writing-specs` | `opus` | full-cycle's writing-specs stage: the spec-writer role session or relay dispatch in a standalone run, and the fresh writer that does the rework before review rounds 3 to 5 |
| `models.stages.developing` | `sonnet` | full-cycle's developing stage (the developer role session's model, fixed at launch, or the fresh developer dispatch) |
| `models.stages.reviewing-prs` | `opus` | full-cycle's reviewing-prs stage (reviewer role session or fresh subagent) |
| `models.stages.testing-prs` | `opus` | full-cycle's testing-prs stage (tester role session or fresh subagent) |
| `models.stages.addressing-pr-comments` | `sonnet` | The fresh developer rework dispatch in the review and test loops (fallback path). Ignored in role-session mode, where the developer session's model is fixed at launch from `models.stages.developing`. Existing configs that set it keep working on the fallback path only. |
| `models.stages.entry-detection` | `sonnet` | full-cycle's resume/entry-detection subagent |
| `models.stages.pr-number-read` | `sonnet` | full-cycle's post-developing PR-number resolution subagent |
| `models.stages.decision-read` | `sonnet` | full-cycle's authoritative review/test decision-read subagent |
| `models.stages.handoff-check` | `sonnet` | full-cycle's pre-send hand-off check subagent (PR draft state and dev-verification evidence) |
| `models.stages.verify` | `sonnet` | The fresh developer `mode: verify` dispatch (fallback path); falls back to `models.implementation` |

**Resolution order** for any dispatch: `models.stages.<stage-key>` → `models.<task-type>` → built-in default. Stage-level keys take priority over task-type keys. Users who never add the `models` section see no change in behavior.

**Migration note:** the `models.stages.*` keys were renamed to match the sc-1623 skill rename (`write-spec` → `writing-specs`, `start-development` → `developing`, `review-pr` → `reviewing-prs`, `test-pr` → `testing-prs`, `address-pr-comments` → `addressing-pr-comments`). If your `settings.json` has an existing `models.stages.start-development`-style entry under one of these five old names, rename it manually to the new key — the old key silently stops applying (falls through to `models.implementation`/`models.review`/default instead of erroring) rather than failing loudly. `entry-detection`, `pr-number-read`, and `decision-read` are unaffected.

### CI / Deploy Gate Exemptions

Two optional arrays let specific repos opt out of the otherwise-mandatory CI gates. Both default to gated.

| Key | Governs | Effect when a repo is listed |
|-----|---------|------------------------------|
| `ci_gate_exempt_repos` | `reviewing-prs`'s dev build CI gate | The review may APPROVE without a passing dev build CI run. The review body states the gate was skipped by exemption. |
| `deploy_gate_exempt_repos` | `testing-prs`'s dev deploy CI gate | The test may APPROVE without a successful dev deploy CI run. The test report states functional dev testing was skipped by exemption. |

Each is an array of repository names (matching `basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"`). The CI and deploy gates keep independent lists — a repo may be exempt from one and not the other. The developer dev-verification gate and the reviewer/tester evidence gates (see below) are skipped for a repo in **either** list.

**Invariant — absence = gated, fallback ≠ exempt:**

- A repo that is **not** listed in the relevant array is **gated**. Exemption requires explicit listing.
- The `review_ci_command` / `deploy_command` `fallback` entry is **not** an exemption — falling back to the fallback instruction still requires the gate to run and pass.
- Absence of a CI/deploy workflow on a non-exempt repo is **not** auto-exempt — it is a `REQUEST_CHANGES` (review) or `REQUEST_CHANGES` + `tests-failing` (test).

A non-passing CI/deploy result on a non-exempt repo always yields `REQUEST_CHANGES`, never `APPROVE`. A local/Makefile/script deploy never satisfies the dev deploy gate — only a successful dev deploy CI run does.

**Exemption claims must show their work.** A skip sentence alone is not enough — the review/test report must also include the literal verification command and its output immediately after the skip sentence, e.g.:

```bash
$ jq '.ci_gate_exempt_repos' ~/.claude/dev-workflow/config.json
["my-app", "claude-quest", ...]
```

A missing verification line invalidates the exemption claim and is treated as a gate failure (`REQUEST_CHANGES`), not a pass.

### Story States

Three optional keys in `~/.claude/dev-workflow/config.json` control story transitions:

| Key | Default | Effect |
|-----|---------|--------|
| `story_states.in_development` | `"In Development"` | The state `developing` (and `toiling`) moves a story to before planning, once per run, single-repo and multi-repo alike. Skipped when the request carries `transition: fired` or the story is already in that state. |
| `story_states.after_test_approval` | unset | The state `testing-prs` moves the story to after it submits `APPROVE`. Unset means no post-test transition. |
| `reviewer_login` | unset | The reviewer's GitHub login, used only by the post-test transition. |

The post-test transition fires only when the reviewer's latest `APPROVED` or `CHANGES_REQUESTED` review (by `submitted_at`) is `APPROVED`. Reviews whose body starts with `## Test Report` are never the reviewer's. The reviewer's reviews are those by `reviewer_login` when it is set and that login has at least one such review on the PR; otherwise (unset, or set but absent from the PR, as on a repo where a person reviews) they are all remaining reviews, so a shared reviewer/tester login still works. The PR-level `reviewDecision` is never used. Otherwise the story is left unchanged pending review approval.

### Developer dev verification

Before hand-off the developer deploys its branch to dev through the dev deploy CI and verifies the change against live data. The ordering for non-exempt repos:

1. Implement, then local tests, lint, and plan.
2. Push and open the PR as a **draft**.
3. Run the dev deploy CI (`deploy_command`) on the branch HEAD and wait for conclusion `success`.
4. Verify every acceptance criterion against live dev.
5. On any failure: fix, commit, push, redeploy, reverify.
6. Post the dev-verification evidence comment (a hidden marker with the SHA and run ID, plus a per-criterion table), then mark the PR ready.
7. Reviewer, then tester.

The reviewer and tester validate that evidence against the GitHub run API, never trusting the comment text. Missing, stale, or invalid evidence is bounced immediately (REQUEST_CHANGES; the tester also applies `tests-failing`) with no build, no review, and no deploy. With valid evidence the reviewer reads the developer's deploy run output instead of triggering its own dev build CI when every build job has a counterpart in the deploy run, and the tester reuses the deploy only when it is still the newest run of the deploy workflow with nothing in flight. A repo in **either** `ci_gate_exempt_repos` or `deploy_gate_exempt_repos` gets no developer deploy and no bounce; the developer posts an exempt-form note with the `jq` proof line. The reviewer also skips its evidence gate for `ci_gate_exempt_repos`. The procedure lives in `skills/shared/dev-deploy.md`, and `dev-workflow:dev-verify` runs it on an existing PR.

## Role Sessions (standalone full-cycle)

Standalone `full-cycle` runs the spec-writer, developer, reviewer, and tester as long-lived, named background Claude Code sessions instead of fresh one-shot subagents per stage and per loop pass. A review result is routed by the orchestrator to the repo's developer session, which already holds the PR's context, and the fix is routed back to the reviewer that still holds its review context; the tester works the same way. Sessions exchange messages through the orchestrator only (hub-and-spoke); workers never message each other. GitHub stays the authoritative source for every review and test decision. **Communication contract.** Each task message (`spec`, `answer`, `revise`, `finalize`, `develop`, `fix`, `review`, `test`) is answered first by an `ack` receipt and then by exactly one terminal reply, `result` or `blocked`, sent with the `SendMessage` tool as the worker's last action on every exit path; ending a turn with plain text is not a reply. After every send the orchestrator names what is in flight and arms a watcher (a visible `Monitor` first, then a scheduled wake-up, then telling you which request is outstanding), completes each multi-step hand-off in the turn its trigger arrives, and reacts differently to a missing `ack` (a check two minutes after the send) and a missing terminal reply (the liveness table and per-round limits). The protocol is in `skills/shared/role-sessions.md`.

**What changed.** The separate fix-loop agent type (named in the PR description) is removed. Fix work is now the developer's rework mode: `dev-workflow:developer` given a PR number lands on the PR's branch and runs `addressing-pr-comments`. `dev-workflow:developer`, `dev-workflow:reviewer`, and `dev-workflow:tester` still work as fresh one-shot dispatches. Downstream plugins that dispatched that agent should dispatch the developer in rework mode, or adopt the role-session protocol.

**Prerequisites.**

- Claude Code v2.1.224 or later.
- The orchestrator session must accept inbound cross-session messages (`crossSessionInbound` set to `accept`), or share the workers' permission-mode class. Otherwise a worker's reply is held for approval and the run falls back.
- Background launches need a trusted workspace.
- Only for `bypassPermissions`: `skipDangerousModePermissionPrompt: true` in user settings (`~/.claude/settings.json`) or managed settings; a background bypass launch is refused until the bypass warning has been accepted there.

**Fallback.** A preflight runs once before the first role-session launch, and each check is executed, never inferred. If a check fails when run (old version, agent view unavailable, background launch refused, no handshake reply), the run announces the failed check and its observed output once and completes the remaining work on the fresh-dispatch path: fresh spec-writer relay, developer, reviewer, and tester dispatches, with a fresh developer dispatch in rework mode for each fix loop. No stage runs on both paths at once; a run that switches from fresh dispatch to role sessions part-way follows the mid-run mode change rule in `role-sessions.md`.

**Config.** Optional `role_sessions.permission_mode` in `config.json` is passed as the sessions' permission mode for `acceptEdits`, `plan` and (with the opt-in) `bypassPermissions`; when unset the host default applies. `default` is permitted but launches every role with no flag, so the host default applies. `plan` applies only to the reviewer and tester; the spec-writer and developer launch with no flag. `bypassPermissions` is accepted, and passed to all four roles, only when `role_sessions.allow_bypass_permissions` is the JSON boolean `true`; without it, and for any other value (including `auto`), the value is refused, announced once, and the sessions launch with no flag. **The opt-in lets a message from any local session make a worker run any tool with no permission prompt, so turn it on only on a machine or container where every local session is your own** (`permissions.deny` rules still apply). When a role would launch with no flag and a launch directory's settings files set `permissions.defaultMode` to `auto`, the preflight prints a one-time, non-failing warning, because the auto-mode classifier can deny role sessions' sub-agent dispatches; managed and account-level defaults are not read. Optional `role_sessions.round_timeout_minutes` overrides the per-request time limit (defaults: 90 minutes for spec, answer, and revise; 60 for develop and fix; 120 for review and test; 30 for finalize).

**Cost.** Each role session consumes subscription usage like any interactive session. A story holds one spec-writer, one developer per repo, plus a reviewer and a tester per PR, each named with its repo. Sessions are never shut down, stopped, or removed without your explicit permission: at Termination and on every non-success path the orchestrator lists the live sessions, says they remain available for more work, and asks. Sessions you leave running are listed in the final report; remove one with `claude stop <name>` then `claude rm <name>`.

## Local Mode

Local Mode keeps a `full-cycle` run moving on a repo where the agent cannot push, or cannot approve its own PR, and asks you to act once, at the end. The rules live in `skills/shared/local-mode.md`.

| Mode | When | What happens |
|------|------|--------------|
| Normal | The repo reads `push=true` | The usual GitHub path: push, PR, reviews, labels. |
| Local Mode | `push=false` on a repo listed in both `ci_gate_exempt_repos` and `deploy_gate_exempt_repos` | The developer commits to a local worktree branch with no push and no PR. Reviewer and tester decisions, and developer fixes, are written as note files. |
| Approval-only | Push works but the reviewer or tester cannot approve or request changes on its own PR | Fixes are pushed to the open PR and the CI and deploy gates run normally; only the review and test decisions move to notes. |
| Backstop | A real push is rejected for permission on a repo listed in both exempt lists | The repo switches to Local Mode from that point, keeping its local commits. |

A `push=false` repo that is not on both exempt lists, or a repo whose push read is `unknown`, stops and asks at entry: the CI, deploy and dev-verify gates need a pushed branch and are never weakened.

**Notes.** Round notes (`develop-round-1.md`, `review-round-N.md`, `test-round-N.md`, `fix-review-round-N.md`, `fix-test-round-N.md`, and the one-time `pr-draft.md`) live in the repo's primary checkout under `.scratch/{story-id}/` when that folder is gitignored, otherwise in `$(git rev-parse --git-dir)/dev-workflow/{story-id}/`. Each note's first line carries the branch head it applies to, and a stage refuses to act on a note whose head is not the current branch tip.

**Termination hand-off.** When every Local Mode repo is review- and test-approved, the end-of-run summary gives the branch name, its tip SHA, the worktree path, the exact push command, and a ready-to-paste PR title and body built from `pr-draft.md` plus the fix notes. You push and open the PR yourself. For an approval-only repo the summary gives the PR number and quotes the decisions from the notes.

**Extension contract.** A wrapper plugin declares optional values in a `## Local Mode Extension` section of its own shared standards file: `push-by-rule` (repos that read `push=true` with no probe), `trusted tag owners` (owners whose pre-release tags may be pinned), and the `git network command` and `gh command` forms. A missing key takes its default: every repo is probed, the dependency repo's owner must equal the dependent repo's owner, and plain `git` and `gh` are used. See `skills/shared/local-mode.md` → "Local Mode Extension" for the exact format.

**Base root.** A wrapper plugin's agents replace the base agents by `subagent_type`, so they read base agent sections from `<root>/agents/<role>.md`. Role sessions receive the root from the session-start context line; orchestrating skills (`full-cycle` and any downstream wrapper's orchestration skill) end every Agent-tool dispatch prompt with a `base-root: <absolute path>` line.

**Pre-release pins.** A dependent repo may pin to a verified pre-release commit of an unmerged dependency instead of blocking on its merge, and is re-pinned to the final tag later. The PR carries a do-not-merge banner and a `pinned-pre-release: {sha}` marker until then. See `skills/shared/pre-release-pin.md`.

## Adapters

**PM adapters** (`skills/pm-adapter/`): `shortcut`, `jira`, `linear`, `github-issues`

**Notes adapters** (`skills/notes-adapter/`): `obsidian`, `local`

## Custom Adapters

You can override any built-in adapter or create a new one by placing a file in `~/.claude/skills/`:

- PM adapters: `~/.claude/skills/pm-adapter/{name}.md`
- Notes adapters: `~/.claude/skills/notes-adapter/{name}.md`

Set the matching name in your config:

```json
{
  "pm_adapter": "my-pm-tool",
  "notes_adapter": "my-notes-tool"
}
```

User adapters in `~/.claude/skills/` take precedence over plugin adapters with the same name. This means you can override a built-in adapter (e.g., create `~/.claude/skills/pm-adapter/shortcut.md` to customize Shortcut behavior) or add support for a new tool entirely.

Your adapter must implement the same interface as built-in adapters — see `skills/pm-adapter/interface.md` or `skills/notes-adapter/interface.md` for the required capabilities.

PM adapters must implement **Resolve story from PR** (the earlier story-reference capability is deprecated and no longer consulted). An adapter without it falls back to a generic delimited match of a story token in the PR's branch name. Any adapter name can carry the optional `adapters.<name>.story_id_prefix` key in `~/.claude/dev-workflow/config.json` (for example `"story_id_prefix": "sc-"` under `shortcut`); the token match is skipped when the key is absent.

## Context Compaction (full-cycle only)

Long `full-cycle` runs accumulate context. Version 2.15.0 introduced three mechanisms
to keep compaction lossless and, where possible, automatic:

**Checkpoints** — full-cycle writes `~/.claude/dev-workflow/state/{story-id}.json`
at every stage boundary and loop iteration. On re-invoke, the pipeline re-enters at
the correct stage regardless of when compaction occurred.

**Context meter** — a PostToolUse hook measures token usage against a fixed 200,000-token
baseline. At 60% it advises writing a checkpoint; at 75% it advises compacting at the
next handoff. Set `DEV_WORKFLOW_COMPACT_BASELINE` (tokens) to override the baseline.

**Compact injector** — a Stop hook fires at turn end. If a `.compact-request` sentinel
exists and the session is inside tmux, the hook spawns a detached process that injects
`/compact` into the pane and sends the resume command after compaction completes. Outside
tmux, full-cycle instead tells you the exact two commands to run manually.

**Role-session context** — a SessionStart hook (`hooks/role-session-context.sh`) that acts only when `DEV_WORKFLOW_ROLE` is set. A role session is given neither its agent file's path nor the plugin root, so the hook injects the absolute plugin root and standards path as session context, and the role agents resolve their relative `skills/` paths against it. It also restates the receipt and terminal-reply contract so it survives compaction and respawn.

All hooks are registered automatically when the plugin is loaded. Role sessions (see above) rely on the host's native auto-compaction instead: the context meter and compact injector exit immediately when the `DEV_WORKFLOW_ROLE` environment variable is set, so they never touch the shared tier file or the tmux sentinel from inside a role session.

## Installation

```json
{
  "enabledPlugins": {
    "dev-workflow@local": { "path": "/path/to/dev-workflow" }
  }
}
```

## License

MIT
