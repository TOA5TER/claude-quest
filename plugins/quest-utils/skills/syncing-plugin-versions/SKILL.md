---
name: syncing-plugin-versions
description: Use when adding automated marketplace version notifications to a claude-quest plugin repo, or when a plugin repo needs to connect to the claude-quest automated version syncing system. Trigger whenever the user wants to set up tag-based version notifications, implement the notify-marketplace workflow, or enable automated marketplace updates after pushing a release tag. Also trigger if someone asks "how do I make claude-quest automatically update when I release?" or asks about connecting their plugin to the marketplace automation. Use this skill — don't just describe the steps.
---

# Syncing Plugin Versions

When a plugin repo pushes a git tag, this workflow notifies claude-quest to automatically open and merge a PR updating the plugin's pinned version in the marketplace. This skill implements the outbound half of that automation.

**Announce at start:** "I'm using the syncing-plugin-versions skill to set up automated marketplace notifications."

## Step 1: Identify the plugin name

The plugin name must exactly match the `name` field in `claude-quest/.claude-plugin/marketplace.json`.

Check `.claude-plugin/plugin.json` first:

```bash
jq -r '.name' .claude-plugin/plugin.json 2>/dev/null
```

If that returns nothing, look up the repo by its directory name:

| Repo | Plugin name |
|------|-------------|
| claude-plugin-audit-trail | `audit-trail` |
| claude-plugin-notifications | `notifications` |
| claude-plugin-dev-workflow | `dev-workflow` |
| claude-plugin-guardrails-git | `guardrails-git` |
| claude-plugin-guardrails-bash | `guardrails-bash` |
| claude-plugin-guardrails-docker | `guardrails-docker` |
| claude-plugin-guardrails-xcode | `guardrails-xcode` |

If the repo isn't in this table, ask the user — the name must be exact.

## Step 2: Check if already set up

```bash
cat .github/workflows/notify-marketplace.yml 2>/dev/null
```

If the file exists and contains `event_type: 'plugin-version-bump'`, the workflow is already configured. Verify the plugin name matches what you found in Step 1, then stop — no further changes needed.

## Step 3: Create the workflow

Create `.github/workflows/notify-marketplace.yml` with the plugin name from Step 1 substituted for `<PLUGIN-NAME>`:

```yaml
name: Notify Marketplace

on:
  push:
    tags: ['v*']

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Dispatch version bump to claude-quest
        uses: actions/github-script@v7
        with:
          github-token: ${{ secrets.MARKETPLACE_DISPATCH_TOKEN }}
          script: |
            await github.rest.repos.createDispatchEvent({
              owner: 'TOA5TER',
              repo: 'claude-quest',
              event_type: 'plugin-version-bump',
              client_payload: {
                plugin: '<PLUGIN-NAME>',
                version: context.ref.replace('refs/tags/v', '')
              }
            })
```

## Step 4: Commit

```bash
git add .github/workflows/notify-marketplace.yml
git commit -m "feat: add automated marketplace version notification"
```

## Step 5: Tell the user what's needed

After committing, surface the prerequisite they'll need to verify:

> The `MARKETPLACE_DISPATCH_TOKEN` secret must exist at your GitHub org level — it's a fine-grained PAT scoped to `TOA5TER/claude-quest` with `Actions: write` permission. If it's not already set up, ask the claude-quest maintainer.
>
> Once the secret is in place: push a tag like `v1.8.0` and claude-quest will automatically open and merge a PR updating your plugin's version within ~60 seconds.

## Troubleshooting

If the user reports problems after setup:

- **Nothing happens after pushing a tag** — `MARKETPLACE_DISPATCH_TOKEN` is missing or expired at the org level
- **claude-quest workflow fails with "Plugin not found"** — the `plugin` value in the workflow doesn't match the `name` in `claude-quest/.claude-plugin/marketplace.json`; fix the name and push a new tag
- **No PR opened, version already matches** — the marketplace already has this version; this is expected and correct
