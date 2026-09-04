# Token Monitor — Design Spec

Data: 2026-09-04
Autor: Vinícius Lima (com Claude Code)
Status: aprovado para implementação

## 1. Objetivo

Monitorar tokens gastos no uso pessoal do Claude Code (WSL, esta máquina), com
persistência local para relatórios (dia/semana/mês/por conversa) e detecção de
anomalias de consumo, com evidência concreta do motivo (contexto grande,
ferramenta usada).

## 2. Escopo

- **Dentro do escopo:** uso do Claude Code por este usuário, nesta máquina.
  Todos os projetos indexados em `~/.claude/projects/**/*.jsonl`.
- **Fora do escopo (por ora):** múltiplas máquinas/sync, múltiplos usuários,
  dashboard web/artifact, API de preço automática, conteúdo bruto de
  tool_result nos relatórios (risco de vazar segredo).
- **Expansão futura prevista:** multi-máquina (sync), multi-usuário (store
  central), dashboard visual.

## 3. Fonte de dados

Cada linha de `type == "assistant"` num JSONL de sessão carrega, entre outros
campos: `uuid` (único por mensagem), `sessionId`, `timestamp`, `cwd`,
`message.model`, `message.usage` (`input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens_details.thinking_tokens`), e `message.content` (array de
blocos, incluindo `tool_use` com campo `name`).

Confirmado via inspeção real de `~/.claude/projects/-home-dev-myproject/*.jsonl`
em 2026-09-04.

## 4. Arquitetura

```
~/.claude/tools/token-monitor/
├── DESIGN.md
├── ingest.py       # parse JSONL → SQLite (idempotente, upsert por uuid)
├── report.py       # agrega SQLite → relatório de tokens/custo por período
├── insights.py     # z-score + regra fixa → anomalias, com evidência
└── pricing.py       # tabela de preço por modelo (constante)

~/.claude/token-monitor/
└── usage.db        # SQLite, dados persistidos (fora do dir de código)
```

Skill pessoal `~/.claude/skills/token-report/SKILL.md` chama os scripts via
`python3` e formata a saída. Runtime: Python 3.10 (stdlib `sqlite3` +
`statistics`, sem dependência externa).

## 5. Schema (SQLite)

```sql
CREATE TABLE usage_events (
  uuid TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  project TEXT NOT NULL,          -- derivado do diretório real do arquivo JSONL, não de cwd
  cwd TEXT NOT NULL,
  timestamp TEXT NOT NULL,        -- ISO8601 UTC, ex: 2026-09-04T20:31:32.706Z
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  thinking_tokens INTEGER NOT NULL DEFAULT 0,
  tool_names TEXT,                -- CSV dos nomes de tool_use naquele turno, ex: "Read,Bash"
  inference_geo TEXT              -- ex: "us" (data residency, cobrado a 1.1x); adicionado via ALTER TABLE em bancos existentes
);
CREATE INDEX idx_usage_ts ON usage_events(timestamp);
CREATE INDEX idx_usage_session ON usage_events(session_id);
CREATE INDEX idx_usage_project ON usage_events(project);
```

Sem tabela de agregação — dia/semana/mês é `GROUP BY strftime(...)` on-the-fly
via view/query SQL (volume baixo, SQLite aguenta sem problema).

`context_size` (proxy de tamanho de contexto processado no turno) é sempre
calculado via expressão SQL, nunca coluna própria:
`input_tokens + cache_creation_tokens + cache_read_tokens`.

## 6. Ingest (`ingest.py`)

- Varre `~/.claude/projects/**/*.jsonl` (todos os projetos).
- Por linha: `json.loads`; se `type == "assistant"` e `message.usage` existe →
  extrai campos do schema; `project` = nome do diretório do `cwd` (último
  segmento do path); `tool_names` = nomes únicos de blocos `tool_use` em
  `message.content`, join por vírgula (vazio se não houver).
- `INSERT OR IGNORE INTO usage_events ... VALUES (...)` com `uuid` como chave
  — idempotente, roda quantas vezes quiser sem duplicar.
