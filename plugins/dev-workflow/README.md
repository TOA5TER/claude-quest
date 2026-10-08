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
      "decision-read": "sonnet"
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
| `models.reasoning` | `opus` | All reasoning/planning subagents (brainstorming, architecture) |
| `models.review` | `opus` | All review/testing subagents (review board, adversarial review, test agents) |
| `models.stages.writing-specs` | `sonnet` | full-cycle's writing-specs stage subagent (autonomous path only) |
| `models.stages.developing` | `sonnet` | full-cycle's developing stage (the developer role session's model, fixed at launch, or the fresh developer dispatch) |
| `models.stages.reviewing-prs` | `opus` | full-cycle's reviewing-prs stage (reviewer role session or fresh subagent) |
| `models.stages.testing-prs` | `opus` | full-cycle's testing-prs stage (tester role session or fresh subagent) |
| `models.stages.addressing-pr-comments` | `sonnet` | The fresh developer rework dispatch in the review and test loops (fallback path and epic per-task workers). Ignored in role-session mode, where the developer session's model is fixed at launch from `models.stages.developing`. Existing configs that set it keep working on the fallback path only. |
| `models.stages.entry-detection` | `sonnet` | full-cycle's resume/entry-detection subagent |
| `models.stages.pr-number-read` | `sonnet` | full-cycle's post-developing PR-number resolution subagent |
| `models.stages.decision-read` | `sonnet` | full-cycle's authoritative review/test decision-read subagent |

**Resolution order** for any dispatch: `models.stages.<stage-key>` → `models.<task-type>` → built-in default. Stage-level keys take priority over task-type keys. Users who never add the `models` section see no change in behavior.

**Migration note:** the `models.stages.*` keys were renamed to match the sc-1623 skill rename (`write-spec` → `writing-specs`, `start-development` → `developing`, `review-pr` → `reviewing-prs`, `test-pr` → `testing-prs`, `address-pr-comments` → `addressing-pr-comments`). If your `settings.json` has an existing `models.stages.start-development`-style entry under one of these five old names, rename it manually to the new key — the old key silently stops applying (falls through to `models.implementation`/`models.review`/default instead of erroring) rather than failing loudly. `entry-detection`, `pr-number-read`, and `decision-read` are unaffected.

### CI / Deploy Gate Exemptions

Two optional arrays let specific repos opt out of the otherwise-mandatory CI gates. Both default to gated.

| Key | Governs | Effect when a repo is listed |
|-----|---------|------------------------------|
| `ci_gate_exempt_repos` | `reviewing-prs`'s dev build CI gate | The review may APPROVE without a passing dev build CI run. The review body states the gate was skipped by exemption. |
| `deploy_gate_exempt_repos` | `testing-prs`'s dev deploy CI gate | The test may APPROVE without a successful dev deploy CI run. The test report states functional dev testing was skipped by exemption. |

Each is an array of repository names (matching `git rev-parse --show-toplevel | xargs basename`). The two gates are independent — a repo may be exempt from one and not the other.

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

## Role Sessions (standalone full-cycle)

Standalone `full-cycle` runs the developer, reviewer, and tester as long-lived, named background Claude Code sessions instead of fresh one-shot subagents per stage and per loop pass. A review result is routed by the orchestrator to the repo's developer session, which already holds the PR's context, and the fix is routed back to the reviewer that still holds its review context; the tester works the same way. Sessions exchange messages through the orchestrator only (hub-and-spoke); workers never message each other. GitHub stays the authoritative source for every review and test decision. **Communication contract.** Each task message (`develop`, `fix`, `review`, `test`) is answered first by an `ack` receipt and then by exactly one terminal reply, `result` or `blocked`, sent with the `SendMessage` tool as the worker's last action on every exit path; ending a turn with plain text is not a reply. After every send the orchestrator names what is in flight and arms a watcher (a visible `Monitor` first, then a scheduled wake-up, then telling you which request is outstanding), completes each multi-step hand-off in the turn its trigger arrives, and reacts differently to a missing `ack` (a check two minutes after the send) and a missing terminal reply (the liveness table and per-round limits). The protocol is in `skills/shared/role-sessions.md`.

**What changed.** The separate fix-loop agent type (named in the PR description) is removed. Fix work is now the developer's rework mode: `dev-workflow:developer` given a PR number lands on the PR's branch and runs `addressing-pr-comments`. `dev-workflow:developer`, `dev-workflow:reviewer`, and `dev-workflow:tester` still work as fresh one-shot dispatches. Downstream plugins that dispatched that agent should dispatch the developer in rework mode, or adopt the role-session protocol.

**Prerequisites.**

- Claude Code v2.1.224 or later.
- The orchestrator session must accept inbound cross-session messages (`crossSessionInbound` set to `accept`), or share the workers' permission-mode class. Otherwise a worker's reply is held for approval and the run falls back.
- Background launches need a trusted workspace.

**Fallback.** A preflight runs once per run. If any check fails (old version, agent view unavailable, background launch refused, no handshake reply), the run announces the reason once and completes entirely on the fresh-dispatch path: fresh developer, reviewer, and tester dispatches, with a fresh developer dispatch in rework mode for each fix loop. Modes are never mixed within a run. `epic` per-task workers always use the fresh-dispatch path.

**Config.** Optional `role_sessions.permission_mode` in `config.json` is passed as the sessions' permission mode; when unset the host default applies. Permitted values are `default`, `acceptEdits`, and `plan` (`plan` applies only to the reviewer and tester; the developer launches with no flag); optional `role_sessions.round_timeout_minutes` overrides the per-request time limit (defaults: 60 minutes for develop and fix, 120 for review and test); `bypassPermissions` and any other value are refused, because role sessions accept inbound messages from any local session.

**Cost.** Each role session consumes subscription usage like any interactive session. A story holds one developer session per repo plus a reviewer and a tester per PR, each named with its repo. Sessions are never shut down, stopped, or removed without your explicit permission: at Termination and on every non-success path the orchestrator lists the live sessions, says they remain available for more work, and asks. Sessions you leave running are listed in the final report; remove one with `claude stop <name>` then `claude rm <name>`.

## Adapters

**PM adapters** (`skills/pm-adapter/`): `shortcut`, `jira`, `linear`, `github-issues`, `tasklist`

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
