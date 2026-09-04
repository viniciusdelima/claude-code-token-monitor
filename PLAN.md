# Token Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal, local token-usage monitor for Claude Code: parse
session JSONL logs into SQLite, report tokens/cost by day/week/month/session/
project, and flag anomalous-consumption days with concrete evidence (which
session, which tool, how much context).

**Architecture:** Four flat Python modules (`pricing.py`, `db.py`,
`ingest.py`, `report.py`, `insights.py`) in `~/.claude/tools/token-monitor/`,
no package/`__init__.py`, no third-party dependencies (stdlib only:
`sqlite3`, `statistics`, `argparse`, `json`, `pathlib`). Data persists in
`~/.claude/token-monitor/usage.db` (separate from the code directory).
A personal skill (`~/.claude/skills/token-report/SKILL.md`) wires the CLIs
together for `/token-report`.

**Tech Stack:** Python 3.10 stdlib, SQLite, pytest 9.1.1 (already installed).

**Spec:** `/home/viniciusdelima/.claude/tools/token-monitor/DESIGN.md`

## Global Constraints

- No third-party Python dependencies — stdlib only (`sqlite3`, `statistics`,
  `argparse`, `json`, `pathlib`, `re` if needed). (Spec §4)
- Data DB lives at `~/.claude/token-monitor/usage.db`, separate from the code
  dir `~/.claude/tools/token-monitor/`. (Spec §4)
- `uuid` is the primary key on `usage_events` — ingest must be idempotent
  (`INSERT OR IGNORE`), safe to re-run any number of times. (Spec §5, §6)
- Never persist raw tool-call content (file contents, tool_result bodies) —
  only tool **names** (`tool_names` column). This is a deliberate
  security decision, not a placeholder to fill in later. (Spec §9, §13)
- Cost is always an *estimate* from the static `pricing.py` table; an
  unrecognized model must degrade to "unknown cost" without crashing the
  script. (Spec §8)
- All file paths in code are built from `pathlib.Path`, not string
  concatenation.

---

## File Structure

```
~/.claude/tools/token-monitor/
├── DESIGN.md              (already exists)
├── PLAN.md                (this file)
├── .gitignore
├── pricing.py             # MODEL_PRICING table + estimate_cost_usd()
├── db.py                  # schema + get_connection()
├── ingest.py              # JSONL -> usage_events (idempotent)
├── report.py              # usage_events -> aggregated text report
├── insights.py            # anomaly detection + concrete evidence
└── tests/
    ├── conftest.py        # puts the parent dir on sys.path
    ├── fixtures/
    │   └── sample_session.jsonl
    ├── test_pricing.py
    ├── test_db.py
    ├── test_ingest.py
    ├── test_report.py
    └── test_insights.py

~/.claude/skills/token-report/
└── SKILL.md               # /token-report skill wiring the three CLIs

~/.claude/token-monitor/
└── usage.db               # created at runtime, not part of the repo
```

Each module has one responsibility: `pricing.py` never touches SQLite,
`db.py` never touches JSONL, `ingest.py` never computes cost or anomalies,
`report.py` never parses JSONL, `insights.py` never writes to the DB.

---

## Task 0: Project scaffolding + git init

