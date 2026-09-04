import statistics
from pathlib import Path

import db

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"


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


def daily_context_series(conn, days=30):
    sql = f"""
        SELECT strftime('%Y-%m-%d', timestamp) AS day,
               SUM({CONTEXT_SIZE_EXPR}) AS total_context
        FROM usage_events
        WHERE timestamp >= date('now', ?)
        GROUP BY day
        ORDER BY day
    """
    cur = conn.execute(sql, (f"-{days} days",))
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


def build_insight_report(conn, days=30, evidence_limit=5):
    series = daily_context_series(conn, days=days)
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


def main(argv=None):
    conn = db.get_connection()
    report = build_insight_report(conn)
    if report:
        print(report)
    conn.close()


if __name__ == "__main__":
    main()
