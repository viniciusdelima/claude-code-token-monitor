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
