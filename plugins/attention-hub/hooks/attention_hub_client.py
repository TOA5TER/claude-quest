#!/usr/bin/env python3
"""
Attention-hub reporting client.

Builds per-session state events and delivers them to the attention hub over
HTTP. Every network operation uses a short timeout and swallows all failures:
an unreachable hub must never block or error an agent session.
"""

import json
import os
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HUB_URL = "http://localhost:8765"
HUB_TIMEOUT_SECONDS = 2
MESSAGE_SNIPPET_MAX = 200
SESSION_NAME_MAX = 256
LABEL_MAX_CHARS = 256
IDENT_MAX_CHARS = 64
IDLE_TRANSCRIPT_QUIET_SECONDS = 30
CONTINUATION_MAX_IDS = 10
# The old process appends these bookkeeping records when it exits, after continued-in.
CONTINUATION_TRAILING_TYPES = {"last-prompt", "cost-state"}
CONTINUATION_TRAILING_LINES = 6
CONTINUATION_MAX_HOPS = 5
CONTINUATION_HEAD_BYTES = 256 * 1024
CONTINUATION_TAIL_BYTES = 8 * 1024
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# Mirrors ACTIVE_WORK_MAX in hub/attention_hub.py; keep both in step.
ACTIVE_WORK_SEND_MAX = 10
STAGE_TERMINAL_STAGES = frozenset({"done", "finished"})
STAGE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # checkpoints idle across nights/weekends/review waits are still active
# creating-stories' pre-story ".pending-*.json" placeholder has no story ID to key a real
# checkpoint by, so nothing prunes it except this cutoff and its own writer-side sweep —
# an abandoned interview would otherwise shadow real checkpoints for the full week above.
PENDING_PLACEHOLDER_MAX_AGE_SECONDS = 60 * 60

# Mirrors STORY_NAME_RE in hub/attention_hub.py; keep both in step.
STORY_NAME_RE = re.compile(r"([A-Za-z]+-[0-9]+)-(.+)")
# Same rule checkpoint seeding applies before a story id becomes a filename.
SAFE_STORY_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# Container-detection signals (module-level so tests can redirect them).
CONTAINER_MARKER_FILES = ("/.dockerenv", "/run/.containerenv")
CONTAINER_ENV_VARS = ("container", "KUBERNETES_SERVICE_HOST")
MOUNTINFO_PATH = "/proc/self/mountinfo"


def log_hub(message, log_file="attention_hub_client.log"):
    """Write a timestamped log message to ~/.claude/logs/{log_file}. Never raises."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path = Path.home() / ".claude" / "logs" / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def get_hub_url():
    """Hub base URL from CLAUDE_ATTENTION_HUB_URL, default http://localhost:8765."""
    url = os.environ.get("CLAUDE_ATTENTION_HUB_URL", "").strip() or DEFAULT_HUB_URL
    return url.rstrip("/")


def get_host_label():
    """Host label from CLAUDE_HOST_LABEL, falling back to the machine hostname."""
    label = os.environ.get("CLAUDE_HOST_LABEL", "").strip()
    if label:
        return label
    try:
        return socket.gethostname() or "unknown-host"
    except Exception:
        return "unknown-host"


def _root_is_overlayfs():
    """True when the root filesystem (/) is an overlayfs mount.

    Reads /proc/self/mountinfo, whose per-line format places the mount point
    at field 5 and the filesystem type right after the "-" separator. An
    overlay root is a strong container signal for environments (some dev
    containers) that expose no marker file or env variable. Never raises;
    missing/unreadable mountinfo (e.g. non-Linux) means "not detected".
    """
    try:
        with open(MOUNTINFO_PATH, "r", encoding="utf-8") as f:
            for line in f:
                fields = line.split()
                if len(fields) < 5 or fields[4] != "/":
                    continue
                try:
                    separator = fields.index("-")
                except ValueError:
                    continue
                if len(fields) > separator + 1:
                    return fields[separator + 1].startswith("overlay")
        return False
    except Exception:
        return False


