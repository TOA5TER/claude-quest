# claude-quest

A marketplace of Claude Code plugins for software development teams.

## Plugins

| Plugin | Description |
|--------|-------------|
| [guardrails-git](https://github.com/TOA5TER/claude-plugin-guardrails-git) | Git/GitHub safety: branch protection, boilerplate blocking, timeout enforcement |
| [guardrails-bash](https://github.com/TOA5TER/claude-plugin-guardrails-bash) | Shell safety: blocks `rm -rf`, `/tmp`, `cat`/`echo` file writes, `cd` outside repo |
| [guardrails-docker](https://github.com/TOA5TER/claude-plugin-guardrails-docker) | Enforces timeout on `docker build` |
| [guardrails-xcode](https://github.com/TOA5TER/claude-plugin-guardrails-xcode) | Enforces timeout on `xcodebuild` |
| [notifications](https://github.com/TOA5TER/claude-plugin-notifications) | macOS notifications when Claude completes tasks or needs attention |
| [audit-trail](https://github.com/TOA5TER/claude-plugin-audit-trail) | Logs every tool call to `~/.claude/logs/` for debugging and auditing |
| [dev-workflow](https://github.com/TOA5TER/claude-plugin-dev-workflow) | Role-based development subagents with pluggable PM and notes adapters |

## Installation

```
/plugin marketplace add TOA5TER/claude-quest
/plugin install <plugin-name>@claude-quest
```
