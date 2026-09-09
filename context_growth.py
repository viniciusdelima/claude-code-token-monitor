import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

import db
import report as report_queries

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"


def _resolve_since(period, since, today=None):
    if since is not None:
        return since
    if period == "day":
        return today or date.today().isoformat()
    return None


def _where_clause(since):
    if since:
        return "WHERE timestamp >= ?", [since]
    return "", []


def _growth_label(tool_names):
    server = report_queries.extract_mcp_server(tool_names)
    if server != "native":
        return f"MCP: {server}"

    category = report_queries.extract_native_category(tool_names)
    if category == "sem_ferramenta":
        return "turno sem ferramenta registrada"
    return report_queries.NATIVE_CATEGORY_LABELS.get(category, category)


def query_context_growth(conn, since=None):
    """Measure observed context growth between consecutive inferences.

    The delta from inference N to N+1 is attributed to inference N, because
    that turn produced the assistant text/tool call whose output can enter the
    next prompt. This is attribution by preceding turn, not byte-perfect
    provenance: user input, assistant output and tool results may all
    contribute to the observed delta.

    First inference of each session is excluded (baseline). Negative deltas
    are tracked as resets/compactions and never subtract from positive growth.
    """
    where, params = _where_clause(since)
    sql = f"""
        SELECT uuid, session_id, project, timestamp, tool_names, output_tokens,
               {CONTEXT_SIZE_EXPR} AS context_size
        FROM usage_events
        {where}
        ORDER BY session_id, timestamp, uuid
    """
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    buckets = defaultdict(lambda: {
        "growth_tokens": 0,
        "events": 0,
        "max_growth": 0,
    })
    top_events = []
    reset_events = 0
    reset_tokens = 0
    first_context_total = 0
    session_count = 0

    previous = None
    previous_session = None
    for row in rows:
        if row["session_id"] != previous_session:
            session_count += 1
            first_context_total += row["context_size"] or 0
            previous = row
            previous_session = row["session_id"]
            continue

        current_context = row["context_size"] or 0
        previous_context = previous["context_size"] or 0
        delta = current_context - previous_context

        if delta > 0:
            label = _growth_label(previous["tool_names"])
            bucket = buckets[label]
            bucket["growth_tokens"] += delta
            bucket["events"] += 1
            bucket["max_growth"] = max(bucket["max_growth"], delta)
            top_events.append({
                "session_id": row["session_id"],
                "project": row["project"],
                "timestamp": row["timestamp"],
                "label": label,
                "growth_tokens": delta,
                "from_context": previous_context,
                "to_context": current_context,
                "previous_tools": previous["tool_names"] or "nenhuma",
            })
        elif delta < 0:
            reset_events += 1
            reset_tokens += abs(delta)

        previous = row

    bucket_rows = []
    for label, values in buckets.items():
        bucket_rows.append({
            "bucket": label,
            "growth_tokens": values["growth_tokens"],
            "events": values["events"],
            "max_growth": values["max_growth"],
            "avg_growth": values["growth_tokens"] / values["events"] if values["events"] else 0,
        })
    bucket_rows.sort(key=lambda r: -r["growth_tokens"])
    top_events.sort(key=lambda r: -r["growth_tokens"])

    return {
        "rows": bucket_rows,
        "total_growth_tokens": sum(r["growth_tokens"] for r in bucket_rows),
        "session_count": session_count,
        "first_context_total": first_context_total,
        "reset_events": reset_events,
        "reset_tokens": reset_tokens,
        "top_events": top_events,
    }


def format_context_growth(metrics, limit=8, event_limit=5):
    total = metrics["total_growth_tokens"]
    if total <= 0:
        return "Crescimento efetivo do contexto:\n  Sem crescimento positivo suficiente no período."

    lines = [
        "Crescimento efetivo do contexto (atribuído ao turno anterior):",
        "  A primeira inferência de cada sessão é baseline e não entra nesta conta.",
    ]
    for row in metrics["rows"][:limit]:
        share = row["growth_tokens"] / total * 100
        lines.append(
            f"  - {row['bucket']}: +{row['growth_tokens']} tokens ({share:.1f}%), "
            f"{row['events']} crescimentos, média +{row['avg_growth']:.0f}, pico +{row['max_growth']}"
        )

    if metrics["reset_events"]:
        lines.append(
            f"  - resets/compactações detectados: {metrics['reset_events']} "
            f"(-{metrics['reset_tokens']} tokens; não abatidos do crescimento positivo)"
        )

    if metrics["top_events"]:
        lines.append("  Maiores saltos observados:")
        for event in metrics["top_events"][:event_limit]:
            lines.append(
                f"    - {event['session_id'][:8]}... {event['from_context']} -> {event['to_context']} "
                f"(+{event['growth_tokens']}) após {event['label']} "
                f"[{event['previous_tools']}]"
            )

    lines.append(
        "  Nota: atribuição é por turno precedente; o delta pode combinar resposta do Claude, "
        "tool result e novo input do usuário. Não representa proveniência byte a byte."
    )
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Crescimento efetivo do contexto do Claude Code")
    parser.add_argument("--period", choices=["day", "week", "month"], default="day")
    parser.add_argument("--since", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    since = _resolve_since(args.period, args.since)
    metrics = query_context_growth(conn, since=since)
    print(format_context_growth(metrics))
    conn.close()


if __name__ == "__main__":
    main()
