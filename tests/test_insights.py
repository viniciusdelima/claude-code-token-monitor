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


def test_resolve_since_defaults_day_to_today_when_omitted():
    assert insights._resolve_since("day", None, today="2026-09-04") == "2026-09-04"


def test_resolve_since_keeps_explicit_value():
    assert insights._resolve_since("day", "2026-08-01", today="2026-09-04") == "2026-08-01"


def test_resolve_since_leaves_week_month_unbounded_without_explicit_since():
    assert insights._resolve_since("week", None, today="2026-09-04") is None


def test_build_mcp_bottleneck_metrics_flags_native_dominance(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 1000, 100, 0, 0, ""),
        ("u2", "s1", "proj", "/p", "2026-09-04T11:00:00Z", 10, 5, 0, 0, "mcp__jira__search"),
    ])

    metrics = insights.build_mcp_bottleneck_metrics(conn)

    assert metrics["native_share"] > 0.8
    assert metrics["top_bucket"] == "native"
    text = insights.format_mcp_bottleneck(metrics)
    assert "uso nativo" in text
    conn.close()


def test_build_mcp_bottleneck_metrics_names_top_external_server(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 10, 5, 0, 0, ""),
        ("u2", "s1", "proj", "/p", "2026-09-04T11:00:00Z", 1000, 500, 0, 0, "mcp__jira__search"),
    ])

    metrics = insights.build_mcp_bottleneck_metrics(conn)

    assert metrics["top_bucket"] == "jira"
    text = insights.format_mcp_bottleneck(metrics)
    assert "jira" in text
    conn.close()


def test_build_mcp_bottleneck_metrics_none_without_data(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    assert insights.build_mcp_bottleneck_metrics(conn) is None
    conn.close()


def test_session_bottleneck_reason_cache_read_heavy():
    entry = {"total_tokens": 1000, "cache_read_tokens": 900, "cache_creation_tokens": 50, "output_tokens": 50}
    assert "clear" in insights.session_bottleneck_reason(entry)


def test_session_bottleneck_reason_cache_creation_heavy():
    entry = {"total_tokens": 1000, "cache_read_tokens": 100, "cache_creation_tokens": 500, "output_tokens": 100}
    assert "grep/head" in insights.session_bottleneck_reason(entry)


def test_session_bottleneck_reason_output_heavy():
    entry = {"total_tokens": 1000, "cache_read_tokens": 100, "cache_creation_tokens": 100, "output_tokens": 500}
    assert "objetiva" in insights.session_bottleneck_reason(entry)


def test_session_bottleneck_reason_mixed():
    entry = {"total_tokens": 1000, "cache_read_tokens": 100, "cache_creation_tokens": 100, "output_tokens": 100}
    assert "sem padrão" in insights.session_bottleneck_reason(entry)


def test_build_session_outlier_metrics_flags_session_above_multiplier(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 100, 0, 0, 0, ""),
        ("u2", "s2", "proj", "/p", "2026-09-04T10:00:00Z", 100, 0, 0, 0, ""),
        ("u3", "s3", "proj", "/p", "2026-09-04T10:00:00Z", 1000, 0, 900, 0, ""),
    ])

    metrics = insights.build_session_outlier_metrics(conn, since="2026-09-04")

    assert metrics["session_count"] == 3
    assert len(metrics["outliers"]) == 1
    assert metrics["outliers"][0]["bucket"] == "s3"
    text = insights.format_session_outliers(metrics)
    assert "s3" in text
    conn.close()


def test_build_session_outlier_metrics_none_without_data(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    assert insights.build_session_outlier_metrics(conn, since="2026-09-04") is None
    conn.close()


def test_diagnosis_snapshot_roundtrip_and_comparison(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    summary_old = {
        "total_tokens": 1000, "native_share": 0.9, "top_external_server": "jira",
        "session_count": 2, "outlier_count": 0, "mean_session_tokens": 500.0,
    }
    insights.save_diagnosis_snapshot(
        conn, "day", "2026-09-03", summary_old, "relatorio antigo",
        generated_at="2026-09-03T12:00:00Z",
    )

    previous = insights.load_previous_snapshot(conn, period="day")
    assert previous["total_tokens"] == 1000
    assert previous["top_external_server"] == "jira"

    summary_new = {
        "total_tokens": 2000, "native_share": 0.5, "top_external_server": "sentry",
        "session_count": 3, "outlier_count": 1, "mean_session_tokens": 600.0,
    }
    comparison = insights.format_snapshot_comparison(previous, summary_new)
    assert "+100.0%" in comparison
    assert "jira -> sentry" in comparison
    conn.close()


def test_format_snapshot_comparison_none_without_previous():
    assert insights.format_snapshot_comparison(None, {}) is None


def test_load_snapshot_history_orders_newest_first(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    base_summary = {
        "total_tokens": 100, "native_share": 1.0, "top_external_server": None,
        "session_count": 1, "outlier_count": 0, "mean_session_tokens": 100.0,
    }
    insights.save_diagnosis_snapshot(conn, "day", "2026-09-03", base_summary, "r1", generated_at="2026-09-03T12:00:00Z")
    insights.save_diagnosis_snapshot(conn, "day", "2026-09-04", base_summary, "r2", generated_at="2026-09-04T12:00:00Z")

    rows = insights.load_snapshot_history(conn, period="day")

    assert [r["generated_at"] for r in rows] == ["2026-09-04T12:00:00Z", "2026-09-03T12:00:00Z"]
    text = insights.format_snapshot_history(rows)
    assert "2026-09-04T12:00:00Z" in text
    conn.close()


def test_build_diagnosis_report_persists_snapshot_for_later_comparison(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-03T10:00:00Z", 100, 0, 0, 0, ""),
    ])

    first = insights.build_diagnosis_report(conn, period="day", since="2026-09-03", today="2026-09-03")
    assert "Sem dados" not in first
    assert insights.load_previous_snapshot(conn, period="day") is not None

    _seed(conn, [
        ("u2", "s2", "proj", "/p", "2026-09-04T10:00:00Z", 500, 0, 0, 0, "mcp__jira__search"),
    ])
    second = insights.build_diagnosis_report(conn, period="day", since="2026-09-04", today="2026-09-04")

    assert "Comparação com diagnóstico anterior" in second
    history = insights.load_snapshot_history(conn, period="day")
    assert len(history) == 2
    conn.close()