def detect_container():
    """Best-effort detection of whether this session runs inside a container.

    Signals (any one suffices): /.dockerenv (Docker), /run/.containerenv
    (Podman), the `container` env var (Podman/systemd-nspawn),
    KUBERNETES_SERVICE_HOST (Kubernetes pod), or — as a final fallback — an
    overlayfs root per /proc/self/mountinfo. Cgroup-text scanning is not used:
    it is unreliable on cgroup v2. Never raises.
    """
    try:
        for marker in CONTAINER_MARKER_FILES:
            if os.path.exists(marker):
                return True
        for env_var in CONTAINER_ENV_VARS:
            if os.environ.get(env_var, "").strip():
                return True
        return _root_is_overlayfs()
    except Exception:
        return False


def is_subagent_event(input_data):
    """True when the hook fired inside a subagent rather than the session's own thread.

    Keys on agent_id alone: agent_type is also set on the main thread of any
    `--agent` session, and those sessions must keep reporting.
    """
    return bool(input_data.get("agent_id"))


def get_session_name(input_data):
    """Best-effort session name for the hook's session.

    Checks the hook input for a title field first, then falls back to scanning
    the transcript for the last {"type": "custom-title"} record — the line
    Claude Code appends on /rename. Returns "" for unnamed sessions. Never
    raises: a malformed transcript must not break a hook.
    """
    try:
        for key in ("session_title", "custom_title"):
            value = str(input_data.get(key) or "").strip()
            if value:
                return value[:SESSION_NAME_MAX]
        transcript_path = str(input_data.get("transcript_path") or "")
        if not transcript_path:
            return ""
        name = ""
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if '"custom-title"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("type") == "custom-title":
                    name = str(record.get("customTitle") or "").strip()
        return name[:SESSION_NAME_MAX]
    except Exception:
        return ""


def _marker_path(subdir, session_id):
    """Marker file path for a session, or None if the ID is unusable.

    The session ID becomes a filename, so reject anything empty, containing
    path separators, or that is a traversal component. Markers live in
    dedicated subdirectories of ~/.claude/attention-hub/ so all of the plugin's
    transient state sits under one gitignorable path.
    """
    session_id = str(session_id or "").strip()
    if (not session_id or "/" in session_id or "\\" in session_id
            or "\x00" in session_id or session_id in (".", "..")):
        return None
    return Path.home() / ".claude" / "attention-hub" / subdir / session_id


def _waiting_marker_path(session_id):
    return _marker_path("waiting-markers", session_id)


def _inflight_marker_path(session_id):
    return _marker_path("inflight-markers", session_id)


def set_inflight_marker(session_id):
    """Record that the session's last Stop listed background work. Never raises."""
    try:
        path = _inflight_marker_path(session_id)
        if path is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return True
    except Exception:
        return False


def has_inflight_marker(session_id):
    """True if the session's last Stop listed background work. Never raises."""
    try:
        path = _inflight_marker_path(session_id)
        return path is not None and path.is_file()
    except Exception:
        return False


def clear_inflight_marker(session_id):
    """Remove the in-flight marker; return True if it existed. Never raises."""
    try:
        path = _inflight_marker_path(session_id)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True
    except Exception:
        return False


def set_waiting_marker(session_id):
    """Record that the hub was told this session is waiting on the user.

    Returns True on success. Never raises — any filesystem error means the
    marker simply is not set and PostToolUse stays on its fast path.
    """
    try:
        path = _waiting_marker_path(session_id)
        if path is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return True
    except Exception:
        return False


def has_waiting_marker(session_id):
    """True if the session has a waiting marker. Never raises."""
    try:
        path = _waiting_marker_path(session_id)
        return path is not None and path.is_file()
    except Exception:
        return False


