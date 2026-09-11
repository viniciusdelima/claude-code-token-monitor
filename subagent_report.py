import argparse
import math
from pathlib import Path

import db
import pricing

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"
MAX_CONTEXT_SEGMENTS = 8


def _rows(conn, sql, params):
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _query_subagent_events(conn, since=None, session_id=None):
    clauses = ["source_type = 'subagent'"]
    params = []
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if session_id:
        clauses.append("parent_session_id = ?")
        params.append(session_id)

    sql = f"""
        SELECT uuid, parent_session_id, agent_id, project, model, inference_geo,
               timestamp, input_tokens, output_tokens, cache_creation_tokens,
               cache_creation_5m_tokens, cache_creation_1h_tokens,
               cache_read_tokens, {CONTEXT_SIZE_EXPR} AS context_size
        FROM usage_events
        WHERE {' AND '.join(clauses)}
        ORDER BY parent_session_id, agent_id, timestamp, uuid
    """
    return _rows(conn, sql, params)


def _segment_events(events, max_segments=MAX_CONTEXT_SEGMENTS):
    """Split ordered events into up to max_segments non-empty chronological buckets."""
    if not events:
        return []
    segment_count = min(max_segments, len(events))
    segments = []
    for i in range(segment_count):
        start = math.floor(i * len(events) / segment_count)
        end = math.floor((i + 1) * len(events) / segment_count)
        chunk = events[start:end]
        if not chunk:
            continue
        contexts = [e["context_size"] or 0 for e in chunk]
        cache_reads = [e["cache_read_tokens"] or 0 for e in chunk]
        segments.append({
            "start_inference": start + 1,
            "end_inference": end,
            "inference_count": len(chunk),
            "avg_context": sum(contexts) / len(contexts),
            "max_context": max(contexts),
            "avg_cache_read": sum(cache_reads) / len(cache_reads),
        })
    return segments


def _context_metrics(events):
    contexts = [e["context_size"] or 0 for e in events]
    positive_growth = 0
    reset_count = 0
    reset_tokens = 0
    for previous, current in zip(contexts, contexts[1:]):
        delta = current - previous
        if delta > 0:
            positive_growth += delta
        elif delta < 0:
            reset_count += 1
            reset_tokens += abs(delta)
    return {
        "initial_context": contexts[0] if contexts else 0,
        "avg_context": sum(contexts) / len(contexts) if contexts else 0,
        "peak_context": max(contexts) if contexts else 0,
        "final_context": contexts[-1] if contexts else 0,
        "positive_context_growth": positive_growth,
        "reset_count": reset_count,
        "reset_tokens": reset_tokens,
        "segments": _segment_events(events),
    }


def query_subagent_report(conn, since=None, session_id=None, include_context=False):
    events = _query_subagent_events(conn, since=since, session_id=session_id)
    buckets = {}
    bucket_events = {}
    for row in events:
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
        bucket_events.setdefault(key, []).append(row)
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
    for key, entry in buckets.items():
        entry["model"] = ",".join(sorted(entry.pop("models"))) or "unknown"
        entry["total_tokens"] = (
            entry["input_tokens"] + entry["output_tokens"]
            + entry["cache_creation_tokens"] + entry["cache_read_tokens"]
        )
        entry["tokens_per_inference"] = (
            entry["total_tokens"] / entry["inference_count"] if entry["inference_count"] else 0
        )
        entry["cache_read_share"] = (
            entry["cache_read_tokens"] / entry["total_tokens"] * 100
            if entry["total_tokens"] else 0
        )
        if include_context:
            entry.update(_context_metrics(bucket_events[key]))
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


def _fmt_tokens(value):
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"


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


