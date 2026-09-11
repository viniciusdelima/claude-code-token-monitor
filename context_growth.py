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


def _growth_detail(tool_names, tool_detail):
    """Choose one non-overlapping detail bucket for the preceding turn."""
    server = report_queries.extract_mcp_server(tool_names)
    if server != "native":
        return f"MCP:{server}"

    category = report_queries.extract_native_category(tool_names)
    details = [d for d in (tool_detail or "").split(",") if d]

    if category == "shell":
        return next((d for d in details if d.startswith("Bash:")), "Bash:unknown")
    if category == "exploracao":
        return next((d for d in details if d in {"Read", "Grep", "Glob"}), "exploração:unknown")
    if category == "codigo":
        return next((d for d in details if d in {"Write", "Edit", "NotebookEdit"}), "código:unknown")
    if category == "sem_ferramenta":
        return "sem ferramenta"
    return report_queries.NATIVE_CATEGORY_LABELS.get(category, category)


def _add_growth(bucket_map, label, delta):
    bucket = bucket_map[label]
    bucket["growth_tokens"] += delta
    bucket["events"] += 1
    bucket["max_growth"] = max(bucket["max_growth"], delta)


def _stream_key(row):
    """Return the independent context stream for an inference.

    Claude Code subagents reuse the parent's sessionId but maintain a separate
    context window. agent_id therefore has to participate in the key; otherwise
    main and sidechain contexts get compared against each other and create fake
    growth/reset events.
    """
    if row.get("source_type") == "subagent":
        return (row["session_id"], "subagent", row.get("agent_id") or "unknown")
    return (row["session_id"], "main", None)


def query_context_growth(conn, since=None):
    """Measure observed context growth between consecutive inferences.

    The delta from inference N to N+1 is attributed to inference N, because
    that turn produced the assistant text/tool call whose output can enter the
    next prompt. This is attribution by preceding turn, not byte-perfect
    provenance: user input, assistant output and tool results may all
    contribute to the observed delta.

    Main conversation and every subagent are independent context streams even
    though Claude Code records the same sessionId on sidechain events. The
    first inference of each stream is excluded as baseline. Negative deltas are
    tracked as resets/compactions and never subtract from positive growth.
    """
    where, params = _where_clause(since)
    sql = f"""
        SELECT uuid, session_id, project, timestamp, tool_names, tool_detail,
               output_tokens, source_type, agent_id,
               {CONTEXT_SIZE_EXPR} AS context_size
        FROM usage_events
        {where}
        ORDER BY timestamp, uuid
    """
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    buckets = defaultdict(lambda: {
        "growth_tokens": 0,
        "events": 0,
        "max_growth": 0,
    })
    detail_buckets = defaultdict(lambda: {
        "growth_tokens": 0,
        "events": 0,
        "max_growth": 0,
    })
    top_events = []
    reset_events = 0
    reset_tokens = 0
    first_context_total = 0
    seen_sessions = set()
    seen_streams = set()
    previous_by_stream = {}

    for row in rows:
        seen_sessions.add(row["session_id"])
        stream = _stream_key(row)
        if stream not in previous_by_stream:
            seen_streams.add(stream)
            first_context_total += row["context_size"] or 0
            previous_by_stream[stream] = row
            continue

        previous = previous_by_stream[stream]
        current_context = row["context_size"] or 0
        previous_context = previous["context_size"] or 0
        delta = current_context - previous_context

        if delta > 0:
            label = _growth_label(previous["tool_names"])
            detail = _growth_detail(previous["tool_names"], previous["tool_detail"])
            _add_growth(buckets, label, delta)
            _add_growth(detail_buckets, detail, delta)
            top_events.append({
                "session_id": row["session_id"],
                "agent_id": row.get("agent_id"),
                "source_type": row.get("source_type") or "main",
                "project": row["project"],
                "timestamp": row["timestamp"],
                "label": label,
                "detail": detail,
                "growth_tokens": delta,
                "from_context": previous_context,
                "to_context": current_context,
                "previous_tools": previous["tool_names"] or "nenhuma",
            })
        elif delta < 0:
            reset_events += 1
            reset_tokens += abs(delta)

        previous_by_stream[stream] = row

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

    detail_rows = []
    for label, values in detail_buckets.items():
        detail_rows.append({
            "bucket": label,
            "growth_tokens": values["growth_tokens"],
            "events": values["events"],
            "max_growth": values["max_growth"],
            "avg_growth": values["growth_tokens"] / values["events"] if values["events"] else 0,
        })
    detail_rows.sort(key=lambda r: -r["growth_tokens"])
    top_events.sort(key=lambda r: -r["growth_tokens"])

    return {
        "rows": bucket_rows,
        "detail_rows": detail_rows,
        "total_growth_tokens": sum(r["growth_tokens"] for r in bucket_rows),
        "session_count": len(seen_sessions),
        "stream_count": len(seen_streams),
        "first_context_total": first_context_total,
        "reset_events": reset_events,
        "reset_tokens": reset_tokens,
        "top_events": top_events,
    }


def format_context_growth(metrics, limit=8, detail_limit=10, event_limit=5):
    total = metrics["total_growth_tokens"]
    if total <= 0:
        return "Crescimento efetivo do contexto:\n  Sem crescimento positivo suficiente no período."

    lines = [
        "Crescimento efetivo do contexto (atribuído ao turno anterior):",
        "  Main e cada subagente são tratados como janelas independentes.",
        "  A primeira inferência de cada janela é baseline e não entra nesta conta.",
    ]
    for row in metrics["rows"][:limit]:
        share = row["growth_tokens"] / total * 100
        lines.append(
            f"  - {row['bucket']}: +{row['growth_tokens']} tokens ({share:.1f}%), "
            f"{row['events']} crescimentos, média +{row['avg_growth']:.0f}, pico +{row['max_growth']}"
        )

    if metrics.get("detail_rows"):
        lines.append("  Detalhamento dos vetores de crescimento:")
        for row in metrics["detail_rows"][:detail_limit]:
            share = row["growth_tokens"] / total * 100
            lines.append(
                f"    - {row['bucket']}: +{row['growth_tokens']} ({share:.1f}%), "
                f"{row['events']} crescimentos, média +{row['avg_growth']:.0f}, "
                f"pico +{row['max_growth']}"
            )

    if metrics["reset_events"]:
        lines.append(
            f"  - resets/compactações detectados: {metrics['reset_events']} "
            f"(-{metrics['reset_tokens']} tokens; não abatidos do crescimento positivo)"
        )

    if metrics["top_events"]:
        lines.append("  Maiores saltos observados:")
        for event in metrics["top_events"][:event_limit]:
            source = "main"
            if event.get("source_type") == "subagent":
                source = f"agent:{(event.get('agent_id') or 'unknown')[:8]}"
            lines.append(
                f"    - {event['session_id'][:8]}.../{source} "
                f"{event['from_context']} -> {event['to_context']} "
                f"(+{event['growth_tokens']}) após {event['label']} / {event['detail']} "
                f"[{event['previous_tools']}]"
            )

    lines.append(
        "  Nota: atribuição é por turno precedente dentro da mesma janela; o delta pode combinar "
        "resposta do Claude, tool result e novo input do usuário. Não representa proveniência byte a byte."
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
