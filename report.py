import argparse
from pathlib import Path

import db
import pricing

PERIOD_FORMATS = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
}

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"


def _bucket_expr(period, group_by):
    if group_by == "session":
        return "session_id"
    if group_by == "project":
        return "project"
    return f"strftime('{PERIOD_FORMATS[period]}', timestamp)"


def _where_clause(since):
    if since:
        return "WHERE timestamp >= ?", [since]
    return "", []


def _rows(conn, sql, params):
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def query_report(conn, period="day", group_by="day", since=None):
    bucket_expr = _bucket_expr(period, group_by)
    where, params = _where_clause(since)

    stats_sql = f"""
        SELECT {bucket_expr} AS bucket,
               COUNT(DISTINCT session_id) AS session_count,
               AVG({CONTEXT_SIZE_EXPR}) AS avg_context,
               MAX({CONTEXT_SIZE_EXPR}) AS peak_context
        FROM usage_events
        {where}
        GROUP BY bucket
    """
    token_sql = f"""
        SELECT {bucket_expr} AS bucket, model,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_creation_tokens) AS cache_creation_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens
        FROM usage_events
        {where}
        GROUP BY bucket, model
    """

    buckets = {}
    for row in _rows(conn, stats_sql, params):
        buckets[row["bucket"]] = {
            "bucket": row["bucket"],
            "session_count": row["session_count"],
            "avg_context": row["avg_context"] or 0.0,
            "peak_context": row["peak_context"] or 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "cost_unknown": False,
        }

    for row in _rows(conn, token_sql, params):
        entry = buckets[row["bucket"]]
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
        entry["cache_creation_tokens"] += row["cache_creation_tokens"]
        entry["cache_read_tokens"] += row["cache_read_tokens"]
        cost = pricing.estimate_cost_usd(
            row["model"], row["input_tokens"], row["output_tokens"],
            row["cache_creation_tokens"], row["cache_read_tokens"],
        )
        if cost is None:
            entry["cost_unknown"] = True
        else:
            entry["cost_usd"] += cost

    results = []
    for entry in buckets.values():
        entry["total_tokens"] = (
            entry["input_tokens"] + entry["output_tokens"]
            + entry["cache_creation_tokens"] + entry["cache_read_tokens"]
        )
        results.append(entry)
    results.sort(key=lambda r: r["bucket"])
    return results


def format_report(rows):
    if not rows:
        return "Sem dados no período."
    header = f"{'Período':<14} {'Tokens':>10} {'Custo($)':>10} {'Conversas':>10} {'CtxMédio':>10} {'CtxPico':>10}"
    lines = [header]
    for r in rows:
        cost = "N/D" if r["cost_unknown"] else f"{r['cost_usd']:.4f}"
        lines.append(
            f"{r['bucket']:<14} {r['total_tokens']:>10} {cost:>10} "
            f"{r['session_count']:>10} {r['avg_context']:>10.0f} {r['peak_context']:>10}"
        )
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Relatório de tokens do Claude Code")
    parser.add_argument("--period", choices=["day", "week", "month"], default="day")
    parser.add_argument("--group-by", dest="group_by", choices=["day", "session", "project"], default="day")
    parser.add_argument("--since", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    rows = query_report(conn, period=args.period, group_by=args.group_by, since=args.since)
    print(format_report(rows))
    conn.close()


if __name__ == "__main__":
    main()
