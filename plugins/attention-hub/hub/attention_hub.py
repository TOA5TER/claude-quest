#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""
Attention hub — self-hosted session attention tracker.

A zero-dependency (stdlib only) HTTP server that receives per-session state
events from any reporting client and serves a web dashboard. Sessions whose
names start with a story id (e.g. sc-1000-developer) are grouped under that
story, one color-coded row per session; every other session is its own card.
Groups, rows and cards are ordered alphabetically, never by status.

Manual start (typically inside tmux/screen):

    uv run hub/attention_hub.py [--port 8765] [--bind 0.0.0.0]
                                [--state-file PATH] [--prune-hours 24]

Security: no authentication or TLS. Run it on a trusted private network
(localhost, LAN, VPN/tailnet) only — see README.
"""

import argparse
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8765
DEFAULT_BIND = "0.0.0.0"
DEFAULT_PRUNE_HOURS = 24.0
DEFAULT_STATE_FILE = str(Path.home() / ".claude" / "attention_hub_state.json")

# Lower value sorts first in the API's flat sessions list: red, yellow, green.
STATE_PRIORITY = {"waiting": 0, "needs_input": 0, "done": 1, "working": 2}
VALID_STATES = set(STATE_PRIORITY)

# Server-side input caps: the hub trusts no client to truncate for it.
MAX_BODY_BYTES = 64 * 1024
MESSAGE_MAX_CHARS = 200
FIELD_MAX_CHARS = 256

# Bounded per-session status-transition history (oldest entries drop first).
HISTORY_MAX = 20
HISTORY_SOURCES = {"hook", "manual"}
ACTIVE_WORK_MAX = 10


def _clamp(value, limit):
    """Coerce to str and truncate to limit characters."""
    return str(value)[:limit]

# Mirrored in hooks/attention_hub_client.py; keep both in step.
STORY_NAME_RE = re.compile(r"([A-Za-z]+-[0-9]+)-(.+)")
WAITING_STATES = {"waiting", "needs_input"}
_DIGIT_RUN_RE = re.compile(r"([0-9]+)")


def parse_story_name(name):
    """Story id, role, repo and row label for a grouped session name, or None."""
    if not isinstance(name, str):
        return None
    match = STORY_NAME_RE.fullmatch(name)
    if not match:
        return None
    story_id, remainder = match.groups()
    role, _, repo = remainder.partition("-")
    if role:
        label = role + (" · " + repo if repo else "")
    else:
        label = remainder
    return {"story_id": story_id, "role": role, "repo": repo, "label": label}


def _story_id(name):
    parsed = parse_story_name(name)
    return parsed["story_id"] if parsed else None


def _natural_key(text):
    """Case-insensitive key that compares digit runs by numeric value."""
    parts = _DIGIT_RUN_RE.split(text.lower())
    return [int(part) if i % 2 else part for i, part in enumerate(parts)]


def _ordering_key(text):
    return (_natural_key(text), text.lower(), text)


def build_groups(rows):
    """Story groups and the ordered top-level layout for a list of session rows."""
    members = {}
    top_level = []
    for row in rows:
        parsed = parse_story_name(row.get("session_name"))
        if parsed is None:
            key = row.get("session_name") or row["session_id"]
            top_level.append((_ordering_key(key), 1, row["session_id"],
                              {"type": "session", "session_id": row["session_id"]}))
            continue
        members.setdefault(parsed["story_id"], []).append((parsed, row))

    groups = []
    for story_id, entries in members.items():
        entries.sort(key=lambda e: (e[0]["role"] != "", _natural_key(e[0]["role"]),
                                    _natural_key(e[0]["repo"]), e[0]["label"],
                                    e[1]["session_id"]))
        rows_in_group = [row for _, row in entries]
        waiting = sum(row["state"] in WAITING_STATES for row in rows_in_group)
        titled = [row for row in rows_in_group if row.get("story_title")]
        titled.sort(key=lambda row: (-row["last_update"], row["session_id"]))
        title = titled[0]["story_title"] if titled else ""
        groups.append({
            "story_id": story_id,
            "title": f"{story_id}: {title}" if title else story_id,
            "color": "red" if waiting == len(rows_in_group) else "green",
            "session_ids": [row["session_id"] for row in rows_in_group],
            "labels": {row["session_id"]: parsed["label"] for parsed, row in entries},
            "waiting": waiting,
            "total": len(rows_in_group),
        })
        top_level.append((_ordering_key(story_id), 0, "",
                          {"type": "group", "story_id": story_id}))

    top_level.sort(key=lambda item: item[:3])
    layout = [item[3] for item in top_level]
    order = {item["story_id"]: i for i, item in enumerate(layout) if item["type"] == "group"}
    groups.sort(key=lambda g: order[g["story_id"]])
    return groups, layout


SESSION_PATH_RE = re.compile(r"^/api/sessions/([^/]+)$")
SESSION_STATE_PATH_RE = re.compile(r"^/api/sessions/([^/]+)/state$")


class AttentionStore:
    """In-memory per-session state, mirrored to a JSON file after each change."""

    def __init__(self, state_file, prune_hours=DEFAULT_PRUNE_HOURS, now=time.time):
        self._state_file = Path(state_file)
        self._prune_seconds = float(prune_hours) * 3600.0
        self._now = now
        self._lock = threading.Lock()
        self._sessions = {}
        self._load()

    def upsert(self, event):
        """Create or update a session from a state event. Returns the stored record."""
        session_id = _clamp(event.get("session_id") or "", FIELD_MAX_CHARS).strip()
        state = str(event.get("state") or "").strip()
        if not session_id:
            raise ValueError("event is missing session_id")
        if state not in VALID_STATES:
            raise ValueError(f"invalid state {state[:64]!r}")

        now = self._now()
        with self._lock:
            existing = self._sessions.get(session_id)
            history = list((existing or {}).get("history") or [])
            if existing is None or existing["state"] != state:
                history.append({"state": state, "entered_at": now, "source": "hook"})
            if "is_container" in event:
                is_container = bool(event.get("is_container"))
            else:
                is_container = bool((existing or {}).get("is_container", False))
            active_work = (self._sanitize_active_work(event.get("active_work"))
                           if "active_work" in event
                           else list((existing or {}).get("active_work") or []))
            session_name = _clamp(event.get("session_name")
                                  or (existing or {}).get("session_name")
                                  or "", FIELD_MAX_CHARS)
            record = {
                "session_id": session_id,
                "session_name": session_name,
                "story_title": self._next_story_title(existing, session_name, event),
                "project": _clamp(event.get("project") or (existing or {}).get("project")
                                  or "unknown", FIELD_MAX_CHARS),
                "host": _clamp(event.get("host") or (existing or {}).get("host")
                               or "unknown", FIELD_MAX_CHARS),
                "state": state,
                "message": _clamp(event.get("message") or "", MESSAGE_MAX_CHARS),
                "stage": _clamp(event.get("stage") or "", FIELD_MAX_CHARS),
                "state_since": existing["state_since"]
                if existing and existing["state"] == state else now,
                "last_update": now,
                "history": history[-HISTORY_MAX:],
                "is_container": is_container,
                "active_work": active_work,
            }
            self._sessions[session_id] = record
            self._save()
            return dict(record)

    @staticmethod
    def _next_story_title(existing, session_name, event):
        """Sticky per-session title, cleared whenever the session's story id changes."""
        story_id = _story_id(session_name)
        if story_id is None:
            return ""
        incoming = _clamp(str(event.get("story_title") or "").strip(), FIELD_MAX_CHARS)
        if existing is None or _story_id(existing.get("session_name")) != story_id:
            return incoming
        return incoming or existing.get("story_title") or ""

    def force_state(self, session_id, state):
        """Manually override a known session's state (dashboard force-status).

        Appends a history entry with source "manual" and persists. The next
        genuine hook event replaces the override like any other state change.
        Returns the updated record, or None when the session is unknown — a
        manual override never creates a session. The message is left unchanged.
        """
        if state not in VALID_STATES:
            raise ValueError(f"invalid state {str(state)[:64]!r}")
        now = self._now()
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            if record["state"] != state:
                record["state"] = state
                record["state_since"] = now
                history = list(record.get("history") or [])
                history.append({"state": state, "entered_at": now,
                                "source": "manual"})
                record["history"] = history[-HISTORY_MAX:]
            record["last_update"] = now
            self._save()
            return dict(record)

    def delete(self, session_id):
        """Remove a session. Returns True if it existed."""
        with self._lock:
            removed = self._sessions.pop(session_id, None) is not None
            if removed:
                self._save()
            return removed

    def list_sessions(self):
        """Prune stale sessions, then list all sessions with computed durations,
        sorted needs-attention first (red, yellow, green). This flat list is the
        API's; the dashboard renders the grouped layout from snapshot()."""
        now = self._now()
        with self._lock:
            self._prune_locked(now)
            return self._rows_locked(now)

    def snapshot(self):
        """The flat sessions list plus story groups and the ordered layout, all
        built from one pruned pass so they always agree."""
        now = self._now()
        with self._lock:
            self._prune_locked(now)
            rows = self._rows_locked(now)
        groups, layout = build_groups(rows)
        return {"sessions": rows, "groups": groups, "layout": layout}

    def _rows_locked(self, now):
        rows = []
        for record in self._sessions.values():
            row = dict(record)
            row["state_seconds"] = max(0.0, now - record["state_since"])
            row["age_seconds"] = max(0.0, now - record["last_update"])
            row["active_work"] = [self._with_duration(entry, now)
                                  for entry in record.get("active_work", [])]
            rows.append(row)
        rows.sort(key=lambda r: (STATE_PRIORITY.get(r["state"], 3), r["state_since"]))
        return rows

    @staticmethod
    def _with_duration(entry, now):
        """entry plus a computed duration_seconds: frozen (completed_at -
        started_at) once completed, live (now - started_at) while active."""
        item = dict(entry)
        if entry.get("status") == "completed" and "completed_at" in entry:
            item["duration_seconds"] = max(0.0, entry["completed_at"] - entry["started_at"])
        else:
            item["duration_seconds"] = max(0.0, now - entry["started_at"])
        return item

    def _prune_locked(self, now):
        stale = [sid for sid, record in self._sessions.items()
                 if now - record["last_update"] > self._prune_seconds]
        for sid in stale:
            del self._sessions[sid]
        if stale:
            self._save()

    def _save(self):
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_file.with_suffix(".tmp")
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"sessions": self._sessions}, f, indent=2)
            os.replace(tmp_path, self._state_file)
        except OSError as e:
            print(f"warning: could not persist state to {self._state_file}: {e}")

    def _load(self):
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions = data.get("sessions", {})
            if isinstance(sessions, dict):
                now = self._now()
                for sid, record in sessions.items():
                    if not (isinstance(record, dict) and record.get("session_id")):
                        continue
                    if record.get("state") not in VALID_STATES:
                        continue
                    for time_field in ("state_since", "last_update"):
                        if not isinstance(record.get(time_field), (int, float)):
                            record[time_field] = now
                    record["session_id"] = sid  # key wins over a hand-edited mismatch
                    record["session_name"] = _clamp(record.get("session_name") or "",
                                                    FIELD_MAX_CHARS)
                    title = record.get("story_title")
                    record["story_title"] = (
                        _clamp(title.strip(), FIELD_MAX_CHARS)
                        if isinstance(title, str) and _story_id(record["session_name"])
                        else "")
                    record["project"] = _clamp(record.get("project") or "unknown",
                                               FIELD_MAX_CHARS)
                    record["host"] = _clamp(record.get("host") or "unknown",
                                            FIELD_MAX_CHARS)
                    record["message"] = _clamp(record.get("message") or "",
                                               MESSAGE_MAX_CHARS)
                    record["stage"] = _clamp(record.get("stage") or "",
                                             FIELD_MAX_CHARS)
                    record["history"] = self._sanitize_history(record)
                    record["active_work"] = self._sanitize_active_work(record.get("active_work"))
                    record["is_container"] = bool(record.get("is_container", False))
                    self._sessions[sid] = record
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError, AttributeError) as e:
            print(f"warning: ignoring unreadable state file {self._state_file}: {e}")

    @staticmethod
    def _sanitize_history(record):
        """Well-formed history entries from a loaded record, capped at
        HISTORY_MAX. Legacy records (or records whose every entry is malformed)
        seed a one-entry history from the current state and state_since."""
        clean = []
        raw = record.get("history")
        if isinstance(raw, list):
            for entry in raw:
                if (isinstance(entry, dict)
                        and entry.get("state") in VALID_STATES
                        and isinstance(entry.get("entered_at"), (int, float))):
                    source = entry.get("source")
                    clean.append({
                        "state": entry["state"],
                        "entered_at": float(entry["entered_at"]),
                        "source": source if source in HISTORY_SOURCES else "hook",
                    })
        if not clean:
            clean = [{"state": record["state"],
                      "entered_at": float(record["state_since"]),
                      "source": "hook"}]
        return clean[-HISTORY_MAX:]

    @staticmethod
    def _sanitize_active_work(raw):
        """Well-formed active_work entries, capped at ACTIVE_WORK_MAX. Each
        entry's id/kind/label is clamped and status/started_at validated the
        same defensive way other record fields are; malformed entries drop."""
        clean = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                status = entry.get("status")
                started_at = entry.get("started_at")
                if status not in ("active", "completed") or not isinstance(started_at, (int, float)):
                    continue
                item = {
                    "id": _clamp(entry.get("id") or "", FIELD_MAX_CHARS),
                    "kind": _clamp(entry.get("kind") or "", FIELD_MAX_CHARS),
                    "label": _clamp(entry.get("label") or "", FIELD_MAX_CHARS),
                    "status": status,
                    "started_at": float(started_at),
                }
                if status == "completed" and isinstance(entry.get("completed_at"), (int, float)):
                    item["completed_at"] = float(entry["completed_at"])
                clean.append(item)
        return clean[:ACTIVE_WORK_MAX]


