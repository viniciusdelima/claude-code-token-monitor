# claude-code-token-monitor

Monitoramento local de tokens gastos no [Claude Code](https://claude.com/claude-code): relatório diário/semanal/mensal/por sessão/por projeto, custo estimado em USD, e detecção de anomalia de consumo com evidência concreta (qual sessão, qual ferramenta, quanto contexto).

100% local. Sem dependência de terceiros (só a stdlib do Python). Nenhum dado sai da sua máquina — o script só lê os logs de sessão que o próprio Claude Code já grava em `~/.claude/projects/`.

## Como funciona

- `ingest.py` — lê os JSONL de sessão do Claude Code, extrai uso de token por turno (input/output/cache write/cache read, modelo, ferramentas usadas — nunca conteúdo de arquivo ou de tool_result) e grava num SQLite local, de forma idempotente.
- `report.py` — agrega o SQLite num relatório de tokens/custo por dia, semana, mês, sessão ou projeto.
- `insights.py` — olha os últimos 30 dias e sinaliza dias fora do padrão (z-score se tiver histórico suficiente, regra fixa como fallback), com evidência: qual sessão/projeto/ferramenta pesou mais, e o caminho do arquivo de log original pra você investigar. Com `--diagnose`, também aponta o gargalo (MCP externo vs uso nativo/contexto), sessões acima da média com motivo provável, e persiste um snapshot local pra comparar com a execução anterior (`--history` lista os snapshots salvos).
- `pricing.py` — tabela de preço por modelo (mantida manualmente, sem chamada de API de preço).

Design completo em [`DESIGN.md`](DESIGN.md).

## Instalação

```bash
git clone https://github.com/viniciusdelima/claude-code-token-monitor.git ~/.claude/tools/token-monitor
cd ~/.claude/tools/token-monitor
python3 -m pytest   # opcional, confirma que tudo passa na sua máquina
```

Requer Python 3.10+ (só stdlib: `sqlite3`, `statistics`, `argparse`, `json`, `pathlib`).

### Skill do Claude Code (`/token-report`)

Pra usar como comando `/token-report` dentro do Claude Code:

```bash
mkdir -p ~/.claude/skills/token-report
cp skills/token-report/SKILL.md ~/.claude/skills/token-report/SKILL.md
```

(ou `ln -s` em vez de `cp`, se quiser manter atualizado automaticamente com o repo)

## Uso

```bash
python3 ingest.py                                   # ingere dados novos (idempotente)
python3 report.py --period week --group-by project   # relatório
python3 report.py --mcp-servers                      # custo/tokens por MCP server
python3 insights.py                                  # checa anomalia nos últimos 30 dias
python3 insights.py --diagnose                        # gargalo + sessões acima da média + comparação com execução anterior
python3 insights.py --diagnose --period week --since 2026-08-25
python3 insights.py --history                         # histórico de diagnósticos salvos (mais recente primeiro)
```

Se você não passar `--period`/`--group-by`, `report.py` reusa o último valor
que você passou (salvo em `~/.claude/token-monitor/last_used.json`) — só cai
pro default (`day`/`day`) se nunca tiver rodado antes.

Ou, dentro do Claude Code, depois de instalar a skill:

```
/token-report week --group-by project
```

### Automatizar com cron

```
0 23 * * * /usr/bin/python3 ~/.claude/tools/token-monitor/ingest.py >> ~/.claude/token-monitor/ingest.log 2>&1
```

(garanta que `~/.claude/token-monitor/` existe antes — `mkdir -p ~/.claude/token-monitor` — senão o redirect do cron falha antes do Python criar o diretório sozinho.)

## Licença

MIT — veja [LICENSE](LICENSE).
