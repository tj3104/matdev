"""プロジェクトのノート: 計算ごとに、何を・なぜ・どの条件で・何が分かったかを残す。"""
from __future__ import annotations

import json
import sqlite3

from .db import now

TOOLS = [
    {"name": "write_note", "eager_input_streaming": True,
     "description": "計算の記録をノートに残す。何を調べ、なぜ、どの条件で、何が分かったかを書く。計算の結果を報告するときに使う。",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["title", "purpose", "conditions", "findings", "job_ids"],
                      "properties": {"title": {"type": "string", "description": "あとで探せる短い題"},
                                     "purpose": {"type": "string", "description": "何を、なぜ調べたか"},
                                     "conditions": {"type": "string", "description": "構造・ポテンシャル・温度などの条件"},
                                     "findings": {"type": "string", "description": "分かったこと。値には単位を付ける。限界も書く"},
                                     "app": {"type": "string",
                                             "description": "ジョブを動かしたアプリの名前 (道具の名前の「__」の前。matsim など)"},
                                     "job_ids": {"type": "array", "items": {"type": ["integer", "string"]},
                                                 "description": "記録する計算のジョブの番号。ノートからグラフを開ける"},
                                     "structure_ids": {"type": "array", "items": {"type": ["integer", "string"]},
                                                       "description": "記録に関わる構造の番号。ノートから構造を 3D で開ける"}}}},
    {"name": "search_notes", "eager_input_streaming": True,
     "description": "過去のノートを語句で探す。query が空なら新しい順に返す。「先週の Ti の結果」のような依頼で使う。",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"],
                      "properties": {"query": {"type": "string"}}}},
]
NAMES = {tool["name"] for tool in TOOLS}


def to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in ("id", "conversation_id", "title", "purpose", "conditions", "findings",
                                      "created_at", "app")} | {"job_ids": json.loads(row["job_ids"]),
                                                     "structure_ids": json.loads(row["structure_ids"] or "[]")}


def write(db: sqlite3.Connection, conversation_id: int | None, args: dict) -> dict:
    stamp = now()
    cursor = db.execute("INSERT INTO notes (conversation_id, title, purpose, conditions, findings, job_ids, "
                        "created_at, updated_at, app, structure_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (conversation_id, args["title"], args["purpose"], args["conditions"], args["findings"],
                         json.dumps(args["job_ids"]), stamp, stamp, args.get("app") or None,
                         json.dumps(args.get("structure_ids") or [])))
    return {"id": cursor.lastrowid}


def search(db: sqlite3.Connection, query: str = "", limit: int = 30) -> list[dict]:
    """すべての語を含むノートを新しい順に返す。"""
    sql, params = "SELECT * FROM notes", []
    words = query.split()
    if words:
        clause = "(title LIKE ? OR purpose LIKE ? OR conditions LIKE ? OR findings LIKE ?)"
        sql += " WHERE " + " AND ".join([clause] * len(words))
        for word in words:
            params += [f"%{word}%"] * 4
    return [to_dict(row) for row in db.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()]
