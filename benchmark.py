import argparse
from datetime import datetime, timezone
from pathlib import Path

import db


def latest_session_id(conn, project=None):
    where = "WHERE session_id IS NOT NULL"
    params = []
    if project:
        where += " AND project = ?"
        params.append(project)
    row = conn.execute(
        f"""
        SELECT session_id
        FROM usage_events
        {where}
        ORDER BY timestamp DESC, rowid DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row[0] if row else None


def session_baseline(conn, session_id=None, project=None):
    """Return one API usage row: the first inference of a session.

    A Claude response may appear more than once in JSONL (one record per
    content block). We intentionally select one earliest row instead of
    summing the opening timestamp, so duplicated usage does not inflate the
    benchmark baseline.
    """
    if session_id is None:
        session_id = latest_session_id(conn, project=project)
    if not session_id:
        return None

    row = conn.execute(
        """
        SELECT session_id, project, model, timestamp,
               input_tokens, output_tokens,
               cache_creation_tokens, cache_read_tokens,
               cache_creation_5m_tokens, cache_creation_1h_tokens
        FROM usage_events
        WHERE session_id = ?
        ORDER BY timestamp ASC, rowid ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if not row:
        return None

    keys = [
        "session_id", "project", "model", "event_timestamp",
        "input_tokens", "output_tokens", "cache_creation_tokens",
        "cache_read_tokens", "cache_creation_5m_tokens",
        "cache_creation_1h_tokens",
    ]
    result = dict(zip(keys, row))
    result["initial_context_tokens"] = (
        result["input_tokens"]
        + result["cache_creation_tokens"]
        + result["cache_read_tokens"]
    )
    return result


def capture_snapshot(conn, label, session_id=None, project=None, generated_at=None):
    baseline = session_baseline(conn, session_id=session_id, project=project)
    if baseline is None:
        raise ValueError("nenhuma sessão com dados de uso encontrada")

    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO benchmark_snapshots
        (generated_at, label, session_id, project, model, event_timestamp,
         input_tokens, output_tokens, cache_creation_tokens,
         cache_creation_5m_tokens, cache_creation_1h_tokens,
         cache_read_tokens, initial_context_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at, label, baseline["session_id"], baseline["project"],
            baseline["model"], baseline["event_timestamp"],
            baseline["input_tokens"], baseline["output_tokens"],
            baseline["cache_creation_tokens"],
            baseline["cache_creation_5m_tokens"],
            baseline["cache_creation_1h_tokens"],
            baseline["cache_read_tokens"], baseline["initial_context_tokens"],
        ),
    )
    conn.commit()
    return {"generated_at": generated_at, "label": label, **baseline}


def load_snapshots(conn, labels=None):
    sql = """
        SELECT id, generated_at, label, session_id, project, model,
               event_timestamp, input_tokens, output_tokens,
               cache_creation_tokens, cache_creation_5m_tokens,
               cache_creation_1h_tokens, cache_read_tokens,
               initial_context_tokens
        FROM benchmark_snapshots
    """
    params = []
    if labels:
        placeholders = ",".join("?" for _ in labels)
        sql += f" WHERE label IN ({placeholders})"
        params.extend(labels)
    sql += " ORDER BY generated_at ASC, id ASC"
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def latest_by_label(rows):
    latest = {}
    for row in rows:
        latest[row["label"]] = row
    return list(latest.values())


def _fmt_int(value):
    return f"{int(value):,}".replace(",", ".")


def format_capture(row):
    lines = [
        f"Benchmark capturado: {row['label']}",
        f"Sessão: {row['session_id']}",
        f"Projeto: {row['project']}",
        f"Modelo: {row['model'] or 'N/D'}",
        f"Contexto inicial: {_fmt_int(row['initial_context_tokens'])}",
        f"  Cache read: {_fmt_int(row['cache_read_tokens'])}",
        f"  Cache write: {_fmt_int(row['cache_creation_tokens'])}",
    ]
    ttl_total = row["cache_creation_5m_tokens"] + row["cache_creation_1h_tokens"]
    if ttl_total:
        lines.append(
            "  Cache write TTL: "
            f"5m={_fmt_int(row['cache_creation_5m_tokens'])} | "
            f"1h={_fmt_int(row['cache_creation_1h_tokens'])}"
        )
    lines.append(f"  Input novo: {_fmt_int(row['input_tokens'])}")
    return "\n".join(lines)


def format_comparison(rows, base_label=None):
    rows = latest_by_label(rows)
    if not rows:
        return "Nenhum benchmark salvo."
    if len(rows) == 1:
        return format_capture(rows[0]) + "\n\nCapture outra configuração para comparar."

    base = None
    if base_label:
        base = next((r for r in rows if r["label"] == base_label), None)
        if base is None:
            raise ValueError(f"baseline '{base_label}' não encontrado")
    else:
        base = rows[0]

    ordered = [base] + [r for r in rows if r is not base]
    header = (
        f"{'Configuração':<24} {'CtxInicial':>11} {'CacheRead':>11} "
        f"{'CacheWrite':>11} {'ΔCtx':>11} {'Δ%':>8}"
    )
    lines = [header]
    base_ctx = base["initial_context_tokens"]
    for row in ordered:
        delta = row["initial_context_tokens"] - base_ctx
        pct = (delta / base_ctx * 100.0) if base_ctx else 0.0
        delta_text = "—" if row is base else f"{delta:+d}"
        pct_text = "—" if row is base else f"{pct:+.1f}%"
        lines.append(
            f"{row['label']:<24} {row['initial_context_tokens']:>11} "
            f"{row['cache_read_tokens']:>11} {row['cache_creation_tokens']:>11} "
            f"{delta_text:>11} {pct_text:>8}"
        )
    lines.append(f"\nBaseline: {base['label']} ({base['session_id']})")
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark do baseline de contexto do Claude Code")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="captura a primeira inferência de uma sessão")
    capture.add_argument("--label", required=True)
    capture.add_argument("--session-id", default=None)
    capture.add_argument("--project", default=None)

    listing = sub.add_parser("list", help="lista snapshots salvos")
    listing.add_argument("--label", action="append", dest="labels")

    compare = sub.add_parser("compare", help="compara o snapshot mais recente de cada label")
    compare.add_argument("--base", default=None)
    compare.add_argument("--label", action="append", dest="labels")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    try:
        if args.command == "capture":
            row = capture_snapshot(
                conn, args.label, session_id=args.session_id, project=args.project
            )
            print(format_capture(row))
        elif args.command == "list":
            rows = load_snapshots(conn, labels=args.labels)
            print(format_comparison(rows))
        else:
            rows = load_snapshots(conn, labels=args.labels)
            print(format_comparison(rows, base_label=args.base))
    except ValueError as exc:
        raise SystemExit(f"benchmark: {exc}") from exc
    finally:
        conn.close()


if __name__ == "__main__":
    main()
