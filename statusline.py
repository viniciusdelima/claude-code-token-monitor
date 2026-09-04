"""Contador compacto de tokens (hoje + sessão + contexto atual) pro statusline.

Lê o payload JSON que o Claude Code manda via stdin pro comando de
statusline (traz session_id e context_window), ingere só o suficiente pra
manter o contador vivo sem reprocessar tudo a cada render, e imprime uma
linha curta tipo:

    🔥 tokens totais hoje 128k | 💬 tokens nesta sessão 12k | 🧠 contexto atual 94.7k/1000k (9%)

Cores (ANSI 256) e ícone de contexto mudam de verde -> amarelo -> vermelho
conforme a % de uso, pra chamar atenção quando a janela está enchendo.

O tamanho de contexto atual vem direto do payload (context_window) -- é o
próprio Claude Code quem calcula isso, não dá pra derivar do banco.

Qualquer falha (db ausente, payload vazio, etc) imprime nada e sai 0 --
statusline não pode quebrar por causa disso.
"""
import json
import sys
import time
from pathlib import Path

import db
import ingest

STATE_DIR = Path.home() / ".claude" / "token-monitor"
THROTTLE_FILE = STATE_DIR / ".statusline_last_ingest"
THROTTLE_SECONDS = 5

TOTAL_EXPR = (
    "input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens"
)

RESET = "\033[0m"
COLOR_TODAY = "\033[38;5;214m"    # laranja vivo
COLOR_SESSION = "\033[38;5;207m"  # magenta vivo
COLOR_CTX_LOW = "\033[38;5;46m"   # verde
COLOR_CTX_MID = "\033[38;5;226m"  # amarelo
COLOR_CTX_HIGH = "\033[38;5;196m"  # vermelho

CONTEXT_MID_TOKENS = 80_000
CONTEXT_WARN_TOKENS = 120_000


def _color(code, text):
    return f"{code}{text}{RESET}"


def _read_payload():
    try:
        raw = sys.stdin.read()
        if not raw:
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _maybe_ingest():
    """Reingest at most once every THROTTLE_SECONDS so the counter stays
    live without re-reading every JSONL file on every statusline render."""
    now = time.time()
    try:
        last = THROTTLE_FILE.stat().st_mtime
    except OSError:
        last = 0
    if now - last < THROTTLE_SECONDS:
        return
    try:
        conn = db.get_connection()
        ingest.ingest_all(conn)
        conn.close()
    except Exception:
        pass
    try:
        THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        THROTTLE_FILE.touch()
    except OSError:
        pass


def _fmt(n):
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _context_part(payload):
    ctx = payload.get("context_window") or {}
    size = ctx.get("context_window_size")
    usage = ctx.get("current_usage") or {}
    used = (
        (usage.get("input_tokens") or 0)
        + (usage.get("output_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    if not size:
        return None
    pct = ctx.get("used_percentage")
    pct_str = f" ({pct}%)" if pct is not None else ""

    if used >= CONTEXT_WARN_TOKENS:
        color = COLOR_CTX_HIGH
    elif used >= CONTEXT_MID_TOKENS:
        color = COLOR_CTX_MID
    else:
        color = COLOR_CTX_LOW

    text = f"🧠 contexto atual {_fmt(used)}/{_fmt(size)}{pct_str}"

    return _color(color, text)


def main():
    payload = _read_payload()
    session_id = payload.get("session_id")
    _maybe_ingest()

    try:
        conn = db.get_connection()
        today_total = conn.execute(
            f"SELECT SUM({TOTAL_EXPR}) FROM usage_events "
            "WHERE date(timestamp) = date('now')"
        ).fetchone()[0] or 0

        session_total = 0
        if session_id:
            session_total = conn.execute(
                f"SELECT SUM({TOTAL_EXPR}) FROM usage_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0] or 0
        conn.close()
    except Exception:
        return

    parts = [
        _color(COLOR_TODAY, f"🔥 tokens totais hoje {_fmt(today_total)}"),
        _color(COLOR_SESSION, f"💬 tokens nesta sessão {_fmt(session_total)}"),
    ]
    ctx_part = _context_part(payload)
    if ctx_part:
        parts.append(ctx_part)

    print(" | ".join(parts), end="")


if __name__ == "__main__":
    main()
