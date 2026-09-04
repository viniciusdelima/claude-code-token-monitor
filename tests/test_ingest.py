from pathlib import Path

import ingest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


def test_extract_tool_names_dedupes_and_joins():
    content = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "text", "text": "..."},
        {"type": "tool_use", "name": "Bash", "input": {}},
        {"type": "tool_use", "name": "Read", "input": {}},
    ]
    assert ingest.extract_tool_names(content) == "Read,Bash"


def test_extract_tool_names_empty_when_no_tool_use():
    assert ingest.extract_tool_names([{"type": "text", "text": "..."}]) == ""


def test_parse_line_extracts_assistant_with_usage():
    lines = FIXTURE.read_text().splitlines()
    event = ingest.parse_line(lines[1])

    assert event == {
        "uuid": "uuid-1",
        "session_id": "sess-1",
        "project": "wiseup-plus",
        "cwd": "/home/viniciusdelima/wiser/wiseup-plus",
        "timestamp": "2026-09-04T20:31:32.706Z",
        "model": "claude-opus-4-7",
        "input_tokens": 6,
        "output_tokens": 188,
        "cache_creation_tokens": 36797,
        "cache_read_tokens": 0,
        "thinking_tokens": 83,
        "tool_names": "Read",
    }


def test_parse_line_skips_non_assistant():
    lines = FIXTURE.read_text().splitlines()
    assert ingest.parse_line(lines[0]) is None


def test_parse_line_skips_assistant_without_usage():
    lines = FIXTURE.read_text().splitlines()
    assert ingest.parse_line(lines[2]) is None


def test_parse_line_skips_blank_line():
    assert ingest.parse_line("") is None
    assert ingest.parse_line("   \n") is None
