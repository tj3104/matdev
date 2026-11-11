"""利用者が画面で変える設定 (アシスタントの名前など)。Web 版と Claude Code の入口の両方が読む。"""
from __future__ import annotations

import sqlite3

DEFAULT_NAME = "ジャービス"
NAME_MAX = 20


def assistant_name(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT value FROM preferences WHERE key = 'assistant_name'").fetchone()
    return row["value"] if row else DEFAULT_NAME


def set_assistant_name(db: sqlite3.Connection, name: str) -> str:
    name = " ".join(name.split())   # 改行や連続した空白は 1 つの空白に
    if not 1 <= len(name) <= NAME_MAX:
        raise ValueError(f"名前は 1〜{NAME_MAX} 文字にしてください")
    db.execute("INSERT INTO preferences (key, value) VALUES ('assistant_name', ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name,))
    return name
