"""ジャービスの本体。LLM と道具のループを回す。

ループは自分で持つ。道具の実行の前に必ず関門 (gate.py) を通し、確認の答えを待つためである。
LLM は llm/ の部品で差し替え、道具は tools.py (設定ファイルのアプリ) から来る。どちらにも依存しない作りにしてある。

履歴は追記だけにする (Claude の thinking のブロックは書き換えると無効になる)。途中で止まった依頼は、次の依頼の前に
_repair が道具の結果を補って形を整える。
"""
from __future__ import annotations

import asyncio
import functools
import json
import sqlite3

import jsonschema

from . import db as database
from . import hub, notes, settings, usage, works
from .config import Config
from .gate import Gate
from .llm import LLM, LLMError
from .prompt import VOICE_HINT, build_system
from .redact import scrub
from .tools import ToolFailed, Toolbox

class Agent:
    def __init__(self, toolbox: Toolbox, llm: LLM, config: Config) -> None:
        self.toolbox = toolbox
        self.gate = Gate(toolbox)
        self.llm = llm
        self.config = config
        self.name = "ジャービス"
        self.system = build_system(config, "web", self.name)
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: dict[int, set[asyncio.Task]] = {}
        self._waiting: dict[int, asyncio.Future] = {}
        self.warned_month: str | None = None

    # ---- 外から呼ぶもの ------------------------------------------------------------------

    def rename(self, name: str) -> None:
        """呼び名を変える。指示文が変わるので、次の依頼からキャッシュは作り直しになる。"""
        self.name = name
        self.system = build_system(self.config, "web", name)

    def busy(self, conversation_id: int) -> bool:
        return bool(self._tasks.get(conversation_id))

    def enqueue(self, conversation_id: int, text: str, *, notice: bool = False, voice: bool = False,
                viewing: list[str] | None = None) -> None:
        """依頼を受け付ける。同じ会話の依頼は順番に動かす。notice は自動の通知で、利用者の発言として見せない。
        voice は声の入力で、聞き間違いと読み上げに向けた注意を LLM に添える。"""
        task = asyncio.create_task(self._run(conversation_id, text, notice, voice, viewing or []))
        tasks = self._tasks.setdefault(conversation_id, set())
        tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            tasks.discard(done)
            if not tasks:
                hub.publish(conversation_id, {"event": "state", "busy": False})
        task.add_done_callback(finished)
        hub.publish(conversation_id, {"event": "state", "busy": True})

    def stop(self, conversation_id: int) -> None:
        for task in list(self._tasks.get(conversation_id, ())):
            task.cancel()

    def answer(self, confirmation_id: int, approved: bool) -> bool:
        future = self._waiting.get(confirmation_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    async def shutdown(self) -> None:
        tasks = [task for group in self._tasks.values() for task in group]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 1 回の依頼 ----------------------------------------------------------------------

    async def _run(self, conversation_id: int, text: str, notice: bool, voice: bool = False,
                   viewing: list[str] | None = None) -> None:
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        db = database.connect()
        try:
            async with lock:
                await self._turn(db, conversation_id, text, notice, voice, viewing or [])
        except asyncio.CancelledError:
            hub.add_item(db, conversation_id, "notice", {"text": "止めました", "level": "info"})
        except LLMError as exc:
            hub.add_item(db, conversation_id, "notice", {"text": str(exc), "level": "error"})
        except Exception as exc:  # 作業は裏で動くので、画面に出さないと気づけない
            hub.add_item(db, conversation_id, "notice", {"text": f"{type(exc).__name__}: {exc}", "level": "error"})
        finally:
            db.close()

    async def _turn(self, db: sqlite3.Connection, conversation_id: int, text: str, notice: bool,
                    voice: bool = False, viewing: list[str] | None = None) -> None:
        summary = usage.month_summary(db)
        if not notice:
            hub.add_item(db, conversation_id, "user", {"text": text, "voice": voice} if voice else {"text": text})
        if summary["limit_usd"] is None:
            hub.add_item(db, conversation_id, "notice",
                         {"text": ".env の MATDEV_MONTHLY_LIMIT_USD に月の上限を設定して起動し直してください", "level": "error"})
            return
        if usage.over_limit(summary):
            hub.add_item(db, conversation_id, "notice",
                         {"text": f"今月の上限 {summary['limit_usd']} USD に達したので止めました", "level": "error"})
            return
        if usage.near_limit(summary) and self.warned_month != summary["month"]:
            self.warned_month = summary["month"]
            hub.add_item(db, conversation_id, "notice",
                         {"text": f"今月の使用量が {summary['cost_usd']:.2f} / {summary['limit_usd']} USD です",
                          "level": "warn"})
        messages = self._history(db, conversation_id)
        messages += self._repair(db, conversation_id, messages)
        text = self._actions(db, conversation_id) + text
        if viewing:   # 画面で並べて見ているもの (作業のファイルのウインドウなど)。会話の表示には出さない
            text = "[画面で開いているもの]\n" + "\n".join(f"- {line}" for line in viewing) + "\n\n" + text
        messages.append(self._append(db, conversation_id, "user",
                                     [{"type": "text", "text": text + (VOICE_HINT if voice else "")}]))
        tools = self.build_tools()
        for _ in range(settings.MAX_ROUNDS):
            turn = await self.llm.turn(self.system, tools, messages,
                                       lambda chunk: hub.publish(conversation_id, {"event": "delta", "text": chunk}))
            hub.publish(conversation_id, {"event": "delta_end"})
            usage.record(db, conversation_id, turn.model or self.llm.config.model, turn.usage,
                         self.llm.config.price_per_mtok)
            if turn.stop == "refusal":
                hub.add_item(db, conversation_id, "notice", {"text": "この依頼には答えられませんでした", "level": "error"})
                return
            if turn.stop == "max_tokens":
                hub.add_item(db, conversation_id, "notice", {"text": "応答が長すぎて途中で切れました", "level": "error"})
                return
            messages.append(self._append(db, conversation_id, "assistant", turn.blocks))
            if turn.text:
                hub.add_item(db, conversation_id, "assistant", {"text": turn.text})
            if not turn.tool_uses:
                if turn.stop == "pause":
                    continue
                return
            results = []
            for block in turn.tool_uses:
                results.append(await self._run_tool(db, conversation_id, block, tools, notice))
            messages.append(self._append(db, conversation_id, "user", results))
        hub.add_item(db, conversation_id, "notice",
                     {"text": f"道具の呼び出しが {settings.MAX_ROUNDS} 回に達したので止めました", "level": "error"})

    @staticmethod
    def _actions(db, conversation_id: int) -> str:
        """利用者が画面 (作業台・道具) で行ったことを、次の依頼の頭に付けて伝える (伝えたら印を付ける)。結果は短く添える。"""
        lines = []
        for row in db.execute("SELECT id, data FROM items WHERE conversation_id = ? AND type = 'action' ORDER BY id",
                              (conversation_id,)).fetchall():
            data = json.loads(row["data"])
            if data.get("told"):
                continue
            result = f" → {'結果' if data.get('ok', True) else '失敗'}: {data['result'][:400]}" if data.get("result") else ""
            lines.append(f"- {data['text']}{result}")
            db.execute("UPDATE items SET data = ? WHERE id = ?", (json.dumps({**data, "told": True}, ensure_ascii=False),
                                                                 row["id"]))
        return f"[画面での操作] 利用者が画面で次のことをしました。\n" + "\n".join(lines) + "\n\n" if lines else ""

    # ---- 履歴 ---------------------------------------------------------------------------

    def _history(self, db, conversation_id: int) -> list[dict]:
        rows = db.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                          (conversation_id,)).fetchall()
        return [{"role": row["role"], "content": json.loads(row["content"])} for row in rows]

    def _append(self, db, conversation_id: int, role: str, content: list[dict]) -> dict:
        db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                   (conversation_id, role, json.dumps(content, ensure_ascii=False)))
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (database.now(), conversation_id))
        return {"role": role, "content": content}

    def _repair(self, db, conversation_id: int, messages: list[dict]) -> list[dict]:
        """止めた・再起動したなどで結果のない道具の呼び出しがあれば、結果を補う。"""
        if not messages or messages[-1]["role"] != "assistant":
            return []
        results = [{"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                    "content": "途中で止まったので、この道具が動いたかは不明です。必要なら状態を確かめてください"}
                   for block in messages[-1]["content"] if block.get("type") == "tool_use"]
        return [self._append(db, conversation_id, "user", results)] if results else []

    # ---- 道具 ---------------------------------------------------------------------------

    def build_tools(self) -> list[dict]:
        """LLM に渡す道具の一覧 (共通の形)。順番が変わるとキャッシュが外れるので名前で並べる。"""
        tools = [{"name": name, "description": tool.description or "", "input_schema": tool.input_schema}
                 for name, _, tool in self.toolbox.qualified()]
        tools += [{key: tool[key] for key in ("name", "description", "input_schema")} for tool in [*notes.TOOLS, *works.TOOLS]]
        return sorted(tools, key=lambda tool: tool["name"])

    async def _run_tool(self, db, conversation_id: int, block: dict, tools: list[dict], notice: bool) -> dict:
        name, arguments = block["name"], block.get("input", {})
        result = {"type": "tool_result", "tool_use_id": block["id"]}
        schema = next((tool["input_schema"] for tool in tools if tool["name"] == name), None)
        try:
            if schema is None:
                raise ToolFailed(f"道具 {name} はありません")
            try:
                jsonschema.validate(arguments, schema)
            except jsonschema.ValidationError as exc:
                raise ToolFailed(f"入力が正しくありません: {exc.message}") from exc
            text = await self.gate.run(
                db, conversation_id, name, arguments,
                confirm=functools.partial(self._confirm, db, conversation_id),
                announce=lambda line: hub.add_item(db, conversation_id, "notice", {"text": line, "level": "info"}),
                automated=notice)
            hub.add_item(db, conversation_id, "tool", {"name": name, "ok": True})
        except ToolFailed as exc:
            hub.add_item(db, conversation_id, "tool", {"name": name, "ok": False, "error": str(exc)[:300]})
            return {**result, "is_error": True, "content": str(exc)}
        if len(text) > settings.MAX_TOOL_RESULT_CHARS:
            text = text[:settings.MAX_TOOL_RESULT_CHARS] + f"\n[長いので {settings.MAX_TOOL_RESULT_CHARS} 文字で切りました]"
        return {**result, "content": scrub(text)}

    async def _confirm(self, db, conversation_id: int, tool: str, summary: str) -> None:
        """利用者の答えを待つ。拒否なら ToolFailed で LLM に返す。"""
        cursor = db.execute("INSERT INTO confirmations (conversation_id, tool, summary, created_at) VALUES (?, ?, ?, ?)",
                            (conversation_id, tool, summary, database.now()))
        confirmation_id = cursor.lastrowid
        future = asyncio.get_running_loop().create_future()
        self._waiting[confirmation_id] = future
        hub.add_item(db, conversation_id, "confirm", {"confirmation_id": confirmation_id, "tool": tool, "summary": summary})
        status = "expired"
        try:
            approved = await future
            status = "approved" if approved else "denied"
        finally:
            self._waiting.pop(confirmation_id, None)
            db.execute("UPDATE confirmations SET status = ? WHERE id = ?", (status, confirmation_id))
            hub.publish(conversation_id, {"event": "confirmed", "confirmation_id": confirmation_id, "status": status})
        if not approved:
            raise ToolFailed("利用者が拒否しました。実行していません")
