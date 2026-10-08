# PM Adapter: Tasklist

A file-backed PM adapter for the `epic` orchestrator. Instead of an external project-management
tool (Shortcut, Jira, Linear, GitHub Issues), every "story" is a **task** recorded in a single
Markdown file the epic owns:

```
~/.claude/dev-workflow/epics/[epic-slug]/tasklist.md
```

This adapter implements the full PM adapter interface (`skills/pm-adapter/interface.md`) against that
file, so `full-cycle` and every stage skill (`writing-specs`, `developing`, `reviewing-prs`,
`testing-prs`, `addressing-pr-comments`) run **unchanged** when pinned to it. No Shortcut/Jira/Linear/GitHub
Issues call is ever made.

## Configuration

This adapter is **not selected via `config.json`**. The global config keeps `pm_adapter: "shortcut"`.
The `epic` orchestrator pins this adapter **per dispatch** by instructing each subagent, in its
prompt, to treat the PM adapter as `tasklist` and supplying two values explicitly:

- `tasklist_path` — absolute path to `~/.claude/dev-workflow/epics/[epic-slug]/tasklist.md`
- `task_id` — the stable ID of the task this run operates on (e.g. `task-3`)

Whenever this adapter says "the story", it means "the task with `task_id` in `tasklist_path`".
A skill that received these two values uses this file as its PM tool for the entire run; it never
reads `pm_adapter` from `config.json` and never contacts an external PM tool. The dispatch-prompt
instruction takes precedence over the configured adapter.

## Task ID and Status Vocabulary

**Task IDs** are stable, sequential, and never reused: `task-1`, `task-2`, `task-3`, …. Once
assigned, a task ID is permanent — renumbering would break branch names and PR links. Bug tasks
allocated mid-run continue the sequence (the next unused integer), regardless of position in the
graph.

**Status values** (the task state machine — both this adapter and the epic scheduler read and write
exactly these):

| Status | Meaning |
|--------|---------|
| `pending` | Created, not yet started. Dependencies may or may not be satisfied. |
| `in-progress` | A `full-cycle` run is actively driving this task (spec/development). |
| `in-review` | The task's PR is open and under code review. |
| `in-test` | The task's PR passed review and is under functional testing. |
| `awaiting-merge` | The PR is both review-approved and test-approved (dual approval) and is open, waiting for a **human** to merge it. The epic never merges; it pauses this task here. Not an error state. |
| `blocked` | Cannot proceed autonomously (loop-safety exhausted, un-mergeable, missing dependency, or an awaiting-merge PR was closed without merging). Carries a recorded reason. |
| `done` | PR merged by a human; task complete. Reached only when a human merge is detected on a later resume. Terminal. |

Legal transitions: `pending → in-progress → in-review → in-test → awaiting-merge → done`. The
`awaiting-merge → done` transition occurs **only** when a human merge is detected on a resume — the
epic never performs it inline during a run. Any non-terminal status may transition to `blocked`
(including `awaiting-merge`, when its PR is closed unmerged). A `blocked` task may return to
`pending` (or the status it held) when the blocker is cleared on a resume. `done` is terminal.

## Tasklist File Format

`tasklist.md` is the single source of truth for the epic — it holds the dependency graph, the
per-task descriptions, and the live status of every task. It has three parts in this exact order:

### 1. Metadata header

```markdown
# Epic: [epic-slug]

- **Summary:** {one-paragraph feature summary}
- **Created:** {YYYY-MM-DD}
- **Repos:** {comma-joined list of every repo this epic touches}
```

### 2. Mermaid dependency graph

A `flowchart` (or `graph`) ordering the tasks by dependency and grouping parallelizable tasks. Each
node is a task ID; edges are `dependency --> dependent`. The node **label embeds the current
status** so the graph is a live status board, and a CSS class colors the node by status.

