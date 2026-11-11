"""道具の実行の関門。Web 版 (agent.py) と Claude Code 用の窓口 (gateway.py) の両方がここを通る。

確認の取り方は入口ごとに違うので confirm として受け取る。confirm は拒否されたら ToolFailed を投げる。
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from . import hub, materials, notes, policy, settings, works
from .tools import ToolFailed, Toolbox

Confirm = Callable[[str, str], Awaitable[None]]
Announce = Callable[[str], None]


def pick(data, path: str) -> list:
    """"jobs[].id" のような道筋で値を集める (ジョブの番号を投入の結果から取り出す)。"""
    values = [data]
    for part in path.split("."):
        many = part.endswith("[]")
        key = part[:-2] if many else part
        nxt = []
        for value in values:
            if isinstance(value, dict) and key in value:
                item = value[key]
                nxt.extend(item if many and isinstance(item, list) else [item])
        values = nxt
    return values


class Gate:
    def __init__(self, toolbox: Toolbox) -> None:
        self.toolbox = toolbox

    async def run(self, db, conversation_id: int | None, name: str, args: dict, *, confirm: Confirm,
                  announce: Announce, automated: bool = False) -> str:
        """決まりに従って道具を動かし、結果の文字列を返す。automated は利用者が見ていない自動の処理。"""
        if name == "write_note":
            if args.get("app") and args["app"] not in self.toolbox.apps:
                raise ToolFailed(f"アプリ {args['app']} はつないでいません ({', '.join(self.toolbox.apps)})")
            return json.dumps(notes.write(db, conversation_id, args))
        if name == "search_notes":
            return json.dumps(notes.search(db, args["query"]), ensure_ascii=False)
        if name in works.NAMES:
            try:
                return await works.run_tool(db, self.toolbox, name, args, conversation_id)
            except works.WorkError as exc:
                raise ToolFailed(str(exc)) from exc
        app, tool = self.toolbox.resolve(name)
        args = self._in_work(db, conversation_id, app, tool.name, args)
        rule = app.config.estimate
        if rule and tool.name == rule.get("tool"):
            return await self._submit(db, conversation_id, name, app, args, rule, confirm, announce, automated)
        if policy.needs_confirmation(app, tool):
            await confirm(name, policy.describe_call(name, args))
        text = await app.call(tool.name, args)
        shown, created = materials.record(db, conversation_id, app, tool.name, args, text)
        self._show_structures(db, conversation_id, app.name, shown)
        await self._save_structures(db, conversation_id, app, created)
        return text

    @staticmethod
    def _in_work(db, conversation_id: int | None, app, tool_name: str, args: dict) -> dict:
        """設定の work に従い、出力先を会話の作業のディレクトリにし、ファイルの引数を作業の中のパスにする (外は断る)。"""
        rule = (app.config.work or {}).get(tool_name)
        if not rule:
            return args
        work = works.for_conversation(db, conversation_id)
        if work is None:
            raise ToolFailed("この会話の出力先 (作業) が決まっていません。画面で出力先を決めてください")
        args = dict(args)
        try:
            for key in rule.get("paths", []):
                if isinstance(args.get(key), str):
                    args[key] = str(works.resolve(work, args[key]))
            if rule.get("dir"):
                args[rule["dir"]] = str(works.resolve(work, rule.get("subdir", app.name)))
        except works.WorkError as exc:
            raise ToolFailed(f"{exc} ({work['path']} の中のファイルを、作業からの相対パスで渡してください)") from exc
        return args

    @staticmethod
    async def _save_structures(db, conversation_id: int | None, app, structure_ids: list[str]) -> None:
        """取ってきた・作った構造を、いまの作業のディレクトリに書き出す (作業がなければ何もしない)。"""
        work = works.for_conversation(db, conversation_id)
        if work is None or not (app.config.export or {}).get("structure"):
            return
        for structure_id in structure_ids:
            try:
                saved = await works.export(app, work, "structure", structure_id)
            except (ToolFailed, works.WorkError, ValueError) as exc:
                if conversation_id is not None:
                    hub.add_item(db, conversation_id, "notice", {"text": f"構造 {structure_id} を作業に書き出せませんでした: {exc}",
                                                                 "level": "warn"})
                continue
            if conversation_id is not None:
                hub.add_item(db, conversation_id, "notice", {
                    "text": f"構造 {structure_id} を作業「{work['name']}」の structures/ に置きました ({', '.join(saved.get('files', []))})",
                    "level": "info"})

    @staticmethod
    def _show_structures(db, conversation_id: int | None, app_name: str, structure_ids: list[str]) -> None:
        """取ってきた・見せた構造を、会話に 3D のカードとして出す (同じ会話で同じ構造は 1 回)。"""
        if conversation_id is None:
            return   # Claude Code の入口: Web の画面 (空間・資料) が資料から拾って出す
        for structure_id in structure_ids:
            data = json.dumps({"app": app_name, "structure_id": structure_id}, ensure_ascii=False)
            if not db.execute("SELECT 1 FROM items WHERE conversation_id = ? AND type = 'structure' AND data = ?",
                              (conversation_id, data)).fetchone():
                hub.add_item(db, conversation_id, "structure", {"app": app_name, "structure_id": structure_id})

    async def _submit(self, db, conversation_id, name, app, args, rule, confirm, announce, automated: bool) -> str:
        """投入: 必ず見積もりを取り、長ければ確認し、ジョブの見張りが設定されていれば登録する。

        自動の通知から動いているときは利用者が見ていないので、短くても確認を取る。終わる → 投入 → 終わる の
        繰り返しで、無人のまま計算資源と利用料を使い続けるのを防ぐ。
        """
        preview_tool = app.tool(rule["preview"])
        if preview_tool is None:
            raise ToolFailed(f"見積もりの道具 {rule['preview']} が {app.name} にありません (設定を確かめてください)")
        preview = await app.call_json(preview_tool.name, policy.preview_arguments(args, preview_tool))
        summary = policy.describe_submission(name, rule, args, preview)
        seconds = preview.get(rule.get("seconds_field", "estimated_seconds"))
        limit = rule.get("confirm_over_seconds", settings.AUTO_RUN_SECONDS)
        if automated or not isinstance(seconds, (int, float)) or seconds > limit:
            await confirm(name, summary)
        else:
            announce(f"投入します: {summary}")
        submitted = await app.call_json(rule["tool"], args)
        jobs = app.config.jobs
        if jobs and conversation_id is not None:
            ids = [i for i in pick(submitted, jobs.get("ids", "jobs[].id")) if i is not None]
            batch = f"{conversation_id}-{app.name}-{ids[0]}" if ids else None
            for job_id in ids:
                db.execute("INSERT OR REPLACE INTO watches (app, job_id, conversation_id, batch) VALUES (?, ?, ?, ?)",
                           (app.name, str(job_id), conversation_id, batch))
        materials.record(db, conversation_id, app, rule["tool"], args, json.dumps(submitted, ensure_ascii=False),
                         submitted=True)
        digest = {key: preview.get(key) for key in [*rule.get("summary", []), rule.get("seconds_field", "estimated_seconds"),
                                                    "notes"] if key in preview}
        return json.dumps({**submitted, "preview": digest} if isinstance(submitted, dict) else
                          {"result": submitted, "preview": digest}, ensure_ascii=False)
