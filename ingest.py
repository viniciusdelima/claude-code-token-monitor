import json
from pathlib import Path

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