```markdown
## Task Graph

​```mermaid
flowchart TD
  task-1["task-1: Scaffold API · done"]:::done
  task-2["task-2: Add auth endpoint · in-review"]:::inreview
  task-3["task-3: Web login form · pending"]:::pending
  task-1 --> task-2
  task-2 --> task-3
  classDef pending fill:#eee,stroke:#999;
  classDef inprogress fill:#cfe,stroke:#3a3;
  classDef inreview fill:#ccf,stroke:#33a;
  classDef intest fill:#ffe,stroke:#aa3;
  classDef awaitingmerge fill:#fef0d0,stroke:#d90;
  classDef blocked fill:#fcc,stroke:#a33;
  classDef done fill:#cfc,stroke:#3a3;
​```
```

Tasks in distinct repos with no dependency edge between them sit at the same depth and may run
concurrently. Tasks in the same repo are serialized by the scheduler even when the graph would
allow concurrency (see `epic` scheduler rules), because at most one PR per repo may be in flight.

### 3. One section per task

Each task is a level-2 section keyed by its ID. This is the full record — it embeds everything a
stage skill needs, so no external PM lookup is required.

```markdown
## task-2: Add auth endpoint

- **Status:** in-review
- **Repo:** api
- **Type:** feature
- **Depends on:** task-1
- **Branch:** [epic-slug]-task-2
- **PR:** https://github.com/owner/api/pull/87

### Description
{full description of the work}

### Acceptance Criteria
- [ ] {ac item 1}
- [ ] {ac item 2}

### Testing Instructions
1. {step 1}
2. {step 2}

### Comments
- {YYYY-MM-DDTHH:MMZ} {comment text}
```

The `PR` field is absent until `developing` opens the PR. The `Depends on` field lists
zero or more task IDs (comma-joined, or `none`). For a **bug task**, `Type` is `bug` and
`Depends on` includes the **source task** that the defect was attributed to.

---

## Required Capabilities

The capability names below match `skills/pm-adapter/interface.md` one-to-one. The "story ID" passed
to every operation is a **task ID** (`task-N`); the backing store is `tasklist_path`.

### 1. Fetch story

Given `task_id`, read `tasklist_path` and locate the `## {task_id}: …` section. Return:

- **title** — text after the colon in the section heading
- **description** — body of the `### Description` block
- **acceptance_criteria** — items under `### Acceptance Criteria`
- **testing_instructions** — items under `### Testing Instructions`
- **story_type** — the `**Type:**` field (`feature` / `bug` / `chore`)
- **workflow_state** — the `**Status:**` field (the task status vocabulary above)
- **repo** — the `**Repo:**` field
- **depends_on** — the `**Depends on:**` field
- **comments** — items under `### Comments`, most recent last
- **pr_link** — the `**PR:**` field, if present

If the task ID is not found, stop with: `Task {task_id} not found in {tasklist_path}.`

### 2. Post comment

Given `task_id` and text, append a bullet to that task's `### Comments` block:

```
- {YYYY-MM-DDTHH:MMZ} {text}
```

Create the `### Comments` block if absent. Use the Edit/Write tool to modify the file — never `echo`
or `cat` redirection (per `skills/shared/standards.md`).

### 3. Update story

Given `task_id` and field/value pairs, edit that task's section. Supported fields map to the section
metadata: `Status`, `Repo`, `Type`, `Depends on`, `Branch`, `PR`, and the `### Description` /
`### Acceptance Criteria` / `### Testing Instructions` blocks.

**When `Status` changes, also update the Mermaid graph** so it stays a live status board:

1. Rewrite the node label's trailing ` · {status}` segment to the new status.
2. Rewrite the node's `:::{class}` to the matching class (`done`, `awaitingmerge`, `inreview`,
   `intest`, `inprogress`, `blocked`, `pending`).

A status change that is not reflected in the Mermaid node is a defect — the graph and the section
must always agree.

#### Stage → status mapping

Stage skills update status as they progress. The expected transitions (the epic scheduler relies on
them):

