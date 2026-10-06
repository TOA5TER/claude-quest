# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**dev-workflow** is a Claude plugin that provides action-based development workflow orchestration with pluggable PM and notes adapters. It enables specialized workflows (Developing, Writing Specs, Reviewing PRs, Testing PRs, Debugging, Creating Stories, Full Cycle) through structured, quality-gated stages.

**Dependency:** Requires the `superpowers` plugin to be installed — it provides core methodology skills (TDD, debugging, brainstorming, subagent orchestration, verification). Also requires `guardrails-git` >= 1.2.0 — every autonomous implement/fix stage now works inside an isolated git worktree (see `skills/shared/standards.md` → "Workspace Isolation"), and older `guardrails-git` versions hard-block `git worktree` commands outright.

## Architecture

### Role Dispatcher Pattern

Skills are invoked directly by name:

| Command | Skill | Purpose |
|---------|-------|---------|
| `/start developing [story-id]` | `dev-workflow:developing` | Feature implementation with TDD |
| `/start writing-specs story-id` | `dev-workflow:writing-specs` | Story → Claude Instructions spec |
| `/start reviewing-prs PR-number` | `dev-workflow:reviewing-prs` | Multi-perspective PR review |
| `/start testing-prs PR-number` | `dev-workflow:testing-prs` | Functional testing with evidence |
| `/start debugging` | `dev-workflow:debugging` | Bug investigation |
| `/start debugging story-id --rework` | `dev-workflow:debugging` (rework mode) | Address review feedback |
| `/start creating-stories` | `dev-workflow:creating-stories` | Interview user → draft → submit story |
| `/start full-cycle [story-id\|description]` | `dev-workflow:full-cycle` | End-to-end lifecycle orchestrator looping review/test until pass |
| `/start epic [summary\|epic-slug]` | `dev-workflow:epic` | Decompose a large initiative into a self-managed tasklist, then autonomously drive each task to a review- and test-approved open PR, pausing for a human to merge |

### Adapter System

PM and notes integrations are **pluggable adapters** with a common interface defined in `skills/pm-adapter/interface.md` and `skills/notes-adapter/interface.md`.

**PM Adapters** (`skills/pm-adapter/`): Shortcut, Linear, Jira, GitHub Issues, Tasklist (file-backed, used by the `epic` orchestrator — no external PM tool)
**Notes Adapters** (`skills/notes-adapter/`): Local filesystem (`docs/specs/`), Obsidian vault

**Override mechanism:** User-provided adapters at `~/.claude/skills/pm-adapter/{name}.md` or `~/.claude/skills/notes-adapter/{name}.md` take precedence over built-in adapters.

### Superpowers Integration

Skills invoke superpowers throughout their workflows:
- `superpowers:writing-plans` — Implementation task structuring
- `superpowers:test-driven-development` — RED-GREEN-REFACTOR cycles
- `superpowers:brainstorming` — Requirement discovery and edge cases
- `superpowers:systematic-debugging` — Root cause analysis
- `superpowers:subagent-driven-development` — Parallel task execution
- `superpowers:requesting-code-review` / `superpowers:receiving-code-review` — Review workflows
- `superpowers:verification-before-completion` — 5-phase verification gate

## Key Design Decisions