**Files:**
- Create: `/home/viniciusdelima/.claude/tools/token-monitor/.gitignore`
- Create: `/home/viniciusdelima/.claude/tools/token-monitor/tests/__init__.py` (empty — not needed, skip; pytest doesn't require it)

**Interfaces:**
- Produces: a git repo at `~/.claude/tools/token-monitor/` so later tasks can
  commit; `tests/` and `tests/fixtures/` directories exist.

- [ ] **Step 1: Create directories**

```bash
mkdir -p /home/viniciusdelima/.claude/tools/token-monitor/tests/fixtures
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 3: Init git repo**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git init
git add DESIGN.md PLAN.md .gitignore
git commit -m "chore: scaffold token-monitor project"
```

- [ ] **Step 4: Verify**

Run: `git -C /home/viniciusdelima/.claude/tools/token-monitor log --oneline`
Expected: one commit, `chore: scaffold token-monitor project`.

---

## Task 1: `pricing.py` — cost table and estimator

**Files:**
- Create: `pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Produces: `MODEL_PRICING: dict[str, dict[str, float]]` (keys per model:
  `input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`, all
  USD per 1,000,000 tokens); `estimate_cost_usd(model: str, input_tokens: int,
  output_tokens: int, cache_creation_tokens: int, cache_read_tokens: int) ->
  float | None` — returns `None` for a model not in `MODEL_PRICING`.

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

`tests/test_pricing.py`:

```python
import pricing


def test_known_model_cost_matches_manual_calculation():
    cost = pricing.estimate_cost_usd(
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # 2.00 (input) + 10.00 (output) + 2.50 (cache write 5m) + 0.20 (cache read)
    assert cost == 14.70


def test_zero_tokens_cost_zero():
    cost = pricing.estimate_cost_usd(
        model="claude-opus-5",
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost == 0.0


def test_unknown_model_returns_none():
    cost = pricing.estimate_cost_usd(
        model="some-future-model",
        input_tokens=100,
        output_tokens=100,
        cache_creation_tokens=0,
        cache_read_tokens=0,
    )
    assert cost is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_pricing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pricing'`.

- [ ] **Step 3: Write `pricing.py`**

```python
MODEL_PRICING = {
    "claude-opus-5": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-6": {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10, "output": 5.00},
    "claude-fable-5-1": {"input": 10.00, "cache_write_5m": 12.50, "cache_write_1h": 20.00, "cache_read": 0.25, "output": 50.00},
    "claude-fable-5": {"input": 10.00, "cache_write_5m": 12.50, "cache_write_1h": 20.00, "cache_read": 1.00, "output": 50.00},
}


def estimate_cost_usd(model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens):
    prices = MODEL_PRICING.get(model)
    if prices is None:
        return None
    # cache_creation_tokens isn't split by TTL in usage_events, so this
    # approximates every cache write at the 5-minute rate (the common case).
    total = (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + cache_creation_tokens * prices["cache_write_5m"]
        + cache_read_tokens * prices["cache_read"]
    )
    return total / 1_000_000
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_pricing.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add pricing.py tests/conftest.py tests/test_pricing.py
git commit -m "feat: add model pricing table and cost estimator"
```

---

## Task 2: `db.py` — schema and connection

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SCHEMA_SQL: str`; `DEFAULT_DB_PATH: pathlib.Path`
  (`~/.claude/token-monitor/usage.db`); `get_connection(db_path: pathlib.Path
  = DEFAULT_DB_PATH) -> sqlite3.Connection` — creates parent directories,
  opens the connection, applies `SCHEMA_SQL` (idempotent, `CREATE TABLE IF
  NOT EXISTS`), returns the connection. Later tasks rely on the table name
  `usage_events` and its exact columns: `uuid, session_id, project, cwd,
  timestamp, model, input_tokens, output_tokens, cache_creation_tokens,
  cache_read_tokens, thinking_tokens, tool_names`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3

import db


def test_get_connection_creates_schema(tmp_path):
    db_path = tmp_path / "sub" / "usage.db"
    conn = db.get_connection(db_path)

    assert db_path.exists()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "usage_events" in tables
    conn.close()


def test_get_connection_is_idempotent(tmp_path):
    db_path = tmp_path / "usage.db"
    db.get_connection(db_path).close()
    conn = db.get_connection(db_path)  # must not raise on second call

    conn.execute(
        """
        INSERT INTO usage_events
        (uuid, session_id, project, cwd, timestamp)
        VALUES ('u1', 's1', 'p1', '/tmp/p1', '2026-09-04T00:00:00Z')
        """
    )
    conn.commit()
    row = conn.execute("SELECT uuid FROM usage_events WHERE uuid = 'u1'").fetchone()
    assert row == ("u1",)
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`.

- [ ] **Step 3: Write `db.py`**

```python
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_events (
  uuid TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  cwd TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  thinking_tokens INTEGER NOT NULL DEFAULT 0,
  tool_names TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_events(project);
"""

DEFAULT_DB_PATH = Path.home() / ".claude" / "token-monitor" / "usage.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_db.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add db.py tests/test_db.py
git commit -m "feat: add SQLite schema and connection helper"
```

---

## Task 3: `ingest.py` — JSONL line parsing

**Files:**
- Create: `ingest.py`
- Create: `tests/fixtures/sample_session.jsonl`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: nothing runtime (pure parsing functions).
- Produces: `extract_tool_names(content_blocks: list[dict]) -> str` (comma-
  joined unique `tool_use` names, in first-seen order, `""` if none);
  `parse_line(line: str) -> dict | None` — returns a dict with exactly the
  keys `uuid, session_id, project, cwd, timestamp, model, input_tokens,
  output_tokens, cache_creation_tokens, cache_read_tokens, thinking_tokens,
  tool_names` (matching `db.py`'s `usage_events` columns) or `None` if the
  line isn't a usable assistant-with-usage record.

- [ ] **Step 1: Write the fixture and failing tests**

`tests/fixtures/sample_session.jsonl` (three lines: one valid assistant
message with tool use, one non-assistant line, one assistant message with no
`usage` field):

```
{"type": "user", "message": {"role": "user", "content": "hi"}, "sessionId": "sess-1", "timestamp": "2026-09-04T20:30:00.000Z", "cwd": "/home/viniciusdelima/wiser/wiseup-plus"}
{"type": "assistant", "uuid": "uuid-1", "sessionId": "sess-1", "timestamp": "2026-09-04T20:31:32.706Z", "cwd": "/home/viniciusdelima/wiser/wiseup-plus", "message": {"model": "claude-opus-4-7", "content": [{"type": "tool_use", "name": "Read", "input": {}}, {"type": "text", "text": "ok"}], "usage": {"input_tokens": 6, "output_tokens": 188, "cache_creation_input_tokens": 36797, "cache_read_input_tokens": 0, "output_tokens_details": {"thinking_tokens": 83}}}}
{"type": "assistant", "uuid": "uuid-2", "sessionId": "sess-1", "timestamp": "2026-09-04T20:32:00.000Z", "cwd": "/home/viniciusdelima/wiser/wiseup-plus", "message": {"model": "claude-opus-4-7", "content": [{"type": "text", "text": "no usage field"}]}}
```

`tests/test_ingest.py`:

```python
from pathlib import Path

import ingest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


def test_extract_tool_names_dedupes_and_joins():
    content = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "text", "text": "..."},
        {"type": "tool_use", "name": "Bash", "input": {}},
        {"type": "tool_use", "name": "Read", "input": {}},
    ]
    assert ingest.extract_tool_names(content) == "Read,Bash"


def test_extract_tool_names_empty_when_no_tool_use():
    assert ingest.extract_tool_names([{"type": "text", "text": "..."}]) == ""


def test_parse_line_extracts_assistant_with_usage():
    lines = FIXTURE.read_text().splitlines()
    event = ingest.parse_line(lines[1])

    assert event == {
        "uuid": "uuid-1",
        "session_id": "sess-1",
        "project": "wiseup-plus",
        "cwd": "/home/viniciusdelima/wiser/wiseup-plus",
        "timestamp": "2026-09-04T20:31:32.706Z",
        "model": "claude-opus-4-7",
        "input_tokens": 6,
        "output_tokens": 188,
        "cache_creation_tokens": 36797,
        "cache_read_tokens": 0,
        "thinking_tokens": 83,
        "tool_names": "Read",
    }


def test_parse_line_skips_non_assistant():
    lines = FIXTURE.read_text().splitlines()
    assert ingest.parse_line(lines[0]) is None


def test_parse_line_skips_assistant_without_usage():
    lines = FIXTURE.read_text().splitlines()
    assert ingest.parse_line(lines[2]) is None


def test_parse_line_skips_blank_line():
    assert ingest.parse_line("") is None
    assert ingest.parse_line("   \n") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest'`.

- [ ] **Step 3: Write `ingest.py` (parsing part only)**

```python
import json
from pathlib import Path

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def extract_tool_names(content_blocks):
    names = []
    for block in content_blocks:
        if block.get("type") == "tool_use" and block.get("name"):
            if block["name"] not in names:
                names.append(block["name"])
    return ",".join(names)


def parse_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if obj.get("type") != "assistant":
        return None
    message = obj.get("message") or {}
    usage = message.get("usage")
    if not usage:
        return None

    cwd = obj.get("cwd", "")
    project = Path(cwd).name if cwd else "unknown"
    thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)

    return {
        "uuid": obj.get("uuid"),
        "session_id": obj.get("sessionId"),
        "project": project,
        "cwd": cwd,
        "timestamp": obj.get("timestamp"),
        "model": message.get("model"),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "thinking_tokens": thinking,
        "tool_names": extract_tool_names(message.get("content") or []),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_ingest.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add ingest.py tests/fixtures/sample_session.jsonl tests/test_ingest.py
git commit -m "feat: parse Claude Code session JSONL lines into usage events"
```

---

## Task 4: `ingest.py` — idempotent DB load + CLI

**Files:**
- Modify: `ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `db.get_connection` (Task 2), `parse_line`/`extract_tool_names`
  (Task 3).
- Produces: `iter_jsonl_files(root: pathlib.Path) -> list[pathlib.Path]`;
  `ingest_file(conn: sqlite3.Connection, path: pathlib.Path) -> int` (count
  of newly-inserted rows); `ingest_all(conn, projects_root:
  pathlib.Path = DEFAULT_PROJECTS_ROOT) -> int`; `main(argv=None)` — CLI
  entry point that connects to `db.DEFAULT_DB_PATH` and calls `ingest_all`,
  printing the count.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest.py`:

```python
import db


def test_ingest_file_inserts_only_usable_events(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_file(conn, FIXTURE)

    assert inserted == 1  # only the middle line has usage
    row = conn.execute("SELECT uuid FROM usage_events").fetchone()
    assert row == ("uuid-1",)
    conn.close()


def test_ingest_file_is_idempotent(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    first = ingest.ingest_file(conn, FIXTURE)
    second = ingest.ingest_file(conn, FIXTURE)

    assert first == 1
    assert second == 0  # same uuid, already present
    count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    assert count == 1
    conn.close()


def test_iter_jsonl_files_finds_nested_files(tmp_path):
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-a" / "session1.jsonl").write_text("")
    (tmp_path / "proj-b").mkdir()
    (tmp_path / "proj-b" / "session2.jsonl").write_text("")
    (tmp_path / "not-jsonl.txt").write_text("")

    found = ingest.iter_jsonl_files(tmp_path)

    assert {p.name for p in found} == {"session1.jsonl", "session2.jsonl"}


def test_ingest_all_sums_across_files(tmp_path):
    projects_root = tmp_path / "projects"
    proj_dir = projects_root / "wiseup-plus"
    proj_dir.mkdir(parents=True)
    (proj_dir / "session.jsonl").write_text(FIXTURE.read_text())

    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    inserted = ingest.ingest_all(conn, projects_root=projects_root)

    assert inserted == 1
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_ingest.py -v`
Expected: FAIL — `AttributeError: module 'ingest' has no attribute 'ingest_file'` (and similar for `iter_jsonl_files`, `ingest_all`).

- [ ] **Step 3: Add DB-loading functions and CLI to `ingest.py`**

Append to `ingest.py` (keep the existing imports and functions from Task 3;
add `sqlite3` isn't needed directly, but `db` is):

```python
import db


def ingest_file(conn, path):
    inserted = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            event = parse_line(line)
            if event is None or not event["uuid"]:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO usage_events
                (uuid, session_id, project, cwd, timestamp, model,
                 input_tokens, output_tokens, cache_creation_tokens,
                 cache_read_tokens, thinking_tokens, tool_names)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["uuid"], event["session_id"], event["project"],
                    event["cwd"], event["timestamp"], event["model"],
                    event["input_tokens"], event["output_tokens"],
                    event["cache_creation_tokens"], event["cache_read_tokens"],
                    event["thinking_tokens"], event["tool_names"],
                ),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def iter_jsonl_files(root):
    if not root.exists():
        return []
    return sorted(root.glob("**/*.jsonl"))


def ingest_all(conn, projects_root=DEFAULT_PROJECTS_ROOT):
    total = 0
    for path in iter_jsonl_files(projects_root):
        total += ingest_file(conn, path)
    return total


def main(argv=None):
    conn = db.get_connection()
    inserted = ingest_all(conn)
    print(f"Ingested {inserted} new events.")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_ingest.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Manual smoke test against real data**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 ingest.py`
Expected: prints `Ingested N new events.` with `N` roughly matching the
number of assistant turns across `~/.claude/projects/**/*.jsonl` (order of
magnitude in the hundreds/thousands is fine — exact count isn't asserted
here, this is a smoke test, not a unit test).

- [ ] **Step 6: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add ingest.py tests/test_ingest.py
git commit -m "feat: idempotent JSONL-to-SQLite ingest with CLI entry point"
```

---

## Task 5: `report.py` — aggregation and CLI

**Files:**
- Create: `report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `db.get_connection`, `db.DEFAULT_DB_PATH` (Task 2);
  `pricing.estimate_cost_usd` (Task 1). Reads the `usage_events` table
  populated by `ingest.py` (Task 4).
- Produces: `query_report(conn, period="day", group_by="day", since=None) ->
  list[dict]`, each dict with keys `bucket, input_tokens, output_tokens,
  cache_creation_tokens, cache_read_tokens, total_tokens, session_count,
  avg_context, peak_context, cost_usd, cost_unknown`, sorted by `bucket`;
  `format_report(rows: list[dict]) -> str`; `parse_args(argv=None) ->
  argparse.Namespace` with `.period`, `.group_by`, `.since`, `.db`;
  `main(argv=None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report.py
import db
import report


def _seed(conn, rows):
    for row in rows:
        conn.execute(
            """
            INSERT INTO usage_events
            (uuid, session_id, project, cwd, timestamp, model,
             input_tokens, output_tokens, cache_creation_tokens,
             cache_read_tokens, thinking_tokens, tool_names)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
            """,
            row,
        )
    conn.commit()


def test_query_report_groups_by_day_and_sums_tokens(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5", 100, 50, 10, 5),
        ("u2", "s1", "proj", "/p", "2026-09-01T12:00:00Z", "claude-sonnet-5", 200, 100, 20, 10),
        ("u3", "s2", "proj", "/p", "2026-09-02T09:00:00Z", "claude-sonnet-5", 300, 150, 30, 15),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert [r["bucket"] for r in rows] == ["2026-09-01", "2026-09-02"]
    day1 = rows[0]
    assert day1["input_tokens"] == 300
    assert day1["output_tokens"] == 150
    assert day1["cache_creation_tokens"] == 30
    assert day1["cache_read_tokens"] == 15
    assert day1["total_tokens"] == 495
    assert day1["session_count"] == 1
    conn.close()


def test_query_report_computes_known_model_cost(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5",
         1_000_000, 0, 0, 0),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_usd"] == 2.00  # 1M input tokens @ $2/MTok
    assert rows[0]["cost_unknown"] is False
    conn.close()


def test_query_report_flags_unknown_model_cost(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "some-future-model",
         1000, 0, 0, 0),
    ])

    rows = report.query_report(conn, period="day", group_by="day")

    assert rows[0]["cost_unknown"] is True
    conn.close()


def test_query_report_group_by_project(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj-a", "/a", "2026-09-01T10:00:00Z", "claude-sonnet-5", 100, 0, 0, 0),
        ("u2", "s2", "proj-b", "/b", "2026-09-01T10:00:00Z", "claude-sonnet-5", 200, 0, 0, 0),
    ])

    rows = report.query_report(conn, group_by="project")

    buckets = {r["bucket"]: r["input_tokens"] for r in rows}
    assert buckets == {"proj-a": 100, "proj-b": 200}
    conn.close()


def test_query_report_since_filters_older_rows(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-08-01T10:00:00Z", "claude-sonnet-5", 100, 0, 0, 0),
        ("u2", "s1", "proj", "/p", "2026-09-01T10:00:00Z", "claude-sonnet-5", 200, 0, 0, 0),
    ])

    rows = report.query_report(conn, since="2026-09-01")

    assert [r["bucket"] for r in rows] == ["2026-09-01"]
    conn.close()


def test_format_report_handles_empty_rows():
    assert report.format_report([]) == "Sem dados no período."


def test_format_report_renders_a_row():
    rows = [{
        "bucket": "2026-09-01", "total_tokens": 165, "cost_usd": 2.5,
        "cost_unknown": False, "session_count": 1, "avg_context": 50.0,
        "peak_context": 60, "input_tokens": 100, "output_tokens": 50,
        "cache_creation_tokens": 10, "cache_read_tokens": 5,
    }]
    output = report.format_report(rows)
    assert "2026-09-01" in output
    assert "165" in output


def test_parse_args_defaults():
    args = report.parse_args([])
    assert args.period == "day"
    assert args.group_by == "day"
    assert args.since is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 3: Write `report.py`**

```python
import argparse
from pathlib import Path

import db
import pricing

PERIOD_FORMATS = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
}

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"


def _bucket_expr(period, group_by):
    if group_by == "session":
        return "session_id"
    if group_by == "project":
        return "project"
    return f"strftime('{PERIOD_FORMATS[period]}', timestamp)"


def _where_clause(since):
    if since:
        return "WHERE timestamp >= ?", [since]
    return "", []


def _rows(conn, sql, params):
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def query_report(conn, period="day", group_by="day", since=None):
    bucket_expr = _bucket_expr(period, group_by)
    where, params = _where_clause(since)

    stats_sql = f"""
        SELECT {bucket_expr} AS bucket,
               COUNT(DISTINCT session_id) AS session_count,
               AVG({CONTEXT_SIZE_EXPR}) AS avg_context,
               MAX({CONTEXT_SIZE_EXPR}) AS peak_context
        FROM usage_events
        {where}
        GROUP BY bucket
    """
    token_sql = f"""
        SELECT {bucket_expr} AS bucket, model,
               SUM(input_tokens) AS input_tokens,
               SUM(output_tokens) AS output_tokens,
               SUM(cache_creation_tokens) AS cache_creation_tokens,
               SUM(cache_read_tokens) AS cache_read_tokens
        FROM usage_events
        {where}
        GROUP BY bucket, model
    """

    buckets = {}
    for row in _rows(conn, stats_sql, params):
        buckets[row["bucket"]] = {
            "bucket": row["bucket"],
            "session_count": row["session_count"],
            "avg_context": row["avg_context"] or 0.0,
            "peak_context": row["peak_context"] or 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": 0.0,
            "cost_unknown": False,
        }

    for row in _rows(conn, token_sql, params):
        entry = buckets[row["bucket"]]
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
        entry["cache_creation_tokens"] += row["cache_creation_tokens"]
        entry["cache_read_tokens"] += row["cache_read_tokens"]
        cost = pricing.estimate_cost_usd(
            row["model"], row["input_tokens"], row["output_tokens"],
            row["cache_creation_tokens"], row["cache_read_tokens"],
        )
        if cost is None:
            entry["cost_unknown"] = True
        else:
            entry["cost_usd"] += cost

    results = []
    for entry in buckets.values():
        entry["total_tokens"] = (
            entry["input_tokens"] + entry["output_tokens"]
            + entry["cache_creation_tokens"] + entry["cache_read_tokens"]
        )
        results.append(entry)
    results.sort(key=lambda r: r["bucket"])
    return results


def format_report(rows):
    if not rows:
        return "Sem dados no período."
    header = f"{'Período':<14} {'Tokens':>10} {'Custo($)':>10} {'Conversas':>10} {'CtxMédio':>10} {'CtxPico':>10}"
    lines = [header]
    for r in rows:
        cost = "N/D" if r["cost_unknown"] and r["cost_usd"] == 0 else f"{r['cost_usd']:.4f}"
        lines.append(
            f"{r['bucket']:<14} {r['total_tokens']:>10} {cost:>10} "
            f"{r['session_count']:>10} {r['avg_context']:>10.0f} {r['peak_context']:>10}"
        )
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Relatório de tokens do Claude Code")
    parser.add_argument("--period", choices=["day", "week", "month"], default="day")
    parser.add_argument("--group-by", dest="group_by", choices=["day", "session", "project"], default="day")
    parser.add_argument("--since", default=None)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    conn = db.get_connection(Path(args.db))
    rows = query_report(conn, period=args.period, group_by=args.group_by, since=args.since)
    print(format_report(rows))
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_report.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Manual smoke test against real data**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 report.py --period day`
Expected: a text table with one row per day you've used Claude Code recently,
no traceback.

- [ ] **Step 6: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add report.py tests/test_report.py
git commit -m "feat: add token/cost report aggregation and CLI"
```

---

## Task 6: `insights.py` — anomaly detection (z-score + fallback rule)

**Files:**
- Create: `insights.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `db.get_connection` (Task 2). This task's functions take a plain
  `list[tuple[str, float]]` series — no DB dependency yet (added in Task 7).
- Produces: `detect_latest_anomaly(series: list[tuple[str, float]],
  z_threshold: float = 2.0, fixed_multiplier: float = 1.5) -> dict | None`.
  Returned dict (when anomalous) has keys `day, value, mean, method, score`
  where `method` is `"zscore"` or `"fixed_rule"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_insights.py
import insights


def test_no_anomaly_with_less_than_two_points():
    assert insights.detect_latest_anomaly([("2026-09-01", 100)]) is None
    assert insights.detect_latest_anomaly([]) is None


def test_fixed_rule_flags_spike_with_short_history():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 300),  # latest, > 1.5x mean of history (105)
    ]
    result = insights.detect_latest_anomaly(series)
    assert result is not None
    assert result["method"] == "fixed_rule"
    assert result["day"] == "2026-09-04"


def test_fixed_rule_does_not_flag_normal_day():
    series = [
        ("2026-09-01", 100),
        ("2026-09-02", 110),
        ("2026-09-03", 105),
        ("2026-09-04", 108),
    ]
    assert insights.detect_latest_anomaly(series) is None


def test_zscore_flags_spike_with_long_history():
    history = [(f"2026-08-{d:02d}", 100) for d in range(1, 8)]  # 7 days, all 100
    series = history + [("2026-09-01", 500)]  # far above mean/stdev=0 case avoided by variance below
    # Introduce slight variance so stdev isn't zero, then a real spike:
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 500)]

    result = insights.detect_latest_anomaly(series)

    assert result is not None
    assert result["method"] == "zscore"
    assert result["day"] == "2026-09-01"