def format_subagent_context_report(rows):
    if not rows:
        return "Sem subagentes encontrados no período."

    summary = summarize_subagents(rows)
    cost = "N/D" if summary["cost_unknown"] else f"{summary['cost_usd']:.4f}"
    lines = [
        "Contexto interno dos subagentes",
        f"Agentes: {summary['agent_count']} | Inferências: {summary['inference_count']} | "
        f"Tokens: {_fmt_tokens(summary['total_tokens'])} | Custo($): {cost}",
        f"Input: {_fmt_tokens(summary['input_tokens'])} | "
        f"CacheEscrita: {_fmt_tokens(summary['cache_creation_tokens'])} | "
        f"CacheLeitura: {_fmt_tokens(summary['cache_read_tokens'])} | "
        f"Output: {_fmt_tokens(summary['output_tokens'])}",
        "",
        f"{'Agent':<10} {'Modelo':<16} {'Inf.':>5} {'CtxIni':>8} {'CtxMed':>8} "
        f"{'CtxPico':>8} {'CtxFim':>8} {'CacheR%':>8} {'Tok/Inf':>9} {'Tokens':>9}",
    ]
    for row in rows:
        lines.append(
            f"{_short(row['agent_id']):<10} {_short(row['model'], 16):<16} "
            f"{row['inference_count']:>5} {_fmt_tokens(row['initial_context']):>8} "
            f"{_fmt_tokens(row['avg_context']):>8} {_fmt_tokens(row['peak_context']):>8} "
            f"{_fmt_tokens(row['final_context']):>8} {row['cache_read_share']:>7.1f}% "
            f"{_fmt_tokens(row['tokens_per_inference']):>9} {_fmt_tokens(row['total_tokens']):>9}"
        )

    lines.append("")
    lines.append("Evolução cronológica por agente")
    for row in rows:
        lines.extend([
            "",
            f"{row['agent_id']} — {_short(row['model'], 30)}",
            f"  Inferências: {row['inference_count']} | Ctx inicial: {_fmt_tokens(row['initial_context'])} | "
            f"médio: {_fmt_tokens(row['avg_context'])} | pico: {_fmt_tokens(row['peak_context'])} | "
            f"final: {_fmt_tokens(row['final_context'])}",
            f"  Processamento: input {_fmt_tokens(row['input_tokens'])} | "
            f"cache read {_fmt_tokens(row['cache_read_tokens'])} ({row['cache_read_share']:.1f}%) | "
            f"cache write {_fmt_tokens(row['cache_creation_tokens'])} | "
            f"output {_fmt_tokens(row['output_tokens'])} | total {_fmt_tokens(row['total_tokens'])}",
            f"  Crescimento positivo observado: +{_fmt_tokens(row['positive_context_growth'])} | "
            f"quedas/resets: {row['reset_count']} (-{_fmt_tokens(row['reset_tokens'])})",
            "  Segmentos:",
        ])
        for segment in row["segments"]:
            range_text = (
                str(segment["start_inference"])
                if segment["start_inference"] == segment["end_inference"]
                else f"{segment['start_inference']}-{segment['end_inference']}"
            )
            lines.append(
                f"    - inf. {range_text}: ctx médio {_fmt_tokens(segment['avg_context'])}, "
                f"pico {_fmt_tokens(segment['max_context'])}, "
                f"cache read médio/inf. {_fmt_tokens(segment['avg_cache_read'])}"
            )

    lines.extend([
        "",
        "Nota: context_size = input + cache write + cache read; output não entra na janela.",
        "Cada agent_id é analisado como uma stream independente, mesmo quando compartilha session_id com o pai.",
        "Cache read conta como contexto processado; alto volume pode refletir reuso repetido de uma janela grande, não entrada equivalente de contexto novo.",
        "Crescimentos/quedas são observacionais entre inferências consecutivas da mesma stream; quedas não são rotuladas automaticamente como /compact manual.",
    ])
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Uso de tokens por subagente do Claude Code")
    parser.add_argument("--since", default=None)
    parser.add_argument("--session", dest="session_id", default=None)
    parser.add_argument("--context", action="store_true", help="analisa evolução da janela interna de cada subagente")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    rows = query_subagent_report(
        conn, since=args.since, session_id=args.session_id, include_context=args.context
    )
    if args.context:
        print(format_subagent_context_report(rows))
    else:
        print(format_subagent_report(rows))
    conn.close()


if __name__ == "__main__":
    main()
