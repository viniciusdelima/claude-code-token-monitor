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


def test_get_connection_has_inference_geo_column(tmp_path):
    db_path = tmp_path / "usage.db"
    conn = db.get_connection(db_path)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}

    assert "inference_geo" in columns
    conn.close()


def test_get_connection_migrates_pre_existing_db_missing_inference_geo(tmp_path):
    db_path = tmp_path / "usage.db"
    old_conn = sqlite3.connect(str(db_path))
    old_conn.executescript(
        """
        CREATE TABLE usage_events (
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
        """
    )
    old_conn.execute(
        """
        INSERT INTO usage_events (uuid, session_id, project, cwd, timestamp)
        VALUES ('u1', 's1', 'p1', '/tmp/p1', '2026-09-04T00:00:00Z')
        """
    )
    old_conn.commit()
    old_conn.close()

    conn = db.get_connection(db_path)  # must not raise migrating an old DB

    columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}
    assert "inference_geo" in columns
    row = conn.execute("SELECT inference_geo FROM usage_events WHERE uuid = 'u1'").fetchone()
    assert row == (None,)
    conn.close()
