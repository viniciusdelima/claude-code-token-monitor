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
- `--mcp-servers`: em vez do relatório por período, mostra custo/tokens
  agrupado por servidor MCP (`native` = só ferramenta nativa, `mixed` = mais
  de um servidor distinto no mesmo turno). Ignora `--period`/`--group-by`
  quando presente.
- `--diagnose`: diagnóstico de gargalo — de onde vem o gasto (MCP externo vs
  uso nativo/contexto), quais sessões do período estão acima da média (com
  motivo provável: contexto acumulado, leitura grande num turno, ou resposta
  longa) e a anomalia vs histórico de 30 dias. Cada execução salva um
  snapshot local e mostra a comparação com o snapshot anterior do mesmo
  `--period`, para acompanhar se o gargalo está piorando ou melhorando entre
  execuções. Aceita `--period`/`--since` como o relatório normal (sem
  `--since`, `--period day` usa o dia de hoje automaticamente).
- `insights.py --history [--period <period>]`: lista os snapshots de
  diagnóstico já salvos para o período dado (mais recente primeiro), para
  comparar entre execuções sem gerar um novo diagnóstico.

## Passos

1. Ingerir dados novos (idempotente, seguro rodar sempre):
   `python3 ~/.claude/tools/token-monitor/ingest.py`

2. Se o usuário pediu diagnóstico/gargalo/insights de redução (ex: "onde está
   o gargalo", "estou gastando acima da média", "como reduzir tokens"):
   - Histórico de execuções anteriores: `python3 ~/.claude/tools/token-monitor/insights.py --history [--period <period>]`
   - Novo diagnóstico (gera e persiste um snapshot, mostrando a comparação
     com o snapshot anterior do mesmo período):
     `python3 ~/.claude/tools/token-monitor/insights.py --diagnose [--period <period>] [--since <since>]`
   - Apresente a saída completa (gargalo MCP/nativo, sessões acima da média
     com motivo, anomalia, comparação com execução anterior) sem resumir os
     números. Pule os passos 3-5 abaixo.

3. Senão, gerar o relatório:
   - Se o usuário passou `--mcp-servers`: `python3 ~/.claude/tools/token-monitor/report.py --mcp-servers`
   - Senão: `python3 ~/.claude/tools/token-monitor/report.py --period <period> --group-by <group_by> [--since <since>]`
     (se `<period>`/`<group_by>` não foram passados pelo usuário, pode omitir
     as flags — o script reusa o último valor salvo automaticamente)

4. Checar anomalia no período recente (30 dias):
   `python3 ~/.claude/tools/token-monitor/insights.py`

5. Apresentar ao usuário a tabela do passo 3. Se o passo 4 imprimir algo
   (não fica em branco), anexar como seção separada "Anomalia detectada",
   com a evidência exatamente como veio na saída do script — não resumir os
   números.

Se qualquer passo falhar (ex: `ModuleNotFoundError`, banco não existe ainda),
reportar o erro ao usuário em vez de inventar números.
