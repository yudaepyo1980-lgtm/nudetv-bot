"""그룹방 오늘 채팅 수를 사람별로 세어 순위를 만든다 (SQLite 저장)."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.db")


def _conn():
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS counts ("
        "day TEXT, chat_id INTEGER, user_id INTEGER, name TEXT, n INTEGER, "
        "PRIMARY KEY (day, chat_id, user_id))"
    )
    return c


def today():
    return datetime.now(KST).strftime("%Y-%m-%d")


def add(chat_id, user_id, name):
    with _conn() as c:
        c.execute(
            "INSERT INTO counts VALUES (?,?,?,?,1) "
            "ON CONFLICT(day, chat_id, user_id) DO UPDATE SET n = n + 1, name = excluded.name",
            (today(), chat_id, user_id, name),
        )


def ranking(chat_id, limit=10):
    with _conn() as c:
        return c.execute(
            "SELECT user_id, name, n FROM counts WHERE day=? AND chat_id=? ORDER BY n DESC, user_id LIMIT ?",
            (today(), chat_id, limit),
        ).fetchall()


def my_stat(chat_id, user_id):
    """(오늘 채팅수, 순위) — 오늘 채팅이 없으면 (0, None)"""
    with _conn() as c:
        row = c.execute(
            "SELECT n FROM counts WHERE day=? AND chat_id=? AND user_id=?", (today(), chat_id, user_id)
        ).fetchone()
        if not row:
            return 0, None
        higher = c.execute(
            "SELECT COUNT(*) FROM counts WHERE day=? AND chat_id=? AND n > ?", (today(), chat_id, row[0])
        ).fetchone()[0]
        return row[0], higher + 1
