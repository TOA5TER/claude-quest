# PM Adapter: Shortcut

Story ID format: `sc-XXXXX` or numeric `XXXXX` (strip "sc-" prefix before MCP calls). Prefer `sc-XXXXX` any time `XXXXX` is not explicitly required.

## Fetch Story

MCP tool: `mcp__shortcut__stories-get-by-id` with numeric story ID

Returns: name, description, comments[], workflow_state, story_type

Also fetch comments with: `mcp__shortcut__stories-get-history` for full comment thread

## Post Comment

MCP tool: `mcp__shortcut__stories-create-comment` with story_id (numeric) and text

## Update Story

MCP tool: `mcp__shortcut__stories-update` with story_id (numeric) and fields object

## Resolving the story from a PR

Shortcut detects the story ID in branch names containing `sc-###` separated by `/` or `-` (for example `feature/sc-123-add-login`, `sc-123-add-login`). Recommended branch shapes: `username/sc-###/description` or `sc-###-description`. A repo that wants guaranteed branch-name linking can enforce the token through a branch-name ruleset.

Resolve in this order:

1. **Link lookup:** `mcp__shortcut__stories-get-by-external-link` with the PR URL returns the story the PR was attached to.
2. **Branch token:** the delimited `sc-###` token in the head branch name, per the delimited-token rule in `skills/pm-adapter/interface.md`.

**Attaching a PR to its story:** after the PR is created, add its URL with `mcp__shortcut__stories-add-external-link` (story ID and PR URL). `mcp__shortcut__stories-set-external-links` replaces the existing links, so use it only after reading them with `mcp__shortcut__stories-get-by-id`.

## Finding PRs linked to a story

**Option 1 — MCP (preferred if Shortcut MCP is configured):**

```
mcp__shortcut__stories-get-by-id
  storyPublicId: {numeric-id}
  full: true
```

The response includes a `pull_requests` array with PR data (title, URL, status, draft, merged) and a `branches` array with linked branches.

**Option 2 — Shortcut REST API:**

```bash
curl -H "Shortcut-Token: $SHORTCUT_API_TOKEN" \
  "https://api.app.shortcut.com/api/v3/stories/{numeric-id}" \
  | jq '.pull_requests[]'
```

**Option 3 — Branch-name match:**
```bash
gh pr list --state all --limit 1000 --json number,url,state,headRefName
```
Keep PRs whose `headRefName` contains the delimited `sc-{id}` token.

## Fetching Inline Image Attachments

Story descriptions and comments may embed image attachments hosted on Shortcut's media CDN
(`media.app.shortcut.com`). These URLs return 401 to unauthenticated requests and to generic
fetch tools (WebFetch, context-mode fetchers), but they accept the same `Shortcut-Token`
header used for the REST API above.

**Scope:** this method applies to `media.app.shortcut.com` URLs only — it is not a
general-purpose image fetcher. For images on any other host (e.g. Figma), this adapter has
no fetch method.

Download into the calling skill's own scratch directory (`./.scratch/tmp/`, never raw
`/tmp`), naming the local file from the URL's basename:

```bash
mkdir -p ./.scratch/tmp
curl -f -sS -H "Shortcut-Token: $SHORTCUT_API_TOKEN" \
  -o ./.scratch/tmp/{url-basename} "{media.app.shortcut.com URL}"
```

Verify the download actually succeeded before reading the file: a non-zero curl exit code
or a non-200 HTTP response counts as failure (`-f` makes curl exit non-zero on HTTP errors).
On success, use the Read tool on the downloaded file to view the image. On failure, treat
the image as inaccessible and follow the calling skill's documented fallback (e.g. STOP and
ask the user to describe it).

## Story reference in notes Adapter

Format: `sc-XXXXX`

## Create Story

**⚠️ Gated operation:** subject to the Story Creation Gate in `skills/shared/standards.md` — never execute unless the gate is satisfied.

MCP tool: `mcp__shortcut__stories-create` with fields:
- `name`: story title
- `description`: full markdown body (see body format below)
- `story_type`: infer from context ("feature" for new capabilities, "bug" for fixes, "chore" for maintenance)
- `workflow_state_id`: use default workflow — call `mcp__shortcut__workflows-get-default` to get the starting state ID

### Story body format

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
```

**Multi-repo stories:** follow the Multi-repo story contract in `skills/pm-adapter/interface.md` (repo tags on AC/testing items; never create subtasks or sub-stories).

Return: the created story's public ID (e.g., `sc-601`) and `app_url` for confirmation.
