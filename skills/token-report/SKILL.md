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
- `benchmark --label <nome>`: captura o baseline da primeira inferência da sessão atual e salva como snapshot nomeado. Use uma sessão nova para cada configuração do harness.
- `benchmark compare [--base <nome>]`: compara o snapshot mais recente de cada configuração; `--base` escolhe a referência.
- `benchmark list`: lista os benchmarks já capturados.
- `--mcp-servers`: em vez do relatório por período, mostra custo/tokens
  agrupado por servidor MCP (`native` = só ferramenta nativa, `mixed` = mais
  de um servidor distinto no mesmo turno). Ignora `--period`/`--group-by`
  quando presente.
- `--diagnose`: diagnóstico de gargalo em duas perspectivas complementares:
  1. **tokens processados/custo por tipo de inferência** — MCP externo vs uso
     nativo/contexto e, dentro do nativo, subagente/Task, skill, escrita de
     código, shell, web, exploração e planejamento. Essa visão atribui todo o
     contexto processado da inferência à categoria daquele turno; portanto
     "sem ferramenta" não significa que texto puro gerou todos aqueles tokens.
  2. **crescimento efetivo da janela de contexto** — mede o delta positivo
     entre inferências consecutivas da mesma sessão e atribui o delta ao turno
     anterior. A primeira inferência é baseline e deltas negativos de
     `/clear`/compactação são reportados separadamente, sem reduzir o crescimento
     positivo. A atribuição é observacional (turno precedente), não proveniência
     byte a byte.
  Também mostra sessões do período acima da média e a anomalia vs histórico de
  30 dias. Cada execução salva um snapshot local do diagnóstico principal e
  mostra a comparação com o snapshot anterior do mesmo `--period`.
- `report.py --native-categories [--since <since>]`: só o recorte de tokens
  processados por tipo de inferência nativa, sem o resto do diagnóstico.
- `context_growth.py [--period <period>] [--since <since>]`: só o crescimento
  efetivo da janela de contexto por origem/turno precedente, incluindo os
  maiores saltos observados.
- `insights.py --history [--period <period>]`: lista os snapshots de
  diagnóstico já salvos para o período dado (mais recente primeiro), para
  comparar entre execuções sem gerar um novo diagnóstico.
- `--setup-statusline`: configura (ou reconfigura) o contador no statusline
  sem gerar relatório nenhum. Roda
  `python3 ~/.claude/tools/token-monitor/setup_statusline.py` e mostra a
  saída ao usuário.

## Statusline (contador no rodapé)

Na primeira vez que este skill for usado nesta máquina (ou sempre que o
usuário pedir explicitamente `--setup-statusline` / "configura o statusline"
/ "mostra os tokens no rodapé"), rode, antes de qualquer outro passo:

`python3 ~/.claude/tools/token-monitor/setup_statusline.py`

Esse script é idempotente e seguro rodar de novo — se o statusline já está
configurado, ele só confirma e não mexe em nada; senão, cria/ajusta
`~/.claude/hooks/combined-statusline.sh` (com backup `.bak` se já existir
algo lá) e aponta `statusLine` do `~/.claude/settings.json` pra ele (também
com backup). Isso liga um contador no rodapé do Claude Code com:
- 🔥 tokens totais gastos hoje
- 💬 tokens gastos nesta sessão
- 🧠 tamanho da janela de contexto atual (usado/total e %), em verde
  (<80k tokens), amarelo (80k–120k) ou vermelho (≥120k)

Se o usuário só pediu o relatório normal (sem mencionar statusline), não
rode esse setup automaticamente toda vez — só na primeira execução deste
skill na sessão/máquina (detectável perguntando ao usuário ou checando se
`~/.claude/hooks/combined-statusline.sh` já existe e menciona
`token-monitor`/`statusline.py`) ou quando pedido explicitamente.

## Benchmark do baseline do harness

Quando o primeiro argumento for `benchmark`, não gere o relatório normal nem rode `insights.py`.

- Captura: primeiro rode `python3 ~/.claude/tools/token-monitor/ingest.py`, depois
  `python3 ~/.claude/tools/token-monitor/benchmark.py capture --label <nome>`.
- Comparação: `python3 ~/.claude/tools/token-monitor/benchmark.py compare [--base <nome>]`.
- Lista: `python3 ~/.claude/tools/token-monitor/benchmark.py list`.

O benchmark mede somente a primeira inferência da sessão (`input + cache write + cache read`), para isolar o piso de contexto do harness. Para um A/B confiável, abra uma sessão nova para cada configuração e use sempre a mesma probe curta antes da captura. O script não chama o Claude e não gera inferências extras.

## Passos

1. Ingerir dados novos (idempotente, seguro rodar sempre):
   `python3 ~/.claude/tools/token-monitor/ingest.py`

2. Se o usuário pediu diagnóstico/gargalo/insights de redução (ex: "onde está
   o gargalo", "estou gastando acima da média", "como reduzir tokens"):
   - Histórico de execuções anteriores: `python3 ~/.claude/tools/token-monitor/insights.py --history [--period <period>]`
   - Novo diagnóstico principal (gera e persiste snapshot):
     `python3 ~/.claude/tools/token-monitor/insights.py --diagnose [--period <period>] [--since <since>]`
   - Crescimento efetivo da janela (não persiste snapshot):
     `python3 ~/.claude/tools/token-monitor/context_growth.py [--period <period>] [--since <since>]`
   - Apresente **as duas saídas completas**, em seções separadas:
     - `Tokens processados / custo por tipo de inferência`
     - `Crescimento efetivo da janela de contexto`
   - Não descreva a categoria `sem ferramenta` como "texto gerado" ou como
     causa direta de crescimento; ela significa apenas que não havia
     ferramenta registrada naquela inferência.
   - No crescimento efetivo, deixe claro que o delta é atribuído ao turno
     anterior e pode combinar resposta do Claude, resultado de tool e novo
     input do usuário.
   - Pule os passos 3-5 abaixo.

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
