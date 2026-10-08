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
        remove_session, log_hub, clear_waiting_marker, clear_all_active_subagents,
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
    remove_session = attention_hub_client.remove_session
    log_hub = attention_hub_client.log_hub
    clear_waiting_marker = attention_hub_client.clear_waiting_marker
    clear_all_active_subagents = attention_hub_client.clear_all_active_subagents


def main():
    # SessionEnd: the session is gone; remove its row from the attention hub.
    # Never block or error the session, even when the hub is unreachable.
    try:
        input_data = json.load(sys.stdin)
        if is_subagent_event(input_data):
            sys.exit(0)
        session_id = input_data.get("session_id", "")
        if not session_id:
            sys.exit(0)

        # Session teardown: drop any waiting marker and active-subagent
        # markers so nothing leaks on disk.
        clear_waiting_marker(session_id)
        clear_all_active_subagents(session_id)

        success = remove_session(session_id)
        log_hub(f"SessionEnd -> remove {session_id}: {'ok' if success else 'hub unreachable'}")
        sys.exit(0)
    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
