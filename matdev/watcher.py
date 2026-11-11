"""ジョブの見張り。同じ投入で作ったジョブ (温度スキャンなど) が全部終わったら、会話に知らせて続きを頼む。

見張るのは、設定ファイルで jobs を書いたアプリだけ。状態は jobs.status の道具で聞く。
"""
from __future__ import annotations

import asyncio

from . import db as database
from . import hub, settings, works
from .agent import Agent
from .tools import ToolFailed, Toolbox


async def _status(app, job_id: str) -> tuple[str, str]:
    rule = app.config.jobs
    try:
        raw_id = int(job_id) if job_id.isdigit() else job_id
        job = await app.call_json(rule["status"], {rule.get("id_arg", "job_id"): raw_id})
        return str(job.get(rule.get("status_field", "status"))), str(job.get(rule.get("message_field", "message")) or "")
    except ToolFailed as exc:
        return "missing", str(exc)


async def _save_jobs(db, conversation_id: int, app, job_ids: list[str]) -> None:
    """終わったジョブの結果を、いまの作業のディレクトリに置く (作業がなければ何もしない)。"""
    work = works.for_conversation(db, conversation_id)
    if work is None or not (app.config.export or {}).get("job"):
        return
    for job_id in job_ids:
        try:
            saved = await works.export(app, work, "job", job_id)
        except (ToolFailed, works.WorkError, ValueError) as exc:
            hub.add_item(db, conversation_id, "notice", {"text": f"ジョブ {job_id} の結果を作業に置けませんでした: {exc}",
                                                         "level": "warn"})
            continue
        skipped = saved.get("skipped") or []
        extra = f" (大きいので置かなかったファイル: {', '.join(s['name'] for s in skipped)})" if skipped else ""
        hub.add_item(db, conversation_id, "notice", {
            "text": f"ジョブ {job_id} の結果を作業「{work['name']}」の jobs/{app.name}-{job_id}/ に置きました{extra}", "level": "info"})


async def check(toolbox: Toolbox, agent: Agent) -> None:
    """未報告のジョブを 1 回見て回る。"""
    db = database.connect()
    try:
        rows = db.execute("SELECT * FROM watches WHERE reported = 0 ORDER BY app, job_id").fetchall()
        batches: dict[str, list] = {}
        for row in rows:
            batches.setdefault(row["batch"], []).append(row)
        for batch, members in batches.items():
            app = toolbox.apps.get(members[0]["app"])
            if app is None or not app.config.jobs:
                continue   # 設定から外したアプリは見張らない
            done = set(app.config.jobs.get("done", ["done", "failed", "cancelled"]))
            states = [(row["job_id"], *(await _status(app, row["job_id"]))) for row in members]
            if not all(status in done or status == "missing" for _, status, _ in states):
                continue
            conversation_id = members[0]["conversation_id"]
            for job_id, status, _ in states:
                db.execute("UPDATE watches SET status = ?, reported = 1 WHERE app = ? AND job_id = ?",
                           (status, app.name, job_id))
            listing = "、".join(f"{app.name} のジョブ {job_id}: {status}" for job_id, status, _ in states)
            hub.add_item(db, conversation_id, "notice", {"text": f"計算が終わりました ({listing})", "level": "info"})
            for job_id, status, message in states:   # 結果 (グラフ・図・構造の時間変化) を会話の中で開けるカード
                if status != "missing":
                    hub.add_item(db, conversation_id, "job", {"app": app.name, "job_id": job_id, "status": status,
                                                              "message": message[:300], "open": len(states) == 1})
            await _save_jobs(db, conversation_id, app, [job_id for job_id, status, _ in states if status == "done"])
            detail = "\n".join(f"- {app.name} のジョブ {job_id}: {status} {message}" for job_id, status, message in states)
            agent.enqueue(conversation_id, f"[自動の通知] 投入した計算が終わりました。\n{detail}\n"
                                           "結果を読み、要点を報告し、ノートに記録してください。", notice=True)
    finally:
        db.close()


async def run(toolbox: Toolbox, agent: Agent) -> None:
    while True:
        await asyncio.sleep(settings.WATCH_SECONDS)
        try:
            await check(toolbox, agent)
        except Exception as exc:  # 見張りが止まると通知が来なくなるので落とさない
            print(f"[watcher] {type(exc).__name__}: {exc}", flush=True)
