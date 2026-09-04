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


import db


def test_ingest_file_inserts_only_usable_events(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_file(conn, FIXTURE)

    assert inserted == 1  # only the middle line has usage
    row = conn.execute("SELECT uuid FROM usage_events").fetchone()
    assert row == ("uuid-1",)
    conn.close()


def test_ingest_file_is_idempotent(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    first = ingest.ingest_file(conn, FIXTURE)
    second = ingest.ingest_file(conn, FIXTURE)

    assert first == 1
    assert second == 0  # same uuid, already present
    count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    assert count == 1
    conn.close()


def test_iter_jsonl_files_finds_nested_files(tmp_path):
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-a" / "session1.jsonl").write_text("")
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-b" / "session2.jsonl").write_text("")
    (tmp_path / "not-jsonl.txt").write_text("")

    found = ingest.iter_jsonl_files(tmp_path)

    assert {p.name for p in found} == {"session1.jsonl", "session2.jsonl"}


def test_ingest_all_sums_across_files(tmp_path):
    projects_root = tmp_path / "projects"
    proj_dir = projects_root / "wiseup-plus"
    proj_dir.mkdir(parents=True)
    (proj_dir / "session.jsonl").write_text(FIXTURE.read_text())

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_all(conn, projects_root=projects_root)

    assert inserted == 1
    conn.close()
