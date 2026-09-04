import json

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


def test_query_report_applies_data_residency_multiplier_per_row(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    conn.execute(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_read_tokens, thinking_tokens, tool_names, inference_geo)
        VALUES ('u1','s1','proj','/p','2026-09-01T10:00:00Z','claude-sonnet-5',
                1000000, 0, 0, 0, 0, '', 'us')
        """
    )
    conn.commit()

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_usd"] == round(2.00 * 1.1, 10)
    conn.close()


def test_query_report_does_not_dilute_rate_when_geo_mixes_in_same_bucket(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    conn.executemany(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_read_tokens, thinking_tokens, tool_names, inference_geo)
        VALUES (?, 's1', 'proj', '/p', '2026-09-01T10:00:00Z', 'claude-sonnet-5',
                1000000, 0, 0, 0, 0, '', ?)
        """,
        [("u1", "us"), ("u2", None)],
    )
    conn.commit()

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_usd"] == round(2.00 * 1.1, 10) + 2.00
    conn.close()


def test_extract_mcp_server_returns_native_for_no_tools():
    assert report.extract_mcp_server("") == "native"
    assert report.extract_mcp_server(None) == "native"


def test_extract_mcp_server_returns_native_for_builtin_tools_only():
    assert report.extract_mcp_server("Read,Bash") == "native"


def test_extract_mcp_server_extracts_single_server():
    assert report.extract_mcp_server("mcp__plugin_context-mode_context-mode__ctx_search") == "plugin_context-mode_context-mode"


def test_extract_mcp_server_mixes_native_tool_with_one_server():
    assert report.extract_mcp_server("Read,mcp__jira__getJiraIssue") == "jira"


def test_extract_mcp_server_returns_mixed_for_multiple_distinct_servers():
    assert report.extract_mcp_server("mcp__jira__search,mcp__bitbucket__listRepositories") == "mixed"


def test_query_mcp_server_report_groups_and_costs(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    conn.executemany(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_read_tokens, thinking_tokens, tool_names)
        VALUES (?, 's1', 'proj', '/p', '2026-09-01T10:00:00Z', 'claude-sonnet-5',
                ?, 0, 0, 0, 0, ?)
        """,
        [
            ("u1", 1_000_000, "mcp__jira__search"),
            ("u2", 500_000, "Read,Bash"),
        ],
    )
    conn.commit()

    rows = report.query_mcp_server_report(conn)

    buckets = {r["bucket"]: r for r in rows}
    assert buckets["jira"]["input_tokens"] == 1_000_000
    assert buckets["jira"]["cost_usd"] == 2.00
    assert buckets["native"]["input_tokens"] == 500_000
    conn.close()


def test_format_mcp_server_report_renders_rows():
    rows = [{
        "bucket": "jira", "total_tokens": 1_000_000, "cost_usd": 2.0,
        "cost_unknown": False,
    }]
    output = report.format_mcp_server_report(rows)
    assert "jira" in output
    assert "1000000" in output


def test_format_mcp_server_report_handles_empty_rows():
    assert report.format_mcp_server_report([]) == "Sem dados no período."


def test_last_used_prefs_round_trip(tmp_path):
    prefs_path = tmp_path / "last_used.json"
    assert report.load_last_used(prefs_path) == {}

    report.save_last_used({"period": "week", "group_by": "project"}, prefs_path)

    assert report.load_last_used(prefs_path) == {"period": "week", "group_by": "project"}


def test_load_last_used_returns_empty_dict_for_invalid_json(tmp_path):
    prefs_path = tmp_path / "last_used.json"
    prefs_path.write_text("{not valid")
    assert report.load_last_used(prefs_path) == {}


def test_resolve_period_group_by_falls_back_to_saved_prefs():
    prefs = {"period": "month", "group_by": "project", "since": "2026-08-01"}
    period, group_by, since = report.resolve_args(period=None, group_by=None, since=None, prefs=prefs)
    assert (period, group_by, since) == ("month", "project", "2026-08-01")


def test_resolve_period_group_by_prefers_explicit_flags_over_prefs():
    prefs = {"period": "month", "group_by": "project", "since": "2026-08-01"}
    period, group_by, since = report.resolve_args(period="day", group_by="session", since=None, prefs=prefs)
    assert (period, group_by, since) == ("day", "session", "2026-08-01")


def test_resolve_period_group_by_defaults_to_day_with_no_prefs():
    period, group_by, since = report.resolve_args(period=None, group_by=None, since=None, prefs={})
    assert (period, group_by, since) == ("day", "day", None)


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
    # No CLI default of "day" anymore -- None means "not explicitly passed",
    # so main() can fall back to saved prefs before finally defaulting to day.
    args = report.parse_args([])
    assert args.period is None
    assert args.group_by is None
    assert args.since is None
    assert args.mcp_servers is False


def test_parse_args_explicit_flags_are_not_none():
    args = report.parse_args(["--period", "week", "--group-by", "project"])
    assert args.period == "week"
    assert args.group_by == "project"
