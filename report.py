import argparse
import json
from pathlib import Path

import db
import pricing

PERIOD_FORMATS = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
}

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"

DEFAULT_PREFS_PATH = Path.home() / ".claude" / "token-monitor" / "last_used.json"


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
        SELECT {bucket_expr} AS bucket, model, inference_geo,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_creation_tokens) AS cache_creation_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens
        FROM usage_events
        {where}
        GROUP BY bucket, model, inference_geo
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
            inference_geo=row["inference_geo"],
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


def extract_mcp_server(tool_names):
    if not tool_names:
        return "native"
    servers = set()
    for name in tool_names.split(","):
        if name.startswith("mcp__"):
            servers.add(name[len("mcp__"):].rsplit("__", 1)[0])
    if not servers:
        return "native"
    if len(servers) > 1:
        return "mixed"
    return next(iter(servers))


# Ordered highest-priority-first: a turn calling both Task and Read is
# classified "subagente", not "exploracao" -- dispatching a subagent is the
# more expensive/actionable signal even if it also read a file.
NATIVE_CATEGORY_PRIORITY = [
    ("subagente", {"Task"}),
    ("skill", {"Skill"}),
    ("codigo", {"Write", "Edit", "NotebookEdit"}),
    ("shell", {"Bash"}),
    ("web", {"WebFetch", "WebSearch"}),
    ("exploracao", {"Read", "Grep", "Glob"}),
    ("planejamento", {"TodoWrite", "ExitPlanMode", "EnterPlanMode", "AskUserQuestion"}),
]


def extract_native_category(tool_names):
    if not tool_names:
        return "sem_ferramenta"
    names = {n for n in tool_names.split(",") if not n.startswith("mcp__")}
    if not names:
        return "sem_ferramenta"
    for label, tools in NATIVE_CATEGORY_PRIORITY:
        if names & tools:
            return label
    return "outro"


def query_mcp_server_report(conn, since=None):
    where, params = _where_clause(since)
    sql = f"""
        SELECT tool_names, model, inference_geo,
               input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
        FROM usage_events
        {where}
    """
    buckets = {}
    for row in _rows(conn, sql, params):
        label = extract_mcp_server(row["tool_names"])
        entry = buckets.setdefault(label, {
            "bucket": label,
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "cost_usd": 0.0, "cost_unknown": False,
        })
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
        entry["cache_creation_tokens"] += row["cache_creation_tokens"]
        entry["cache_read_tokens"] += row["cache_read_tokens"]
        cost = pricing.estimate_cost_usd(
            row["model"], row["input_tokens"], row["output_tokens"],
            row["cache_creation_tokens"], row["cache_read_tokens"],
            inference_geo=row["inference_geo"],
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
    results.sort(key=lambda r: -r["total_tokens"])
    return results


def query_native_category_report(conn, since=None):
    where, params = _where_clause(since)
    sql = f"""
        SELECT tool_names, model, inference_geo,
               input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
        FROM usage_events
        {where}
    """
    buckets = {}
    for row in _rows(conn, sql, params):
        if extract_mcp_server(row["tool_names"]) != "native":
            continue
        label = extract_native_category(row["tool_names"])
        entry = buckets.setdefault(label, {
            "bucket": label,
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "cost_usd": 0.0, "cost_unknown": False,
        })
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
        entry["cache_creation_tokens"] += row["cache_creation_tokens"]
        entry["cache_read_tokens"] += row["cache_read_tokens"]
        cost = pricing.estimate_cost_usd(
            row["model"], row["input_tokens"], row["output_tokens"],
            row["cache_creation_tokens"], row["cache_read_tokens"],
            inference_geo=row["inference_geo"],
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
    results.sort(key=lambda r: -r["total_tokens"])
    return results


NATIVE_CATEGORY_LABELS = {
    "subagente": "subagentes (Task)",
    "skill": "skills/plugins",
    "codigo": "escrita de código (Write/Edit/NotebookEdit)",
    "shell": "shell (Bash)",
    "web": "web (WebFetch/WebSearch)",
    "exploracao": "exploração (Read/Grep/Glob)",
    "planejamento": "planejamento (TodoWrite/plan mode/perguntas)",
    "outro": "outras ferramentas nativas",
    "sem_ferramenta": "sem ferramenta (só texto)",
}


def format_native_category_report(rows):
    if not rows:
        return "Sem dados no período."
    header = f"{'Categoria nativa':<40} {'Tokens':>12} {'Custo($)':>10}"
    lines = [header]
    for r in rows:
        cost = "N/D" if r["cost_unknown"] else f"{r['cost_usd']:.4f}"
        label = NATIVE_CATEGORY_LABELS.get(r["bucket"], r["bucket"])
        lines.append(f"{label:<40} {r['total_tokens']:>12} {cost:>10}")
    return "\n".join(lines)


def format_report(rows):
    if not rows:
        return "Sem dados no período."
    header = (
        f"{'Período':<14} {'Tokens':>10} {'Custo($)':>10} {'Conversas':>10} "
        f"{'CtxMédio':>10} {'CtxPico':>10} {'Input':>10} {'Output':>10} "
        f"{'CacheEscrita':>14} {'CacheLeitura':>14}"
    )
    lines = [header]
    for r in rows:
        cost = "N/D" if r["cost_unknown"] else f"{r['cost_usd']:.4f}"
        lines.append(
            f"{r['bucket']:<14} {r['total_tokens']:>10} {cost:>10} "
            f"{r['session_count']:>10} {r['avg_context']:>10.0f} {r['peak_context']:>10} "
            f"{r['input_tokens']:>10} {r['output_tokens']:>10} "
            f"{r['cache_creation_tokens']:>14} {r['cache_read_tokens']:>14}"
        )
    return "\n".join(lines)


def format_mcp_server_report(rows):
    if not rows:
        return "Sem dados no período."
    header = f"{'MCP server':<40} {'Tokens':>12} {'Custo($)':>10}"
    lines = [header]
    for r in rows:
        cost = "N/D" if r["cost_unknown"] else f"{r['cost_usd']:.4f}"
        lines.append(f"{r['bucket']:<40} {r['total_tokens']:>12} {cost:>10}")
    return "\n".join(lines)


def load_last_used(path=DEFAULT_PREFS_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def save_last_used(prefs, path=DEFAULT_PREFS_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


def resolve_args(period, group_by, since, prefs):
    resolved_period = period or prefs.get("period") or "day"
    resolved_group_by = group_by or prefs.get("group_by") or "day"
    resolved_since = since if since is not None else prefs.get("since")
    return resolved_period, resolved_group_by, resolved_since


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Relatório de tokens do Claude Code")
    parser.add_argument("--period", choices=["day", "week", "month"], default=None)
    parser.add_argument("--group-by", dest="group_by", choices=["day", "session", "project"], default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    parser.add_argument("--mcp-servers", dest="mcp_servers", action="store_true")
    parser.add_argument("--native-categories", dest="native_categories", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))

    if args.mcp_servers:
        rows = query_mcp_server_report(conn, since=args.since)
        print(format_mcp_server_report(rows))
        conn.close()
        return

    if args.native_categories:
        rows = query_native_category_report(conn, since=args.since)
        print(format_native_category_report(rows))
        conn.close()
        return

    prefs = load_last_used()
    period, group_by, since = resolve_args(args.period, args.group_by, args.since, prefs)
    save_last_used({"period": period, "group_by": group_by, "since": since})

    rows = query_report(conn, period=period, group_by=group_by, since=since)
    print(format_report(rows))
    conn.close()


if __name__ == "__main__":
    main()
