#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///

import json
import sys
from pathlib import Path

try:
    from attention_hub_client import report_state, log_hub, get_session_name, set_waiting_marker, has_inflight_marker, \
        has_waiting_marker, transcript_recently_modified
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
    has_waiting_marker = attention_hub_client.has_waiting_marker
    transcript_recently_modified = attention_hub_client.transcript_recently_modified

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

# Notification types this hook handles. permission_prompt and elicitation_dialog mean the
# user is blocked and report waiting; idle_prompt has its own rule in main().
ACTIONABLE_NOTIFICATION_TYPES = {"permission_prompt", "idle_prompt", "elicitation_dialog"}


def main():
    # Notification: report "waiting" for permission and elicitation prompts; idle_prompt
    # never reports waiting (see below). No macOS/Slack here -- that stays in the
    # notifications plugin.
    try:
        input_data = json.load(sys.stdin)
        session_id = input_data.get("session_id", "")
        transcript_path = input_data.get("transcript_path", "")
        notification_type = input_data.get("notification_type", "")

        if notification_type not in ACTIONABLE_NOTIFICATION_TYPES:
            sys.exit(0)

        if not session_id:
            sys.exit(0)

        if notification_type == "idle_prompt":
            # idle_prompt only says the prompt has sat unanswered, so it never means
            # "blocked". Stop is the source of truth for done, needs_input and working;
            # an idle_prompt must not overwrite any of them with waiting.
            if has_inflight_marker(session_id):
                log_hub(f"Notification idle_prompt ignored for {session_id}: background work in flight")
                sys.exit(0)
            if has_waiting_marker(session_id) or has_ask_user_question(transcript_path):
                log_hub(f"Notification idle_prompt ignored for {session_id}: a wait or question is pending")
                sys.exit(0)
            # A late idle_prompt can land just after a new prompt was submitted; a turn
            # that is running keeps writing its transcript, so a recent write means the
            # card is genuinely working and must not be set to done.
            if transcript_recently_modified(transcript_path):
                log_hub(f"Notification idle_prompt ignored for {session_id}: transcript is still being written")
                sys.exit(0)
            # No Stop is sent when a turn is interrupted, which leaves the card working;
            # an idle prompt on a quiet transcript is the only sign it ended.
            message = extract_latest_message(transcript_path) or input_data.get("message", "")
            success = report_state(session_id, input_data.get("cwd", ""), "done", message,
                                   session_name=get_session_name(input_data),
                                   transcript_path=transcript_path)
            log_hub(f"Notification idle_prompt -> done for {session_id}: {'ok' if success else 'hub unreachable'}")
            sys.exit(0)

        message = extract_latest_message(transcript_path) or input_data.get("message", "")

        # Set the waiting marker even if the hub POST fails: intent matters,
        # and a later redundant "working" report is harmless.
        set_waiting_marker(session_id)

        success = report_state(session_id, input_data.get("cwd", ""), "waiting", message,
                               session_name=get_session_name(input_data),
                               transcript_path=input_data.get("transcript_path"))
        log_hub(f"Notification -> waiting for {session_id}: {'ok' if success else 'hub unreachable'}")
        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
