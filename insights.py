import argparse
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import db
import report as report_queries

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"

SESSION_ABOVE_AVG_MULTIPLIER = 1.5
NATIVE_BOTTLENECK_SHARE = 0.8


def detect_latest_anomaly(series, z_threshold=2.0, fixed_multiplier=1.5):
    if len(series) < 2:
        return None

    *history, latest = series
    latest_day, latest_value = latest
    history_values = [v for _, v in history]
    mean = statistics.mean(history_values)

    if len(history_values) >= 7:
        stdev = statistics.stdev(history_values)
        if stdev == 0:
            return None
        z = (latest_value - mean) / stdev
        if abs(z) > z_threshold:
            return {"day": latest_day, "value": latest_value, "mean": mean, "method": "zscore", "score": z}
        return None

    if mean > 0 and latest_value > mean * fixed_multiplier:
        return {"day": latest_day, "value": latest_value, "mean": mean, "method": "fixed_rule", "score": latest_value / mean}
    return None


def daily_context_series(conn, days=30, today=None):
    # `today` lets callers (tests) pin the reference date for the lookback
    # window instead of the real system clock, which the wall-clock-driven
    # `date('now', ...)` used otherwise. Production call sites pass nothing
    # and get the exact old (wall-clock) behavior.
    # Per DESIGN.md §9, the anomaly series sums context_size + output_tokens
    # (total token volume), not just context_size -- that's used elsewhere
    # (evidence display, report.py) with its own narrower meaning.
    if today is None:
        where = "WHERE timestamp >= date('now', ?)"
        params = (f"-{days} days",)
    else:
        cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
        where = "WHERE timestamp >= ?"
        params = (cutoff,)

    sql = f"""
        SELECT strftime('%Y-%m-%d', timestamp) AS day,
               SUM({CONTEXT_SIZE_EXPR} + output_tokens) AS total_context
        FROM usage_events
        {where}
        GROUP BY day
        ORDER BY day
    """
    cur = conn.execute(sql, params)
    return [(row[0], row[1]) for row in cur.fetchall()]


def top_context_events(conn, day, limit=5):
    sql = f"""
        SELECT session_id, project, cwd, timestamp, tool_names,
               cache_creation_tokens, cache_read_tokens,
               {CONTEXT_SIZE_EXPR} AS context_size
        FROM usage_events
        WHERE strftime('%Y-%m-%d', timestamp) = ?
        ORDER BY context_size DESC
        LIMIT ?
    """
    cur = conn.execute(sql, (day, limit))
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def dominant_reason(event):
    if event["cache_creation_tokens"] >= event["cache_read_tokens"]:
        return "contexto cresceu muito num turno (leitura ou tool output grande)"
    return "conversa longa, contexto acumulado alto"


def jsonl_path_for(event):
    return str(Path.home() / ".claude" / "projects" / event["project"] / f"{event['session_id']}.jsonl")


def build_insight_report(conn, days=30, evidence_limit=5, today=None):
    series = daily_context_series(conn, days=days, today=today)
    anomaly = detect_latest_anomaly(series)
    if anomaly is None:
        return None

    events = top_context_events(conn, anomaly["day"], limit=evidence_limit)

    if anomaly["method"] == "zscore":
        header = (
            f"Dia {anomaly['day']}: desvio de {anomaly['score']:.1f} sigma da média "
            f"({anomaly['value']:.0f} vs média {anomaly['mean']:.0f}) — anomalia detectada."
        )
    else:
        header = (
            f"Dia {anomaly['day']}: {anomaly['score']:.1f}x acima da média "
            f"({anomaly['value']:.0f} vs média {anomaly['mean']:.0f}) — anomalia detectada."
        )

    lines = [header, "Evidência:"]
    for event in events:
        reason = dominant_reason(event)
        lines.append(
            f"  - sessão {event['session_id']} ({event['project']}, {event['timestamp']}) "
            f"— {event['context_size']} tokens de contexto, ferramentas: "
            f"{event['tool_names'] or 'nenhuma'} — {reason}"
        )
        lines.append(f"    arquivo: {jsonl_path_for(event)}")
    return "\n".join(lines)


def _resolve_since(period, since, today=None):
    if since is not None:
        return since
    if period == "day":
        return today or date.today().isoformat()
    return None