def test_zscore_does_not_flag_typical_day_with_long_history():
    history = [("2026-08-01", 95), ("2026-08-02", 105), ("2026-08-03", 98),
               ("2026-08-04", 102), ("2026-08-05", 97), ("2026-08-06", 103),
               ("2026-08-07", 100)]
    series = history + [("2026-09-01", 101)]

    assert insights.detect_latest_anomaly(series) is None


def test_zero_variance_history_never_flags():
    history = [("2026-08-0" + str(d), 100) for d in range(1, 8)]
    series = history + [("2026-09-01", 100)]

    assert insights.detect_latest_anomaly(series) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_insights.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'insights'`.

- [ ] **Step 3: Write `insights.py` (detection part only)**

```python
import statistics


def detect_latest_anomaly(series, z_threshold=2.0, fixed_multiplier=1.5):
    if len(series) < 2:
        return None

    *history, latest = series
    latest_day, latest_value = latest
    history_values = [v for _, v in history]
    mean = statistics.mean(history_values)

    if len(history_values) >= 7:
        stdev = statistics.stdev(history_values)
        if stdev == 0:
            return None
        z = (latest_value - mean) / stdev
        if abs(z) > z_threshold:
            return {"day": latest_day, "value": latest_value, "mean": mean, "method": "zscore", "score": z}
        return None

    if mean > 0 and latest_value > mean * fixed_multiplier:
        return {"day": latest_day, "value": latest_value, "mean": mean, "method": "fixed_rule", "score": latest_value / mean}
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_insights.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add insights.py tests/test_insights.py
git commit -m "feat: add z-score/fixed-rule anomaly detection over a daily series"
```

---

## Task 7: `insights.py` — evidence query, report text, CLI

**Files:**
- Modify: `insights.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: `db.get_connection`, `db.DEFAULT_DB_PATH` (Task 2);
  `detect_latest_anomaly` (Task 6). Reads `usage_events` (populated by
  `ingest.py`).
