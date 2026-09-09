import benchmark
import db


def _seed(conn, session_id, ts, cache_read, cache_write, input_tokens=2, project="proj"):
    conn.execute(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_read_tokens, thinking_tokens, tool_names, inference_geo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{session_id}-{ts}", session_id, project, f"/{project}", ts,
            "claude-sonnet-5", input_tokens, 100, cache_write, cache_read,
            0, "", None,
        ),
    )
    conn.commit()


def test_session_baseline_uses_first_inference_only(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, "s1", "2026-09-09T10:00:00Z", 35442, 25900)
    _seed(conn, "s1", "2026-09-09T10:01:00Z", 61342, 471)

    row = benchmark.session_baseline(conn, "s1")

    assert row["cache_read_tokens"] == 35442
    assert row["cache_creation_tokens"] == 25900
    assert row["initial_context_tokens"] == 61344
    conn.close()


def test_capture_persists_snapshot(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, "s1", "2026-09-09T10:00:00Z", 35442, 25900)

    captured = benchmark.capture_snapshot(
        conn, "atual", session_id="s1", generated_at="2026-09-09T10:05:00Z"
    )
    rows = benchmark.load_snapshots(conn)

    assert captured["label"] == "atual"
    assert len(rows) == 1
    assert rows[0]["initial_context_tokens"] == 61344
    conn.close()


def test_latest_session_is_used_by_default(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, "old", "2026-09-09T09:00:00Z", 100, 100)
    _seed(conn, "new", "2026-09-09T10:00:00Z", 200, 200)

    row = benchmark.session_baseline(conn)

    assert row["session_id"] == "new"
    conn.close()


def test_compare_uses_latest_snapshot_per_label(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, "s1", "2026-09-09T10:00:00Z", 35442, 25900)
    benchmark.capture_snapshot(conn, "atual", "s1", generated_at="2026-09-09T10:01:00Z")
    _seed(conn, "s2", "2026-09-09T11:00:00Z", 30000, 15000)
    benchmark.capture_snapshot(conn, "sem-mcp", "s2", generated_at="2026-09-09T11:01:00Z")

    text = benchmark.format_comparison(benchmark.load_snapshots(conn), base_label="atual")

    assert "atual" in text
    assert "sem-mcp" in text
    assert "-16342" in text
    assert "-26.6%" in text
    conn.close()
