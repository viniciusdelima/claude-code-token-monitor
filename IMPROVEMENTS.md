# Ideias de melhoria — token-monitor

Levantamento feito em 2026-09-04, cruzando: (1) gaps já conhecidos do nosso
próprio código, (2) o projeto de referência
[Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)
(8.6k stars, v4.0.0), e (3) documentação oficial do Claude Code
(`docs.anthropic.com/en/docs/claude-code/costs` e `.../statusline`).

Não é um plano de implementação — é uma lista priorizada pra decidir o que
vira v2. Nenhum código foi alterado.

---

## 1. Gaps já conhecidos do nosso código (deferidos na v1)

Da revisão final da implementação (ver `DESIGN.md` §9 e histórico de commits):

- Detecção de anomalia é uma série única combinada (`context_size + output_tokens`), não uma série separada por dimensão (input/output/cache cada um com seu próprio z-score). Um pico isolado só em output (muita geração, contexto normal) pode passar batido.
- Sem flag `--db` em `ingest.py`/`insights.py` (só `report.py` tem) — dificulta testar/scriptar contra bancos alternativos.
- `except Exception` genérico em `ingest_file` pode mascarar falha real de disco/DB sob cron (silencioso, exit code 0).
- `dominant_reason` decide por evento isolado, mas o texto que gera ("conversa longa, contexto acumulado") implica padrão multi-turno — não checa isso de fato.
- `--period week` usa `strftime('%Y-W%W', ...)`, que não é numeração ISO de semana.
- Só uma máquina (sem sync), só um usuário (sem agregação de time) — documentado como fora de escopo da v1.

## 2. O que o Claude-Code-Usage-Monitor faz que a gente não faz

Referência: [README do projeto](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) (v4.0.0, licença MIT, mesma fonte de dados — JSONL de sessão).

### 2.1 Captura de dado oficial via statusline (a ideia mais valiosa daqui)

O monitor deles roda `claude-monitor --statusline`, que se instala como hook de status line do Claude Code e captura o campo oficial `rate_limits` que a própria Anthropic calcula (ver seção 3.2). Quando esse dado está fresco, ele **substitui** a estimativa local; quando expira, cai pro estimado com um rótulo indicando isso.

**Por que isso importa pra gente:** hoje nosso `pricing.py` reimplementa do zero uma tabela de preço e recalcula custo por token. O Claude Code já faz esse cálculo pra você (campo `cost.total_cost_usd` do statusline, e o comando `/usage`), inclusive aplicando corretamente o multiplicador de 1.1x de data residency (ver 3.3) — coisa que a gente não faz hoje.

### 2.2 Rótulos de proveniência (`official` / `local_estimate` / `experimental` / `unknown`)

Todo número exportado carrega um rótulo dizendo de onde veio. Se a gente adotar captura via statusline (2.1) só pra alguns dias/sessões, isso vira essencial: sem rótulo, um relatório mistura silenciosamente números "de verdade" (do statusline) com números "estimados" (do nosso `pricing.py`), e ninguém sabe qual é qual.

### 2.3 Burn rate + previsão (janela deles: 5 horas, a nossa seria diária/mensal)

Eles calculam velocidade de consumo (tokens/minuto) dentro da janela de sessão de 5h da assinatura e projetam quando você vai estourar o limite. Isso é complementar à nossa detecção de anomalia: a nossa é **retrospectiva** (dia X foi anômalo comparado ao histórico); a deles é **prospectiva** (no ritmo atual, você vai estourar em N minutos/dias). Dá pra adaptar a ideia pra nossa escala: "no ritmo deste mês, você vai fechar em $X" comparado a um teto configurável.

### 2.4 P90 (percentil 90) pra detectar limite "de fato"

Em vez de um limite fixo hardcoded, eles calculam o P90 do histórico de uso pra sugerir automaticamente qual é o "limite normal" do usuário. Isso é uma alternativa/complemento ao nosso z-score + regra fixa (`insights.py`) — P90 é mais robusto a outliers do que média+desvio padrão quando a distribuição não é normal (uso de token tende a ter cauda longa).

### 2.5 Multi-fonte sem merge indevido

