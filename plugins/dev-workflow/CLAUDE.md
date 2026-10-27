# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**dev-workflow** is a Claude plugin that provides action-based development workflow orchestration with pluggable PM and notes adapters. It enables specialized workflows (Developing, Writing Specs, Reviewing PRs, Testing PRs, Debugging, Creating Stories, Toiling, Full Cycle) through structured, quality-gated stages.

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
| `/start toiling [story-id]` | `dev-workflow:toiling` | Operations work, agent-executed or runbook, no branch/PR |
| `/start full-cycle [story-id\|description]` | `dev-workflow:full-cycle` | End-to-end lifecycle orchestrator looping review/test until pass |

### Adapter System

PM and notes integrations are **pluggable adapters** with a common interface defined in `skills/pm-adapter/interface.md` and `skills/notes-adapter/interface.md`.

**PM Adapters** (`skills/pm-adapter/`): Shortcut, Linear, Jira, GitHub Issues
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
- **Stage isolation via dedicated subagent types:** `full-cycle` runs every non-interactive stage in a fresh, isolated context by **dispatching the Agent tool** with a stage-specific `subagent_type` from `agents/` — never by invoking the `Skill` tool itself (a `Skill` call loads into the *current* context, which is what made stages run in one agent). Each worker's body invokes the matching `dev-workflow:{stage}` skill autonomously, so stage logic/resumability/loops are unchanged; the `model` parameter on the dispatch overrides the worker's frontmatter default, preserving config-driven model resolution. See `skills/shared/standards.md` → "Subagent Dispatch". Workers that fan out (spec-writer/developer/reviewer/tester) keep the `Agent` tool; `pr-state-reader` is tool-restricted.
- **Role sessions for standalone full-cycle:** the spec-writer (one per story, relaying its questions and specs to the user through the orchestrator, which owns the approval gate), the developer (one per repo, launched in that repo's checkout), reviewer, and tester (one per PR) run as persistent, named background sessions (`claude --bg --agent dev-workflow:<agent>`) reached through cross-session messages in a hub-and-spoke topology (workers reply only to the orchestrator; the orchestrator alone routes), instead of fresh dispatches per stage and loop pass. Every task message gets a receipt (`ack`) and exactly one terminal reply (`result` or `blocked`) sent through the `SendMessage` tool; plain text is never a reply, and the orchestrator never ends a turn without a named in-flight message and an armed watcher (a visible `Monitor`, then a scheduled wake-up, then telling the user). Fix work is the developer's rework mode (there is no separate fix agent). Sessions are torn down (`shutdown`, `claude stop`, `claude rm`) only with the user's explicit permission; sessions left running are listed with the manual cleanup commands. When messaging is unavailable the run falls back to fresh dispatch. `compact-injector.sh` and `context-meter.sh` exit when `DEV_WORKFLOW_ROLE` is set; `role-session-context.sh` acts only when it is set and also restates the terminal-reply contract. See `skills/shared/role-sessions.md`.
- **Subagent nesting (Claude Code v2.1.172+):** a subagent may nest further subagents (fixed depth-5 cap) when it has the `Agent` tool. This is what lets a stage worker such as the developer give its per-repo implementer subagents fresh context (depth 2). On builds older than v2.1.172, nesting is unavailable and a worker that would fan out runs that work inline within its own context. See `skills/shared/standards.md` → "Subagent Nesting".
- **Reality Filter:** All skills enforce labeling unverified content as `[Inference]`, `[Speculation]`, or `[Unverified]`; `writing-specs` narrows this for finished specs (see its Cold-Start Completeness Standard): unverified or open items are resolved, not labelled and shipped
- **Story states are configurable:** `story_states.in_development` (default "In Development") is the state `developing` and `toiling` move a story to, once per run on single-repo and multi-repo paths alike unless the request carries `transition: fired`; `story_states.after_test_approval` (unset means no transition) is the state `testing-prs` moves a story to after it approves and the reviewer's latest decision is `APPROVED`, with `reviewer_login` narrowing whose reviews count. See the README's "Story States".
- **One Review Standards Checklist:** `reviewing-prs` and `testing-prs` both run the checklist in `skills/shared/standards.md`; in Re-Review, violations in code added since the last review are Required Changes even though they fall outside the Critical Exception Threshold.
- **Local Mode is a base feature behind a small extension contract:** `skills/shared/local-mode.md` is the single source for push detection, the mode table (Local Mode, approval-only, backstop), the round notes and their head check, the resume order, and the `## Local Mode Extension` contract a wrapper plugin fills (push-by-rule repos, trusted pre-release tag owners, git and gh command forms; every key has a plain default). `skills/shared/pre-release-pin.md` is the single source for the cross-repo pre-release pin, re-pin, draft-PR resume routing and the review/test pin disclosure. `full-cycle` has a "Local Mode and Approval-Only Repos" section. Orchestrating skills end every Agent-tool dispatch prompt with a `base-root:` line so wrapper agents can read base agent sections.
- **One size gate, shared:** `skills/shared/size-gate.md` is the single definition of the counting rule, thresholds, size classes, split patterns, split-plan format and the size-accepted marker. `writing-specs` C1 and C10 and `creating-stories` Phase 3.5 cite it instead of restating numbers; `spec-lint.py` keeps its own S and M caps, held equal to the shared table by `tests/test_size_gate.sh`.
- **Config location:** User configuration lives at `~/.claude/dev-workflow/config.json`, not in the repo

## File Layout

```
skills/
  developing/   # Full development workflow (TDD, subagents, PR)
  writing-specs/          # Story → Claude Instructions spec transformation
    spec-template.html # Standalone HTML document shell for generated specs
    type-checklists.md # Per-type checklist rows copied into a spec's Type Checklists table
    spec-lint.py       # Python 3 lint that must report zero findings before the spec review
  reviewing-prs/           # Multi-perspective PR review with mode detection
  testing-prs/             # Evidence-based functional testing
  debugging/     # Debug/dev/rework unified skill
  creating-stories/        # Interactive interview → PM story creation
  full-cycle/          # End-to-end lifecycle orchestrator (sequences all stages)
  addressing-pr-comments/ # Address review feedback in current session
  dev-verify/          # Developer dev deploy + verification gate on an existing PR (verify mode, deferred gates)
  toiling/             # Operations work with no branch or PR: agent-executed record or runbook under .scratch/, production never mutated
  pm-adapter/          # PM tool adapters + interface spec
  notes-adapter/       # Notes storage adapters + interface spec
  shared/              # Shared protocol docs (standards, adapter-loading, context-compaction, dev-deploy, ...)
agents/                # Dedicated subagent types dispatched by full-cycle (one per pipeline role)
  spec-writer.md      # writing-specs; role session (relay mode), fresh relay dispatch, or autonomous dispatch (`mode: autonomous`)
  developer.md        # developing (story mode) + addressing-pr-comments (rework mode); role session or fresh dispatch
  reviewer.md         # reviewing-prs; role session or fresh dispatch
  tester.md           # testing-prs; role session or fresh dispatch
  pr-state-reader.md  # entry/resume detection + PR-number resolution + authoritative decision read (read-only)
hooks/
  context-meter.sh     # PostToolUse: token usage meter — emits at 60%/75% of 200k baseline
  compact-injector.sh  # Stop: consumes .compact-request sentinel and injects /compact via tmux
  role-session-context.sh  # SessionStart (role sessions only): injects the absolute plugin root and standards path
  hooks.json           # Hook registration (CLAUDE_PLUGIN_ROOT-relative paths)
.claude-plugin/        # Plugin manifest (plugin.json)
```

Runtime state (not committed): `~/.claude/dev-workflow/state/`
- `{story-id}.json` — per-story checkpoint (stage, PR numbers, loop counts, next action, and an optional display-only story title)
- `.compact-request` — sentinel written by full-cycle at a high-context handoff (inside tmux only)
- `.compact-request.failed` — written by compact-injector on injection failure
- `context-meter-tier.txt` — last announced meter tier (prevents repeat emissions)

## Working on This Codebase

Content is mostly Markdown skill definitions, plus a small number of static assets (e.g., `skills/writing-specs/spec-template.html`) — there is no compiled code and no build step, apart from one standard-library Python lint (`skills/writing-specs/spec-lint.py`). The only tests are the shell tests under `tests/`. Run the matching test after each kind of edit:

- `bash tests/test_spec_completeness.sh` — after touching `skills/writing-specs/SKILL.md`, `skills/writing-specs/spec-template.html`, `skills/writing-specs/type-checklists.md`, `skills/shared/adversarial-review.md` or `agents/spec-writer.md`
- `bash tests/test_size_gate.sh` — after touching `skills/shared/size-gate.md`, writing-specs C1 or C10, the caps in `spec-lint.py`, creating-stories, or the creating-stories stage of full-cycle
- `bash tests/test_spec_lint.sh` — after touching `skills/writing-specs/spec-lint.py` or `tests/fixtures/spec-lint/`
- `bash tests/test_role_session_context.sh` — after touching a hook, `hooks/hooks.json` or the role agents
- `bash tests/test_developer_rules.sh` — after touching the worktree or attribution wording in the developing skill, repo-discovery or the developer agent
- `bash tests/test_dev_verification_gate.sh` — after touching the dev-verification gate wording in `skills/shared/dev-deploy.md`, the developing, addressing-pr-comments, debugging, reviewing-prs, testing-prs, dev-verify or full-cycle skills, `skills/shared/standards.md`, `skills/shared/role-sessions.md`, `skills/shared/context-compaction.md`, the README, or the agent definitions
- `bash tests/test_role_session_permission_mode.sh` — after touching the permission-mode, bypass opt-in or auto-mode warning wording in `skills/shared/role-sessions.md` or the README's Role Sessions section
- `bash tests/test_role_session_reuse.sh` — after touching the session lookup, Names, or Resume wording in `skills/shared/role-sessions.md`, or the scope boundary, Lazy launch, Role sessions on resume, or stage launch wording in `skills/full-cycle/SKILL.md`
- `bash tests/test_upstreamed_standards.sh` — after touching the Prod Boundary, Quality Gates, PR Description Updates, Review Standards Checklist, Playwright or Resource-Limit sections of `skills/shared/standards.md`, the story-state config in the README, the post-test transition in `skills/testing-prs/SKILL.md`, the PM Context in `skills/developing/SKILL.md`, or Step 1 of `skills/addressing-pr-comments/SKILL.md`
- `bash tests/test_local_mode.sh` — after touching `skills/shared/local-mode.md`, `skills/shared/pre-release-pin.md`, the Local Mode section of `skills/full-cycle/SKILL.md`, the `base-root:` dispatch rule in `skills/full-cycle/SKILL.md`, or the Local Mode and pin sections of `agents/developer.md` and `agents/pr-state-reader.md`
- `bash tests/test_agent_names.sh` and `bash tests/test_pr_story_resolution.sh` — after any version bump (each pins the exact plugin version)

Changes are made by editing `.md` files (and the occasional asset file) in `skills/` and `commands/`.

When modifying a skill:
- Update the version in `.claude-plugin/plugin.json` if changing behavior, **and** bump the matching entry's `version` in the repo-root `.claude-plugin/marketplace.json` to the same value, both in the same PR as the behavior change — the two drift independently and only the second one is what marketplace consumers actually see; also update the exact version pins in `tests/test_agent_names.sh` and `tests/test_pr_story_resolution.sh`
- Maintain phase numbering consistency within skills (phases are referenced by number in other skills and documentation)
- Preserve the adapter interface contracts in `interface.md` files — adapters must implement all required operations
- Test skill changes by invoking them with `/start <role>` in a target repository
