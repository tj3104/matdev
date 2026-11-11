"""Claude Code から MatDev を使うための MCP サーバー (標準入出力)。

    python -m matdev mcp

Claude Code には組み込みの道具を渡さず、ここだけを見せる (claude_matdev.sh)。道具は設定ファイル (matdev.json) の
アプリから集める。実行の決まりは Web 版と同じ gate.py で、
確認は MCP の elicitation で人に直接聞く。LLM はこの答えを作れない。答えられない環境 (claude -p など) では実行しない。
"""
from __future__ import annotations

import asyncio
import functools
import sqlite3

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__, db as database, notes, preferences, works
from .agent import scrub
from . import config as configuration
from .gate import Gate
from .prompt import build_system
from .tools import ToolFailed, Toolbox

APPROVE_SCHEMA = {"type": "object", "required": ["approve"],
                  "properties": {"approve": {"type": "boolean", "title": "実行する"}}}


def _note_tools() -> list[types.Tool]:
    return [types.Tool(name=tool["name"], description=tool["description"], input_schema=tool["input_schema"])
            for tool in [*notes.TOOLS, *works.TOOLS]]


async def _confirm(ctx, tool: str, summary: str) -> None:
    """人に聞く。はっきり「実行する」と答えたときだけ通す。"""
    try:
        answer = await ctx.session.elicit_form(f"実行してよいですか\n{summary}", APPROVE_SCHEMA,
                                               related_request_id=ctx.request_id)
    except Exception as exc:
        raise ToolFailed(f"確認できなかったので実行していません ({type(exc).__name__})") from exc
    if answer.action != "accept" or (answer.content or {}).get("approve") is not True:
        raise ToolFailed("利用者が拒否したか確認できなかったので、実行していません")


def _result(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=scrub(text))], is_error=error)


def _name() -> str:
    """Web 版の画面で付けた呼び名 (起動したときのもの)。"""
    db = database.connect()
    try:
        return preferences.assistant_name(db)
    except sqlite3.OperationalError:   # まだ表がない (Web 版を一度も起動していない)
        return preferences.DEFAULT_NAME
    finally:
        db.close()


def build_server(toolbox: Toolbox, config) -> Server:
    gate = Gate(toolbox)

    def tools() -> list[types.Tool]:
        # 名前は「アプリ名__道具名」。結果は文字で返すので、元の道具の構造化した結果の宣言は外す
        return [*(tool.model_copy(update={"name": name, "output_schema": None}) for name, _, tool in toolbox.qualified()),
                *_note_tools()]

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools())

    async def on_call_tool(ctx, params) -> types.CallToolResult:
        name, args = params.name, params.arguments or {}
        tool = next((item for item in tools() if item.name == name), None)
        if tool is None:
            return _result(f"道具 {name} はありません", True)
        try:
            jsonschema.validate(args, tool.input_schema)
        except jsonschema.ValidationError as exc:
            return _result(f"入力が正しくありません: {exc.message}", True)
        db = database.connect()
        try:
            text = await gate.run(db, None, name, args, confirm=functools.partial(_confirm, ctx), announce=lambda _: None)
            return _result(text)
        except ToolFailed as exc:
            return _result(str(exc), True)
        finally:
            db.close()

    return Server("matdev", version=__version__, instructions=build_system(config, "cli", _name()), on_list_tools=on_list_tools,
                  on_call_tool=on_call_tool)


async def serve() -> None:
    database.init_db(expire_pending=False)   # Web 版が同時に動いていても、その確認を消さない
    config = configuration.load()
    toolbox = Toolbox(config.apps)
    await toolbox.start()
    if toolbox.errors and len(toolbox.errors) == len(toolbox.apps):
        raise SystemExit(f"どのアプリにも接続できません: {toolbox.errors}")
    server = build_server(toolbox, config)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await toolbox.stop()


def main() -> None:
    asyncio.run(serve())
