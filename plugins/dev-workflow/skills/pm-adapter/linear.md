# PM Adapter: Linear

Story ID format: `LIN-XXX` or team-prefixed `TEAM-XXX`

## Fetch Story

Use Linear MCP if available, otherwise use Linear GraphQL API with your API token.

Key fields to retrieve: title, description, comments, state, type, assignee

GraphQL query example:
```graphql
query Issue($id: String!) {
  issue(id: $id) {
    title
    description
    state { name }
    comments { nodes { body createdAt } }
  }
}
```

## Post Comment

Linear MCP comment tool, or GraphQL mutation:
```graphql
mutation CreateComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
```

## Update Story

Linear MCP update tool, or GraphQL mutation for state/label changes.

## Resolving the story from a PR

Linear has no lookup from a PR to its issue, so resolution uses the branch token alone: the delimited `TEAM-###` identifier in the head branch name (for example `username/ENG-123-fix-login`, `ENG-123-fix-login`), per the delimited-token rule in `skills/pm-adapter/interface.md`. Linear's auto-generated branch names (`username/TEAM-###-short-title`) carry the token.

## Finding PRs linked to a story

**MCP:** Linear's official MCP server (`mcp.linear.app/mcp`) does not expose a tool to list linked GitHub PRs. No MCP option available.

**Branch-name match (only option):**
```bash
gh pr list --state all --limit 1000 --json number,url,state,headRefName
```
Keep PRs whose `headRefName` contains the delimited `TEAM-{id}` token.

## Story reference in notes Adapter

Format: `LIN-XXX` (or team-prefixed `TEAM-XXX` — use the same format that was passed in)

## Create Story

**⚠️ Gated operation:** subject to the Story Creation Gate in `skills/shared/standards.md` — never execute unless the gate is satisfied.

Use the Linear MCP create tool if available, otherwise use the GraphQL `issueCreate` mutation:

```graphql
mutation CreateIssue($teamId: String!, $title: String!, $description: String!) {
  issueCreate(input: {
    teamId: $teamId
    title: $title
    description: $description
  }) {
    success
    issue {
      identifier
      url
    }
  }
}
```

**Note:** `teamId` is required by Linear. If not available in context, ask the user for their Linear team ID before creating the story.

### Description format

Construct the description as:

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

Return: the created issue identifier (e.g., `LIN-42`) and URL for confirmation.
