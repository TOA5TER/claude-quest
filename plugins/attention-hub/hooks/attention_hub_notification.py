#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///

import json
import sys
from pathlib import Path

try:
    from attention_hub_client import report_state, log_hub, get_session_name, set_waiting_marker, has_inflight_marker
except ImportError:
    import importlib.util
    hub_spec = importlib.util.spec_from_file_location(
        "attention_hub_client",
        Path(__file__).parent / "attention_hub_client.py"
    )
    attention_hub_client = importlib.util.module_from_spec(hub_spec)
    hub_spec.loader.exec_module(attention_hub_client)
    report_state = attention_hub_client.report_state
    log_hub = attention_hub_client.log_hub
    get_session_name = attention_hub_client.get_session_name
    set_waiting_marker = attention_hub_client.set_waiting_marker
    has_inflight_marker = attention_hub_client.has_inflight_marker

try:
    from attention_hub_transcript import extract_latest_message
except ImportError:
    import importlib.util
    transcript_spec = importlib.util.spec_from_file_location(
        "attention_hub_transcript",
        Path(__file__).parent / "attention_hub_transcript.py"
    )
    attention_hub_transcript = importlib.util.module_from_spec(transcript_spec)
    transcript_spec.loader.exec_module(attention_hub_transcript)
    extract_latest_message = attention_hub_transcript.extract_latest_message

# Notification types that mean the agent is blocked and needs user action.
ACTIONABLE_NOTIFICATION_TYPES = {"permission_prompt", "idle_prompt", "elicitation_dialog"}


def main():
    # Notification: report "waiting" to the hub for actionable notification
    # types only. No macOS/Slack here -- that stays in the notifications plugin.
    try:
        input_data = json.load(sys.stdin)
        session_id = input_data.get("session_id", "")
        transcript_path = input_data.get("transcript_path", "")
        notification_type = input_data.get("notification_type", "")

        if notification_type not in ACTIONABLE_NOTIFICATION_TYPES:
            sys.exit(0)

        if not session_id:
            sys.exit(0)

        # Claude Code fires idle_prompt while a background shell is still running; the
        # user is not blocked then, so the card must stay working.
        if notification_type == "idle_prompt" and has_inflight_marker(session_id):
            log_hub(f"Notification idle_prompt ignored for {session_id}: background work in flight")
            sys.exit(0)

        message = extract_latest_message(transcript_path) or input_data.get("message", "")

        # Set the waiting marker even if the hub POST fails: intent matters,
        # and a later redundant "working" report is harmless.
        set_waiting_marker(session_id)

        success = report_state(session_id, input_data.get("cwd", ""), "waiting", message,
                               session_name=get_session_name(input_data))
        log_hub(f"Notification -> waiting for {session_id}: {'ok' if success else 'hub unreachable'}")
        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
