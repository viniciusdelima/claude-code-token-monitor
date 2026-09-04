---
name: token-report
description: Relatório de tokens gastos no Claude Code (dia/semana/mês/sessão/projeto), com custo estimado e detecção de anomalia com evidência concreta. Use quando o usuário pedir "quanto gastei de token", "relatório de uso de tokens", "/token-report", ou perguntar sobre consumo/custo do Claude Code.
---

Gera o relatório de uso de tokens do Claude Code nesta máquina, a partir dos
logs de sessão em `~/.claude/projects/`.

Argumentos aceitos após `/token-report` (todos opcionais):
- período: `day` (default), `week`, `month`
- `--group-by`: `day` (default), `session`, `project`
- `--since YYYY-MM-DD`

## Passos

1. Ingerir dados novos (idempotente, seguro rodar sempre):
   `python3 ~/.claude/tools/token-monitor/ingest.py`

2. Gerar o relatório do período pedido:
   `python3 ~/.claude/tools/token-monitor/report.py --period <period> --group-by <group_by> [--since <since>]`

3. Checar anomalia no período recente (30 dias):
   `python3 ~/.claude/tools/token-monitor/insights.py`

4. Apresentar ao usuário a tabela do passo 2. Se o passo 3 imprimir algo
   (não fica em branco), anexar como seção separada "Anomalia detectada",
   com a evidência exatamente como veio na saída do script — não resumir os
   números.

Se qualquer passo falhar (ex: `ModuleNotFoundError`, banco não existe ainda),
reportar o erro ao usuário em vez de inventar números.