Padrão deles pra multi-máquina: `--data-paths` (lista explícita de diretórios), variável de ambiente `CLAUDE_CONFIG_DIR`, e descoberta automática em WSL — mas **sem** fundir contas/fontes diferentes numa mesma janela de limite. Isso é diretamente relevante pro nosso "v2: multi-máquina" (documentado em `DESIGN.md` §2 como fora de escopo da v1) — é o padrão de referência a seguir quando formos implementar.

### 2.6 Armazém de uso local que sobrevive à limpeza de 30 dias do Claude

Eles enfatizam isso como feature: os logs JSONL do Claude Code são limpos automaticamente depois de ~30 dias, então **qualquer ferramenta que só lê JSONL ao vivo perde histórico**. A gente já resolve isso corretamente (SQLite persistente, idempotente) — só não estava explícito no nosso DESIGN.md que essa é uma razão de existir do projeto, não só um "nice to have". Vale documentar essa motivação explicitamente.

### 2.7 Outras ideias menores deles

- Views prontas: realtime (live), daily, monthly — a gente já cobre daily/weekly/monthly/session/project via `--period`/`--group-by`, mas não tem uma view "ao vivo" (watch mode).
- Persistência de preferências entre execuções (`~/.claude-monitor/last_used.json`) — lembra o último `--plan`/`--theme`/etc. Equivalente pra gente: lembrar o último `--period`/`--group-by` usado.
- Export CSV/JSON do warehouse — a gente só imprime texto hoje.
- Detecção automática de tema claro/escuro do terminal — baixa prioridade pro nosso caso (saída é tabela de texto simples, não TUI rica).

## 3. Insights da documentação oficial do Claude Code

### 3.1 `/usage` já existe e faz o que a gente faz (valida a abordagem, mas mostra o limite dela)

O comando `/usage` do próprio Claude Code mostra, por sessão: tokens por modelo (input/output/cache read/cache write), custo estimado, duração. A frase-chave da doc:

> "Claude Code computes the dollar figure locally from token counts at list price... The figure is an estimate, so for authoritative billing see the Usage page in the Claude Console."

Ou seja: **o próprio Claude Code faz exatamente o que a gente faz** (estimativa local a partir de contagem de token) — não existe uma fonte 100% "oficial" acessível localmente; o dado de verdade fica no Claude Console (billing real). Isso não invalida o projeto, só recalibra a promessa: nosso "custo estimado" já está no mesmo nível de confiança que o `/usage` nativo — não é pior nem melhor, é a mesma categoria de número.

### 3.2 `rate_limits` no statusline — dado que a gente simplesmente não tem hoje

Campo exclusivo de assinantes Pro/Max (não aparece pra quem usa API key pura), exposto via statusline:
```json
"rate_limits": {
  "five_hour": { "used_percentage": 23.5, "resets_at": ... },
  "seven_day": { ... },
  "spend_limit": { ... }
}
```
Isso é a cota da **assinatura** (janela rolante de 5h + semanal + limite de gasto), completamente diferente do que a gente mede (tokens processados via API, JSONL). Se o usuário for assinante Pro/Max (não API pura), esse campo é o dado que realmente importa pra saber "quanto da minha cota eu já usei" — coisa que nenhuma leitura de JSONL consegue derivar, porque a cota não é calculada a partir do texto trocado, é um contador do lado do servidor.

**Implicação prática:** se o objetivo for "não me deixe surpreender com limite batido", capturar esse campo (via um script de statusline que a gente escreve e que grava num log) é mais valioso do que qualquer melhoria na nossa detecção de anomalia — porque anomalia é heurística, `rate_limits` é o número real.

### 3.3 Multiplicador de 1.1x pra data residency

Resposta processada com `inference_geo: "us"` (rate de residência de dados) é cobrada a 1.1x o preço de tabela. O campo `inference_geo` já vem no `usage` de cada mensagem no JSONL (a gente viu isso na inspeção real, ficou `"not_available"` nos seus dados) — só não estamos guardando nem usando. Se algum dia esse campo virar `"us"` pra você, nosso `pricing.py` vai subestimar o custo real em 10% sem avisar.

### 3.4 `modelPricing` — tabela de preço customizada via managed settings

