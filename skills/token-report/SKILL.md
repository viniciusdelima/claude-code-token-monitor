---
name: token-report
description: Relatório de tokens gastos no Claude Code (dia/semana/mês/sessão/projeto), com custo estimado, uso/contexto por subagente e detecção de anomalia com evidência concreta. Use quando o usuário pedir "quanto gastei de token", "relatório de uso de tokens", "/token-report", ou perguntar sobre consumo/custo do Claude Code.
---

Gera o relatório de uso de tokens do Claude Code nesta máquina, a partir dos
logs de sessão em `~/.claude/projects/`, incluindo os JSONL de sidechains em
`<session>/subagents/agent-<agentId>.jsonl`.

Argumentos aceitos após `/token-report` (todos opcionais):
- período: `day` (default), `week`, `month`
- `--group-by`: `day` (default), `session`, `project`
- `--since YYYY-MM-DD`
- `--subagents [--session <session-id>]`: mostra uso real executado dentro de
  cada subagente, separado por `agentId`, incluindo inferências, input, output,
  cache write, cache read, tokens totais e custo. Não confundir com a categoria
  `Task`, que mede apenas a inferência que despacha o subagente no agente pai.
- `--subagents --context [--since <since>] [--session <session-id>]`: além do
  consumo real, analisa a evolução da janela interna de cada subagente. Mostra
  contexto inicial/médio/pico/final, cache-read share, tokens por inferência,
  crescimento positivo observado, quedas/resets e até 8 segmentos cronológicos
  por agente com contexto médio/pico e cache read médio por inferência.
- `benchmark --label <nome>`: captura o baseline da primeira inferência da sessão atual e salva como snapshot nomeado. Use uma sessão nova para cada configuração do harness.
- `benchmark compare [--base <nome>]`: compara o snapshot mais recente de cada configuração; `--base` escolhe a referência.
- `benchmark list`: lista os benchmarks já capturados.
- `--mcp-servers`: em vez do relatório por período, mostra custo/tokens
  agrupado por servidor MCP (`native` = só ferramenta nativa, `mixed` = mais
  de um servidor distinto no mesmo turno). Ignora `--period`/`--group-by`
  quando presente.
- `--diagnose`: diagnóstico de gargalo em três perspectivas complementares:
  1. **tokens processados/custo por tipo de inferência** — MCP externo vs uso
     nativo/contexto e, dentro do nativo, despacho de subagente/Task, skill,
     escrita de código, shell, web, exploração e planejamento.
  2. **crescimento efetivo da janela de contexto** — mede deltas positivos
     entre inferências consecutivas da mesma stream/janela, nunca misturando
     main e subagentes diferentes mesmo quando compartilham `session_id`.
     A primeira inferência de cada stream é baseline; quedas são reportadas
     separadamente e não reduzem o crescimento positivo.
  3. **uso interno dos subagentes** — agrega eventos `isSidechain: true` por
     `agentId`, mostrando inferências e consumo em input/cache/output/custo.
  O `--diagnose` mantém apenas o resumo de subagentes. Para curva detalhada de
  janela interna use `/token-report --subagents --context`.
- `report.py --native-categories [--since <since>]`: só o recorte de tokens
  processados por tipo de inferência nativa.
- `subagent_report.py [--context] [--since <since>] [--session <session-id>]`:
  visão de consumo real por subagente; com `--context`, inclui evolução da
  janela interna de cada `agentId`.
- `context_growth.py [--period <period>] [--since <since>]`: crescimento
  efetivo da janela por stream e origem/turno precedente.
- `insights.py --history [--period <period>]`: lista snapshots anteriores.
- `--setup-statusline`: configura/reconfigura o contador no statusline.

## Statusline

Na primeira vez que este skill for usado nesta máquina, ou quando pedido
explicitamente, rode:

`python3 ~/.claude/tools/token-monitor/setup_statusline.py`

Se o usuário só pediu relatório normal, não rode o setup automaticamente toda
vez.

## Benchmark do baseline do harness

Quando o primeiro argumento for `benchmark`, não gere o relatório normal nem
rode `insights.py`.

- Captura: `python3 ~/.claude/tools/token-monitor/ingest.py`, depois
  `python3 ~/.claude/tools/token-monitor/benchmark.py capture --label <nome>`.
- Comparação: `python3 ~/.claude/tools/token-monitor/benchmark.py compare [--base <nome>]`.
- Lista: `python3 ~/.claude/tools/token-monitor/benchmark.py list`.

## Passos

1. Ingerir dados novos (idempotente):
   `python3 ~/.claude/tools/token-monitor/ingest.py`

2. Se o usuário pediu `--subagents --context`:
   `python3 ~/.claude/tools/token-monitor/subagent_report.py --context [--since <since>] [--session <session-id>]`
   - Apresente a saída completa.
   - Explique que `context_size = input + cache write + cache read`; output não
     entra no tamanho da janela.
   - Cada `agent_id` é uma stream independente.
   - Cache read é contexto processado/reutilizado; alto cache read não significa
     o mesmo volume de contexto novo.
   - Crescimentos/quedas são observacionais; não rotule toda queda como
     `/compact` manual.
   - Tokens altos não significam automaticamente desperdício.
   - Pule os demais passos.

3. Se o usuário pediu `--subagents` sem `--context`:
   `python3 ~/.claude/tools/token-monitor/subagent_report.py [--since <since>] [--session <session-id>]`
   - Apresente a saída completa.
   - Explique que os totais representam inferências executadas dentro dos JSONL
     dos subagentes, não apenas o turno `Task` do agente pai.
   - Pule os demais passos.

4. Se o usuário pediu diagnóstico/gargalo/insights de redução:
   - `python3 ~/.claude/tools/token-monitor/insights.py --history [--period <period>]`
   - `python3 ~/.claude/tools/token-monitor/insights.py --diagnose [--period <period>] [--since <since>]`
   - `python3 ~/.claude/tools/token-monitor/context_growth.py [--period <period>] [--since <since>]`
   - `python3 ~/.claude/tools/token-monitor/subagent_report.py [--since <since>]`
   - Apresente as três saídas em seções separadas.
   - Não descreva `sem ferramenta` como texto gerado ou causa direta.
   - Não descreva `Task` como custo completo dos subagentes.
   - No crescimento, deixe claro que o delta é da mesma stream e atribuído ao
     turno precedente; pode combinar resposta, tool result e novo input.
   - Sugira `--subagents --context` quando for útil investigar inflação de
     janela/reprocessamento.
   - Pule os passos seguintes.

5. Senão, gerar relatório normal:
   - `--mcp-servers`: `python3 ~/.claude/tools/token-monitor/report.py --mcp-servers`
   - caso contrário: `python3 ~/.claude/tools/token-monitor/report.py --period <period> --group-by <group_by> [--since <since>]`

6. Checar anomalia recente:
   `python3 ~/.claude/tools/token-monitor/insights.py`

7. Apresentar a tabela; se houver anomalia, anexar a evidência sem inventar
números.

Se qualquer passo falhar, reporte o erro ao usuário em vez de inventar dados.
