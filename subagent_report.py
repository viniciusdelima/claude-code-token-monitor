import argparse
from pathlib import Path

import db
import pricing


def _rows(conn, sql, params):
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def query_subagent_report(conn, since=None, session_id=None):
    clauses = ["source_type = 'subagent'"]
    params = []
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if session_id:
        clauses.append("parent_session_id = ?")
        params.append(session_id)

    sql = f"""
        SELECT parent_session_id, agent_id, project, model, inference_geo,
               input_tokens, output_tokens, cache_creation_tokens,
               cache_creation_5m_tokens, cache_creation_1h_tokens,
               cache_read_tokens
        FROM usage_events
        WHERE {' AND '.join(clauses)}
        ORDER BY timestamp
    """

    buckets = {}
    for row in _rows(conn, sql, params):
        key = (row["parent_session_id"], row["agent_id"])
        entry = buckets.setdefault(key, {
            "parent_session_id": row["parent_session_id"],
            "agent_id": row["agent_id"] or "unknown",
            "project": row["project"],
            "models": set(),
            "inference_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "cost_unknown": False,
        })
        if row["model"]:
            entry["models"].add(row["model"])
        entry["inference_count"] += 1
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
        entry["cache_creation_tokens"] += row["cache_creation_tokens"]
        entry["cache_read_tokens"] += row["cache_read_tokens"]

        cost = pricing.estimate_cost_usd(
            row["model"], row["input_tokens"], row["output_tokens"],
            row["cache_creation_5m_tokens"], row["cache_creation_1h_tokens"],
            row["cache_read_tokens"], inference_geo=row["inference_geo"],
        )
        if cost is None:
            entry["cost_unknown"] = True
        else:
            entry["cost_usd"] += cost

    results = []
    for entry in buckets.values():
        entry["model"] = ",".join(sorted(entry.pop("models"))) or "unknown"
        entry["total_tokens"] = (
            entry["input_tokens"] + entry["output_tokens"]
            + entry["cache_creation_tokens"] + entry["cache_read_tokens"]
        )
        results.append(entry)

    results.sort(key=lambda r: -r["total_tokens"])
    return results


def summarize_subagents(rows):
    if not rows:
        return {
            "agent_count": 0,
            "inference_count": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "cost_unknown": False,
        }
    return {
        "agent_count": len(rows),
        "inference_count": sum(r["inference_count"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "cache_creation_tokens": sum(r["cache_creation_tokens"] for r in rows),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in rows),
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "cost_unknown": any(r["cost_unknown"] for r in rows),
    }


def _short(value, width=10):
    if not value:
        return "-"
    return value if len(value) <= width else value[:width]


def format_subagent_report(rows):
    if not rows:
        return "Sem subagentes encontrados no período."

    summary = summarize_subagents(rows)
    cost = "N/D" if summary["cost_unknown"] else f"{summary['cost_usd']:.4f}"
    lines = [
        "Resumo de subagentes",
        f"Agentes: {summary['agent_count']} | Inferências: {summary['inference_count']} | "
        f"Tokens: {summary['total_tokens']} | Custo($): {cost}",
        f"Input: {summary['input_tokens']} | CacheEscrita: {summary['cache_creation_tokens']} | "
        f"CacheLeitura: {summary['cache_read_tokens']} | Output: {summary['output_tokens']}",
        "",
        f"{'Sessão':<10} {'Agent':<10} {'Modelo':<22} {'Inf.':>6} {'Tokens':>12} "
        f"{'Input':>10} {'CacheW':>10} {'CacheR':>12} {'Output':>9} {'Custo($)':>10}",
    ]
    for row in rows:
        row_cost = "N/D" if row["cost_unknown"] else f"{row['cost_usd']:.4f}"
        lines.append(
            f"{_short(row['parent_session_id']):<10} {_short(row['agent_id']):<10} "
            f"{_short(row['model'], 22):<22} {row['inference_count']:>6} "
            f"{row['total_tokens']:>12} {row['input_tokens']:>10} "
            f"{row['cache_creation_tokens']:>10} {row['cache_read_tokens']:>12} "
            f"{row['output_tokens']:>9} {row_cost:>10}"
        )
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Uso de tokens por subagente do Claude Code")
    parser.add_argument("--since", default=None)
    parser.add_argument("--session", dest="session_id", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    rows = query_subagent_report(conn, since=args.since, session_id=args.session_id)
    print(format_subagent_report(rows))
    conn.close()


if __name__ == "__main__":
    main()
