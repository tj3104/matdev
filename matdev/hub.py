"""画面への配信。接続中のブラウザに流すだけで、残すものは items に入れる。"""
from __future__ import annotations

import asyncio
import json
import sqlite3

from .db import item_dict, now

_listeners: dict[int, set[asyncio.Queue]] = {}


def publish(conversation_id: int, event: dict) -> None:
    for queue in _listeners.get(conversation_id, ()):
        queue.put_nowait(event)


def listen(conversation_id: int) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _listeners.setdefault(conversation_id, set()).add(queue)
    return queue


def unlisten(conversation_id: int, queue: asyncio.Queue) -> None:
    _listeners.get(conversation_id, set()).discard(queue)


def add_item(db: sqlite3.Connection, conversation_id: int, kind: str, data: dict) -> int:
    cursor = db.execute("INSERT INTO items (conversation_id, type, data, created_at) VALUES (?, ?, ?, ?)",
                        (conversation_id, kind, json.dumps(data, ensure_ascii=False), now()))
    row = db.execute("SELECT * FROM items WHERE id = ?", (cursor.lastrowid,)).fetchone()
    publish(conversation_id, {"event": "item", "item": item_dict(row)})
    return cursor.lastrowid
