import json
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
        "project": "myproject",
        "cwd": "/home/dev/myproject",
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
    proj_dir = projects_root / "myproject"
    proj_dir.mkdir(parents=True)
    (proj_dir / "session.jsonl").write_text(FIXTURE.read_text())

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_all(conn, projects_root=projects_root)

    assert inserted == 1
    conn.close()


def test_ingest_file_derives_project_from_path_not_cwd(tmp_path):
    # Claude Code names a session's project dir from the cwd the session was
    # *launched* from, but `cwd` on later JSONL lines follows the shell if
    # the user `cd`s mid-session. `project` must reflect the file's real
    # on-disk containing directory, never a drifted `cwd`.
    proj_dir = tmp_path / "actual-project-dir"
    proj_dir.mkdir()
    line = json.dumps(
        {
            "type": "assistant",
            "uuid": "uuid-drift",
            "sessionId": "sess-drift",
            "cwd": "/some/other/directory/that/does/not/match",
            "timestamp": "2026-09-04T20:35:00.000Z",
            "message": {
                "model": "claude-opus-4-7",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )
    session_path = proj_dir / "session.jsonl"
    session_path.write_text(line + "\n")

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_file(conn, session_path)

    assert inserted == 1
    row = conn.execute(
        "SELECT project FROM usage_events WHERE uuid = 'uuid-drift'"
    ).fetchone()
    assert row == ("actual-project-dir",)  # not "that-does-not-match" from cwd
    conn.close()


def test_ingest_file_skips_line_with_null_required_field(tmp_path):
    # A line that parses fine (has a uuid) but is missing "sessionId",
    # which is a NOT NULL column in the schema -> would raise
    # sqlite3.IntegrityError on INSERT if not caught.
    bad_line = json.dumps(
        {
            "type": "assistant",
            "uuid": "uuid-bad",
            # no "sessionId" at all -> parse_line yields session_id=None
            "cwd": "/home/dev/myproject",
            "timestamp": "2026-09-04T20:33:00.000Z",
            "message": {
                "model": "claude-opus-4-7",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )
    good_line = json.dumps(
        {
            "type": "assistant",
            "uuid": "uuid-good",
            "sessionId": "sess-1",
            "cwd": "/home/dev/myproject",
            "timestamp": "2026-09-04T20:34:00.000Z",
            "message": {
                "model": "claude-opus-4-7",
                "content": [],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )
    session_path = tmp_path / "mixed_session.jsonl"
    session_path.write_text(bad_line + "\n" + good_line + "\n")

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_file(conn, session_path)  # must not raise

    assert inserted == 1  # only the good line counted
    row = conn.execute("SELECT uuid FROM usage_events").fetchone()
    assert row == ("uuid-good",)
    conn.close()