def build_mcp_bottleneck_metrics(conn, since=None):
    rows = report_queries.query_mcp_server_report(conn, since=since)
    total = sum(r["total_tokens"] for r in rows)
    if not rows or total == 0:
        return None
    native = next((r for r in rows if r["bucket"] == "native"), None)
    native_tokens = native["total_tokens"] if native else 0
    top = rows[0]
    return {
        "rows": rows,
        "total_tokens": total,
        "native_tokens": native_tokens,
        "native_share": native_tokens / total,
        "top_bucket": top["bucket"],
        "top_tokens": top["total_tokens"],
    }


def format_mcp_bottleneck(metrics):
    lines = ["Gargalo por origem (MCP vs nativo):"]
    for r in metrics["rows"][:5]:
        share = r["total_tokens"] / metrics["total_tokens"] * 100
        cost = "N/D" if r["cost_unknown"] else f"${r['cost_usd']:.2f}"
        lines.append(f"  - {r['bucket']}: {r['total_tokens']} tokens ({share:.1f}%), {cost}")
    if metrics["native_share"] >= NATIVE_BOTTLENECK_SHARE:
        lines.append("  -> Gargalo é uso nativo (contexto/cache), não MCP externo.")
    elif metrics["top_bucket"] != "native":
        lines.append(f"  -> Maior consumidor externo: {metrics['top_bucket']}.")
    return "\n".join(lines)


def session_bottleneck_reason(entry):
    total = entry["total_tokens"] or 1
    cache_read_share = entry["cache_read_tokens"] / total
    cache_creation_share = entry["cache_creation_tokens"] / total
    output_share = entry["output_tokens"] / total
    if cache_read_share > 0.6:
        return "contexto acumulado alto (sessão longa) — feche/`/clear` cedo"
    if cache_creation_share > 0.3:
        return "leitura grande num turno (arquivo/tool output) — prefira grep/head a ler tudo"
    if output_share > 0.3:
        return "resposta longa gerada — peça saída mais objetiva"
    return "uso misto, sem padrão dominante"


def build_session_outlier_metrics(conn, period="day", since=None, multiplier=SESSION_ABOVE_AVG_MULTIPLIER):
    rows = report_queries.query_report(conn, period=period, group_by="session", since=since)
    if not rows:
        return None
    totals = [r["total_tokens"] for r in rows]
    mean = statistics.mean(totals)
    outliers = sorted(
        [r for r in rows if mean > 0 and r["total_tokens"] > mean * multiplier],
        key=lambda r: -r["total_tokens"],
    )
    return {"session_count": len(rows), "mean_tokens": mean, "outliers": outliers}


def format_session_outliers(metrics, limit=5):
    mean = metrics["mean_tokens"]
    outliers = metrics["outliers"]
    if not outliers:
        return f"Nenhuma sessão acima de {SESSION_ABOVE_AVG_MULTIPLIER:.1f}x a média ({mean:.0f} tokens)."
    lines = [f"Sessões acima da média ({mean:.0f} tokens, limiar {SESSION_ABOVE_AVG_MULTIPLIER:.1f}x):"]
    for r in outliers[:limit]:
        ratio = r["total_tokens"] / mean if mean else 0
        reason = session_bottleneck_reason(r)
        lines.append(f"  - {r['bucket']}: {r['total_tokens']} tokens ({ratio:.1f}x média) — {reason}")
    return "\n".join(lines)


def summarize_diagnosis_metrics(mcp_metrics, session_metrics):
    top_external = None
    if mcp_metrics:
        top_external = next(
            (r["bucket"] for r in mcp_metrics["rows"] if r["bucket"] != "native"), None
        )
    return {
        "total_tokens": mcp_metrics["total_tokens"] if mcp_metrics else 0,
        "native_share": mcp_metrics["native_share"] if mcp_metrics else 0.0,
        "top_external_server": top_external,
        "session_count": session_metrics["session_count"] if session_metrics else 0,
        "outlier_count": len(session_metrics["outliers"]) if session_metrics else 0,
        "mean_session_tokens": session_metrics["mean_tokens"] if session_metrics else 0.0,
    }


