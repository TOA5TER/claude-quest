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
        remove_session, log_hub, clear_waiting_marker, clear_inflight_marker,
        is_subagent_event, clear_needs_user_marker, get_session_name, parse_story_name,
        story_in_progress, report_state, list_sessions, get_host_label,
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
    remove_session = attention_hub_client.remove_session
    log_hub = attention_hub_client.log_hub
    clear_waiting_marker = attention_hub_client.clear_waiting_marker
    clear_inflight_marker = attention_hub_client.clear_inflight_marker
    clear_needs_user_marker = attention_hub_client.clear_needs_user_marker
    get_session_name = attention_hub_client.get_session_name
    parse_story_name = attention_hub_client.parse_story_name
    story_in_progress = attention_hub_client.story_in_progress
    report_state = attention_hub_client.report_state
    list_sessions = attention_hub_client.list_sessions
    get_host_label = attention_hub_client.get_host_label

SUSPENDED_MESSAGE = "session ended; story still in progress"


def remove_finished_story_suspended(session_id, story_id):
    """Delete this host's other suspended rows of a finished story."""
    host = get_host_label()
    for row in list_sessions():
        if (row.get("session_id") != session_id
                and row.get("state") == "suspended"
                and row.get("host") == host
                and (parse_story_name(row.get("session_name")) or {}).get("story_id") == story_id):
            remove_session(row["session_id"])


def main():
    # SessionEnd: the session is gone. A story-named session of a story still in progress is
    # reported suspended; every other row is removed. Never block or error the session,
    # even when the hub is unreachable.
    try:
        input_data = json.load(sys.stdin)
        if is_subagent_event(input_data):
            sys.exit(0)
        session_id = input_data.get("session_id", "")
        if not session_id:
            sys.exit(0)

        # Drop every per-session marker so nothing leaks on disk.
        clear_waiting_marker(session_id)
        clear_inflight_marker(session_id)
        clear_needs_user_marker(session_id)

        session_name = get_session_name(input_data)
        parsed = parse_story_name(session_name)
        in_progress = story_in_progress(parsed["story_id"]) if parsed else None

        if in_progress:
            success = report_state(session_id, input_data.get("cwd", ""), "suspended",
                                   SUSPENDED_MESSAGE, session_name=session_name,
                                   transcript_path=input_data.get("transcript_path"))
            log_hub(f"SessionEnd -> suspended {session_id}: {'ok' if success else 'hub unreachable'}")
            sys.exit(0)

        success = remove_session(session_id)
        log_hub(f"SessionEnd -> remove {session_id}: {'ok' if success else 'hub unreachable'}")
        if parsed and in_progress is False:
            remove_finished_story_suspended(session_id, parsed["story_id"])
        sys.exit(0)
    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
