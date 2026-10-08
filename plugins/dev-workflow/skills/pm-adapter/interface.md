# PM Adapter Interface

A PM adapter tells you how to interact with the project management tool for this project.

## Configuration

Read `~/.claude/dev-workflow/config.json`. The `pm_adapter` field names which adapter to use.

## Required Capabilities

**1. Fetch story** — given a story ID, return: title, description, acceptance criteria, story type, comments

**2. Post comment** — given story ID and text, add a comment to the story

**3. Update story** — given story ID and field/value pairs, update the story

**4. Resolve story from PR** — given a PR (its URL and head branch name), return the story ID or "none". Never read the PR title or body. Try, in order:
1. The tool's own link lookup, where one exists (for example a story found by the PR URL attached to it).
2. A story token in the head branch name, using the adapter's token format. A token matches only as a delimited unit: preceded by the start of the name or a non-alphanumeric character, and followed by a non-digit or the end of the name, so `sc-12` never matches `sc-123`.

A branch name supplied by the caller (an epic task's branch, or a branch already checked out) always wins over the token default. Adapters that have no link lookup rely on the branch token alone.

**5. Create story** — given a story draft (title, description, story_type, reposToModify, reposToReference, acceptanceCriteria, testingInstructions, originalRequest), create a new story in the PM tool and return its ID and URL. `reposToModify` is a list of strings (one per repo/service). Adapters must follow the Multi-repo story contract below. This operation may only be executed under the Story Creation Gate in `skills/shared/standards.md` — adapters must not present Create Story as an available operation outside that gate.

## Multi-repo story contract

All Create Story adapters must obey this single contract — do not restate the policy verbatim in each adapter; reference this section.

- **Repos to modify:** render `reposToModify` as a comma-joined list (`**Repos to modify:** {reposToModify joined with ", "}`).
- **Per-item repo tags:** when a story spans multiple repos, prefix each Acceptance Criteria and Testing Instruction item with a bracketed repo tag matching the repo/folder name (e.g. `[api]`, `[web]`). Use `[all]` or leave untagged for items that apply across all repos. Single-repo stories may omit the tag. Items may also carry an environment tag (`[dev]`, `[prod]`) in the same bracket syntax, per `creating-stories/SKILL.md`'s "Multi-environment stories" subsection — this is independent of, and does not change, repo-tag filtering.
- **No subtasks:** the adapter must never create subtasks or sub-stories — all per-repo scope lives in the single story.

## How to use

1. Read config to get `pm_adapter` value
2. Load adapter per procedure in `skills/shared/adapter-loading.md` (## Load PM Adapter)
3. Follow adapter's instructions for all PM operations

## User Adapters

Place a file at `~/.claude/skills/pm-adapter/{name}.md` to create a custom adapter. Set `pm_adapter: "{name}"` in config.

User adapters take precedence over plugin adapters with the same name. This allows you to override any built-in adapter or create one for an unsupported PM tool.

Your adapter must implement the same interface: Fetch story, Post comment, Update story, Resolve story from PR, Create story. An adapter without Resolve story from PR is handled by the generic delimited branch-token match, taking the token format from the optional `adapters.<name>.story_id_prefix` key in `~/.claude/dev-workflow/config.json`; the match is skipped when the key is absent.
