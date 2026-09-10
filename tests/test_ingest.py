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
        "cache_creation_5m_tokens": 36797,
        "cache_creation_1h_tokens": 0,
        "cache_read_tokens": 0,
        "thinking_tokens": 83,
        "tool_names": "Read",
        "tool_detail": "Read",
        "inference_geo": None,
        "source_type": "main",
        "parent_session_id": None,
        "agent_id": None,
    }


def test_parse_line_extracts_subagent_metadata():
    line = json.dumps({
        "type": "assistant", "uuid": "u-side", "sessionId": "parent-session",
        "agentId": "agent-123", "isSidechain": True,
        "cwd": "/home/dev/myproject", "timestamp": "2026-09-10T10:00:00Z",
        "message": {
            "model": "claude-sonnet-5", "content": [],
            "usage": {"input_tokens": 5, "output_tokens": 10,
                      "cache_read_input_tokens": 100},
        },
    })
    event = ingest.parse_line(line)
    assert event["source_type"] == "subagent"
    assert event["parent_session_id"] == "parent-session"
    assert event["agent_id"] == "agent-123"


def test_parse_line_extracts_inference_geo_when_present():
    line = json.dumps({
        "type": "assistant", "uuid": "u1", "sessionId": "s1",
        "cwd": "/home/dev/myproject", "timestamp": "2026-09-04T20:00:00Z",
        "message": {
            "model": "claude-sonnet-5", "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "inference_geo": "us"},
        },
    })
    event = ingest.parse_line(line)
    assert event["inference_geo"] == "us"


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

    assert inserted == 1
    row = conn.execute("SELECT uuid FROM usage_events").fetchone()
    assert row == ("uuid-1",)
    conn.close()


def test_ingest_file_is_idempotent(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    first = ingest.ingest_file(conn, FIXTURE)
    second = ingest.ingest_file(conn, FIXTURE)

    assert first == 1
    assert second == 0
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
    assert row == ("actual-project-dir",)
    conn.close()


def test_ingest_file_derives_subagent_source_from_nested_path(tmp_path):
    project_dir = tmp_path / "actual-project"
    subagents_dir = project_dir / "parent-session" / "subagents"
    subagents_dir.mkdir(parents=True)
    line = json.dumps({
        "type": "assistant", "uuid": "uuid-sub", "sessionId": "parent-session",
        "agentId": "abc123", "isSidechain": True,
        "cwd": "/home/dev/actual-project", "timestamp": "2026-09-10T10:00:00Z",
        "message": {
            "model": "claude-sonnet-5", "content": [],
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_read_input_tokens": 1000},
        },
    })
    path = subagents_dir / "agent-abc123.jsonl"
    path.write_text(line + "\n")

    conn = db.get_connection(tmp_path / "usage.db")
    assert ingest.ingest_file(conn, path) == 1
    row = conn.execute(
        "SELECT project, source_type, parent_session_id, agent_id "
        "FROM usage_events WHERE uuid = 'uuid-sub'"
    ).fetchone()
    assert row == ("actual-project", "subagent", "parent-session", "abc123")
    conn.close()


def test_ingest_file_backfills_existing_subagent_row(tmp_path):
    project_dir = tmp_path / "actual-project"
    subagents_dir = project_dir / "parent-session" / "subagents"
    subagents_dir.mkdir(parents=True)
    line = json.dumps({
        "type": "assistant", "uuid": "uuid-old", "sessionId": "parent-session",
        "agentId": "abc999", "isSidechain": True,
        "cwd": "/home/dev/actual-project", "timestamp": "2026-09-10T10:00:00Z",
        "message": {"model": "claude-sonnet-5", "content": [],
                    "usage": {"input_tokens": 10, "output_tokens": 20}},
    })
    path = subagents_dir / "agent-abc999.jsonl"
    path.write_text(line + "\n")

    conn = db.get_connection(tmp_path / "usage.db")
    conn.execute(
        """INSERT INTO usage_events
           (uuid, session_id, project, cwd, timestamp, model, input_tokens, output_tokens)
           VALUES ('uuid-old', 'parent-session', 'subagents', '/home/dev/actual-project',
                   '2026-09-10T10:00:00Z', 'claude-sonnet-5', 10, 20)"""
    )
    conn.commit()

    assert ingest.ingest_file(conn, path) == 1
    row = conn.execute(
        "SELECT project, source_type, parent_session_id, agent_id "
        "FROM usage_events WHERE uuid = 'uuid-old'"
    ).fetchone()
    assert row == ("actual-project", "subagent", "parent-session", "abc999")
    conn.close()


def test_ingest_file_persists_inference_geo(tmp_path):
    line = json.dumps({
        "type": "assistant", "uuid": "uuid-geo", "sessionId": "sess-geo",
        "cwd": "/home/dev/myproject", "timestamp": "2026-09-04T20:36:00.000Z",
        "message": {
            "model": "claude-sonnet-5", "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "inference_geo": "us"},
        },
    })
    session_path = tmp_path / "geo_session.jsonl"
    session_path.write_text(line + "\n")

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)
    ingest.ingest_file(conn, session_path)

    row = conn.execute(
        "SELECT inference_geo FROM usage_events WHERE uuid = 'uuid-geo'"
    ).fetchone()
    assert row == ("us",)
    conn.close()


def test_ingest_file_skips_line_with_null_required_field(tmp_path):
    bad_line = json.dumps(
        {
            "type": "assistant",
            "uuid": "uuid-bad",
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
    inserted = ingest.ingest_file(conn, session_path)

    assert inserted == 1
    row = conn.execute("SELECT uuid FROM usage_events").fetchone()
    assert row == ("uuid-good",)
    conn.close()
