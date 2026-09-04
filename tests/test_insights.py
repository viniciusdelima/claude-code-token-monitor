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


def test_daily_context_series_includes_output_tokens(tmp_path):
    # DESIGN.md §9: the anomaly series sums context_size + output_tokens,
    # not just context_size.
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", 100, 50, 10, 5, "Read"),
    ])

    series = insights.daily_context_series(conn, days=30)

    assert series == [("2026-09-01", 165.0)]  # 100+50+10+5
    conn.close()


def test_daily_context_series_today_pins_the_lookback_window(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-08-01T10:00:00Z", 100, 0, 0, 0, ""),
        ("u2", "s1", "proj", "/p", "2026-09-01T10:00:00Z", 200, 0, 0, 0, ""),
    ])

    # With today pinned well past both events but within 30 days of the
    # earlier one, both days show up regardless of the real wall clock.
    series = insights.daily_context_series(conn, days=30, today="2026-08-31")

    assert series == [("2026-08-01", 100.0), ("2026-09-01", 200.0)]
    conn.close()


def test_top_context_events_orders_by_context_size_desc(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "myproject", "/home/dev/myproject", "2026-09-04T10:00:00Z", 10, 0, 5, 0, "Read"),
        ("u2", "s2", "myproject", "/home/dev/myproject", "2026-09-04T11:00:00Z", 100, 0, 50, 0, "Bash"),
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
        "project": "myproject",
        "session_id": "abc",
    }
    path = insights.jsonl_path_for(event)
    assert path.endswith("/.claude/projects/myproject/abc.jsonl")


def test_build_insight_report_returns_none_without_anomaly(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 100, 0, 0, 0, ""),
    ])

    assert insights.build_insight_report(conn) is None
    conn.close()


def test_build_insight_report_includes_evidence_on_anomaly(tmp_path):
    # daily_context_series filters on a 30-day lookback from "today". Without
    # pinning "today", this test's 7-day seeded history ages out of that
    # window as real wall-clock time advances, so it was accidentally
    # exercising the fixed_rule branch (<7 days of history) instead of the
    # intended zscore branch (>=7 days). Pin "today" so the full 7-day
    # history (2026-08-01..07) plus the spike (2026-09-01) reliably falls
    # inside the window regardless of when this test actually runs.
    conn = db.get_connection(tmp_path / "usage.db")
    history_values = [95, 105, 98, 102, 97, 103, 100]  # non-zero variance
    rows = []
    for day, value in zip(range(1, 8), history_values):
        rows.append((f"u{day}", f"s{day}", "proj", "/p", f"2026-08-0{day}T10:00:00Z", value, 0, 0, 0, ""))
    rows.append(("u-spike", "s-spike", "proj", "/p", "2026-09-01T10:00:00Z", 5000, 0, 0, 0, "Read"))
    _seed(conn, rows)

    report = insights.build_insight_report(conn, today="2026-08-31")

    assert report is not None
    assert "s-spike" in report
    assert "sigma" in report  # zscore branch's header wording, not fixed_rule's
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
