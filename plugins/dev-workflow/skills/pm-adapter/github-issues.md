# PM Adapter: GitHub Issues

Story ID format: `#XXX` or numeric `XXX`

## Fetch Story

```bash
gh issue view {number} --json title,body,comments,labels,state
```

Parse the JSON to extract title, description (body), and comments array.

## Post Comment

```bash
gh issue comment {number} --body "{text}"
```

## Update Story

```bash
# Add label
gh issue edit {number} --add-label "{label}"

# Set milestone
gh issue edit {number} --milestone "{milestone-name}"

# Close issue
gh issue close {number}
```

## Resolving the story from a PR

Resolve in this order:

1. **Link lookup:** the issues the PR closes, from GitHub's own linkage:
   ```bash
   gh pr view {pr-number} --json closingIssuesReferences --jq '.closingIssuesReferences[].number'
   ```
2. **Branch token:** the issue number directly after the branch prefix (for example `feature/123-add-login` resolves to `#123`), matched as a delimited unit per `skills/pm-adapter/interface.md`.

A repo that wants an issue closed automatically on merge keeps the closing keyword in its own pull request template, which the developer fills in.

## Finding PRs linked to a story

**Option 1 — `gh` CLI:**

```bash
gh pr list --state all --search "linked:{issue-number}"
```

**Option 2 — Issue timeline API (finds all cross-references):**

```bash
gh api repos/OWNER/REPO/issues/{issue-number}/timeline --paginate \
  --jq '.[] | select(.event == "cross-referenced") | select(.source.type == "pull_request") | .source.issue.number'
```

## Story reference in notes Adapter

Format: `#XXX` — use the hash-prefixed issue number (e.g., `#123`)

## Create Story

**⚠️ Gated operation:** subject to the Story Creation Gate in `skills/shared/standards.md` — never execute unless the gate is satisfied.

Use the `gh` CLI to create a new issue. Write the body to a temp file first to avoid shell interpolation issues with multi-line markdown:

```bash
# Write body to temp file
# Then create the issue using --body-file
gh issue create --title "{title}" --body-file .scratch/tmp/gh-issue-body.md
```

Write the body content to `.scratch/tmp/gh-issue-body.md` using the Write tool before running the command.

### Body format

Construct the body as:

```
## Original Request
{originalRequest}

---

## Story

{description}

**Repos to modify:** {reposToModify joined with ", "}

**Repos to reference:** {reposToReference joined with ", " or "(none)" if empty}

**Acceptance Criteria**
- [ ] {ac item 1}
- [ ] {ac item 2}
...

**Testing Instructions**
1. {step 1}
2. {step 2}
...

Note: For multi-repo stories, follow the Multi-repo story contract in `skills/pm-adapter/interface.md` (repo tags on AC/testing items; never create subtasks or sub-stories).
```

Return: the created issue number (e.g., `#42`) and URL for confirmation.