- Sem checkpoint/offset — relê tudo a cada execução. Com o volume atual
  (~90 sessões), roda em segundos; se crescer muito, otimizar depois (YAGNI
  agora — não é gargalo hoje).
- Execução: manual (`python3 ingest.py`) ou cron diário.

## 7. Report (`report.py`)

CLI: `python3 report.py --period day|week|month --group-by session|day|project [--since YYYY-MM-DD]`

Saída: tabela texto no stdout com, por linha de período:
- total de tokens (input + output + cache_creation + cache_read)
- tokens por categoria (separado, pra quem quiser detalhe)
- custo estimado em $ (via `pricing.py`, por modelo predominante no grupo;
  se o grupo mistura modelos, soma custo por evento antes de agregar)
- número de conversas (sessões distintas) no período
- contexto médio e contexto pico (`context_size`) no período

## 8. Pricing (`pricing.py`)

Dict Python constante, preço por 1M tokens (USD), por `model` id, com colunas
input / cache_write_5m / cache_write_1h / cache_read / output. Fonte:
`https://platform.claude.com/docs/en/about-claude/pricing` (consultado
2026-09-04). Modelo fora da tabela → custo reportado como `N/D`, sem quebrar
o script. Atualização é manual — sem chamada de API de preço (YAGNI).

| Modelo | Input | Cache write 5m | Cache write 1h | Cache read | Output |
|---|---|---|---|---|---|
| claude-opus-5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 |
| claude-opus-4-8 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 |
| claude-opus-4-7 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 |
| claude-opus-4-6 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 |
| claude-sonnet-5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 |
| claude-sonnet-4-6 | 3.00 | 3.75 | 6.00 | 0.30 | 15.00 |
| claude-haiku-4-5 | 1.00 | 1.25 | 2.00 | 0.10 | 5.00 |
| claude-fable-5-1 | 10.00 | 12.50 | 20.00 | 0.25 | 50.00 |
| claude-fable-5 | 10.00 | 12.50 | 20.00 | 1.00 | 50.00 |

## 9. Insights / detecção de anomalia (`insights.py`)

- Pega série diária dos últimos 30 dias (soma de `context_size` +
  `output_tokens` por dia, a partir de `usage_events`).
- **≥7 dias de histórico:** z-score sobre essa série única combinada —
  `(hoje - média)/desvio`; `|z| > 2` → sinaliza anomalia.
- **Decisão de v1 (revisão final, 2026-09-04):** a ideia original de rodar
  z-score **separado por dimensão** (input/output/cache cada com sua própria
  série e seu próprio limiar) foi simplificada pra uma série única combinada
  em v1 — decisão deliberada, não bug. Custo: um dia com pico isolado só em
  `output_tokens` (muita geração, contexto normal) pode não disparar
  anomalia, já que ele se dilui na soma. Detecção por dimensão fica pra v2,
  se o uso real deste mês mostrar que faz falta.
- **<7 dias de histórico:** regra fixa de fallback — dia atual > 1.5x a
  média móvel disponível (mesmo com poucos pontos).
- Motivo textual por dimensão dominante:
  - `cache_creation_tokens` domina → "contexto cresceu muito num turno".
  - `cache_read_tokens` domina em vários turnos da mesma sessão → "conversa
    longa, contexto acumulado alto".
- **Evidência concreta:** ao marcar um dia como anômalo, query adicional
  "top N eventos por `context_size` naquele período", listando por evento:
  `session_id`, `project`, `timestamp`, `context_size`, `tool_names`, e o
  caminho do arquivo JSONL de origem (`~/.claude/projects/<project>/<session_id>.jsonl`)
  para o usuário abrir e investigar manualmente se quiser.
- **Sem conteúdo bruto de tool_result/mensagem** — decisão explícita: pegar
  só nomes de ferramenta (`tool_names`), nunca o conteúdo, para não arriscar
  vazar segredo de arquivo lido/tool output num relatório persistido (este
  ambiente já tem arquivos de credencial soltos fora do controle de versão).

## 10. Scheduling

