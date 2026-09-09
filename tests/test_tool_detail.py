import tool_detail


def test_classify_common_bash_families():
    assert tool_detail.classify_bash_command("git status") == "git"
    assert tool_detail.classify_bash_command("pytest -q") == "pytest"
    assert tool_detail.classify_bash_command("docker compose ps") == "docker"
    assert tool_detail.classify_bash_command("npm test") == "npm"


def test_classify_chained_command_uses_earliest_known_family():
    assert tool_detail.classify_bash_command("cd /tmp && git status && pytest") == "git"
    assert tool_detail.classify_bash_command("pytest && git status") == "pytest"


def test_classify_unknown_command_does_not_leak_command():
    assert tool_detail.classify_bash_command("my-secret-custom-command --token abc") == "other"


def test_extract_tool_detail_never_stores_raw_bash_command():
    blocks = [{
        "type": "tool_use",
        "name": "Bash",
        "input": {"command": "git push origin feature/sensitive-name"},
    }]
    result = tool_detail.extract_tool_detail(blocks)
    assert result == "Bash:git"
    assert "feature/sensitive-name" not in result


def test_extract_tool_detail_separates_exploration_tools():
    blocks = [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}},
        {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo"}},
        {"type": "tool_use", "name": "Glob", "input": {"pattern": "**/*.py"}},
    ]
    assert tool_detail.extract_tool_detail(blocks) == "Read,Grep,Glob"
