#!/usr/bin/env python3
"""SessionStart / Stop hook: maintains .chat-history/log.md.

Usage: log_chat_history.py session_start | stop
Reads the hook JSON payload on stdin (per Claude Code hook contract).
"""
import json
import os
import sys
from datetime import datetime, timezone

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
LOG_DIR = os.path.join(PROJECT_DIR, ".chat-history")
LOG_PATH = os.path.join(LOG_DIR, "log.md")
STATE_PATH = os.path.join(LOG_DIR, ".state.json")
MAX_INJECT_CHARS = 20000
SUMMARY_MAX_CHARS = 600


def read_stdin_json():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def ensure_log_exists():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_PATH):
        with open(LOG_PATH, "w") as f:
            f.write("# Chat History Log\n\nAppend-only log of prompt/response exchanges for this project.\n")


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def esc(s):
    if s is None:
        return ""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    s = " ".join(s.split())
    return s


def cmd_session_start():
    ensure_log_exists()
    try:
        with open(LOG_PATH) as f:
            content = f.read()
    except Exception:
        content = ""
    if len(content) > MAX_INJECT_CHARS:
        content = "...(truncated, showing tail)...\n" + content[-MAX_INJECT_CHARS:]
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "Prior chat history for this project (.chat-history/log.md):\n\n" + content
            ),
        }
    }
    print(json.dumps(output))


def iter_transcript(transcript_path):
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def cmd_stop(payload):
    ensure_log_exists()
    transcript_path = payload.get("transcript_path")
    if not transcript_path or not os.path.exists(transcript_path):
        return

    entries = list(iter_transcript(transcript_path))

    last_user_idx = None
    last_user_text = None
    last_user_uuid = None
    for i, obj in enumerate(entries):
        if obj.get("type") != "user" or obj.get("isMeta"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            last_user_idx = i
            last_user_text = content.strip()
            last_user_uuid = obj.get("uuid")

    if last_user_idx is None:
        return

    state = load_state()
    if state.get("last_logged_uuid") == last_user_uuid:
        return

    summary_parts = []
    files = []
    last_timestamp = None
    for obj in entries[last_user_idx + 1 :]:
        if obj.get("type") != "assistant":
            continue
        last_timestamp = obj.get("timestamp") or last_timestamp
        msg = obj.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if text.strip():
                    summary_parts.append(text.strip())
            elif btype == "tool_use":
                name = block.get("name")
                tool_input = block.get("input") or {}
                if name in ("Write", "Edit", "MultiEdit"):
                    fp = tool_input.get("file_path")
                    if fp:
                        files.append(fp)
                elif name == "NotebookEdit":
                    fp = tool_input.get("notebook_path") or tool_input.get("file_path")
                    if fp:
                        files.append(fp)

    summary = " ".join(summary_parts).strip()
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "..."
    if not summary:
        summary = "(no text response — tool calls only)" if files else "(no text response)"

    files_affected = ", ".join(sorted(set(files))) if files else "none"

    timestamp = last_timestamp or datetime.now(timezone.utc).isoformat()

    entry = (
        "\n---\n"
        f'- timestamp: "{esc(timestamp)}"\n'
        f'- user_prompt: "{esc(last_user_text)}"\n'
        f'- assistant_response_summary: "{esc(summary)}"\n'
        f'- files_affected: "{esc(files_affected)}"\n'
    )

    with open(LOG_PATH, "a") as f:
        f.write(entry)

    state["last_logged_uuid"] = last_user_uuid
    save_state(state)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_stdin_json()
    if mode == "session_start":
        cmd_session_start()
    elif mode == "stop":
        cmd_stop(payload)


if __name__ == "__main__":
    main()
