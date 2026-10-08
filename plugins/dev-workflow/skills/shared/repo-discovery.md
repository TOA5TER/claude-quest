# Repo Discovery

Shared procedure for determining which repo(s) a story or task operates on. `creating-stories`, `writing-specs`, `developing`, and `full-cycle` all use this — keep behavior identical across them.

## Two-path detection

Run `git rev-parse --show-toplevel` (via the Bash tool):

- **Path 1 — inside a repo:** output is a non-empty absolute path (e.g. `/workspace/my-service`). You are inside one git repo. Use that path as the single repo root; skip all sub-folder scanning. This is the single-repo path — behave exactly as today.
- **Path 2 — parent/workspace folder:** output contains "not a git repository" or is empty. You are not inside a git repo. Use Glob to find `{CWD}/*/.git`, `{CWD}/*/*/.git`, and `{CWD}/*/*/*/.git` (one, two, and three levels deep) — a matching repo checkout may sit in a nested subdirectory, not just directly under `{CWD}`. **Exclude any match under a `.worktrees/` path segment** — that is `using-git-worktrees`'s default placement convention for isolated dev-workflow worktrees (`skills/shared/standards.md` → "Workspace Isolation"), and a worktree's `.git` is a *file*, not a directory, that would otherwise match these globs and get misdetected as its own independent repo root. Each remaining matching folder (the parent of the `.git` entry) is a repo root. Each repo is a separate checkout with its own feature branch; discovery says nothing about where worktrees go. Worktrees go at `<repo root>/.worktrees/<slug>`, where the slug is the story or task ID, never in a sibling folder of the repo and never in the workspace parent folder.
- **Not found:** if no `.git` match turns up after searching all three levels, STOP and report to the user/orchestrator that no matching repo could be located on disk. Never clone a new copy, never improvise a location, and never proceed with a fabricated repo root.
  This prohibition governs resolving the repo(s) this procedure itself is trying to establish as the operating repo root(s) via two-path detection. The sole exceptions are `creating-stories/SKILL.md` Phase 0 step 3 (its Phase 3 deferred re-run included), which makes its own temporary, read-only, shallow clone of a *different*, explicitly-named repo purely for investigation, stored outside this procedure's own Path 2 glob search tree — that step states its scratch location's placement outside this glob explicitly; and that same file's Contract-Repo Detection subsection, which reuses that same step-3 procedure, at its own separate trigger point, to verify a candidate contract-repo name before it is added to `reposToModify`, under the identical scratch-location, cleanup, and validation bounds. No other caller of this procedure is authorized to make such a clone. Neither investigative clone is ever treated as a repo root by this procedure and must not be substituted as one.

## Service name

A repo's service name is identical to its folder name and repository name (e.g. repo at `/workspace/my-service` → service name `my-service`).

## Reconciling with the story's "Repos to modify" field

There are two repo-determination signals — on-disk discovery (above) and the story's **"Repos to modify"** field. Apply this precedence:

- **Path 1 (inside a single repo):** the field is informational. Operate on that one repo regardless of what the field lists. This preserves full single-repo backward compatibility.
- **Path 2 (parent folder), field present:** use only the listed repos (match by folder/service name).
- **Path 2 (parent folder), field absent or empty:** use all discovered repos.

## Per-item repo tags

Acceptance-criteria and testing-instruction items may carry a bracketed repo marker (e.g. `[api]`, `[web]`). Items tagged `[all]` or untagged apply to every repo. Skills filter scope per repo using these tags.

Items may also carry an environment tag (`[dev]`, `[prod]`) in the same bracket syntax, per `creating-stories/SKILL.md`'s "Multi-environment stories" subsection — this is independent of, and does not change, the repo-tag filtering behavior described above.

## Single-repo shortcut

When exactly one repo is in scope — inside a git repo, or the story names exactly one repo — skip any per-repo loop and use existing single-repo behavior unchanged.
