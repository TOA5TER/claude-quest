#!/usr/bin/env bash
# Tests that no PR carries a workflow-written story ID and that stories resolve from a link lookup or the branch name.
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELF="$PLUGIN_ROOT/tests/test_pr_story_resolution.sh"
ADAPTERS="$PLUGIN_ROOT/skills/pm-adapter"

FAILURES=0
fail() {
  echo "FAIL: $*" >&2
  FAILURES=$((FAILURES + 1))
}

normalise() {
  sed -e 's/^[[:space:]]*>[[:space:]]*//' -e 's/^[[:space:]]*//' -e 's/`//g' -e 's/\*\*//g' | tr '\n' ' ' | sed -e 's/  */ /g' | tr '[:upper:]' '[:lower:]'
}

whole() {
  normalise < "$1"
}

has() {
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

# region FILE START_REGEX : prints the normalised text from the matching heading to the next heading of the same or higher level.
region() {
  awk -v start="$2" '
    !on && $0 ~ start { on = 1; print; next }
    on && $0 ~ /^#{1,3} / { exit }
    on { print }
  ' "$1" | normalise
}

tree_files() {
  (cd "$PLUGIN_ROOT" && find . -type f \( -name '*.md' -o -name '*.json' -o -name '*.sh' \) -not -path './.worktrees/*' -not -path './.scratch/*' ! -samefile "$SELF" | sed "s|^\./|$PLUGIN_ROOT/|")
}

TREE_PHRASES=(
  "fallback reference (no auto-close)"
  "story reference in prs"
  "story id in prs"
  "story reference format"
  "fallback reference in pr body"
  "in the format your pm adapter expects"
  "parse the pr's body and title"
  "parse pr body for story reference"
  "also check pr title"
  "pr title containing"
  "pr description containing"
  "pr title alone"
  "story reference (pm adapter format)"
  "story reference in pm adapter format"
  "[sc-###]"
  "shortcut story: sc-"
  "linear issue: team"
  "jira issue: {key}"
  "fixes team-###"
  "closes #xxx in the pr description"
  "closes #n"
  "closes #{issue-number}"
  "closes:#{issue-number}"
  "--search \"{story_id}\""
  "--search \"sc-{id}\""
  "--search \"{key}\""
  "--search \"team-{id}\""
  "no sc- id"
  "carries no sc-"
  "sc-xxxxx in every pr"
  "epic prs have none"
)

ADAPTER_ONLY_PHRASES=(
  "pr body"
)

while IFS= read -r file; do
  text="$(whole "$file")"
  rel="${file#"$PLUGIN_ROOT"/}"
  for phrase in "${TREE_PHRASES[@]}"; do
    has "$text" "$phrase" && fail "$rel still contains: $phrase"
  done
  case "$file" in
    "$ADAPTERS"/*)
      for phrase in "${ADAPTER_ONLY_PHRASES[@]}"; do
        has "$text" "$phrase" && fail "$rel still contains: $phrase"
      done
      ;;
  esac
done < <(tree_files)

DEVELOPING="$PLUGIN_ROOT/skills/developing/SKILL.md"
DEBUGGING="$PLUGIN_ROOT/skills/debugging/SKILL.md"

pcr="$(region "$DEVELOPING" '^## PR Creation Requirements')"
[ -n "$pcr" ] || fail "developing: PR Creation Requirements section not found"
has "$pcr" "pull_request_template.md" || fail "developing: PR Creation Requirements does not name pull_request_template.md"
has "$pcr" "how to test" || fail "developing: PR Creation Requirements lacks the no-template fallback"

for skill in "$DEVELOPING" "$DEBUGGING"; do
  label="${skill#"$PLUGIN_ROOT"/skills/}"
  text="$(whole "$skill")"
  has "$text" "rules/branches/" || fail "$label: does not tell the developer to look up the repo's branch rules"
  has "$text" "story token" || fail "$label: does not name the story-token branch default"
  has "$text" "attach the pr to the story" || fail "$label: does not instruct the post-PR link attach"
done

has "$(whole "$ADAPTERS/interface.md")" "resolve story from pr" || fail "interface.md: Resolve story from PR capability missing"
for adapter in shortcut jira linear github-issues tasklist; do
  has "$(whole "$ADAPTERS/$adapter.md")" "resolving the story from a pr" || fail "$adapter.md: no Resolving the story from a PR section"
done

has "$(whole "$ADAPTERS/shortcut.md")" "stories-get-by-external-link" || fail "shortcut.md: by-URL lookup not documented"
has "$(whole "$ADAPTERS/shortcut.md")" "stories-add-external-link" || fail "shortcut.md: link attach not documented"

for consumer in reviewing-prs testing-prs addressing-pr-comments; do
  has "$(whole "$PLUGIN_ROOT/skills/$consumer/SKILL.md")" "resolve story from pr" || fail "$consumer: does not use Resolve story from PR"
done

PLUGIN_VERSION="$(jq -r '.version' "$PLUGIN_ROOT/.claude-plugin/plugin.json")"
[ "$PLUGIN_VERSION" = "2.51.0" ] || fail "plugin version $PLUGIN_VERSION is not 2.51.0"

if [ "$FAILURES" -eq 0 ]; then
  echo "PASS"
else
  echo "$FAILURES failure(s)" >&2
  exit 1
fi
