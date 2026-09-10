import json
import sqlite3
import sys
from pathlib import Path

import db
from tool_detail import extract_tool_detail

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

    cache_creation_total = usage.get("cache_creation_input_tokens", 0)
    cache_creation_detail = usage.get("cache_creation")
    if cache_creation_detail:
        cache_creation_5m = cache_creation_detail.get("ephemeral_5m_input_tokens", 0)
        cache_creation_1h = cache_creation_detail.get("ephemeral_1h_input_tokens", 0)
    else:
        # Older transcripts predate the ephemeral_5m/1h split -- assume 5m
        # (the common case, and what the pricing code assumed before this field existed).
        cache_creation_5m = cache_creation_total
        cache_creation_1h = 0

    is_sidechain = obj.get("isSidechain") is True
    content_blocks = message.get("content") or []
    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": project,
        "cwd": cwd,
        "timestamp": obj.get("timestamp"),
        "model": message.get("model"),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_tokens": cache_creation_total,
        "cache_creation_5m_tokens": cache_creation_5m,
        "cache_creation_1h_tokens": cache_creation_1h,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "thinking_tokens": thinking,
        "tool_names": extract_tool_names(content_blocks),
        "tool_detail": extract_tool_detail(content_blocks),
        "inference_geo": usage.get("inference_geo"),
        "source_type": "subagent" if is_sidechain else "main",
        "parent_session_id": obj.get("sessionId") if is_sidechain else None,
        "agent_id": obj.get("agentId") if is_sidechain else None,
    }


def _derive_file_metadata(path, event):
    """Enrich source metadata from Claude Code's on-disk layout.

    Main transcripts live at <project>/<session>.jsonl. Subagent traces live at
    <project>/<session>/subagents/agent-<agentId>.jsonl. The path is useful as
    a fallback for older/incomplete records and, importantly, keeps the real
    project name instead of accidentally classifying the project as
    "subagents".
    """
    is_subagent_path = path.parent.name == "subagents"
    if is_subagent_path:
        event["project"] = path.parents[2].name
        event["source_type"] = "subagent"
        if not event.get("parent_session_id"):
            event["parent_session_id"] = path.parents[1].name
        if not event.get("agent_id"):
            stem = path.stem
            event["agent_id"] = stem[len("agent-"):] if stem.startswith("agent-") else stem
    else:
        event["project"] = path.parent.name
    return event


def ingest_file(conn, path):
    inserted = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                event = parse_line(line)
                if event is None or not event["uuid"]:
                    continue
                # `cwd` on a JSONL line drifts if the user `cd`s mid-session,
                # so use the transcript path for project/source attribution.
                event = _derive_file_metadata(path, event)
                cur = conn.execute(
                    """
                    INSERT INTO usage_events
                    (uuid, session_id, project, cwd, timestamp, model,
                     input_tokens, output_tokens, cache_creation_tokens,
                     cache_creation_5m_tokens, cache_creation_1h_tokens,
                     cache_read_tokens, thinking_tokens, tool_names, tool_detail,
                     inference_geo, source_type, parent_session_id, agent_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uuid) DO UPDATE SET
                        cache_creation_5m_tokens = CASE
                            WHEN usage_events.cache_creation_5m_tokens = 0
                             AND usage_events.cache_creation_1h_tokens = 0
                             AND usage_events.cache_creation_tokens > 0
                            THEN excluded.cache_creation_5m_tokens
                            ELSE usage_events.cache_creation_5m_tokens
                        END,
                        cache_creation_1h_tokens = CASE
                            WHEN usage_events.cache_creation_5m_tokens = 0
                             AND usage_events.cache_creation_1h_tokens = 0
                             AND usage_events.cache_creation_tokens > 0
                            THEN excluded.cache_creation_1h_tokens
                            ELSE usage_events.cache_creation_1h_tokens
                        END,
                        tool_detail = CASE
                            WHEN usage_events.tool_detail IS NULL OR usage_events.tool_detail = ''
                            THEN excluded.tool_detail
                            ELSE usage_events.tool_detail
                        END,
                        project = CASE
                            WHEN excluded.source_type = 'subagent' THEN excluded.project
                            ELSE usage_events.project
                        END,
                        source_type = CASE
                            WHEN excluded.source_type = 'subagent' THEN 'subagent'
                            ELSE usage_events.source_type
                        END,
                        parent_session_id = COALESCE(usage_events.parent_session_id, excluded.parent_session_id),
                        agent_id = COALESCE(usage_events.agent_id, excluded.agent_id)
                    WHERE (usage_events.cache_creation_5m_tokens = 0
                           AND usage_events.cache_creation_1h_tokens = 0
                           AND usage_events.cache_creation_tokens > 0)
                       OR ((usage_events.tool_detail IS NULL OR usage_events.tool_detail = '')
                           AND excluded.tool_detail != '')
                       OR (excluded.source_type = 'subagent'
                           AND (usage_events.source_type != 'subagent'
                                OR usage_events.parent_session_id IS NULL
                                OR usage_events.agent_id IS NULL
                                OR usage_events.project != excluded.project))
                    """,
                    (
                        event["uuid"], event["session_id"], event["project"],
                        event["cwd"], event["timestamp"], event["model"],
                        event["input_tokens"], event["output_tokens"],
                        event["cache_creation_tokens"],
                        event["cache_creation_5m_tokens"], event["cache_creation_1h_tokens"],
                        event["cache_read_tokens"],
                        event["thinking_tokens"], event["tool_names"],
                        event["tool_detail"], event["inference_geo"],
                        event["source_type"], event["parent_session_id"], event["agent_id"],
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
    print(f"Ingested {inserted} new or enriched events.")
    conn.close()


if __name__ == "__main__":
    main()