- Produces: `daily_context_series(conn, days: int = 30) ->
  list[tuple[str, float]]`; `top_context_events(conn, day: str, limit: int =
  5) -> list[dict]` (each with `session_id, project, cwd, timestamp,
  tool_names, cache_creation_tokens, cache_read_tokens, context_size`);
  `dominant_reason(event: dict) -> str`; `jsonl_path_for(event: dict) ->
  str`; `build_insight_report(conn, days: int = 30, evidence_limit: int = 5)
  -> str | None` (None when no anomaly); `main(argv=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_insights.py`:

```python
import db


def _seed(conn, rows):
    for row in rows:
        conn.execute(
            """
            INSERT INTO usage_events
            (uuid, session_id, project, cwd, timestamp, model,
             input_tokens, output_tokens, cache_creation_tokens,
             cache_read_tokens, thinking_tokens, tool_names)
            VALUES (?, ?, ?, ?, ?, 'claude-sonnet-5', ?, ?, ?, ?, 0, ?)
            """,
            row,
        )
    conn.commit()


def test_daily_context_series_sums_per_day(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-01T10:00:00Z", 100, 0, 10, 5, "Read"),
        ("u2", "s1", "proj", "/p", "2026-09-01T12:00:00Z", 200, 0, 20, 10, "Bash"),
    ])

    series = insights.daily_context_series(conn, days=30)

    assert series == [("2026-09-01", 345.0)]  # (100+10+5)+(200+20+10)
    conn.close()


def test_top_context_events_orders_by_context_size_desc(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "wiseup-plus", "/home/v/wiser/wiseup-plus", "2026-09-04T10:00:00Z", 10, 0, 5, 0, "Read"),
        ("u2", "s2", "wiseup-plus", "/home/v/wiser/wiseup-plus", "2026-09-04T11:00:00Z", 100, 0, 50, 0, "Bash"),
    ])

    events = insights.top_context_events(conn, "2026-09-04", limit=5)

    assert len(events) == 2
    assert events[0]["session_id"] == "s2"  # 150 > 15
    assert events[0]["context_size"] == 150
    conn.close()


def test_dominant_reason_cache_creation_heavy():
    event = {"cache_creation_tokens": 100, "cache_read_tokens": 10}
    assert "contexto cresceu" in insights.dominant_reason(event)


def test_dominant_reason_cache_read_heavy():
    event = {"cache_creation_tokens": 10, "cache_read_tokens": 100}
    assert "acumulado" in insights.dominant_reason(event)


def test_jsonl_path_for_replaces_slashes_in_cwd():
    event = {"cwd": "/home/viniciusdelima/wiser/wiseup-plus", "session_id": "abc"}
    path = insights.jsonl_path_for(event)
    assert path.endswith("/.claude/projects/-home-viniciusdelima-wiser-wiseup-plus/abc.jsonl")


def test_build_insight_report_returns_none_without_anomaly(tmp_path):
    conn = db.get_connection(tmp_path / "usage.db")
    _seed(conn, [
        ("u1", "s1", "proj", "/p", "2026-09-04T10:00:00Z", 100, 0, 0, 0, ""),
    ])

    assert insights.build_insight_report(conn) is None
    conn.close()


def test_build_insight_report_includes_evidence_on_anomaly(tmp_path, monkeypatch):
    conn = db.get_connection(tmp_path / "usage.db")
    rows = []
    for day in range(1, 8):
        rows.append((f"u{day}", f"s{day}", "proj", "/p", f"2026-08-0{day}T10:00:00Z", 100, 0, 0, 0, ""))
    rows.append(("u-spike", "s-spike", "proj", "/p", "2026-09-01T10:00:00Z", 5000, 0, 0, 0, "Read"))
    _seed(conn, rows)

    report = insights.build_insight_report(conn)

    assert report is not None
    assert "s-spike" in report
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_insights.py -v`
Expected: FAIL — `AttributeError: module 'insights' has no attribute 'daily_context_series'` (and similar for the other new names).

