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
  tool_names TEXT,
  inference_geo TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_events(project);

CREATE TABLE IF NOT EXISTS diagnosis_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generated_at TEXT NOT NULL,
  period TEXT NOT NULL,
  since TEXT,
  total_tokens INTEGER NOT NULL,
  native_share REAL NOT NULL,
  top_external_server TEXT,
  session_count INTEGER NOT NULL,
  outlier_count INTEGER NOT NULL,
  mean_session_tokens REAL NOT NULL,
  report_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnosis_period_time ON diagnosis_snapshots(period, generated_at);
"""

DEFAULT_DB_PATH = Path.home() / ".claude" / "token-monitor" / "usage.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    try:
        conn.execute("ALTER TABLE usage_events ADD COLUMN inference_geo TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists (fresh DBs get it from SCHEMA_SQL above)
    return conn