Cron diário roda `ingest.py` sozinho:
```
0 23 * * * /usr/bin/python3 ~/.claude/tools/token-monitor/ingest.py >> ~/.claude/token-monitor/ingest.log 2>&1
```
Skill `/token-report` roda `report.py` + `insights.py` (dado já ingerido).
`ingest.py` também pode ser rodado manual a qualquer momento (idempotente).

**Caveat (revisão final, 2026-09-04):** o redirecionamento `>> ~/.claude/token-monitor/ingest.log` é feito pelo shell do cron *antes* do Python rodar — se `~/.claude/token-monitor/` ainda não existir numa máquina nova, esse redirect falha e o cron nunca chega a rodar `ingest.py` (que criaria o diretório sozinho via `mkdir(parents=True)`). Inofensivo aqui (diretório já existe), mas se replicar esse setup numa máquina nova: crie o diretório antes de adicionar a entrada no cron (`mkdir -p ~/.claude/token-monitor`), ou rode `ingest.py` manual uma vez primeiro.

## 11. Interface (skill)

`~/.claude/skills/token-report/SKILL.md` — skill pessoal, invocada via
`/token-report [day|week|month] [--group-by ...]`. Chama `report.py` e, se
houver anomalia no período, anexa a seção de insights com evidência.

## 12. Testes

- Unit: parsing de uma linha JSONL sintética (com `usage` e `tool_use`) →
  valida extração de todos os campos do schema.
- Unit: idempotência do ingest — rodar 2x sobre o mesmo arquivo, contar linhas
  no banco, esperar mesmo total.
- Unit: cálculo de z-score e fallback de regra fixa com séries sintéticas
  (histórico curto e longo).
- Integração: rodar `ingest.py` contra uma cópia de um JSONL real (fixture),
  validar que `report.py` produz números consistentes (soma manual vs. saída
  do script).

## 13. Riscos e decisões explícitas

- **Custo em $ é estimativa**, baseada em tabela de preço mantida manualmente
  — pode ficar desatualizada se a Anthropic mudar preço; aceito por agora.
- **Sem sync multi-máquina** — se o usuário trocar de máquina, dado fica
  fragmentado. Aceito como ponto de expansão futura, não bloqueia o V1.
- **Sem conteúdo bruto de tool call na evidência** — trade-off deliberado
  entre riqueza de diagnóstico e risco de exposição de segredo.

## 14. V2 — ganhos baratos implementados (2026-09-04)

Levantamento completo em [`IMPROVEMENTS.md`](IMPROVEMENTS.md) (comparação com
[Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)
e documentação oficial do Claude Code). Dos itens priorizados como "ganhos
baratos", os 4 abaixo foram implementados:

- **Multiplicador de data residency (1.1x):** `usage.inference_geo` do JSONL
  é capturado e persistido; `pricing.estimate_cost_usd` aplica 1.1x quando
  `inference_geo == "us"`. `report.py` agrupa custo por `(bucket, model,
  inference_geo)` antes de somar, pra não diluir a taxa quando o mesmo modelo
  aparece com e sem residência de dados no mesmo período.
- **Relatório por MCP server:** `report.py --mcp-servers` — atribui cada
  evento a 1 servidor MCP (convenção `mcp__<server>__<tool>` em
  `tool_names`), a `"native"` se só usou ferramenta nativa, ou a `"mixed"`
  se tocou mais de um servidor distinto no mesmo turno (evita duplicar total
  entre buckets).
- **Lembrar último `--period`/`--group-by`:** `~/.claude/token-monitor/last_used.json`.
  Se a flag não é passada explicitamente, usa o último valor salvo (default
  `None` no argparse, não mais `"day"` fixo); CLI explícito sempre tem
  prioridade sobre o salvo.
- **`modelPricing` de managed settings:** `pricing.py` lê
  `/etc/claude-code/managed-settings.json` (escopo *Managed* do Claude
  Code — confirmado na doc oficial, não fica em `~/.claude/settings.json`)
  se existir; aplica `overrides` por modelo e depois `multiplier` geral,
  igual ao comportamento nativo do `/usage`. Cai pra tabela estática se o
  arquivo não existir (caso desta máquina hoje).