def clear_waiting_marker(session_id):
    """Remove the session's waiting marker; return True if it existed.

    Never raises — any filesystem error behaves as "no marker".
    """
    try:
        path = _waiting_marker_path(session_id)
        if path is None or not path.is_file():
            return False
        path.unlink()
        return True
    except Exception:
        return False


def _usable_id(entry):
    value = entry.get("id")
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) else None


def _clipped(value):
    return value[:LABEL_MAX_CHARS] if isinstance(value, str) else ""


def background_work_entries(background_tasks, session_crons):
    """Convert Stop's background_tasks and session_crons into active-work entries.

    Entries without a usable id are skipped; absent, non-list or malformed
    input yields no entries. Never raises.
    """
    entries = []
    try:
        for task in background_tasks if isinstance(background_tasks, list) else []:
            if not isinstance(task, dict) or _usable_id(task) is None:
                continue
            entries.append({
                "id": str(_usable_id(task))[:IDENT_MAX_CHARS],
                "kind": str(task.get("type") or "task")[:IDENT_MAX_CHARS],
                "label": _clipped(task.get("description")),
                "status": str(task.get("status") or "running")[:IDENT_MAX_CHARS],
            })
        for cron in session_crons if isinstance(session_crons, list) else []:
            if not isinstance(cron, dict) or _usable_id(cron) is None:
                continue
            entries.append({
                "id": str(_usable_id(cron))[:IDENT_MAX_CHARS],
                "kind": "cron",
                "label": _clipped(cron.get("prompt")),
                "status": "scheduled",
            })
    except Exception:
        pass
    return entries


def get_dev_workflow_stage(cwd):
    """Pipeline stage line for cwd's repo from dev-workflow checkpoint files.

    Every call stat()s the full checkpoint directory unconditionally — that
    sweep's cost scales with directory size, not age. Only the read/parse
    phase is bounded by STAGE_MAX_AGE_SECONDS, sorted newest-first so it
    stops at the first match; a stat() is cheap enough not to need an age
    guard, json.loads() is not. Returns "" when nothing matches. Never
    raises.
    """
    try:
        if not cwd:
            return ""
        path = os.path.normpath(cwd)
        parts = path.split(os.sep)
        candidates = [os.path.basename(path)]
        for i, segment in enumerate(parts[:-1]):
            if segment == ".worktrees" and i > 0 and parts[i - 1]:
                if parts[i - 1] not in candidates:
                    candidates.append(parts[i - 1])
        state_dir = Path.home() / ".claude" / "dev-workflow" / "state"
        now = time.time()
        cutoff = now - STAGE_MAX_AGE_SECONDS
        pending_cutoff = now - PENDING_PLACEHOLDER_MAX_AGE_SECONDS
        dated_paths = []
        for checkpoint_path in state_dir.glob("*.json"):
            try:
                mtime = checkpoint_path.stat().st_mtime
            except OSError:
                continue
            if checkpoint_path.name.startswith(".pending-"):
                if mtime < pending_cutoff:
                    continue
            elif mtime < cutoff:
                continue
            dated_paths.append((mtime, checkpoint_path))
        dated_paths.sort(key=lambda item: item[0], reverse=True)
        for _, checkpoint_path in dated_paths:
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if not isinstance(checkpoint, dict):
                    continue
                repos = checkpoint.get("repos")
                if not isinstance(repos, dict):
                    continue
                matched = False
                for candidate in candidates:
                    if candidate not in repos:
                        continue
                    entry = repos[candidate]
                    stage_value = entry.get("stage") if isinstance(entry, dict) else None
                    if not isinstance(stage_value, str) or stage_value in STAGE_TERMINAL_STAGES:
                        continue
                    matched = True
                    break
                if not matched:
                    continue
            except Exception:
                continue
            return "\n".join(
                f"{repo}:{entry['stage']}" for repo, entry in repos.items()
                if isinstance(entry, dict) and isinstance(entry.get("stage"), str)
            )
        return ""
    except Exception:
        return ""


