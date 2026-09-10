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


def _seed_context_streams(conn):
    rows = [
        # a1 intentionally inserted out of chronological order. Contexts after ORDER BY: 100, 150, 120.
        ('c2', 's1', '2026-09-10T10:02:00Z', 0, 5, 0, 150, 'a1'),
        ('c1', 's1', '2026-09-10T10:01:00Z', 0, 5, 0, 100, 'a1'),
        ('c3', 's1', '2026-09-10T10:03:00Z', 0, 5, 0, 120, 'a1'),
        # Same parent session, independent agent stream. Context must never mix with a1.
        ('d1', 's1', '2026-09-10T10:01:30Z', 0, 5, 0, 1000, 'a2'),
        ('d2', 's1', '2026-09-10T10:02:30Z', 0, 5, 0, 1100, 'a2'),
        # Different parent session for --session coverage.
        ('e1', 's2', '2026-09-10T11:00:00Z', 0, 5, 0, 500, 'b1'),
    ]
    conn.executemany(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp, model,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_creation_5m_tokens, cache_creation_1h_tokens, cache_read_tokens,
         thinking_tokens, tool_names, source_type, parent_session_id, agent_id)
        VALUES (?, ?, 'proj', '/p', ?, 'claude-sonnet-5', ?, ?, ?, 0, 0, ?, 0, '', 'subagent', ?, ?)
        """,
        [(u, s, ts, inp, out, cachew, cacher, s, agent) for u, s, ts, inp, out, cachew, cacher, agent in rows],
    )
    conn.commit()


def test_context_metrics_initial_avg_peak_final_and_chronological_order(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed_context_streams(conn)

    rows = subagent_report.query_subagent_report(conn, session_id='s1', include_context=True)
    a1 = next(r for r in rows if r['agent_id'] == 'a1')

    assert a1['initial_context'] == 100
    assert a1['avg_context'] == 370 / 3
    assert a1['peak_context'] == 150
    assert a1['final_context'] == 120
    assert a1['positive_context_growth'] == 50
    assert a1['reset_count'] == 1
    assert a1['reset_tokens'] == 30
    conn.close()


def test_context_analysis_never_mixes_agents_with_same_parent_session(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed_context_streams(conn)

    rows = subagent_report.query_subagent_report(conn, session_id='s1', include_context=True)
    metrics = {r['agent_id']: r for r in rows}

    assert metrics['a1']['initial_context'] == 100
    assert metrics['a1']['peak_context'] == 150
    assert metrics['a2']['initial_context'] == 1000
    assert metrics['a2']['peak_context'] == 1100
    assert metrics['a2']['positive_context_growth'] == 100
    conn.close()


def test_context_report_cache_share_and_tokens_per_inference(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)

    rows = subagent_report.query_subagent_report(conn, include_context=True)
    a1 = next(r for r in rows if r['agent_id'] == 'a1')

    assert round(a1['cache_read_share'], 4) == round(3000 / 3380 * 100, 4)
    assert a1['tokens_per_inference'] == 1690
    conn.close()


def test_segment_events_uses_up_to_eight_non_empty_dynamic_segments():
    events = [{'context_size': i * 100, 'cache_read_tokens': i * 50} for i in range(1, 18)]
    segments = subagent_report._segment_events(events)

    assert len(segments) == 8
    assert all(segment['inference_count'] > 0 for segment in segments)
    assert segments[0]['start_inference'] == 1
    assert segments[-1]['end_inference'] == 17


def test_segment_events_small_agent_has_no_empty_segments():
    events = [
        {'context_size': 100, 'cache_read_tokens': 80},
        {'context_size': 200, 'cache_read_tokens': 160},
        {'context_size': 300, 'cache_read_tokens': 240},
    ]
    segments = subagent_report._segment_events(events)

    assert len(segments) == 3
    assert [(s['start_inference'], s['end_inference']) for s in segments] == [(1, 1), (2, 2), (3, 3)]


def test_context_filters_since_and_session_together(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed_context_streams(conn)

    rows = subagent_report.query_subagent_report(
        conn, since='2026-09-10T10:02:00Z', session_id='s1', include_context=True
    )

    assert {r['agent_id'] for r in rows} == {'a1', 'a2'}
    assert next(r for r in rows if r['agent_id'] == 'a1')['initial_context'] == 150
    conn.close()


def test_context_output_explains_proxy_and_stream_isolation(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)
    rows = subagent_report.query_subagent_report(conn, include_context=True)

    output = subagent_report.format_subagent_context_report(rows)

    assert 'Contexto interno dos subagentes' in output
    assert 'context_size = input + cache write + cache read' in output
    assert 'stream independente' in output
    assert 'cache read' in output.lower()
    conn.close()


def test_legacy_report_behavior_does_not_require_context_fields(tmp_path):
    conn = db.get_connection(tmp_path / 'usage.db')
    _seed(conn)
    rows = subagent_report.query_subagent_report(conn, include_context=False)

    assert 'initial_context' not in rows[0]
    output = subagent_report.format_subagent_report(rows)
    assert 'Resumo de subagentes' in output
    conn.close()
