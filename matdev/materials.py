"""資料: 会話の中でジャービスが道具で読んだ・作ったもの (道具の呼び出しと結果、関わったジョブ) を残して、あとから見られるようにする。

関門 (gate.py) を通った道具の呼び出しを、Web 版も Claude Code の入口 (会話の番号なし) も同じ形で残す。
ジョブは、設定で jobs か view を書いたアプリについて、引数の job_id / job_ids (jobs.id_arg に合わせる) と
投入の結果 (jobs.ids の道筋) から拾う。結果は鍵の形の文字列を伏せ、長いものは切って残す。
"""
from __future__ import annotations

import json
import sqlite3

from .db import now
from .redact import scrub

MAX_RESULT_CHARS = 200_000
SUMMARY_CHARS = 120


def job_refs(app, args: dict, result_text: str, submitted: bool) -> list[str]:
    """この呼び出しが関わったジョブの番号。"""
    if not (app.config.jobs or app.config.view):
        return []
    key = (app.config.jobs or {}).get("id_arg", "job_id")
    found = []
    for name, value in args.items():
        if name == key and isinstance(value, (int, str)):
            found.append(value)
        elif name == key + "s" and isinstance(value, list):
            found += [v for v in value if isinstance(v, (int, str))]
    if submitted and app.config.jobs:
        from .gate import pick   # gate がこのモジュールを使うので、ここで読む
        try:
            found += [v for v in pick(json.loads(result_text), app.config.jobs.get("ids", "jobs[].id"))
                      if isinstance(v, (int, str))]
        except ValueError:
            pass
    return list(dict.fromkeys(str(v) for v in found))


def structure_refs(app, tool_name: str, args: dict, result_text: str) -> tuple[list[str], bool]:
    """この呼び出しが関わった構造の番号と、新しく作った (取ってきた) 構造かどうか。設定の view.structure で決まる。"""
    rule = (app.config.view or {}).get("structure")
    if not rule:
        return [], False
    found, created = [], False
    key = rule.get("id_arg", "structure_id")
    value = args.get(key)
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        found.append(value)
    path = rule.get("created_by", {}).get(tool_name)
    if path:
        from .gate import pick
        try:
            made = [v for v in pick(json.loads(result_text), path) if isinstance(v, (int, str))]
        except ValueError:
            made = []
        found += made
        created = bool(made)
    return list(dict.fromkeys(str(v) for v in found)), created


def summarize(args: dict) -> str:
    text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS - 1] + "…"


def record(db: sqlite3.Connection, conversation_id: int | None, app, tool_name: str, args: dict, result_text: str,
           submitted: bool = False) -> tuple[list[str], list[str]]:
    """残して、(画面に出す構造の番号, 新しく作った・取ってきた構造の番号) を返す。"""
    jobs = job_refs(app, args, result_text, submitted)
    structures, created = structure_refs(app, tool_name, args, result_text)
    text = scrub(result_text)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[長いので {MAX_RESULT_CHARS} 文字で切りました]"
    db.execute("INSERT INTO materials (conversation_id, app, tool, args, result, jobs, created_at, structures) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (conversation_id, app.name, tool_name, scrub(json.dumps(args, ensure_ascii=False)), text,
                json.dumps(jobs), now(), json.dumps(structures)))
    rule = (app.config.view or {}).get("structure") or {}
    shows = created or tool_name in (rule.get("tool"), "get_structure")
    return (structures if shows else []), (structures if created else [])


def _row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "conversation_id": row["conversation_id"], "app": row["app"], "tool": row["tool"],
            "summary": summarize(json.loads(row["args"])), "jobs": json.loads(row["jobs"]),
            "structures": json.loads(row["structures"]),
            "size": len(row["result"]), "created_at": row["created_at"],
            "conversation": row["title"] if "title" in row.keys() else None,
            "conversation_deleted": row["conversation_id"] is not None and ("title" in row.keys() and row["title"] is None)}


def _jobs(rows: list[dict]) -> list[dict]:
    """呼び出しの一覧から、ジョブごとにまとめる (新しく触れた順)。"""
    jobs: dict[tuple, dict] = {}
    for row in rows:
        for job_id in row["jobs"]:
            entry = jobs.setdefault((row["app"], job_id), {"app": row["app"], "job_id": job_id, "uses": 0,
                                                           "last": row["created_at"], "tools": [],
                                                           "conversation_id": row["conversation_id"],
                                                           "conversation": row["conversation"],
                                                           "conversation_deleted": row["conversation_deleted"]})
            entry["uses"] += 1
            if row["tool"] not in entry["tools"]:
                entry["tools"].append(row["tool"])
    return list(jobs.values())


SELECT = ("SELECT m.*, c.title FROM materials m LEFT JOIN conversations c ON c.id = m.conversation_id")


def _structures(rows: list[dict]) -> list[dict]:
    """呼び出しの一覧から、構造ごとにまとめる (新しく触れた順)。"""
    found: dict[tuple, dict] = {}
    for row in rows:
        for structure_id in row["structures"]:
            entry = found.setdefault((row["app"], structure_id), {
                "app": row["app"], "structure_id": structure_id, "last": row["created_at"], "material_id": row["id"],
                "tools": [], "conversation_id": row["conversation_id"], "conversation": row["conversation"],
                "conversation_deleted": row["conversation_deleted"]})
            if row["tool"] not in entry["tools"]:
                entry["tools"].append(row["tool"])
    return list(found.values())


def for_conversation(db: sqlite3.Connection, conversation_id: int) -> dict:
    rows = [_row(r) for r in db.execute(SELECT + " WHERE m.conversation_id = ? ORDER BY m.id DESC",
                                        (conversation_id,)).fetchall()]
    return {"jobs": _jobs(rows), "structures": _structures(rows), "calls": rows}


def latest_structures(db: sqlite3.Connection, conversation_id: int | None, limit: int = 10) -> list[dict]:
    """空間に出す構造の候補: この会話のものと、Claude Code の入口で触れたもの (新しい順)。"""
    rows = db.execute(SELECT + " WHERE m.structures != '[]' AND (m.conversation_id IS NULL OR m.conversation_id = ?) "
                      "ORDER BY m.id DESC LIMIT ?", (conversation_id, limit * 3)).fetchall()
    return _structures([_row(r) for r in rows])[:limit]


def everything(db: sqlite3.Connection, query: str = "", limit: int = 300) -> dict:
    """これまでの会話 (Claude Code の入口を含む) の資料。query は道具・アプリ・引数・会話の題に含まれる語。"""
    sql, params = SELECT, []
    words = query.split()
    if words:
        clause = "(m.tool LIKE ? OR m.app LIKE ? OR m.args LIKE ? OR c.title LIKE ? OR m.jobs LIKE ?)"
        sql += " WHERE " + " AND ".join([clause] * len(words))
        for word in words:
            params += [f"%{word}%"] * 5
    rows = [_row(r) for r in db.execute(sql + " ORDER BY m.id DESC LIMIT ?", (*params, limit)).fetchall()]
    return {"jobs": _jobs(rows), "structures": _structures(rows), "calls": rows}


def detail(db: sqlite3.Connection, material_id: int) -> dict | None:
    row = db.execute(SELECT + " WHERE m.id = ?", (material_id,)).fetchone()
    if row is None:
        return None
    result = row["result"]
    try:   # JSON なら読みやすく整える
        result = json.dumps(json.loads(result), ensure_ascii=False, indent=2)
    except ValueError:
        pass
    return _row(row) | {"args": json.loads(row["args"]), "result": result}
