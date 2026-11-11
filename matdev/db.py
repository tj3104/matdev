"""SQLite。会話の履歴 (messages) は LLM に送る形のまま追記だけする。画面に出す内容は items に持つ。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, type TEXT NOT NULL,
    data TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS confirmations (id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, tool TEXT NOT NULL,
    summary TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (app TEXT NOT NULL, job_id TEXT NOT NULL, conversation_id INTEGER NOT NULL,
    batch TEXT NOT NULL, status TEXT, reported INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (app, job_id));
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, conversation_id INTEGER, title TEXT NOT NULL,
    purpose TEXT NOT NULL, conditions TEXT NOT NULL, findings TEXT NOT NULL, job_ids TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, app TEXT);
CREATE TABLE IF NOT EXISTS materials (id INTEGER PRIMARY KEY, conversation_id INTEGER, app TEXT NOT NULL,
    tool TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL, jobs TEXT NOT NULL, created_at TEXT NOT NULL,
    structures TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS materials_conversation ON materials (conversation_id);
CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS works (id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, conversation_id INTEGER, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(*, expire_pending: bool = True) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.DATA_DIR.chmod(0o700)   # 会話とパスワードのハッシュが入るので、ほかのユーザーに見せない
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # 複数のアプリに対応する前の見張り (watched_jobs) は MatSim のものとして移す
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'watched_jobs'").fetchone():
            conn.execute("INSERT OR IGNORE INTO watches (app, job_id, conversation_id, batch, status, reported) "
                         "SELECT 'matsim', CAST(job_id AS TEXT), conversation_id, batch, status, reported FROM watched_jobs")
            conn.execute("DROP TABLE watched_jobs")
        # ノートにアプリの名前がなかったころのもの (app は空のまま。読むときに既定のアプリとして扱う)
        if "app" not in {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}:
            conn.execute("ALTER TABLE notes ADD COLUMN app TEXT")
        if "structure_ids" not in {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}:   # 構造も記録する前のノート
            conn.execute("ALTER TABLE notes ADD COLUMN structure_ids TEXT NOT NULL DEFAULT '[]'")
        # 会話ごとの出力先 (作業) がなかったころの会話は、開いたときに決めてもらう
        if "work_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}:
            conn.execute("ALTER TABLE conversations ADD COLUMN work_id INTEGER")
        # 構造の記録がなかったころの資料
        if "structures" not in {row["name"] for row in conn.execute("PRAGMA table_info(materials)")}:
            conn.execute("ALTER TABLE materials ADD COLUMN structures TEXT NOT NULL DEFAULT '[]'")
        if expire_pending:   # 再起動の前に答えを待っていた確認は、もう誰も待っていない
            conn.execute("UPDATE confirmations SET status = 'expired' WHERE status = 'pending'")
    finally:
        conn.close()


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def item_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "type": row["type"], "data": json.loads(row["data"]), "created_at": row["created_at"]}
