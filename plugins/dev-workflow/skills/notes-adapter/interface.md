# Notes Adapter Interface

A notes adapter tells you where to read/write implementation specs for stories.

## Configuration

Read `~/.claude/dev-workflow/config.json`. The `notes_adapter` field names which adapter to use.

## Required Capabilities

**1. Read spec** — given story ID and service name, return spec content (or "not found")

**2. Write spec** — given story ID, service name, and content, persist the spec

## Service name detection

```bash
basename "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
```

Run this to determine the current service name. Use it as part of the spec path.

## How to use

1. Read config to get `notes_adapter` value
2. Load adapter per procedure in `skills/shared/adapter-loading.md` (## Load Notes Adapter)
3. Follow adapter's instructions for all read/write spec operations

## User Adapters

Place a file at `~/.claude/skills/notes-adapter/{name}.md` to create a custom adapter. Set `notes_adapter: "{name}"` in config.

User adapters take precedence over plugin adapters with the same name. This allows you to override any built-in adapter or create one for an unsupported notes tool.

Your adapter must implement the same interface: Read spec, Write spec.
