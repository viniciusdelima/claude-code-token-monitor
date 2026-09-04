import insights
import db


def _seed(conn, rows):
    for row in rows:
        conn.execute(
            """
            INSERT INTO usage_events
            (uuid, session_id, project, cwd, timestamp, model,
             input_tokens, output_tokens, cache_creation_tokens,
             cache_read_tokens, thinking_tokens, tool_names)
            VALUES (?, ?, ?, ?, ?, 'claude-sonnet-5', ?, ?, ?, ?, 0, ?)
            """,
            row,
        )
    conn.commit()


def test_daily_context_series_sums_per_day(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", 100, 0, 10, 5, "Read"),
        ("u2", "s1", "proj", "/p", "2026-09-01T12:00:00Z", 200, 0, 20, 10, "Bash"),
    ])

    series = insights.daily_context_series(conn, days=30)

    assert series == [("2026-09-01", 345.0)]  # (100+10+5)+(200+20+10)
    conn.close()


def test_top_context_events_orders_by_context_size_desc(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "wiseup-plus", "/home/v/wiser/wiseup-plus", "2026-09-04T10:00:00Z", 10, 0, 5, 0, "Read"),
        ("u2", "s2", "wiseup-plus", "/home/v/wiser/wiseup-plus", "2026-09-04T11:00:00Z", 100, 0, 50, 0, "Bash"),
    ])

    events = insights.top_context_events(conn, "2026-09-04", limit=5)

    assert len(events) == 2
    assert events[0]["session_id"] == "s2"  # 150 > 15
    assert events[0]["context_size"] == 150
    conn.close()


def test_dominant_reason_cache_creation_heavy():
    event = {"cache_creation_tokens": 100, "cache_read_tokens": 10}
    assert "contexto cresceu" in insights.dominant_reason(event)


def test_dominant_reason_cache_read_heavy():
    event = {"cache_creation_tokens": 10, "cache_read_tokens": 100}
    assert "acumulado" in insights.dominant_reason(event)


def test_jsonl_path_for_uses_project_field_not_cwd():
    # `project` (derived at ingest time from the file's real on-disk
    # directory) is the source of truth -- `cwd` can drift mid-session and
    # must not be used here, even if it disagrees with `project`.
    event = {
        "cwd": "/some/drifted/directory",
        "project": "wiseup-plus",
        "session_id": "abc",
    }
    path = insights.jsonl_path_for(event)
    assert path.endswith("/.claude/projects/wiseup-plus/abc.jsonl")


def test_build_insight_report_returns_none_without_anomaly(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 100, 0, 0, 0, ""),
    ])

    assert insights.build_insight_report(conn) is None
    conn.close()


def test_build_insight_report_includes_evidence_on_anomaly(tmp_path, monkeypatch):
    conn = db.get_connection(tmp_path / "usage.db")
    rows = []
    for day in range(1, 8):
        rows.append((f"u{day}", f"s{day}", "proj", "/p", f"2026-08-0{day}T10:00:00Z", 100, 0, 0, 0, ""))
    rows.append(("u-spike", "s-spike", "proj", "/p", "2026-09-01T10:00:00Z", 5000, 0, 0, 0, "Read"))
    _seed(conn, rows)

    report = insights.build_insight_report(conn)

    assert report is not None
    assert "s-spike" in report
    conn.close()


def test_no_anomaly_with_less_than_two_points():
    assert insights.detect_latest_anomaly([("2026-09-01", 100)]) is None
    assert insights.detect_latest_anomaly([]) is None


def test_fixed_rule_flags_spike_with_short_history():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 300),  # latest, > 1.5x mean of history (105)
    ]
    result = insights.detect_latest_anomaly(series)
    assert result is not None
    assert result["method"] == "fixed_rule"
    assert result["day"] == "2026-09-04"


def test_fixed_rule_does_not_flag_normal_day():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 108),
    ]
    assert insights.detect_latest_anomaly(series) is None


def test_zscore_flags_spike_with_long_history():
    history = [(f"2026-08-{d:02d}", 100) for d in range(1, 8)]  # 7 days, all 100
    series = history + [("2026-09-01", 500)]  # far above mean/stdev=0 case avoided by variance below
    # Introduce slight variance so stdev isn't zero, then a real spike:
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 500)]

    result = insights.detect_latest_anomaly(series)

    assert result is not None
    assert result["method"] == "zscore"
    assert result["day"] == "2026-09-01"


def test_zscore_does_not_flag_typical_day_with_long_history():
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 101)]

    assert insights.detect_latest_anomaly(series) is None


def test_zero_variance_history_never_flags():
    history = [("2026-08-0" + str(d), 100) for d in range(1, 8)]
    series = history + [("2026-09-01", 100)]

    assert insights.detect_latest_anomaly(series) is None