Organizações podem sobrescrever o preço de lista com uma tabela `modelPricing` (multiplicador flat ou override por token, por modelo) em managed settings. Se você (ou a Wiser) um dia tiver contrato com taxa diferente da lista pública, nosso `pricing.py` estático vai estar sistematicamente errado até alguém atualizar manualmente — o Claude Code já resolve isso lendo a config; a gente poderia ler o mesmo arquivo (`~/.claude/settings.json` ou managed settings) se ele existir.

### 3.5 Cache health mais rico que o nosso

O statusline expõe `prompt_cache.hit_ratio`, `cache_write_tokens`, `miss_recache_tokens`, `expected_rebuilds` — métricas cumulativas de saúde do cache que não existem no JSONL por turno (a gente só vê `cache_creation_input_tokens`/`cache_read_input_tokens` por mensagem, não uma taxa de acerto agregada). Isso encaixaria direto na nossa evidência de anomalia (`insights.py`'s `dominant_reason`): em vez de só dizer "contexto cresceu", poderíamos dizer "cache hit ratio caiu de 91% pra 60% nesse período — X tokens a mais recriando contexto".

### 3.6 Atribuição por MCP server e por `/loop`

O `/usage` avançado do Claude Code mostra atribuição de uso por MCP server conectado (cada um contribui tokens de definição de tool em toda mensagem) e por tarefa de `/loop`/scheduled task. A gente já captura `tool_names` por evento — daria pra agrupar por prefixo `mcp__<server>__...` (convenção real, vimos isso nos nossos próprios dados) num relatório "custo por MCP server", sem precisar de dado novo, só uma nova view em `report.py`.

### 3.7 Claude Code Analytics API (Enterprise) — caminho oficial pro "v2 multi-usuário"

Pra organizações Enterprise, existe uma Analytics API (`platform.claude.com/docs/en/build-with-claude/claude-code-analytics-api`) que retorna métricas diárias por usuário via Admin API key — é o caminho "certo" caso o projeto expanda pra time inteiro (nosso `DESIGN.md` já lista isso como fora de escopo da v1, mas hoje sem saber que existe um endpoint oficial pronto pra isso ao invés de reinventar agregação central).

## 4. Recomendações priorizadas

**Ganhos baratos (poucas linhas, alto valor, sem nova infraestrutura):**
1. Capturar e guardar `inference_geo` no schema; aplicar o multiplicador 1.1x em `pricing.py` quando for uma região de data residency (§3.3).
2. Nova view em `report.py`: custo/tokens agrupado por prefixo de MCP server extraído de `tool_names` (§3.6) — dado já existe, só falta a query.
3. Lembrar o último `--period`/`--group-by` usado (arquivo tipo `~/.claude/token-monitor/last_used.json`), inspirado em §2.7.
4. Ler `modelPricing` de `~/.claude/settings.json` se existir, e usar em vez da tabela estática quando presente (§3.4).

**Média complexidade, alto valor:**
5. Script de captura via statusline hook (§2.1, §3.2) — grava `cost.total_cost_usd` e `rate_limits.*` (quando existir, só assinantes Pro/Max) num log append-only separado; `ingest.py` passa a ler duas fontes (JSONL + esse log) e rotula proveniência (§2.2) na hora de reportar.
6. Métrica de cache health (`hit_ratio`, tokens de rebuild) capturada via statusline, incorporada na evidência de anomalia (§3.5) — precisa da mesma infra do item 5.
7. Detecção de limite com P90 como alternativa ao z-score atual (§2.4) — só precisa de dado que já temos (SQLite), sem infra nova.

**Maior escopo (feature nova, não bugfix):**
8. Anomalia por dimensão separada (input/output/cache cada um com seu z-score) — item já registrado como deferido na v1 (§1).
9. Burn rate + projeção "no ritmo atual, você fecha o mês em $X" (§2.3) — complementa a anomalia retrospectiva com uma prospectiva.
10. Export CSV/JSON do relatório (§2.7).

**Fora de escopo por ora (mencionado só por completude):**
- Multi-máquina / multi-usuário — já documentado como v2 futura; §2.5 e §3.7 dão o padrão de referência pra quando chegar a hora.
- Watch/live mode em terminal (TUI rica) — nosso caso de uso é relatório sob demanda, não monitoramento contínuo; baixo valor pro jeito que você usa hoje.
