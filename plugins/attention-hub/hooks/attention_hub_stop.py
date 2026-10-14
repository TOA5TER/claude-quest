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
        background_work_entries, set_inflight_marker, clear_inflight_marker,
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
    background_work_entries = attention_hub_client.background_work_entries
    set_inflight_marker = attention_hub_client.set_inflight_marker
    clear_inflight_marker = attention_hub_client.clear_inflight_marker

try:
    from attention_hub_transcript import extract_latest_message, has_ask_user_question
except ImportError:
    import importlib.util
    transcript_spec = importlib.util.spec_from_file_location(
        "attention_hub_transcript",
        Path(__file__).parent / "attention_hub_transcript.py"
    )
    attention_hub_transcript = importlib.util.module_from_spec(transcript_spec)
    transcript_spec.loader.exec_module(attention_hub_transcript)
    extract_latest_message = attention_hub_transcript.extract_latest_message
    has_ask_user_question = attention_hub_transcript.has_ask_user_question


def main():
    # Stop: needs_input, else working while background_tasks or session_crons
    # hold an entry, else done. No macOS/Slack here -- that stays in notifications.
    try:
        input_data = json.load(sys.stdin)
        if is_subagent_event(input_data):
            sys.exit(0)
        session_id = input_data.get("session_id", "")
        transcript_path = input_data.get("transcript_path", "")

        if not session_id:
            sys.exit(0)

        # End of turn: consume any waiting marker. Covers the denied-permission
        # path (no tool ran) so the next turn cannot report stale "working".
        clear_waiting_marker(session_id)
        active_work = background_work_entries(
            input_data.get("background_tasks"), input_data.get("session_crons"))
        # Lets the Notification hook ignore idle_prompt while work is in flight.
        if active_work:
            set_inflight_marker(session_id)
        else:
            clear_inflight_marker(session_id)

        message = extract_latest_message(transcript_path)

        if has_ask_user_question(transcript_path):
            hub_state = "needs_input"
        elif active_work:
            hub_state = "working"
        else:
            hub_state = "done"

        success = report_state(session_id, input_data.get("cwd", ""), hub_state, message,
                               session_name=get_session_name(input_data),
                               transcript_path=input_data.get("transcript_path"),
                               active_work=active_work)
        log_hub(f"Stop -> {hub_state} for {session_id}: {'ok' if success else 'hub unreachable'}")
        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