- **reviewing-prs skill has two modes:** First Review (exhaustive 4-perspective analysis) vs. Re-Review (verify previous `CHANGES_REQUESTED` items were addressed, new findings only if they meet the Critical Exception Threshold)
- **debugging skill is a unified 3-mode skill:** Debug mode (no args), Development mode (story-id), Rework mode (story-id + `--rework`)
- **epic skill is an orchestrator-only initiative driver:** deep discovery → one up-front consensus gate → self-managed `tasklist.md` (Mermaid graph + embedded per-task description/status) at `~/.claude/dev-workflow/epics/[epic-slug]/` → autonomous scheduler (cross-repo concurrent, same-repo sequential, one in-flight PR per repo) driving each task through `full-cycle` pinned to the `tasklist` adapter, each task isolated in its own git worktree (a workspace-isolation property, not a parallelism one — see `skills/shared/standards.md` → "Workspace Isolation"; reclaimed at the task's `awaiting-merge → done` transition). On reviewer+tester dual approval it does **not** merge — it marks the task `awaiting-merge`, tracks the open PR, and pauses that line of work for a human to merge; a later resume detects the human merge and advances the task to done (unblocking dependents). Epic PRs carry **no** `sc-` ID (documented exception). Bug intake: a subagent *reports* a defect from a prior task; the *orchestrator* appends a priority-scheduled `bug` task. Resumable from `tasklist.md`.
- **tasklist PM adapter is file-backed, not config-selected:** the `epic` orchestrator pins it per dispatch (supplying the tasklist path + task ID in the subagent prompt) rather than mutating global `config.json`. It implements the full pm-adapter interface against `tasklist.md` so `full-cycle` and the stage skills run unchanged.
- **Stage isolation via dedicated subagent types:** the orchestrators (`full-cycle`, `epic`) run every non-interactive stage in a fresh, isolated context by **dispatching the Agent tool** with a stage-specific `subagent_type` from `agents/` — never by invoking the `Skill` tool themselves (a `Skill` call loads into the *current* context, which is what made stages run in one agent). Each worker's body invokes the matching `dev-workflow:{stage}` skill autonomously, so stage logic/resumability/loops are unchanged; the `model` parameter on the dispatch overrides the worker's frontmatter default, preserving config-driven model resolution. See `skills/shared/standards.md` → "Subagent Dispatch". Workers that fan out (developer/reviewer/tester/orchestrator) keep the `Agent` tool; `pr-state-reader` is tool-restricted.
- **Role sessions for standalone full-cycle:** the developer (one per repo, launched in that repo's checkout), reviewer, and tester (one per PR) run as persistent, named background sessions (`claude --bg --agent dev-workflow:<agent>`) reached through cross-session messages in a hub-and-spoke topology (workers reply only to the orchestrator; the orchestrator alone routes), instead of fresh dispatches per stage and loop pass. Fix work is the developer's rework mode (there is no separate fix agent). Sessions are torn down (`shutdown`, `claude stop`, `claude rm`) only with the user's explicit permission; sessions left running are listed with the manual cleanup commands. When messaging is unavailable the run falls back to fresh dispatch, and `epic` per-task workers always use fresh dispatch. `compact-injector.sh` and `context-meter.sh` exit when `DEV_WORKFLOW_ROLE` is set; `role-session-context.sh` acts only when it is set. See `skills/shared/role-sessions.md`.
- **Subagent nesting (Claude Code v2.1.172+):** a subagent may nest further subagents (fixed depth-5 cap) when it has the `Agent` tool. This is what lets `epic → dev-workflow-orchestrator (full-cycle) → per-stage worker` give each stage fresh context (depth 3). On builds older than v2.1.172, nesting is unavailable and a task's stages run inline within its worker — isolated per task, not per stage. See `skills/shared/standards.md` → "Subagent Nesting".
- **Reality Filter:** All skills enforce labeling unverified content as `[Inference]`, `[Speculation]`, or `[Unverified]`
- **Config location:** User configuration lives at `~/.claude/dev-workflow/config.json`, not in the repo

## File Layout

```
skills/
  developing/   # Full development workflow (TDD, subagents, PR)
  writing-specs/          # Story → Claude Instructions spec transformation
    spec-template.html # Standalone HTML document shell for generated specs
  reviewing-prs/           # Multi-perspective PR review with mode detection
  testing-prs/             # Evidence-based functional testing
  debugging/     # Debug/dev/rework unified skill
  creating-stories/        # Interactive interview → PM story creation
  full-cycle/          # End-to-end lifecycle orchestrator (sequences all stages)
  epic/                # Initiative orchestrator: discovery → consensus → self-managed tasklist → autonomous per-task full-cycle drive
  addressing-pr-comments/ # Address review feedback in current session
  pm-adapter/          # PM tool adapters + interface spec (includes file-backed tasklist adapter)
  notes-adapter/       # Notes storage adapters + interface spec
  shared/              # Shared protocol docs (standards, adapter-loading, context-compaction, ...)
agents/                # Dedicated subagent types dispatched by the orchestrators (one per pipeline role)
  dev-workflow-spec-writer.md      # writing-specs (autonomous path only)
  dev-workflow-developer.md        # developing (story mode) + addressing-pr-comments (rework mode); role session or fresh dispatch
  dev-workflow-reviewer.md         # reviewing-prs; role session or fresh dispatch
  dev-workflow-tester.md           # testing-prs; role session or fresh dispatch
  dev-workflow-pr-state-reader.md  # entry/resume detection + PR-number resolution + authoritative decision read (read-only)
  dev-workflow-orchestrator.md     # full-cycle, dispatched per-task by epic (retains Agent tool to nest)
hooks/
  context-meter.sh     # PostToolUse: token usage meter — emits at 60%/75% of 200k baseline
  compact-injector.sh  # Stop: consumes .compact-request sentinel and injects /compact via tmux
  role-session-context.sh  # SessionStart (role sessions only): injects the absolute plugin root and standards path
  hooks.json           # Hook registration (CLAUDE_PLUGIN_ROOT-relative paths)
.claude-plugin/        # Plugin manifest (plugin.json)
```

Runtime state (not committed): `~/.claude/dev-workflow/state/`
- `{story-id}.json` — per-story checkpoint (stage, PR numbers, loop counts, next action)

Epic state (not committed): `~/.claude/dev-workflow/epics/[epic-slug]/`
- `tasklist.md` — the epic's single source of truth: Mermaid dependency graph + embedded per-task description/AC/testing/status. Doubles as durable cross-task resume state.
- `.compact-request` — sentinel written by full-cycle at a high-context handoff (inside tmux only)
- `.compact-request.failed` — written by compact-injector on injection failure
- `context-meter-tier.txt` — last announced meter tier (prevents repeat emissions)

## Working on This Codebase

Content is mostly Markdown skill definitions, plus a small number of static assets (e.g., `skills/writing-specs/spec-template.html`) — there is no compiled code and no build step. The only tests are the shell tests under `tests/` for the hooks; run `bash tests/test_role_session_context.sh` after touching a hook, `hooks/hooks.json` or the role agents. Changes are made by editing `.md` files (and the occasional asset file) in `skills/` and `commands/`.

When modifying a skill:
- Update the version in `.claude-plugin/plugin.json` if changing behavior, **and** bump the matching entry's `version` in the repo-root `.claude-plugin/marketplace.json` to the same value, both in the same PR as the behavior change — the two drift independently and only the second one is what marketplace consumers actually see
- Maintain phase numbering consistency within skills (phases are referenced by number in other skills and documentation)
- Preserve the adapter interface contracts in `interface.md` files — adapters must implement all required operations
- Test skill changes by invoking them with `/start <role>` in a target repository
