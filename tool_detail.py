import re


# Compact command-family labels only. Raw Bash commands are never persisted.
BASH_FAMILIES = (
    "git", "gh", "pytest", "phpunit", "composer", "npm", "npx", "pnpm",
    "yarn", "docker", "kubectl", "php", "python", "node", "make", "curl",
    "rg", "grep", "find", "cat", "ls",
)


def classify_bash_command(command):
    """Return a safe family label for a Bash command.

    If a Bash block chains multiple commands, the earliest recognized family
    wins so one inference stays in exactly one growth-attribution bucket.
    """
    if not command:
        return "other"

    best = None
    for family in BASH_FAMILIES:
        names = [family]
        if family == "python":
            names.append("python3")
        pattern = (
            r"(?:^|[\s;&|()/])("
            + "|".join(map(re.escape, names))
            + r")(?:\s|$)"
        )
        match = re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), family)
    return best[1] if best else "other"


def extract_tool_detail(content_blocks):
    """Return compact details for tools in an assistant inference.

    Bash stores Bash:<family>; all other tools store only their existing tool
    name. Details are deduplicated while preserving occurrence order.
    """
    details = []
    for block in content_blocks:
        if block.get("type") != "tool_use" or not block.get("name"):
            continue
        name = block["name"]
        if name == "Bash":
            command = (block.get("input") or {}).get("command", "")
            detail = f"Bash:{classify_bash_command(command)}"
        else:
            detail = name
        if detail not in details:
            details.append(detail)
    return ",".join(details)