class AttentionHubHandler(BaseHTTPRequestHandler):
    # Keep-alive for the dashboard's 3s poll (every response sets Content-Length).
    protocol_version = "HTTP/1.1"

    @property
    def store(self):
        return self.server.store

    def log_message(self, format, *args):
        pass  # keep the terminal quiet; the dashboard is the surface

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/sessions":
            self._send_json(200, self.store.snapshot())
        else:
            self._send_json(404, {"error": "not found"})

    def _reject_unread_body(self, status, error):
        """Reject a request whose body will not be read. The connection must
        close: with keep-alive the unread body would corrupt the next request."""
        self.close_connection = True
        self._send_json(status, {"error": error})

    def _read_json_object_body(self):
        """Validate headers, read the body, and parse a JSON object.

        Returns the parsed dict, or None after an error response has already
        been sent (the same guards for every POST route: content type, length,
        size cap, unread-body close, object-shaped JSON)."""
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._reject_unread_body(415, "Content-Type must be application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            length = -1
        if length <= 0:
            self._reject_unread_body(400, "missing or invalid Content-Length")
            return None
        if length > MAX_BODY_BYTES:
            self._reject_unread_body(413, f"request body exceeds {MAX_BODY_BYTES} bytes")
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": str(e)})
            return None
        return body

    def do_POST(self):
        if self.path == "/api/events":
            self._handle_event_post()
            return
        match = SESSION_STATE_PATH_RE.match(self.path)
        if match:
            self._handle_force_state(urllib.parse.unquote(match.group(1)))
            return
        self._send_json(404, {"error": "not found"})

    def _handle_event_post(self):
        event = self._read_json_object_body()
        if event is None:
            return
        try:
            record = self.store.upsert(event)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"ok": True, "session": record})

    def _handle_force_state(self, session_id):
        """Manual state override: POST /api/sessions/{id}/state.

        200 with the updated record, 400 on a missing/invalid state or
        malformed body, 404 on an unknown session. Never creates a session."""
        body = self._read_json_object_body()
        if body is None:
            return
        try:
            record = self.store.force_state(session_id, body.get("state"))
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        if record is None:
            self._send_json(404, {"error": "unknown session"})
            return
        self._send_json(200, {"ok": True, "session": record})

    def do_DELETE(self):
        match = SESSION_PATH_RE.match(self.path)
        if not match:
            self._send_json(404, {"error": "not found"})
            return
        if self.store.delete(urllib.parse.unquote(match.group(1))):
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "unknown session"})


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Attention Hub</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background: #14171c; color: #e6e8eb;
         max-width: 64rem; margin: 1.5rem auto; padding: 0 1rem; }
  h1 { font-size: 1.2rem; font-weight: 600; }
  h1 small { color: #8b939e; font-weight: 400; margin-left: .6rem; }
  #sessions { display: flex; flex-direction: column; gap: .5rem; margin-top: 1rem; }
  .card { background: #1d2128; border-radius: 8px; border-left: 6px solid #555; }
  .card.red { border-left-color: #e5534b; }
  .card.yellow { border-left-color: #d4a72c; }
  .card.green { border-left-color: #46954a; }
  .row { display: flex; align-items: center; gap: .9rem; padding: .7rem .9rem;
         cursor: pointer; }
  .who { flex: 0 0 16rem; min-width: 0; }
  .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .stage { color: #8b939e; font-size: .85rem; white-space: pre-line; overflow: hidden;
           text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 3;
           -webkit-box-orient: vertical; }
  .state { min-width: 10rem; font-size: .9rem; }
  .red .state { color: #e5534b; }
  .yellow .state { color: #d4a72c; }
  .green .state { color: #46954a; }
  .snippet { flex: 1; color: #b4bac2; font-size: .85rem; overflow: hidden;
             text-overflow: ellipsis; white-space: nowrap; }
  .age { color: #8b939e; font-size: .8rem; white-space: nowrap; }
  button.dismiss { background: none; border: 1px solid #3a4048; color: #8b939e;
                   border-radius: 6px; padding: .15rem .55rem; cursor: pointer; }
  button.dismiss:hover { color: #e6e8eb; border-color: #8b939e; }
  .detail { border-top: 1px solid #2a2f37; padding: .7rem .9rem .9rem;
            font-size: .85rem; }
  .detail dl { display: grid; grid-template-columns: 7rem 1fr;
               gap: .25rem .8rem; margin: 0; }
  .detail dt { color: #8b939e; }
  .detail dd { margin: 0; overflow-wrap: anywhere; }
  .badge { display: inline-block; margin-left: .45rem; padding: 0 .45rem;
           border-radius: 999px; font-size: .7rem; font-weight: 700;
           vertical-align: middle; }
  .badge.container { background: #1f3a5f; color: #79b8ff; }
  .badge.manual { background: #4a3a10; color: #d4a72c; }
  .history { list-style: none; margin: 0; padding: 0; }
  .history li { padding: .1rem 0; color: #b4bac2; }
  .history .when { color: #8b939e; }
  .force { margin-top: .6rem; display: flex; gap: .4rem; align-items: center;
           flex-wrap: wrap; }
  .force span { color: #8b939e; }
  .force button { background: none; border: 1px solid #3a4048; color: #b4bac2;
                  border-radius: 6px; padding: .15rem .55rem; cursor: pointer; }
  .force button:hover:not(:disabled) { color: #e6e8eb; border-color: #8b939e; }
  .force button:disabled { opacity: .45; cursor: default; }
  .group { background: #171a20; border-radius: 8px; border-left: 6px solid #555;
           padding: .3rem .4rem .45rem; }
  .group.red { border-left-color: #e5534b; }
  .group.green { border-left-color: #46954a; }
  .group-head { display: flex; align-items: baseline; gap: .9rem; padding: .35rem .5rem .5rem; }
  .group-title { flex: 1; min-width: 0; font-weight: 700; overflow: hidden;
                 text-overflow: ellipsis; white-space: nowrap; }
  .group-summary { color: #8b939e; font-size: .85rem; white-space: nowrap; }
  .group.red .group-summary { color: #e5534b; }
  .members { display: flex; flex-direction: column; gap: .35rem; }
  @media (max-width: 40rem) {
    .row { flex-wrap: wrap; }
    .who { flex: 1 1 100%; }
    .snippet { flex: 1 1 100%; }
  }
  #empty { color: #8b939e; margin-top: 2rem; }
</style>
</head>
<body>
<h1>Attention Hub <small id="meta"></small></h1>
<div id="sessions"></div>
<p id="empty" hidden>No sessions reporting.</p>
<script>
const STATE_LABEL = {
  waiting: "waiting on you",
  needs_input: "needs your input",
  done: "done, awaiting review",
  working: "working",
};
const STATE_COLOR = { waiting: "red", needs_input: "red", done: "yellow", working: "green" };
const FORCE_STATES = ["working", "done", "needs_input", "waiting"];

// Expanded card IDs live OUTSIDE render(): the 3s poll re-render replaces all
// children, and re-applying this set is what keeps open cards open.
const expandedIds = new Set();

function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  return Math.floor(s / 3600) + "h" + Math.floor((s % 3600) / 60) + "m";
}

function fmtTime(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleString();
}

function dismiss(sessionId) {
  fetch("/api/sessions/" + encodeURIComponent(sessionId), { method: "DELETE" })
    .then(refresh).catch(() => {});
}

function forceState(sessionId, state) {
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: state }),
  }).then(refresh).catch(() => {});
}

function detailField(dl, label, value) {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value;
  dl.append(dt, dd);
  return dd;
}

function makeBadge(kind) {
  const badge = document.createElement("span");
  badge.className = "badge " + kind;
  badge.textContent = kind;
  return badge;
}

function buildHistoryList(s) {
  // Durations are derived, not stored: each entry runs until the next entry
  // starts; the current (last) entry uses the served state_seconds.
  const list = document.createElement("ul");
  list.className = "history";
  const entries = s.history || [];
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    const next = entries[i + 1];
    const seconds = next ? next.entered_at - entry.entered_at : s.state_seconds;
    const li = document.createElement("li");
    li.textContent = (STATE_LABEL[entry.state] || entry.state)
      + " for " + fmtDuration(seconds);
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = " — since " + fmtTime(entry.entered_at);
    li.append(when);
    if (entry.source === "manual") li.append(makeBadge("manual"));
    list.append(li);
  }
  return list;
}

function buildActiveWorkList(entries) {
  const list = document.createElement("ul");
  list.className = "history";
  for (const entry of entries) {
    const li = document.createElement("li");
    li.textContent = entry.kind + ": " + (entry.label || "(no label)")
      + " — " + entry.status + " " + fmtDuration(entry.duration_seconds);
    list.append(li);
  }
  return list;
}

function buildDetail(s) {
  const detail = document.createElement("div");
  detail.className = "detail";

  const dl = document.createElement("dl");
  const hostField = detailField(dl, "host", s.host);
  if (s.is_container) hostField.append(makeBadge("container"));
  detailField(dl, "project", s.project);
  detailField(dl, "session id", s.session_id);
  detailField(dl, "message", s.message || "—");
  detailField(dl, "last update", fmtDuration(s.age_seconds) + " ago");
  detailField(dl, "history", buildHistoryList(s));
  if (s.active_work && s.active_work.length) {
    detailField(dl, "active work", buildActiveWorkList(s.active_work));
  }
  detail.append(dl);

  const force = document.createElement("div");
  force.className = "force";
  const label = document.createElement("span");
  label.textContent = "force status:";
  force.append(label);
  for (const state of FORCE_STATES) {
    const btn = document.createElement("button");
    btn.textContent = state;
    btn.disabled = s.state === state;
    btn.title = "Manually set this session to " + state;
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      forceState(s.session_id, state);
    });
    force.append(btn);
  }
  detail.append(force);
  return detail;
}

function buildCard(s, label) {
  const card = document.createElement("div");
  card.className = "card " + (STATE_COLOR[s.state] || "");

  const row = document.createElement("div");
  row.className = "row";

  const who = document.createElement("div");
  who.className = "who";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = label || s.session_name || s.session_id;
  who.append(title);
  if (typeof s.stage === "string" && s.stage) {
    const stage = document.createElement("div");
    stage.className = "stage";
    stage.textContent = s.stage;
    stage.title = s.stage;
    who.append(stage);
  }

  const state = document.createElement("div");
  state.className = "state";
  state.textContent = (STATE_LABEL[s.state] || s.state) + " " + fmtDuration(s.state_seconds);

  const snippet = document.createElement("div");
  snippet.className = "snippet";
  if (s.state === "waiting" || s.state === "needs_input" || s.state === "done") {
    snippet.textContent = s.message || "";
    snippet.title = s.message || "";
  }

  const age = document.createElement("div");
  age.className = "age";
  age.textContent = "updated " + fmtDuration(s.age_seconds) + " ago";

  const btn = document.createElement("button");
  btn.className = "dismiss";
  btn.textContent = "dismiss";
  btn.title = "Remove this session from the hub";
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    dismiss(s.session_id);
  });

  row.append(who, state, snippet, age, btn);

  const detail = buildDetail(s);
  detail.hidden = !expandedIds.has(s.session_id);

  row.addEventListener("click", () => {
    if (expandedIds.has(s.session_id)) expandedIds.delete(s.session_id);
    else expandedIds.add(s.session_id);
    detail.hidden = !expandedIds.has(s.session_id);
  });

  card.append(row, detail);
  return card;
}

function buildGroup(g, members) {
  const group = document.createElement("div");
  group.className = "group " + (g.color || "");

  const header = document.createElement("div");
  header.className = "group-head";
  const head = document.createElement("div");
  head.className = "group-title";
  head.textContent = g.title;
  head.title = g.title;
  const summary = document.createElement("div");
  summary.className = "group-summary";
  summary.textContent = g.waiting + " of " + g.total + " waiting";
  header.append(head, summary);

  const rows = document.createElement("div");
  rows.className = "members";
  for (const s of members) {
    rows.append(buildCard(s, (g.labels || {})[s.session_id]));
  }
  group.append(header, rows);
  return group;
}

function render(data) {
  const sessions = data.sessions || [];
  const container = document.getElementById("sessions");
  container.replaceChildren();
  document.getElementById("empty").hidden = sessions.length > 0;
  document.getElementById("meta").textContent =
    sessions.length + " session" + (sessions.length === 1 ? "" : "s");
  const byId = new Map(sessions.map((s) => [s.session_id, s]));
  for (const id of expandedIds) {
    if (!byId.has(id)) expandedIds.delete(id);
  }
  const groups = new Map((data.groups || []).map((g) => [g.story_id, g]));
  for (const item of data.layout || []) {
    if (item.type === "group") {
      const g = groups.get(item.story_id);
      if (!g) continue;
      const members = (g.session_ids || []).map((id) => byId.get(id)).filter(Boolean);
      if (members.length) container.append(buildGroup(g, members));
    } else {
      const s = byId.get(item.session_id);
      if (s) container.append(buildCard(s));
    }
  }
}

function refresh() {
  fetch("/api/sessions")
    .then((r) => r.json())
    .then((data) => render(data))
    .catch(() => {});
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def create_server(bind, port, state_file, prune_hours):
    """Build a ThreadingHTTPServer wired to an AttentionStore."""
    server = ThreadingHTTPServer((bind, port), AttentionHubHandler)
    server.store = AttentionStore(state_file, prune_hours=prune_hours)
    return server


def main():
    parser = argparse.ArgumentParser(description="Attention hub")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--bind", default=DEFAULT_BIND,
                        help=f"address to bind (default {DEFAULT_BIND}; use 127.0.0.1 for localhost only)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE,
                        help=f"JSON state file path (default {DEFAULT_STATE_FILE})")
    parser.add_argument("--prune-hours", type=float, default=DEFAULT_PRUNE_HOURS,
                        help=f"drop sessions silent for this many hours (default {DEFAULT_PRUNE_HOURS:g})")
    args = parser.parse_args()

    server = create_server(args.bind, args.port, args.state_file, args.prune_hours)
    print(f"attention hub listening on http://{args.bind}:{args.port}"
          f" (state: {args.state_file}, prune: {args.prune_hours:g}h)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.server_close()


if __name__ == "__main__":
    main()