| Stage event | Set Status to |
|-------------|---------------|
| `developing` begins | `in-progress` |
| PR opened | `in-review` |
| review approved, `testing-prs` begins | `in-test` |
| review **and** test both approved (dual approval) | `awaiting-merge` (PR left open for a human to merge — epic never merges) |
| human merge of an awaiting-merge PR detected on resume | `done` |
| task cannot proceed autonomously (incl. an awaiting-merge PR closed unmerged) | `blocked` (record reason as a comment) |

This adapter exposes `Status` as a writable field; it does **not** itself decide when to change it —
the stage skills and the epic orchestrator do, via this capability.

### 4. Resolving the story from a PR

The tasklist has no link lookup. Resolve by the **branch-name convention**:

```
[epic-slug]-[task-id]
```

e.g. `checkout-revamp-task-2`. Compare the full exact head branch against `[epic-slug]-[task-id]`, never a partial match, so `task-2` never resolves `task-20`. Do not add an external PM identifier or link.

### 5. Create story

**Story Creation Gate (per `skills/shared/standards.md`):** this operation runs only under explicit
authority. For this adapter, the authority is the **user's up-front consensus** in the `epic`
discovery gate — the user explicitly invoked `epic` and approved the full task set. The **orchestrator**
performs every Create call. A dispatched subagent must **never** create a task; if a subagent
discovers a needed task (e.g. a bug), it **reports** it and the orchestrator creates it. Surfacing
the report is not creation.

Given a task draft (title, description, story_type, repo, depends_on, acceptanceCriteria,
testingInstructions), the adapter:

1. Allocates the next unused task ID (`task-N`).
2. Appends a new `## task-N: {title}` section in the format above, with `Status: pending` and the
   `Branch:` field set to `[epic-slug]-task-N`.
3. Adds a node for `task-N` to the Mermaid graph (label `task-N: {title} · pending`, class
   `:::pending`) and an edge from each `depends_on` task to `task-N`.
4. Returns the task ID and the `tasklist_path` as its "URL".

**Multi-repo story contract** (per `skills/pm-adapter/interface.md`): each task targets exactly one
repo via its `Repo` field — the cross-repo spread of an epic lives in *separate tasks*, one per repo,
linked by dependency edges, not in a single multi-repo task. Because a task is single-repo, per-item
`[repo]` tags are unnecessary within a task. The adapter **never** creates subtasks or sub-stories;
all scope lives in the flat task list. `reposToModify` for any single task is therefore the one repo
named in `Repo`.

### 6. Finding PRs linked to a story

Given `task_id`, return the task's PR:

1. **Primary:** read the `**PR:**` field from the task's section (set when the PR was opened).
2. **Fallback (GitHub search by branch convention):** if the field is absent, search GitHub for the
   branch `[epic-slug]-[task-id]`:

   ```bash
   gh pr list --state all --head "[epic-slug]-[task-id]" --json number,url,state,reviewDecision
   ```

   Match by the exact head branch.

---

## State Lifecycle

Unlike the Shortcut adapter, there is no external workflow with named states — the task `Status`
field **is** the lifecycle. The stage → status mapping in capability 3 is the equivalent of the
Shortcut adapter's "Story Lifecycle & State Transitions" table. The epic orchestrator owns the
`awaiting-merge` and `done` transitions — on dual approval it sets `awaiting-merge` and leaves the
PR open for a human (it **never** merges), and on a later resume it sets `done` only when it detects
that a human merged the PR. Stage skills own the intermediate transitions for the task they are
driving.

## Error handling

- Task ID not found → stop with `Task {task_id} not found in {tasklist_path}.`
- `tasklist_path` missing or unreadable → stop with the path and the underlying error; do not
  fabricate an empty tasklist.
- Mermaid node missing for an existing task → stop and report the inconsistency; the file is
  corrupt and must be repaired before scheduling continues.