def save_diagnosis_snapshot(conn, period, since, summary, report_text, generated_at=None):
    generated_at = generated_at or (datetime.utcnow().isoformat(timespec="seconds") + "Z")
    conn.execute(
        """
        INSERT INTO diagnosis_snapshots
        (generated_at, period, since, total_tokens, native_share, top_external_server,
         session_count, outlier_count, mean_session_tokens, report_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at, period, since,
            summary["total_tokens"], summary["native_share"], summary["top_external_server"],
            summary["session_count"], summary["outlier_count"], summary["mean_session_tokens"],
            report_text,
        ),
    )
    conn.commit()
    return generated_at


def load_previous_snapshot(conn, period):
    sql = """
        SELECT * FROM diagnosis_snapshots
        WHERE period = ?
        ORDER BY generated_at DESC
        LIMIT 1
    """
    cur = conn.execute(sql, (period,))
    row = cur.fetchone()
    if row is None:
        return None
    columns = [d[0] for d in cur.description]
    return dict(zip(columns, row))


def _pct_delta(curr, prev):
    if prev == 0:
        return "novo" if curr else "sem mudança"
    return f"{(curr - prev) / prev * 100:+.1f}%"


def format_snapshot_comparison(previous, summary):
    if previous is None:
        return None
    lines = [
        f"Comparação com diagnóstico anterior ({previous['generated_at']}, período={previous['period']}):",
        f"  - Tokens totais: {summary['total_tokens']} vs {previous['total_tokens']} "
        f"({_pct_delta(summary['total_tokens'], previous['total_tokens'])})",
        f"  - Share nativo: {summary['native_share'] * 100:.1f}% vs {previous['native_share'] * 100:.1f}%",
        f"  - Sessões acima da média: {summary['outlier_count']} vs {previous['outlier_count']}",
    ]
    if summary["top_external_server"] != previous["top_external_server"]:
        lines.append(
            f"  - Maior MCP externo mudou: {previous['top_external_server']} -> "
            f"{summary['top_external_server']}"
        )
    return "\n".join(lines)


def load_snapshot_history(conn, period=None, limit=10):
    where, params = "", []
    if period:
        where = "WHERE period = ?"
        params.append(period)
    sql = f"""
        SELECT * FROM diagnosis_snapshots
        {where}
        ORDER BY generated_at DESC
        LIMIT ?
    """
    params.append(limit)
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def format_snapshot_history(rows):
    if not rows:
        return "Sem histórico de diagnóstico salvo."
    header = f"{'Quando':<22} {'Período':<8} {'Tokens':>12} {'ShareNativo':>12} {'Outliers':>9}"
    lines = [header]
    for r in rows:
        lines.append(
            f"{r['generated_at']:<22} {r['period']:<8} {r['total_tokens']:>12} "
            f"{r['native_share'] * 100:>11.1f}% {r['outlier_count']:>9}"
        )
    return "\n".join(lines)


def build_diagnosis_report(conn, period="day", since=None, days=30, today=None, persist=True):
    resolved_since = _resolve_since(period, since, today=today)

    mcp_metrics = build_mcp_bottleneck_metrics(conn, since=resolved_since)
    session_metrics = build_session_outlier_metrics(conn, period=period, since=resolved_since)
    anomaly_report = build_insight_report(conn, days=days, today=today)

    sections = []
    if mcp_metrics:
        sections.append(format_mcp_bottleneck(mcp_metrics))
    if session_metrics:
        sections.append(format_session_outliers(session_metrics))
    if anomaly_report:
        sections.append("Anomalia vs histórico (30 dias):\n" + anomaly_report)

    summary = summarize_diagnosis_metrics(mcp_metrics, session_metrics)

    if persist:
        previous = load_previous_snapshot(conn, period=period)
        comparison = format_snapshot_comparison(previous, summary)
        if comparison:
            sections.append(comparison)

    report_text = "\n\n".join(sections) if sections else "Sem dados suficientes para diagnóstico."

    if persist:
        save_diagnosis_snapshot(conn, period, resolved_since, summary, report_text)

    return report_text


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Insights de uso de tokens")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--period", choices=["day", "week", "month"], default="day")
    parser.add_argument("--since", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))

    if args.history:
        rows = load_snapshot_history(conn, period=args.period)
        print(format_snapshot_history(rows))
    elif args.diagnose:
        print(build_diagnosis_report(conn, period=args.period, since=args.since))
    else:
        report = build_insight_report(conn)
        if report:
            print(report)

    conn.close()


if __name__ == "__main__":
    main()
