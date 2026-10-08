#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///

import json
import sys
from pathlib import Path

try:
    from attention_hub_client import mark_subagent_active, mark_background_active, is_subagent_event
except ImportError:
    import importlib.util
    hub_spec = importlib.util.spec_from_file_location(
        "attention_hub_client",
        Path(__file__).parent / "attention_hub_client.py"
    )
    attention_hub_client = importlib.util.module_from_spec(hub_spec)
    hub_spec.loader.exec_module(attention_hub_client)
    is_subagent_event = attention_hub_client.is_subagent_event
    mark_subagent_active = attention_hub_client.mark_subagent_active
    mark_background_active = attention_hub_client.mark_background_active


def _bash_background_label(tool_input):
    description = str(tool_input.get("description") or "").strip()
    if description:
        return description
    return str(tool_input.get("command") or "").strip()[:100]


def main():
    # PreToolUse: pure observer, not a gate. Must always exit 0 and never
    # print a permission-decision payload -- if this hook ever blocked an
    # Agent dispatch, subagents would stop running entirely, which is strictly
    # worse than the "shows waiting/done while working" bug it fixes.
    try:
        input_data = json.load(sys.stdin)
        if is_subagent_event(input_data):
            sys.exit(0)
        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        session_id = input_data.get("session_id", "")

        if tool_name == "Agent":
            # Confirmed via Claude Code's official docs: "Agent" is the real
            # tool_name for subagent dispatch in a PreToolUse hook payload.
            # "Task" is a legacy/former name for the same tool and never
            # matches a real payload -- this was a pre-existing bug.
            label = str(tool_input.get("description") or "").strip()
            mark_subagent_active(session_id, label=label)
        elif tool_name == "Bash" and tool_input.get("run_in_background"):
            mark_background_active(session_id, "bash", _bash_background_label(tool_input))
        # Workflow/Monitor detection was removed here: confirmed those tools
        # do not fire PreToolUse hooks at all in Claude Code's documented
        # hook system (only Bash, Edit, Write, Read, Glob, Grep, Agent,
        # WebFetch, WebSearch, AskUserQuestion, ExitPlanMode, and MCP tools
        # do). Tracking background/persistent Workflow and Monitor dispatches
        # is a follow-up gap that needs a different mechanism, not achievable
        # via this hook.
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
