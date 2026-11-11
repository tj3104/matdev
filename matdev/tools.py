"""道具の層。設定ファイルに並べたアプリ (MCP サーバー) をそれぞれ子プロセスとして保ち、道具をまとめて扱う。

道具の名前は「アプリ名__道具名」(例: matsim__get_job)。Claude も OpenAI 形式も名前にピリオドを使えないので __ で区切る。
アプリごとに 1 つの接続を保ち、切れたら次の呼び出しで起動し直す。
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import AppConfig

SEP = "__"
# 子プロセスに渡さない環境変数 (ワーカーや計算プログラムまで引き継がれるので、秘密は渡さない)
SECRET_ENV = re.compile(r"^(ANTHROPIC_|OPENAI_|MATDEV_)|KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)


class ToolFailed(Exception):
    """道具が失敗した。文面はそのまま LLM に返して直してもらう。"""


def child_env(app: AppConfig) -> dict:
    env = {key: value for key, value in os.environ.items() if not SECRET_ENV.search(key)}
    return {**env, **app.env}


def _hide_work_args(config: AppConfig, tool):
    """出力先の引数 (設定の work の dir) はジャービスに見せない。MatDev の関門が会話の作業のディレクトリで決める。"""
    rule = (config.work or {}).get(tool.name) or {}
    name = rule.get("dir")
    schema = tool.input_schema or {}
    if not name or name not in schema.get("properties", {}):
        return tool
    schema = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != name},
              "required": [r for r in schema.get("required", []) if r != name]}
    return tool.model_copy(update={"input_schema": schema})


class McpApp:
    """MCP サーバー 1 つとの接続。"""

    def __init__(self, config: AppConfig, hidden: set[str] = frozenset()) -> None:
        self.config = config
        self.name = config.name
        self.hidden = set(hidden)
        self.tools: list = []
        self.error: str | None = None
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._start_lock = asyncio.Lock()

    async def _run(self) -> None:
        params = StdioServerParameters(command=self.config.command, args=self.config.args, env=child_env(self.config))
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.tools = [_hide_work_args(self.config, t) for t in (await session.list_tools()).tools
                                  if t.name not in self.hidden]
                    self._session, self.error = session, None
                    self._ready.set()
                    await self._stop.wait()
        except Exception as exc:  # 起動の失敗は状態として見せ、次の呼び出しでやり直す
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._session = None
            self._ready.set()

    async def start(self) -> None:
        async with self._start_lock:
            if self._session is not None:
                return
            self._ready.clear()
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
            await self._ready.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    def tool(self, name: str):
        return next((tool for tool in self.tools if tool.name == name), None)

    async def call(self, name: str, arguments: dict) -> str:
        """道具を呼んで文字列を返す。失敗は ToolFailed。"""
        if self._session is None:
            await self.start()
        if self._session is None:
            raise ToolFailed(f"{self.name} に接続できません: {self.error}")
        result = await self._session.call_tool(name, arguments)
        text = "\n".join(block.text for block in result.content if getattr(block, "text", None) is not None)
        if result.is_error:
            raise ToolFailed(text or f"{name} が失敗しました")
        return text

    async def call_json(self, name: str, arguments: dict):
        """JSON で受け取る。一覧を返す道具は要素ごとに分かれて届くので、続けて読んで一覧にする。"""
        text, decoder, values, position = await self.call(name, arguments), json.JSONDecoder(), [], 0
        while position < len(text.rstrip()):
            value, position = decoder.raw_decode(text, position)
            values.append(value)
            while position < len(text) and text[position].isspace():
                position += 1
        if not values:
            raise ValueError(f"{name} の応答が空です")
        return values[0] if len(values) == 1 else values


def export_tools(app: AppConfig) -> set[str]:
    """作業のディレクトリへ書き出す道具の名前 (設定の export の job・structure)。"""
    return {app.export[kind] for kind in ("job", "structure") if app.export and app.export.get(kind)}


class Toolbox:
    """設定ファイルのアプリをまとめる。"""

    def __init__(self, apps: list[AppConfig]) -> None:
        # 長く待たせる道具は渡さない (ほかの道具を止めてしまう。終わりは見張りが知らせる)
        # 長く待たせる道具と、作業のディレクトリへ書き出す道具 (MatDev だけが使う) はジャービスに渡さない
        self.apps = {app.name: McpApp(app, hidden={"wait_for_job"} | export_tools(app)) for app in apps}

    async def start(self) -> None:
        await asyncio.gather(*(app.start() for app in self.apps.values()))

    async def stop(self) -> None:
        await asyncio.gather(*(app.stop() for app in self.apps.values()))

    @property
    def errors(self) -> dict[str, str]:
        return {name: app.error for name, app in self.apps.items() if app.error}

    def qualified(self) -> list[tuple[str, McpApp, object]]:
        """(道具の名前, アプリ, MCP の道具) の一覧。"""
        return [(f"{app.name}{SEP}{tool.name}", app, tool) for app in self.apps.values() for tool in app.tools]

    def resolve(self, name: str):
        """道具の名前から (アプリ, MCP の道具)。なければ ToolFailed。"""
        app_name, _, tool_name = name.partition(SEP)
        app = self.apps.get(app_name)
        tool = app.tool(tool_name) if app else None
        if tool is None:
            raise ToolFailed(f"道具 {name} はありません")
        return app, tool
