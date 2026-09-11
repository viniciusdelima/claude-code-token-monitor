import context_growth
import db


def _seed(conn, rows):
    for row in rows:
        conn.execute(
            """
            INSERT INTO usage_events
            (uuid, session_id, project, cwd, timestamp, model,
             input_tokens, output_tokens, cache_creation_tokens,
             cache_read_tokens, thinking_tokens, tool_names)
            VALUES (?, ?, ?, '/p', ?, 'claude-sonnet-5', ?, ?, ?, ?, 0, ?)
            """,
            row,
        )
    conn.commit()


def test_first_inference_is_baseline_not_growth(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "2026-09-09T10:00:00Z", 2, 10, 20000, 40000, ""),
    ])

    metrics = context_growth.query_context_growth(conn)

    assert metrics["session_count"] == 1
    assert metrics["stream_count"] == 1
    assert metrics["first_context_total"] == 60002
    assert metrics["total_growth_tokens"] == 0
    conn.close()


def test_growth_is_attributed_to_previous_turn(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "2026-09-09T10:00:00Z", 0, 100, 20000, 40000, "Bash"),
        ("u2", "s1", "proj", "2026-09-09T10:01:00Z", 0, 100, 25000, 40000, "Read"),
    ])

    metrics = context_growth.query_context_growth(conn)

    assert metrics["total_growth_tokens"] == 5000
    assert metrics["rows"][0]["bucket"] == "shell (Bash)"
    assert metrics["rows"][0]["growth_tokens"] == 5000
    assert metrics["top_events"][0]["previous_tools"] == "Bash"
    conn.close()


def test_negative_delta_counts_as_reset_not_negative_growth(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "2026-09-09T10:00:00Z", 0, 10, 20000, 80000, "Bash"),
        ("u2", "s1", "proj", "2026-09-09T10:01:00Z", 0, 10, 10000, 50000, ""),
    ])

    metrics = context_growth.query_context_growth(conn)

    assert metrics["total_growth_tokens"] == 0
    assert metrics["reset_events"] == 1
    assert metrics["reset_tokens"] == 40000
    conn.close()


def test_growth_separates_native_and_mcp_labels(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "2026-09-09T10:00:00Z", 0, 10, 20000, 40000, "mcp__jira__search"),
        ("u2", "s1", "proj", "2026-09-09T10:01:00Z", 0, 10, 23000, 40000, ""),
        ("u3", "s1", "proj", "2026-09-09T10:02:00Z", 0, 10, 25000, 40000, ""),
    ])

    metrics = context_growth.query_context_growth(conn)
    rows = {r["bucket"]: r for r in metrics["rows"]}

    assert rows["MCP: jira"]["growth_tokens"] == 3000
    assert rows["turno sem ferramenta registrada"]["growth_tokens"] == 2000
    conn.close()


def test_main_and_subagent_with_same_session_id_are_independent_streams(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    conn.executemany(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
         thinking_tokens, tool_names, source_type, parent_session_id, agent_id)
        VALUES (?, 's1', 'proj', '/p', ?, 'claude-sonnet-5',
                0, 10, ?, 0, 0, ?, ?, ?, ?)
        """,
        [
            ("main1", "2026-09-09T10:00:00Z", 10000, "Bash", "main", None, None),
            ("sub1", "2026-09-09T10:00:30Z", 80000, "Read", "subagent", "s1", "a1"),
            ("main2", "2026-09-09T10:01:00Z", 12000, "", "main", None, None),
            ("sub2", "2026-09-09T10:01:30Z", 83000, "", "subagent", "s1", "a1"),
        ],
    )
    conn.commit()

    metrics = context_growth.query_context_growth(conn)
    rows = {r["bucket"]: r for r in metrics["rows"]}

    assert metrics["session_count"] == 1
    assert metrics["stream_count"] == 2
    assert metrics["total_growth_tokens"] == 5000
    assert rows["shell (Bash)"]["growth_tokens"] == 2000
    assert rows["exploração (Read/Grep/Glob)"]["growth_tokens"] == 3000
    assert metrics["reset_events"] == 0
    conn.close()


def test_format_explains_attribution_limit(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "2026-09-09T10:00:00Z", 0, 10, 20000, 40000, "Bash"),
        ("u2", "s1", "proj", "2026-09-09T10:01:00Z", 0, 10, 22000, 40000, ""),
    ])

    text = context_growth.format_context_growth(context_growth.query_context_growth(conn))

    assert "Crescimento efetivo do contexto" in text
    assert "atribuído ao turno anterior" in text
    assert "janelas independentes" in text
    assert "Não representa proveniência byte a byte" in text
    conn.close()