- [ ] **Step 3: Add evidence functions and CLI to `insights.py`**

Append to `insights.py` (keep the Task 6 imports/functions; add):

```python
from pathlib import Path

import db

CONTEXT_SIZE_EXPR = "(input_tokens + cache_creation_tokens + cache_read_tokens)"


def daily_context_series(conn, days=30):
    sql = f"""
        SELECT strftime('%Y-%m-%d', timestamp) AS day,
               SUM({CONTEXT_SIZE_EXPR}) AS total_context
        FROM usage_events
        WHERE timestamp >= date('now', ?)
        GROUP BY day
        ORDER BY day
    """
    cur = conn.execute(sql, (f"-{days} days",))
    return [(row[0], row[1]) for row in cur.fetchall()]


def top_context_events(conn, day, limit=5):
    sql = f"""
        SELECT session_id, project, cwd, timestamp, tool_names,
               cache_creation_tokens, cache_read_tokens,
               {CONTEXT_SIZE_EXPR} AS context_size
        FROM usage_events
        WHERE strftime('%Y-%m-%d', timestamp) = ?
        ORDER BY context_size DESC
        LIMIT ?
    """
    cur = conn.execute(sql, (day, limit))
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def dominant_reason(event):
    if event["cache_creation_tokens"] >= event["cache_read_tokens"]:
        return "contexto cresceu muito num turno (leitura ou tool output grande)"
    return "conversa longa, contexto acumulado alto"


def jsonl_path_for(event):
    project_dir = event["cwd"].replace("/", "-")
    return str(Path.home() / ".claude" / "projects" / project_dir / f"{event['session_id']}.jsonl")


def build_insight_report(conn, days=30, evidence_limit=5):
    series = daily_context_series(conn, days=days)
    anomaly = detect_latest_anomaly(series)
    if anomaly is None:
        return None

    events = top_context_events(conn, anomaly["day"], limit=evidence_limit)

    if anomaly["method"] == "zscore":
        header = (
            f"Dia {anomaly['day']}: desvio de {anomaly['score']:.1f} sigma da média "
            f"({anomaly['value']:.0f} vs média {anomaly['mean']:.0f}) — anomalia detectada."
        )
    else:
        header = (
            f"Dia {anomaly['day']}: {anomaly['score']:.1f}x acima da média "
            f"({anomaly['value']:.0f} vs média {anomaly['mean']:.0f}) — anomalia detectada."
        )

    lines = [header, "Evidência:"]
    for event in events:
        reason = dominant_reason(event)
        lines.append(
            f"  - sessão {event['session_id']} ({event['project']}, {event['timestamp']}) "
            f"— {event['context_size']} tokens de contexto, ferramentas: "
            f"{event['tool_names'] or 'nenhuma'} — {reason}"
        )
        lines.append(f"    arquivo: {jsonl_path_for(event)}")
    return "\n".join(lines)


def main(argv=None):
    conn = db.get_connection()
    report = build_insight_report(conn)
    if report:
        print(report)
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest tests/test_insights.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Run the full test suite**

Run: `cd /home/viniciusdelima/.claude/tools/token-monitor && python3 -m pytest -v`
Expected: all tests across all files PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git add insights.py tests/test_insights.py
git commit -m "feat: add anomaly evidence lookup and insights CLI"
```

