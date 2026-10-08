#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///

import json
import sys
from pathlib import Path

try:
    from attention_hub_client import (
        report_state, log_hub, get_session_name, clear_waiting_marker,
        is_subagent_event,
    )
except ImportError:
    import importlib.util
    hub_spec = importlib.util.spec_from_file_location(
        "attention_hub_client",
        Path(__file__).parent / "attention_hub_client.py"
    )
    attention_hub_client = importlib.util.module_from_spec(hub_spec)
    hub_spec.loader.exec_module(attention_hub_client)
    is_subagent_event = attention_hub_client.is_subagent_event
    report_state = attention_hub_client.report_state
    log_hub = attention_hub_client.log_hub
    get_session_name = attention_hub_client.get_session_name
    clear_waiting_marker = attention_hub_client.clear_waiting_marker


def main():
    # PostToolUse: a tool can only complete after any pending permission was
    # granted, so a completing tool proves the session is no longer blocked.
    # This fires after EVERY tool call, so the waiting-marker check gates all
    # work: no marker -> exit instantly with zero network activity. The marker
    # is cleared BEFORE the hub POST so a hub outage costs one delayed attempt,
    # not one per subsequent tool call. Never block or error the session.
    try:
        input_data = json.load(sys.stdin)
        if is_subagent_event(input_data):
            sys.exit(0)
        session_id = input_data.get("session_id", "")
        if not session_id:
            sys.exit(0)

        if not clear_waiting_marker(session_id):
            sys.exit(0)

        success = report_state(session_id, input_data.get("cwd", ""), "working",
                               session_name=get_session_name(input_data))
        log_hub(f"PostToolUse -> working for {session_id}: {'ok' if success else 'hub unreachable'}")
        sys.exit(0)
    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
