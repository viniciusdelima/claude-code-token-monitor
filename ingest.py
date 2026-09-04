import json
import sqlite3
import sys
from pathlib import Path

import db

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def extract_tool_names(content_blocks):
    names = []
    for block in content_blocks:
        if block.get("type") == "tool_use" and block.get("name"):
            if block["name"] not in names:
                names.append(block["name"])
    return ",".join(names)


def parse_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "assistant":
        return None
    message = obj.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return None

    cwd = obj.get("cwd", "")
    project = Path(cwd).name if cwd else "unknown"
    thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)

    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": project,
        "cwd": cwd,
        "timestamp": obj.get("timestamp"),
        "model": message.get("model"),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "thinking_tokens": thinking,
        "tool_names": extract_tool_names(message.get("content") or []),
    }


def ingest_file(conn, path):
    inserted = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                event = parse_line(line)
                if event is None or not event["uuid"]:
                    continue
                # `cwd` on a JSONL line drifts if the user `cd`s mid-session,
                # so it's not a reliable source for `project`. The directory
                # actually containing this file is always correct -- that's
                # literally where Claude Code put it.
                event["project"] = path.parent.name
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO usage_events
                    (uuid, session_id, project, cwd, timestamp, model,
                     input_tokens, output_tokens, cache_creation_tokens,
                     cache_read_tokens, thinking_tokens, tool_names)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["uuid"], event["session_id"], event["project"],
                        event["cwd"], event["timestamp"], event["model"],
                        event["input_tokens"], event["output_tokens"],
                        event["cache_creation_tokens"], event["cache_read_tokens"],
                        event["thinking_tokens"], event["tool_names"],
                    ),
                )
                inserted += cur.rowcount
            except sqlite3.IntegrityError as exc:
                print(
                    f"ingest: skipping bad line in {path}: {exc}",
                    file=sys.stderr,
                )
                continue
            except Exception as exc:  # defensive: never let one bad line kill the run
                print(
                    f"ingest: skipping bad line in {path}: {exc}",
                    file=sys.stderr,
                )
                continue
    conn.commit()
    return inserted


def iter_jsonl_files(root):
    if not root.exists():
        return []
    return sorted(root.glob("**/*.jsonl"))


def ingest_all(conn, projects_root=DEFAULT_PROJECTS_ROOT):
    total = 0
    for path in iter_jsonl_files(projects_root):
        total += ingest_file(conn, path)
    return total


def main(argv=None):
    conn = db.get_connection()
    inserted = ingest_all(conn)
    print(f"Ingested {inserted} new events.")
    conn.close()


if __name__ == "__main__":
    main()
