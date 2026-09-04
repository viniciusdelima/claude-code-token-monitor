import db
import report


def _seed(conn, rows):
    for row in rows:
        conn.execute(
            """
            INSERT INTO usage_events
            (uuid, session_id, project, cwd, timestamp, model,
             input_tokens, output_tokens, cache_creation_tokens,
             cache_read_tokens, thinking_tokens, tool_names)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
            """,
            row,
        )
    conn.commit()


def test_query_report_groups_by_day_and_sums_tokens(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5", 100, 50, 10, 5),
        ("u2", "s1", "proj", "/p", "2026-09-01T12:00:00Z", "claude-sonnet-5", 200, 100, 20, 10),
        ("u3", "s2", "proj", "/p", "2026-09-02T09:00:00Z", "claude-sonnet-5", 300, 150, 30, 15),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert [r["bucket"] for r in rows] == ["2026-09-01", "2026-09-02"]
    day1 = rows[0]
    assert day1["input_tokens"] == 300
    assert day1["output_tokens"] == 150
    assert day1["cache_creation_tokens"] == 30
    assert day1["cache_read_tokens"] == 15
    assert day1["total_tokens"] == 495
    assert day1["session_count"] == 1
    conn.close()


def test_query_report_computes_known_model_cost(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5",
         1_000_000, 0, 0, 0),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_usd"] == 2.00  # 1M input tokens @ $2/MTok
    assert rows[0]["cost_unknown"] is False
    conn.close()


def test_query_report_flags_unknown_model_cost(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "some-future-model",
         1000, 0, 0, 0),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_unknown"] is True
    conn.close()


def test_query_report_group_by_project(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj-a", "/a", "2026-09-01T10:00:00Z", "claude-sonnet-5", 100, 0, 0, 0),
        ("u2", "s2", "proj-b", "/b", "2026-09-01T10:00:00Z", "claude-sonnet-5", 200, 0, 0, 0),
    ])

    rows = report.query_report(conn, group_by="project")

    buckets = {r["bucket"]: r["input_tokens"] for r in rows}
    assert buckets == {"proj-a": 100, "proj-b": 200}
    conn.close()


def test_query_report_since_filters_older_rows(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-08-01T10:00:00Z", "claude-sonnet-5", 100, 0, 0, 0),
        ("u2", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5", 200, 0, 0, 0),
    ])

    rows = report.query_report(conn, since="2026-09-01")

    assert [r["bucket"] for r in rows] == ["2026-09-01"]
    conn.close()


def test_format_report_shows_nd_for_mixed_known_and_unknown_model_bucket(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5", 1_000_000, 0, 0, 0),
        ("u2", "s2", "proj", "/p", "2026-09-01T12:00:00Z", "some-future-model", 1000, 0, 0, 0),
    ])

    rows = report.query_report(conn, period="day", group_by="day")
    conn.close()

    assert rows[0]["cost_unknown"] is True
    assert rows[0]["cost_usd"] == 2.00  # partial cost from the known model only

    output = report.format_report(rows)

    assert "N/D" in output
    assert "2.0000" not in output


def test_format_report_handles_empty_rows():
    assert report.format_report([]) == "Sem dados no período."


def test_format_report_renders_a_row():
    rows = [{
        "bucket": "2026-09-01", "total_tokens": 165, "cost_usd": 2.5,
        "cost_unknown": False, "session_count": 1, "avg_context": 50.0,
        "peak_context": 60, "input_tokens": 100, "output_tokens": 50,
        "cache_creation_tokens": 10, "cache_read_tokens": 5,
    }]
    output = report.format_report(rows)
    assert "2026-09-01" in output
    assert "165" in output
    # per-category token columns (DESIGN.md §7)
    assert "Input" in output and "Output" in output
    assert "CacheEscrita" in output and "CacheLeitura" in output


def test_parse_args_defaults():
    args = report.parse_args([])
    assert args.period == "day"
    assert args.group_by == "day"
    assert args.since is None