---

## Task 8: `/token-report` skill

**Files:**
- Create: `/home/viniciusdelima/.claude/skills/token-report/SKILL.md`

**Interfaces:**
- Consumes: `ingest.py`, `report.py`, `insights.py` CLIs (Tasks 4, 5, 7) —
  invoked as subprocesses via their documented `python3 <script>.py [args]`
  interface, not imported as Python.
- Produces: the `/token-report` slash command available in Claude Code.

- [ ] **Step 1: Create the skill directory and file**

```bash
mkdir -p /home/viniciusdelima/.claude/skills/token-report
```

`/home/viniciusdelima/.claude/skills/token-report/SKILL.md`:

```markdown
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
```

- [ ] **Step 2: Manual verification**

Run: `python3 ~/.claude/tools/token-monitor/ingest.py && python3 ~/.claude/tools/token-monitor/report.py --period day && python3 ~/.claude/tools/token-monitor/insights.py`
Expected: ingest count printed, then a report table, then either nothing
(no anomaly) or an anomaly block — no traceback in any of the three.

- [ ] **Step 3: Commit** (in the `token-monitor` repo only — the skill file
lives outside it, under `~/.claude/skills/`, which is not version-controlled
here; no commit needed for that file)

```bash
cd /home/viniciusdelima/.claude/tools/token-monitor
git status  # confirm nothing left uncommitted in this repo from this task
```

