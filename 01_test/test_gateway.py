"""Claude Code 用の窓口の試験。LLM は使わず、MCP の利用者として本物のサーバーを起動して関門を確かめる。

    ~/.venv/matdev312/bin/python -m pytest 01_test/test_gateway.py -v

データは一時フォルダに置き、本番には触らない。小さい Cu の MD を流すので GPU を少し使う。
"""
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

STAGES = [{"ensemble": "nvt", "steps": 300, "temperature": 300}]
OUTSIDE = "この文字列は外に出てはいけない"


@asynccontextmanager
async def connect(data: Path, answer=None, auto_seconds="1800"):
    env = {**os.environ, "MATDEV_DATA_DIR": str(data / "matdev"), "MATSIM_DATA_DIR": str(data / "matsim"),
           "EZCAL_MCP_STATE": str(data / "ezcal_state"), "MATDEV_WORKS_ROOT": str(data / "works"),
           "MATDEV_AUTO_RUN_SECONDS": auto_seconds}
    params = StdioServerParameters(command=sys.executable, args=["-m", "matdev", "mcp"], env=env,
                                   cwd=str(Path(__file__).parent.parent))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, elicitation_callback=answer) as session:
            await session.initialize()
            yield session


async def tool(session, name, args):
    result = await session.call_tool(name, args)
    return result.is_error, "\n".join(block.text for block in result.content)


def accept(asked: list):
    async def callback(context, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"approve": True})
    return callback


def decide(action, content=None):
    async def callback(context, params):
        return types.ElicitResult(action=action, content=content)
    return callback


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return tmp_path_factory.mktemp("gateway")


def jobs(raw: str) -> list[int]:
    decoder, found, position = json.JSONDecoder(), [], 0
    raw = raw.strip()
    while position < len(raw):
        value, position = decoder.raw_decode(raw, position)
        found.append(value["id"])
        while position < len(raw) and raw[position].isspace():
            position += 1
    return found


def run(function, *args):
    anyio.run(function, *args)


def test_tool_list(data):
    async def body():
        async with connect(data) as session:
            names = {item.name for item in (await session.list_tools()).tools}
            assert {"write_note", "search_notes", "matsim__submit_calculation", "matsim__get_job"} <= names
            assert "matsim__wait_for_job" not in names
            assert len(names) == 55   # MatSim 41 (wait_for_job・書き出しを除く) + ezcal 9 + ノート 2 + 作業 3
    run(body)


def test_unknown_tool_and_bad_input_are_refused(data):
    async def body():
        async with connect(data) as session:
            assert (await tool(session, "Bash", {"command": "ls"}))[0] is True
            assert (await tool(session, "matsim__get_job", {"job_id": "abc"}))[0] is True
            assert (await tool(session, "get_job", {"job_id": 1}))[0] is True   # アプリ名のない名前は受け付けない
    run(body)


def test_short_job_runs_without_question_and_long_needs_a_human(data):
    async def body():
        asked = []
        async with connect(data, accept(asked)) as session:
            structure = json.loads((await tool(session, "matsim__add_sample_structure", {"key": "Cu"}))[1])["id"]
            args = {"kind": "md", "structure_id": structure, "stages": STAGES, "repeat": [3, 3, 3]}
            error, text = await tool(session, "matsim__submit_calculation", args)
            assert error is False and json.loads(text)["preview"]["n_atoms"] == 108
            assert asked == []
        async with connect(data, None, "0") as session:          # 聞く相手がいない
            before = jobs((await tool(session, "matsim__list_jobs", {}))[1])
            error, text = await tool(session, "matsim__submit_calculation", args)
            assert error is True
            assert jobs((await tool(session, "matsim__list_jobs", {}))[1]) == before
        for answer in (decide("decline"), decide("cancel"), decide("accept", {"approve": False}), decide("accept", {})):
            async with connect(data, answer, "0") as session:
                assert (await tool(session, "matsim__submit_calculation", args))[0] is True
                assert jobs((await tool(session, "matsim__list_jobs", {}))[1]) == before
    run(body)


def test_destructive_tools_need_a_human(data):
    async def body():
        async with connect(data) as session:
            listed = jobs((await tool(session, "matsim__list_jobs", {}))[1])
            job = listed[0]
            assert (await tool(session, "matsim__delete_jobs", {"ids": [job]}))[0] is True
            assert (await tool(session, "matsim__cancel_job", {"job_id": job}))[0] is True
            assert jobs((await tool(session, "matsim__list_jobs", {}))[1]) == listed
        asked = []
        async with connect(data, accept(asked)) as session:
            # 実行中なら skipped になるので、終わるまで待つ
            for _ in range(600):
                if json.loads((await tool(session, "matsim__get_job", {"job_id": job}))[1])["status"] in ("done", "failed"):
                    break
                await anyio.sleep(0.2)
            assert (await tool(session, "matsim__delete_jobs", {"ids": [job]}))[0] is False
            assert len(asked) == 1 and "削除" in asked[0]
            assert job not in jobs((await tool(session, "matsim__list_jobs", {}))[1])
    run(body)


def test_outside_file_is_not_read_without_a_human(data):
    secret = data / "outside.txt"
    secret.write_text(OUTSIDE)

    async def body():
        async with connect(data) as session:
            error, text = await tool(session, "matsim__add_structure_file", {"path": str(secret)})
            assert error is True and OUTSIDE not in text
    run(body)


def test_notes_work_without_conversation(data):
    note = {"title": "窓口の確認", "purpose": "p", "conditions": "c", "findings": "f", "job_ids": [1]}

    async def body():
        async with connect(data) as session:
            assert (await tool(session, "write_note", note))[0] is False
            assert "窓口の確認" in (await tool(session, "search_notes", {"query": "窓口"}))[1]
    run(body)


def test_secret_is_hidden_from_results(data):
    from matdev.agent import scrub
    from matdev.gateway import _result
    assert "sk-ant" not in scrub("sk-ant-api03-abcdefghij1234567890") and "sk-ant" not in _result("sk-ant-api03-abcdefghij1234567890").content[0].text


def test_calls_are_kept_as_materials_without_conversation(data):
    # Claude Code の入口から使った道具も、Web 版の「これまで」の資料に出す (会話の番号はなし)
    import sqlite3
    db = sqlite3.connect(data / "matdev" / "matdev.db")
    rows = db.execute("SELECT conversation_id, tool, jobs FROM materials").fetchall()
    db.close()
    submitted = [row for row in rows if row[1] == "submit_calculation"]
    assert submitted and all(row[0] is None for row in rows) and json.loads(submitted[0][2])