def story_id_from_name(session_name):
    """Story id the hub groups this session name under, or "" when it never groups."""
    if not isinstance(session_name, str):
        return ""
    match = STORY_NAME_RE.fullmatch(session_name)
    return match.group(1) if match else ""


def get_story_title(story_id):
    """story_title from the story's dev-workflow checkpoint, or "".

    No age cutoff and no stage filter: the title is static story information.
    Never raises.
    """
    try:
        if not isinstance(story_id, str) or not SAFE_STORY_ID_RE.fullmatch(story_id):
            return ""
        path = Path.home() / ".claude" / "dev-workflow" / "state" / f"{story_id}.json"
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(checkpoint, dict):
            return ""
        title = checkpoint.get("story_title")
        return title.strip()[:SESSION_NAME_MAX] if isinstance(title, str) else ""
    except Exception:
        return ""


def _open_regular(path):
    """Open a regular file for reading without ever blocking, or raise.

    A FIFO would block a plain open() until a writer appears and a character
    device such as /dev/zero would read forever, either of which would hang or
    exhaust a hook. O_NONBLOCK makes the open return at once and fstat on the
    opened descriptor (after any symlink is followed) rejects anything that is
    not a regular file.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        return os.fdopen(fd, "rb")
    except Exception:
        os.close(fd)
        raise


def transcript_recently_modified(transcript_path, within_seconds=IDLE_TRANSCRIPT_QUIET_SECONDS):
    """True if the transcript regular file was written to within the last few seconds.

    A turn that is actively running keeps appending to its transcript, so a recent
    write means the session is not idle. A missing, unreadable or special file counts
    as not recent. Never raises, never blocks, reads no content.
    """
    try:
        if not transcript_path:
            return False
        with _open_regular(transcript_path) as f:
            return time.time() - os.fstat(f.fileno()).st_mtime < within_seconds
    except Exception:
        return False


def _continued_in(transcript_dir, old_id):
    """The session ID a transcript says it continued into, or None.

    Claude Code ends the old transcript with a continued-in record when it moves
    a running conversation to a new session ID. Best-effort: any read or parse
    problem means "no continuation".
    """
    try:
        path = transcript_dir / f"{old_id}.jsonl"
        with _open_regular(path) as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - CONTINUATION_TAIL_BYTES))
            lines = f.read(CONTINUATION_TAIL_BYTES).splitlines()
        record = None
        for line in reversed(lines[-CONTINUATION_TRAILING_LINES:]):
            record = json.loads(line)
            if not (isinstance(record, dict) and record.get("type") in CONTINUATION_TRAILING_TYPES):
                break
        if not isinstance(record, dict) or record.get("type") != "continued-in":
            return None
        target = record.get("continuedInSessionId")
        if isinstance(target, str) and SESSION_ID_RE.fullmatch(target):
            return target
    except Exception:
        pass
    return None


def continued_from(transcript_path, session_id):
    """IDs of earlier sessions this session continues, oldest first. Never raises.

    A continued conversation copies the old records into the new transcript with
    their original `session_id` kept, but the copy alone is not proof: a fork
    copies them too while the original keeps running. So each candidate ID counts
    only if its own transcript's last record is a continued-in link that leads,
    within a few hops, to this session.
    """
    try:
        if not transcript_path or not session_id:
            return []
        transcript = Path(transcript_path)
        with _open_regular(transcript) as f:
            head = f.read(CONTINUATION_HEAD_BYTES)
        candidates = []
        for line in head.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            old_id = record.get("session_id") if isinstance(record, dict) else None
            if (isinstance(old_id, str) and old_id != session_id
                    and SESSION_ID_RE.fullmatch(old_id) and old_id not in candidates):
                candidates.append(old_id)
        found = []
        for start in candidates:
            chain, current = [], start
            for _ in range(CONTINUATION_MAX_HOPS):
                chain.append(current)
                current = _continued_in(transcript.parent, current)
                if current is None or current in chain:
                    break
                if current == session_id:
                    found.extend(i for i in chain if i not in found)
                    break
        return found[:CONTINUATION_MAX_IDS]
    except Exception:
        return []


def build_event_payload(session_id, cwd, state, message=None, session_name=None, active_work=None,
                        supersedes=None):
    """Build the state-event payload identifying this session to the hub."""
    snippet = (message or "").strip()
    if len(snippet) > MESSAGE_SNIPPET_MAX:
        snippet = snippet[: MESSAGE_SNIPPET_MAX - 3] + "..."
    payload = {
        "session_id": session_id,
        "session_name": (session_name or "").strip()[:SESSION_NAME_MAX],
        "project": os.path.basename(os.path.normpath(cwd)) if cwd else "unknown",
        "cwd": os.path.normpath(cwd) if cwd else "",
        "host": get_host_label(),
        "state": state,
        "message": snippet,
        "stage": get_dev_workflow_stage(cwd),
        "is_container": detect_container(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    story_id = story_id_from_name(payload["session_name"])
    story_title = get_story_title(story_id) if story_id else ""
    if story_title:
        payload["story_title"] = story_title
    if active_work is not None:
        payload["active_work"] = active_work[:ACTIVE_WORK_SEND_MAX]
    if supersedes:
        payload["supersedes"] = list(supersedes)[:CONTINUATION_MAX_IDS]
    return payload


def _issue_request(url, method, body, timeout, result):
    """Worker body for _request. Stores True/False in result["ok"]. Never raises."""
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        response = urllib.request.urlopen(req, timeout=timeout)
        try:
            result["ok"] = 200 <= getattr(response, "status", 200) < 300
        finally:
            response.close()
    except urllib.error.HTTPError as e:
        log_hub(f"Hub rejected request ({method} {url}): HTTP {e.code}")
        result["ok"] = False
    except Exception as e:
        log_hub(f"Hub unreachable ({method} {url}): {e}")
        result["ok"] = False


def _request(url, method, body=None):
    """Issue an HTTP request to the hub with a bounded total wall clock.

    urlopen's timeout only covers socket operations — DNS resolution
    (getaddrinfo) runs before it applies, so a black-holed or unresolvable hub
    URL could stall a hook well past the budget. The request runs in a daemon
    thread joined for HUB_TIMEOUT_SECONDS, capping total time regardless of
    where the delay occurs. Returns True on 2xx, False otherwise. Never raises.
    """
    result = {}
    try:
        worker = threading.Thread(
            target=_issue_request,
            args=(url, method, body, HUB_TIMEOUT_SECONDS, result),
            daemon=True,
        )
        worker.start()
        worker.join(HUB_TIMEOUT_SECONDS)
    except Exception as e:
        log_hub(f"Hub request failed to dispatch ({method} {url}): {e}")
        return False
    if "ok" not in result:
        log_hub(f"Hub request exceeded {HUB_TIMEOUT_SECONDS}s budget ({method} {url})")
        return False
    return result["ok"]


def report_state(session_id, cwd, state, message=None, session_name=None, active_work=None,
                 transcript_path=None):
    """POST a state event to the hub. Swallows every failure; returns success bool."""
    if not session_id:
        return False
    payload = build_event_payload(session_id, cwd, state, message, session_name, active_work,
                                  supersedes=continued_from(transcript_path, session_id))
    return _request(f"{get_hub_url()}/api/events", "POST", payload)


def remove_session(session_id):
    """Ask the hub to forget a session. Swallows every failure; returns success bool."""
    if not session_id:
        return False
    quoted_id = urllib.parse.quote(str(session_id), safe="")
    return _request(f"{get_hub_url()}/api/sessions/{quoted_id}", "DELETE")