---

## Task 9: Cron entry (manual, documented — not auto-applied)

**Files:**
- Modify: `DESIGN.md` (append a short "Setup" note — optional, skip if it
  feels redundant with §10) — no code changes.

This task is intentionally **manual**: adding a crontab entry changes the
user's system scheduler, which is outside what an implementation task should
do unattended. Present the exact line and let the user add it themselves.

- [ ] **Step 1: Show the user the exact crontab line to add**

```
0 23 * * * /usr/bin/python3 /home/viniciusdelima/.claude/tools/token-monitor/ingest.py >> /home/viniciusdelima/.claude/token-monitor/ingest.log 2>&1
```

- [ ] **Step 2: Confirm `python3` path matches the line above**

Run: `which python3`
Expected: `/usr/bin/python3` (if different, use the actual path in the
crontab line instead).

- [ ] **Step 3: User adds it themselves**

Tell the user to run `crontab -e`, paste the line, save. Do not run
`crontab` on the user's behalf without their explicit go-ahead in the
moment — this changes what runs automatically on their machine.

- [ ] **Step 4: Verify (after the user confirms they added it)**

Run: `crontab -l | grep token-monitor`
Expected: the line from Step 1 is present.

---

## Self-Review Notes

- **Spec coverage:** §3 data source → Task 3; §5 schema → Task 2; §6 ingest
  → Tasks 3-4; §7 report → Task 5; §8 pricing → Task 1; §9 insights/evidence
  → Tasks 6-7; §10 scheduling → Task 9; §11 skill → Task 8; §12 tests →
  woven into every task (TDD); §13 risks → reflected in Global Constraints
  and Task 1's cost-unknown handling. No gaps found.
- **Placeholder scan:** no TBD/TODO, every step has runnable code or an
  exact shell command.
- **Type/name consistency checked:** `usage_events` columns identical across
  `db.py` (Task 2), `ingest.py`'s `parse_line` dict keys (Task 3), and every
  SQL query in `report.py`/`insights.py` (Tasks 5-7). `estimate_cost_usd`
  signature (Task 1) matches every call site in `report.py` (Task 5).
  `detect_latest_anomaly`'s return shape (Task 6) matches every field read
  in `build_insight_report` (Task 7).
