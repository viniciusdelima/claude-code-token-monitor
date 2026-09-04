"""Configura o statusline pra mostrar o contador do token-report.

Idempotente e seguro pra rodar de novo:
- Se `combined-statusline.sh` já chama `statusline.py`, não mexe em nada.
- Se ele existir mas não chamar `statusline.py`, faz backup (.bak) e injeta
  o bloco marcado (BEGIN/END token-report) preservando o resto do script.
- Se ele não existir, cria do zero (detecta e integra caveman-statusline.sh
  se estiver instalado, senão só o token-report).
- Aponta `statusLine` do ~/.claude/settings.json pro combined-statusline.sh,
  sem tocar em mais nenhuma chave do settings.json.

Rodar: `python3 ~/.claude/tools/token-monitor/setup_statusline.py`
"""
import json
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
COMBINED_SCRIPT = HOOKS_DIR / "combined-statusline.sh"
CAVEMAN_SCRIPT = HOOKS_DIR / "caveman-statusline.sh"
TOKREPORT_SCRIPT = Path(__file__).resolve().parent / "statusline.py"
SETTINGS_PATH = CLAUDE_DIR / "settings.json"

BEGIN_MARKER = "# >>> token-report statusline >>>"
END_MARKER = "# <<< token-report statusline <<<"

TOKREPORT_BLOCK = f"""{BEGIN_MARKER}
TOKREPORT_OUT=$(python3 "{TOKREPORT_SCRIPT}" 2>/dev/null <<<"$PAYLOAD")
[ -n "$TOKREPORT_OUT" ] && PARTS+=("$TOKREPORT_OUT")
{END_MARKER}"""

FRESH_TEMPLATE = """#!/bin/bash
# combined-statusline — gerado por token-monitor/setup_statusline.py.
#
# stdin (o payload JSON que o Claude Code manda pro comando de statusline)
# é um stream único -- lê uma vez aqui e reenvia pra cada filho que precisa,
# já que um pipe só pode ser consumido uma vez.

PAYLOAD=$(cat)

PARTS=()
{caveman_line}
{tokreport_block}

OUT=""
for p in "${{PARTS[@]}}"; do
  if [ -z "$OUT" ]; then
    OUT="$p"
  else
    OUT="$OUT  |  $p"
  fi
done

printf '%s' "$OUT"
"""

CAVEMAN_LINE_TEMPLATE = (
    'CAVEMAN_OUT=$(bash "{path}" 2>/dev/null <<<"$PAYLOAD")\n'
    '[ -n "$CAVEMAN_OUT" ] && PARTS+=("$CAVEMAN_OUT")'
)


def _already_configured(content: str) -> bool:
    return "statusline.py" in content or "token-monitor" in content


def _write_fresh_script():
    caveman_line = (
        CAVEMAN_LINE_TEMPLATE.format(path=CAVEMAN_SCRIPT)
        if CAVEMAN_SCRIPT.exists()
        else "# caveman-statusline.sh não encontrado -- pulando badge caveman"
    )
    content = FRESH_TEMPLATE.format(
        caveman_line=caveman_line,
        tokreport_block=TOKREPORT_BLOCK,
    )
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    COMBINED_SCRIPT.write_text(content, encoding="utf-8")
    print(f"criado {COMBINED_SCRIPT}")


def _inject_into_existing_script():
    original = COMBINED_SCRIPT.read_text(encoding="utf-8")
    backup = COMBINED_SCRIPT.with_suffix(".sh.bak")
    backup.write_text(original, encoding="utf-8")

    lines = original.splitlines()
    # injeta antes da primeira linha que monta a saída final (procura o
    # primeiro uso de PARTS ou, na falta dele, acrescenta no fim antes do
    # último printf/echo)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if "PARTS" in line or line.strip().startswith(("printf", "echo")):
            insert_at = i
            break

    new_lines = lines[:insert_at] + [TOKREPORT_BLOCK] + lines[insert_at:]
    COMBINED_SCRIPT.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"bloco token-report injetado em {COMBINED_SCRIPT} (backup em {backup})")


def ensure_combined_script():
    if not COMBINED_SCRIPT.exists():
        _write_fresh_script()
        return
    content = COMBINED_SCRIPT.read_text(encoding="utf-8")
    if _already_configured(content):
        print(f"{COMBINED_SCRIPT} já chama o token-report, nada a fazer")
        return
    _inject_into_existing_script()


def ensure_settings_statusline():
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}

    desired = {
        "type": "command",
        "command": f'bash "{COMBINED_SCRIPT}"',
    }

    if settings.get("statusLine") == desired:
        print("settings.json já aponta statusLine pro combined-statusline.sh")
        return

    backup = SETTINGS_PATH.with_suffix(".json.bak")
    if SETTINGS_PATH.exists():
        backup.write_text(SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    settings["statusLine"] = desired
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"settings.json atualizado (backup em {backup})" if SETTINGS_PATH.exists() else "settings.json criado")


def main():
    ensure_combined_script()
    ensure_settings_statusline()
    print("statusline do token-report configurado.")


if __name__ == "__main__":
    main()
