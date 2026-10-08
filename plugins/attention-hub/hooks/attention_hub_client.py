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
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HUB_URL = "http://localhost:8765"
HUB_TIMEOUT_SECONDS = 2
MESSAGE_SNIPPET_MAX = 200
SESSION_NAME_MAX = 256
ACTIVE_SUBAGENT_TTL_SECONDS = 2 * 60 * 60
BACKGROUND_SUBAGENT_TTL_SECONDS = 15 * 60
COMPLETED_RETENTION_SECONDS = 5 * 60
LABEL_MAX_CHARS = 256
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


def _waiting_marker_path(session_id):
    """Marker file path for a session, or None if the ID is unusable.

    The session ID becomes a filename, so reject anything empty, containing
    path separators, or that is a traversal component. Markers live in a
    dedicated subdirectory of ~/.claude/attention-hub/ so all of the plugin's
    transient state sits under one gitignorable path.
    """
    session_id = str(session_id or "").strip()
    if (not session_id or "/" in session_id or "\\" in session_id
            or "\x00" in session_id or session_id in (".", "..")):
        return None
    return Path.home() / ".claude" / "attention-hub" / "waiting-markers" / session_id


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


def _active_subagent_dir_path(session_id):
    """Per-session directory holding one marker file per active subagent.

    Reuses the same sanitization as _waiting_marker_path: the session ID
    becomes a directory name, so reject anything empty, containing path
    separators, null bytes, or a traversal component.
    """
    session_id = str(session_id or "").strip()
    if (not session_id or "/" in session_id or "\\" in session_id
            or "\x00" in session_id or session_id in (".", "..")):
        return None
    return Path.home() / ".claude" / "attention-hub" / "active-subagents" / session_id


