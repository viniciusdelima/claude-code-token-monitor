import db
import subagent_report


def _seed(conn):
    conn.executemany(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_creation_5m_tokens, cache_creation_1h_tokens,
         cache_read_tokens, thinking_tokens, tool_names, inference_geo,
         source_type, parent_session_id, agent_id)
        VALUES (?, ?, 'proj', '/p', ?, 'claude-sonnet-5', ?, ?, ?, ?, 0, ?, 0, '', NULL, ?, ?, ?)
        """,
        [
            ('u1', 's1', '2026-09-10T10:00:00Z', 100, 20, 10, 10, 1000, 'subagent', 's1', 'a1'),
            ('u2', 's1', '2026-09-10T10:01:00Z', 200, 30, 20, 20, 2000, 'subagent', 's1', 'a1'),
            ('u3', 's1', '2026-09-10T10:02:00Z', 50, 10, 5, 5, 500, 'subagent', 's1', 'a2'),
            ('u4', 's1', '2026-09-10T10:03:00Z', 999, 99, 0, 0, 9999, 'main', None, None),
        ],
    )
    conn.commit()


def test_query_subagent_report_groups_inferences_by_agent(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)

    rows = subagent_report.query_subagent_report(conn)

    assert [r['agent_id'] for r in rows] == ['a1', 'a2']
    assert rows[0]['inference_count'] == 2
    assert rows[0]['input_tokens'] == 300
    assert rows[0]['output_tokens'] == 50
    assert rows[0]['cache_creation_tokens'] == 30
    assert rows[0]['cache_read_tokens'] == 3000
    assert rows[0]['total_tokens'] == 3380
    conn.close()


def test_query_subagent_report_filters_by_parent_session(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)
    conn.execute(
        """INSERT INTO usage_events
           (uuid, session_id, project, cwd, timestamp, model, input_tokens,
            output_tokens, source_type, parent_session_id, agent_id)
           VALUES ('other', 's2', 'proj', '/p', '2026-09-10T11:00:00Z',
                   'claude-sonnet-5', 10, 10, 'subagent', 's2', 'b1')"""
    )
    conn.commit()

    rows = subagent_report.query_subagent_report(conn, session_id='s1')

    assert {r['agent_id'] for r in rows} == {'a1', 'a2'}
    conn.close()


def test_query_subagent_report_filters_since(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)

    rows = subagent_report.query_subagent_report(conn, since='2026-09-10T10:02:00Z')

    assert [r['agent_id'] for r in rows] == ['a2']
    conn.close()


def test_summarize_subagents_sums_agent_totals(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)
    rows = subagent_report.query_subagent_report(conn)

    summary = subagent_report.summarize_subagents(rows)

    assert summary['agent_count'] == 2
    assert summary['inference_count'] == 3
    assert summary['total_tokens'] == 3945
    assert summary['cache_read_tokens'] == 3500
    conn.close()


def test_format_subagent_report_includes_usage_dimensions(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)
    output = subagent_report.format_subagent_report(
        subagent_report.query_subagent_report(conn)
    )

    assert 'Resumo de subagentes' in output
    assert 'CacheEscrita' in output
    assert 'CacheLeitura' in output
    assert 'a1' in output
    assert '3380' in output
    conn.close()


def test_format_subagent_report_handles_empty_rows():
    assert subagent_report.format_subagent_report([]) == 'Sem subagentes encontrados no período.'
