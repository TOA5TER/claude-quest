# PM Adapter: Jira

Story ID format: variable prefix — full key required (e.g., `PROJ-123`, `ENG-456`).

## Session Start

At the beginning of each session using the Jira adapter, ask the user:

> "What is the Jira issue key for this session? (e.g., PROJ-123)"

Store the key for all subsequent PM operations in this session.

## Fetch Story

Use the `jira` CLI (Ankitpokhrel):

```bash
jira issue view {KEY} --plain
```

Parse from output:
- **Summary**: value on the `Summary:` labeled line
- **Description**: content under the `Description:` label
- **Issue type**: value on the `Type:` labeled line
- **Status**: value on the `Status:` labeled line
- **Acceptance Criteria**: look in a dedicated `Acceptance Criteria:` custom field, or as a structured section (heading or bullet list) within the description
- **Comments**: content under the `Comments:` section at the bottom

## Post Comment

```bash
jira issue comment add {KEY} --body "{text}"
```

## Update Story

Standard fields use named flags:
```bash
jira issue edit {KEY} --priority High
jira issue edit {KEY} --assignee username
```

Custom fields use `--custom`:
```bash
jira issue edit {KEY} --custom "story-points=5"
```

For status transitions:
```bash
jira issue move {KEY} "In Progress"
```

## Resolving the story from a PR

Jira has no lookup from a PR to its issue, so resolution uses the branch token alone: the delimited `PROJ-###` key in the head branch name (for example `feature/DEV-123-fix-login`, `DEV-123-fix-login`), per the delimited-token rule in `skills/pm-adapter/interface.md`. Including the key in the branch name also shows the PR in the issue's Development panel when the GitHub for Jira app is installed.

## Finding PRs linked to a story

**Option 1 — MCP (if `@aashari/mcp-server-atlassian-jira` is configured):**

Two-step process using the generic `jira_get` tool:

```
# Step 1: get the numeric issue ID
jira_get
  path: /rest/api/3/issue/{KEY}
  jq: .id
```

```
# Step 2: fetch linked pull requests
jira_get
  path: /rest/dev-status/1.0/issue/detail
  query-params: {"issueId": "{numeric-id}", "applicationType": "stash", "dataType": "pullrequest"}
```

Note: `applicationType` must be `"stash"` (legacy name used even for GitHub integrations).

**Option 2 — Jira REST API directly:**

```bash
# Step 1: get the numeric issue ID
ISSUE_ID=$(curl -su "user@example.com:$JIRA_API_TOKEN" \
  "https://your-domain.atlassian.net/rest/api/3/issue/{KEY}" \
  | jq -r '.id')

# Step 2: fetch linked pull requests
curl -su "user@example.com:$JIRA_API_TOKEN" \
  "https://your-domain.atlassian.net/rest/dev-status/1.0/issue/detail?issueId=$ISSUE_ID&applicationType=stash&dataType=pullrequest" \
  | jq '.detail[].pullRequests[]'
```

**Option 3 — Branch-name match:**
```bash
gh pr list --state all --limit 1000 --json number,url,state,headRefName
```
Keep PRs whose `headRefName` contains the delimited `{KEY}` token.

## Story reference in notes Adapter

Format: `{KEY}` — use the full issue key as-is (e.g., `PROJ-123`, `ENG-456`)

## Create Story

**⚠️ Gated operation:** subject to the Story Creation Gate in `skills/shared/standards.md` — never execute unless the gate is satisfied.

Use the `jira` CLI (Ankitpokhrel) to create a new issue. Write the body to a temp file first to avoid shell interpolation issues with multi-line markdown:

```bash
# Write body to .scratch/tmp/jira-issue-body.md using the Write tool first, then:
jira issue create --project {PROJECT_KEY} --summary "{title}" --body-file .scratch/tmp/jira-issue-body.md --type Story
```

**Note:** `PROJECT_KEY` is the Jira project prefix (e.g., `ENG`, `PROJ`). If not available in context, ask the user for the project key before creating the story.

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

Return: the created issue key (e.g., `PROJ-123`) and URL for confirmation.