def _read_marker(path):
    """Parse a marker file's JSON body, with legacy-empty-file fallback.

    Returns a dict with id/kind/label/status/started_at (and completed_at
    when present). Any unparsable or empty file is treated as a valid
    legacy record: kind="task", empty label, status="active", and the
    file's own mtime as started_at. Never raises.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            record = json.loads(text)
            if isinstance(record, dict) and record.get("kind") and record.get("status"):
                return record
    except Exception:
        pass
    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = time.time()
    return {
        "id": path.name,
        "kind": "task",
        "label": "",
        "status": "active",
        "started_at": mtime,
    }


def _write_marker(path, record):
    """Write a marker record as JSON. Never raises."""
    try:
        path.write_text(json.dumps(record), encoding="utf-8")
        return True
    except Exception:
        return False


def _create_marker(session_id, kind, label=None):
    """Create one active-status marker of the given kind for this session.

    Shared by mark_subagent_active (kind="task") and mark_background_active
    (any other kind). Creates the per-session directory if needed, creates
    a uuid4-named marker file via exclusive create so concurrent dispatches
    can never collide on a filename, and writes the JSON record via
    _write_marker -- propagating its actual success/failure rather than
    assuming success. Returns True only if the marker was both created and
    its JSON body written successfully. Never raises.
    """
    try:
        dir_path = _active_subagent_dir_path(session_id)
        if dir_path is None:
            return False
        dir_path.mkdir(parents=True, exist_ok=True)
        marker_id = uuid.uuid4().hex
        marker_path = dir_path / marker_id
        marker_path.touch(exist_ok=False)
        return _write_marker(marker_path, {
            "id": marker_id,
            "kind": kind,
            "label": str(label or "")[:LABEL_MAX_CHARS],
            "status": "active",
            "started_at": time.time(),
        })
    except Exception:
        return False


def mark_subagent_active(session_id, label=None):
    """Record one active subagent dispatch for this session.

    Creates a "task"-kind marker via _create_marker -- see its docstring
    for the full mechanism. "task" here is just this marker kind's internal
    label; it is unrelated to the real PreToolUse tool_name, which is
    "Agent" ("Task" is a legacy/former name for the same tool -- see
    attention_hub_pre_tool_use.py). Returns True on success, False if the
    session_id is invalid or the filesystem operation fails. Never raises.
    """
    return _create_marker(session_id, "task", label)


def mark_background_active(session_id, kind, label=None):
    """Record one active background-tool dispatch for this session.

    Same mechanism as mark_subagent_active via _create_marker, but for
    non-Agent tools that run in the background. Only "bash" (backgrounded
    Bash) is currently detected by attention_hub_pre_tool_use.py -- Workflow
    and Monitor detection was removed there since neither tool fires a
    PreToolUse hook in Claude Code's documented hook system, so tracking
    them via this function is not currently achievable (tracked as a
    follow-up gap, not implemented here). These kinds have no completion
    hook (see spec's Warning callout), so they are pruned only by
    BACKGROUND_SUBAGENT_TTL_SECONDS in list_active_work and never flip to
    status="completed". Returns True on success, False if session_id is
    invalid or the filesystem operation fails. Never raises.
    """
    return _create_marker(session_id, kind, label)


def list_active_work(session_id):
    """List surviving active-work records for this session, pruning stale ones.

    Active-status markers (any kind) prune by file mtime against the TTL for
    their kind (ACTIVE_SUBAGENT_TTL_SECONDS for "task",
    BACKGROUND_SUBAGENT_TTL_SECONDS for every other kind) -- unchanged from
    today's mtime-based mechanism, never the JSON started_at field, so
    existing mtime-manipulation tests keep passing. Completed-status markers
    (task kind only) prune by COMPLETED_RETENTION_SECONDS against their own
    completed_at field. Returns a list of surviving marker records. Never
    raises.
    """
    try:
        dir_path = _active_subagent_dir_path(session_id)
        if dir_path is None or not dir_path.is_dir():
            return []
        now = time.time()
        surviving = []
        for marker in dir_path.iterdir():
            try:
                if not marker.is_file():
                    continue
                record = _read_marker(marker)
                if record.get("status") == "completed":
                    completed_at = record.get("completed_at", now)
                    if now - completed_at > COMPLETED_RETENTION_SECONDS:
                        marker.unlink()
                        continue
                else:
                    ttl = (ACTIVE_SUBAGENT_TTL_SECONDS if record.get("kind") == "task"
                           else BACKGROUND_SUBAGENT_TTL_SECONDS)
                    age = now - marker.stat().st_mtime
                    if age > ttl:
                        marker.unlink()
                        continue
                surviving.append(record)
            except Exception:
                continue
        return surviving
    except Exception:
        return []


def count_active_subagents(session_id):
    """Count active-status markers for this session, across every kind,
    pruning stale ones via list_active_work. Returns 0 if none exist. Never
    raises. This is the mechanism that closes the false-done bug for
    Bash-background dispatches (the only background kind actually marked --
    Workflow/Monitor detection was confirmed unreachable via PreToolUse and
    removed, see attention_hub_pre_tool_use.py): it counts every marked
    kind, not just task, so notifications_stop.py's existing, unmodified
    `count_active_subagents(session_id) > 0` check naturally trips for them.
    """
    try:
        return sum(1 for record in list_active_work(session_id)
                    if record.get("status") == "active")
    except Exception:
        return 0


def clear_active_subagent(session_id):
    """Flip the oldest still-active task-kind marker to status="completed".

    Selects by file mtime ascending (FIFO) among markers whose kind is
    "task" and whose status is "active" -- today's harness gives
    SubagentStop no identifier for which subagent finished, so FIFO is the
    best available approximation (see spec's marker-attribution decision:
    correct whenever subagents complete in dispatch order, the common
    case). Sets completed_at to now instead of deleting the file, so a
    just-finished subagent still shows briefly as "completed" on the
    dashboard. Returns True if a marker was flipped, False if none existed.
    Never raises.
    """
    try:
        dir_path = _active_subagent_dir_path(session_id)
        if dir_path is None or not dir_path.is_dir():
            return False
        candidates = []
        for marker in dir_path.iterdir():
            if not marker.is_file():
                continue
            record = _read_marker(marker)
            if record.get("kind") == "task" and record.get("status") == "active":
                try:
                    mtime = marker.stat().st_mtime
                except Exception:
                    continue
                candidates.append((mtime, marker, record))
        if not candidates:
            return False
        candidates.sort(key=lambda c: c[0])
        _, marker, record = candidates[0]
        record["status"] = "completed"
        record["completed_at"] = time.time()
        _write_marker(marker, record)
        return True
    except Exception:
        return False


def clear_all_active_subagents(session_id):
    """Remove the entire active-subagent directory for this session at once.

    Used at session end so no marker files outlive their session. Returns
    True if the directory existed and was removed, False otherwise. Never
    raises.
    """
    try:
        dir_path = _active_subagent_dir_path(session_id)
        if dir_path is None or not dir_path.is_dir():
            return False
        shutil.rmtree(dir_path)
        return True
    except Exception:
        return False


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


def build_event_payload(session_id, cwd, state, message=None, session_name=None, active_work=None):
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
    if active_work:
        payload["active_work"] = active_work
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


def report_state(session_id, cwd, state, message=None, session_name=None, active_work=None):
    """POST a state event to the hub. Swallows every failure; returns success bool."""
    if not session_id:
        return False
    payload = build_event_payload(session_id, cwd, state, message, session_name, active_work)
    return _request(f"{get_hub_url()}/api/events", "POST", payload)


def remove_session(session_id):
    """Ask the hub to forget a session. Swallows every failure; returns success bool."""
    if not session_id:
        return False
    quoted_id = urllib.parse.quote(str(session_id), safe="")
    return _request(f"{get_hub_url()}/api/sessions/{quoted_id}", "DELETE")
