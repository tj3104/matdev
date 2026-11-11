"""複数のアプリと LLM の差し替えの試験 (設定ファイル matdev.json)。

    ~/.venv/matdev312/bin/python -m pytest 01_test/test_multi.py -v

- MatSim と試験用のアプリ (fake_app.py) を同時につなぎ、道具が「アプリ名__道具名」でそろうこと
- 印による判断 (読むだけ・消す・印なし) と、設定の見積もり・ジョブの見張りが MatSim 以外でも効くこと
- 頭脳を OpenAI 形式に替えて (通信の相手は台本)、道具の呼び出しから返事まで回ること
LLM は本物を使わない。MatSim のデータは一時フォルダ。
"""
import asyncio
import copy
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DATA = HERE / "data_multi"
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir()
STATE = DATA / "fake_state.json"
CONFIG = {
    "llm": {"provider": "openai", "model": "local-test", "base_url": "http://127.0.0.1:1/v1", "price_per_mtok": [0, 0]},
    "works": {"roots": [str(DATA / "works")]},
    "mcpServers": {
        "matsim": {"command": str(Path.home() / ".venv/gpumd312/bin/matsimapi"), "args": ["mcp"],
                   "env": {"MATSIM_DATA_DIR": str(DATA / "matsim")}},
        "fake": {"command": sys.executable, "args": [str(HERE / "fake_app.py")], "env": {"FAKE_STATE": str(STATE)},
                 "matdev": {"guide": "試験用のアプリ",
                            "estimate": {"tool": "start_task", "preview": "estimate_task", "seconds_field": "seconds",
                                         "summary": ["size"], "confirm_over_seconds": 100},
                            "jobs": {"ids": "task.id", "status": "task_status", "id_arg": "task_id",
                                     "status_field": "state", "message_field": "note", "done": ["finished"]},
                            "view": {"list": {"tool": "task_tables", "args": {"task_id": "{job_id}"}, "items": "tables[]"},
                                     "data": {"tool": "task_table", "args": {"task_id": "{job_id}", "key": "{key}"},
                                              "columns": "table"},
                                     "link": "https://fake.example/#/tasks/{job_id}",
                                     "structure": {"tool": "shape_view", "id_arg": "shape_id",
                                                   "args": {"shape_id": "{structure_id}", "repeat": "{repeat}"},
                                                   "created_by": {"make_shape": "shape.id"}}},
                            "allow": ["make_shape"]}},
    },
}
(DATA / "matdev.json").write_text(json.dumps(CONFIG))
os.environ["MATDEV_CONFIG"] = str(DATA / "matdev.json")
os.environ["MATDEV_DATA_DIR"] = str(DATA / "matdev")
os.environ["MATDEV_MONTHLY_LIMIT_USD"] = "1"
os.environ["MATDEV_ALLOWED_HOSTS"] = "testserver"
os.environ["MATDEV_WATCH_SECONDS"] = "0.3"

from fastapi.testclient import TestClient  # noqa: E402

from matdev import config as configuration, main  # noqa: E402
from matdev.llm.openai_llm import to_openai  # noqa: E402

H = {"X-Requested-With": "matdev"}


def chunk(content=None, calls=None, finish=None, usage=None):
    delta = types.SimpleNamespace(content=content, tool_calls=calls)
    choices = [types.SimpleNamespace(delta=delta, finish_reason=finish)] if (content or calls or finish) else []
    return types.SimpleNamespace(choices=choices, usage=usage)


def call(index, id=None, name=None, arguments=None):
    return types.SimpleNamespace(index=index, id=id, function=types.SimpleNamespace(name=name, arguments=arguments))


class FakeOpenAI:
    """OpenAI 形式の台本の相手。応答を細切れの chunk で返し、受け取った依頼を記録する。"""

    def __init__(self):
        self.script, self.requests = [], []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        chunks = self.script.pop(0)

        async def stream():
            for item in chunks:
                yield item
        return stream()


def tool_turn(name, arguments, call_id="call_1"):
    text = json.dumps(arguments)
    return [chunk(calls=[call(0, id=call_id, name=name, arguments=text[:5])]),
            chunk(calls=[call(0, arguments=text[5:])]), chunk(finish="tool_calls"),
            chunk(usage=types.SimpleNamespace(prompt_tokens=50, completion_tokens=10))]


def text_turn(value):
    return [chunk(content=value[:3]), chunk(content=value[3:]), chunk(finish="stop"),
            chunk(usage=types.SimpleNamespace(prompt_tokens=60, completion_tokens=5))]


@pytest.fixture(scope="module")
def llm():
    return FakeOpenAI()


@pytest.fixture(scope="module")
def client(llm):
    main.client_factory = lambda: llm
    with TestClient(main.app) as test_client:
        assert test_client.post("/api/auth/setup", json={"username": "tester", "password": "password-123"},
                                headers=H).status_code == 200
        test_client.headers.update(H)
        yield test_client
    main.client_factory = None


def wait(condition, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("時間切れ")


def default_work(client):
    """会話の出力先 (作業)。会話を始める前に必ず決めるので、試験用の作業を 1 つ使い回す。"""
    found = [w for w in client.get("/api/works").json()["works"] if w["name"] == "試験の出力先"]
    return found[0]["id"] if found else client.post("/api/works", json={"name": "試験の出力先"}).json()["id"]


def chat(client):
    return client.post("/api/conversations", json={"work_id": default_work(client)}).json()["id"]


def items(client, conversation):
    return client.get(f"/api/conversations/{conversation}").json()["items"]


def idle(client, conversation):
    return wait(lambda: not client.get(f"/api/conversations/{conversation}").json()["busy"])


def send(client, conversation, value):
    assert client.post(f"/api/conversations/{conversation}/messages", json={"text": value}).status_code == 200


def pending(client, conversation):
    return wait(lambda: (client.get(f"/api/conversations/{conversation}").json()["pending"] or [None])[0])


def fake_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"tasks": {}, "wiped": 0, "poked": 0}


def test_two_apps_are_connected(client):
    status = client.get("/api/status").json()
    apps = {a["name"]: a for a in status["apps"]}
    assert apps["matsim"]["error"] is None and apps["fake"]["error"] is None
    assert apps["fake"]["tools"] == 10 and status["tools"] == apps["matsim"]["tools"] + 10
    assert apps["fake"]["structures"] and not apps["matsim"]["structures"]
    assert not apps["fake"]["workbench"]["edit"] and not apps["fake"]["workbench"]["calculate"]   # 作業台の道具を書いていない
    assert apps["fake"]["graphs"] and apps["fake"]["link"] and not apps["matsim"]["graphs"]
    assert status["llm"] == "openai / local-test" and status["llm_ready"] is True
    names = {name for name, _, _ in client.app.state.toolbox.qualified()}
    assert {"fake__look", "fake__wipe", "matsim__get_job"} <= names


def test_openai_round_trip_with_read_only_tool(client, llm):
    conversation = chat(client)
    llm.script += [tool_turn("fake__look", {"what": "x"}), text_turn("見ました")]
    send(client, conversation, "見て")
    idle(client, conversation)
    second = llm.requests[-1]
    roles = [m["role"] for m in second["messages"]]
    assert roles[:2] == ["system", "user"] and "tool" in roles
    tool_message = next(m for m in second["messages"] if m["role"] == "tool")
    assert tool_message["tool_call_id"] == "call_1" and '"what": "x"' in tool_message["content"]
    assert any(t["function"]["name"] == "fake__look" for t in second["tools"])
    assert "試験用のアプリ" in second["messages"][0]["content"]
    assert any(i["data"].get("text") == "見ました" for i in items(client, conversation) if i["type"] == "assistant")
    usage = client.get("/api/usage").json()
    assert usage["calls"] >= 2 and usage["cost_usd"] == 0   # ローカル扱い (price_per_mtok [0, 0])


def test_destructive_and_unmarked_tools_need_confirmation(client, llm):
    for tool_name, field in (("fake__wipe", "wiped"), ("fake__poke", "poked")):
        conversation = chat(client)
        before = fake_state()[field]
        llm.script += [tool_turn(tool_name, {}), text_turn("やめました")]
        send(client, conversation, "やって")
        card = pending(client, conversation)
        assert fake_state()[field] == before
        client.post(f"/api/confirmations/{card}", json={"approve": False})
        idle(client, conversation)
        assert fake_state()[field] == before
        result = next(m for m in llm.requests[-1]["messages"] if m["role"] == "tool")
        assert result["content"].startswith("[失敗]") and "拒否" in result["content"]


def test_estimate_and_job_watch_work_for_other_apps(client, llm):
    conversation = chat(client)
    # 見積もり 30 秒 (上限 100 秒以下) → 確認なしで始め、終わりを見張って自動で続きを頼む
    llm.script += [tool_turn("fake__start_task", {"size": 3, "label": "a"}), text_turn("始めました"),
                   text_turn("終わりを確認しました")]
    send(client, conversation, "始めて")
    wait(lambda: any(i["data"].get("text") == "終わりを確認しました" for i in items(client, conversation)), 30)
    log = items(client, conversation)
    assert any("投入します" in i["data"].get("text", "") and "size 3" in i["data"]["text"] for i in log)
    assert any("fake のジョブ" in i["data"].get("text", "") for i in log if i["type"] == "notice")
    # 見積もり 1000 秒 (上限超え) → 確認を取り、拒否すれば始めない
    conversation = chat(client)
    count = len(fake_state()["tasks"])
    llm.script += [tool_turn("fake__start_task", {"size": 100}), text_turn("やめました")]
    send(client, conversation, "大きいのを始めて")
    card = pending(client, conversation)
    client.post(f"/api/confirmations/{card}", json={"approve": False})
    idle(client, conversation)
    assert len(fake_state()["tasks"]) == count


def test_matsim_rules_come_from_the_config(client, llm):
    # 試験の設定の matsim には matdev の欄がない → 見積もりの強制・常に確認の一覧はなく、印だけで判断する
    app = client.app.state.toolbox.apps["matsim"]
    assert app.config.estimate is None and app.config.confirm == []
    default = configuration.parse(configuration.DEFAULT).app("matsim")
    assert default.estimate["tool"] == "submit_calculation" and "cancel_job" in default.confirm


NOTE = {"title": "試験の記録", "purpose": "p", "conditions": "c", "findings": "f"}


def test_note_opens_graphs_of_other_app(client, llm):
    conversation = chat(client)
    llm.script += [tool_turn("write_note", {**NOTE, "app": "fake", "job_ids": ["7"], "structure_ids": ["s-ti"]}),
                   text_turn("記録しました")]
    send(client, conversation, "記録して")
    idle(client, conversation)
    note = client.get("/api/notes?q=試験の記録").json()[0]
    assert note["app"] == "fake" and note["job_ids"] == ["7"] and note["structure_ids"] == ["s-ti"]
    view = client.get("/api/views/fake/jobs/7").json()
    assert view["link"] == "https://fake.example/#/tasks/7"
    assert view["datasets"] == [{"key": "curve", "title": "曲線", "x": "t", "y": ["v"],
                                 "columns": [{"key": "t", "label": "時間 (s)"}, {"key": "v", "label": "値"},
                                             {"key": "w", "label": "w"}]}]
    table = client.get("/api/views/fake/jobs/7/data", params={"key": "curve"}).json()
    assert table["columns"] == {"t": [0, 1, 2, 3], "v": [1.0, 4.0, None, 9.5], "w": [None, 2, 3, 4]}
    missing = client.get("/api/views/fake/jobs/7/data", params={"key": "nothing"})
    assert missing.status_code == 502 and "task_table" in missing.json()["detail"]
    assert llm.requests   # 表を読んでも LLM には何も送らない
    sent = len(llm.requests)
    client.get("/api/views/fake/jobs/7")
    assert len(llm.requests) == sent


def test_note_with_unknown_app_is_refused(client, llm):
    conversation = chat(client)
    llm.script += [tool_turn("write_note", {**NOTE, "title": "存在しないアプリ", "app": "nope", "job_ids": [1]}),
                   text_turn("失敗しました")]
    send(client, conversation, "記録して")
    idle(client, conversation)
    result = next(m for m in llm.requests[-1]["messages"] if m["role"] == "tool")
    assert result["content"].startswith("[失敗]") and "nope" in result["content"]
    assert client.get("/api/notes?q=存在しないアプリ").json() == []


def test_view_only_calls_read_only_tools(client):
    app = client.app.state.toolbox.apps["fake"]
    original = app.config.view
    app.config.view = {**original, "list": {"tool": "poke"}}   # 印のない道具 (呼ぶと poked が増える)
    try:
        before = fake_state()["poked"]
        response = client.get("/api/views/fake/jobs/7")
        assert response.status_code == 400 and "読むだけの印" in response.json()["detail"]
        assert fake_state()["poked"] == before
    finally:
        app.config.view = original
    assert client.get("/api/views/matsim/jobs/1").status_code == 404   # 試験の設定の matsim には view がない
    assert client.get("/api/views/fake/jobs/..%2Fx").status_code in (404, 422)


def test_old_notes_belong_to_the_viewable_app(client):
    db = main.database.connect()
    try:
        db.execute("INSERT INTO notes (conversation_id, title, purpose, conditions, findings, job_ids, created_at, "
                   "updated_at) VALUES (NULL, '古いノート', 'p', 'c', 'f', '[3]', '2026-11-01', '2026-11-01')")
    finally:
        db.close()
    note = client.get("/api/notes?q=古いノート").json()[0]
    assert note["app"] == "fake" and note["job_ids"] == [3]


def test_materials_keep_tool_calls_and_jobs(client, llm):
    conversation = chat(client)
    key = "sk-ant-api03-abcdefghij1234567890"
    llm.script += [tool_turn("fake__look", {"what": key}), tool_turn("fake__task_status", {"task_id": "1"}, "call_2"),
                   text_turn("見ました")]
    send(client, conversation, "見て")
    idle(client, conversation)
    data = client.get(f"/api/conversations/{conversation}/materials").json()
    assert [c["tool"] for c in data["calls"]] == ["task_status", "look"]   # 新しい順
    assert [(j["app"], j["job_id"], j["tools"]) for j in data["jobs"]] == [("fake", "1", ["task_status"])]
    look = client.get(f"/api/materials/{data['calls'][1]['id']}").json()
    assert key not in json.dumps(look, ensure_ascii=False) and "鍵を伏せました" in look["args"]["what"]
    assert look["result"].startswith("{\n")   # JSON は整えて返す
    # これまでの資料: 投入の結果からもジョブを拾い、語で絞れる
    everything = client.get("/api/materials", params={"q": "start_task"}).json()
    assert everything["calls"] and all(c["tool"] == "start_task" for c in everything["calls"])
    assert any(j["tools"] == ["start_task"] for j in everything["jobs"])
    assert client.get("/api/materials/999999").status_code == 404
    assert client.get("/api/conversations/999999/materials").status_code == 404


def test_voice_input_is_transcribed_on_this_pc(client):
    # 試験の声は Windows の読み上げ (Haruka) で作った「チタンの構造相転移を調べて。温度は 800 ケルビンから…」
    status = client.get("/api/status").json()
    assert status["speech"]["ready"] and "whisper" in status["speech"]["name"]
    wav = (HERE / "voice_ti.wav").read_bytes()
    result = client.post("/api/speech/transcribe", files={"audio": ("voice.wav", wav, "audio/wav")})
    assert result.status_code == 200, result.text
    assert "チタン" in result.json()["text"] and "相転移" in result.json()["text"] and "800" in result.json()["text"]
    broken = client.post("/api/speech/transcribe", files={"audio": ("voice.webm", b"not audio", "audio/webm")})
    assert broken.status_code == 400 and "読めません" in broken.json()["detail"]
    assert client.post("/api/speech/transcribe", files={"audio": ("voice.webm", b"", "audio/webm")}).status_code == 400


def test_voice_request_gets_a_hint_but_is_shown_as_said(client, llm):
    conversation = chat(client)
    llm.script += [text_turn("はい")]
    assert client.post(f"/api/conversations/{conversation}/messages",
                       json={"text": "チタンを調べて", "voice": True}).status_code == 200
    idle(client, conversation)
    user_item = next(i for i in items(client, conversation) if i["type"] == "user")
    assert user_item["data"] == {"text": "チタンを調べて", "voice": True}
    sent = llm.requests[-1]["messages"][1]["content"]
    sent = sent if isinstance(sent, str) else json.dumps(sent, ensure_ascii=False)
    assert "チタンを調べて" in sent and "[声の入力]" in sent
    assert "[声の入力]" not in llm.requests[-1]["messages"][0]["content"]   # 指示文は変えない (キャッシュ)


def test_delete_conversation_keeps_notes_and_optionally_materials(client, llm):
    def used(note_title):
        conversation = chat(client)
        llm.script += [tool_turn("fake__look", {"what": note_title}),
                       tool_turn("write_note", {**NOTE, "title": note_title, "app": "fake", "job_ids": []}, "call_2"),
                       text_turn("済み")]
        send(client, conversation, "見て記録して")
        idle(client, conversation)
        return conversation

    kept = used("残す資料の会話")
    gone = used("資料ごと消す会話")
    assert client.delete(f"/api/conversations/{kept}").json() == {"ok": True, "materials_removed": 0}
    assert client.delete(f"/api/conversations/{gone}", params={"materials": "true"}).json()["materials_removed"] == 1
    assert client.get(f"/api/conversations/{kept}").status_code == 404
    assert not {c["id"] for c in client.get("/api/conversations").json()} & {kept, gone}
    # ノートは残る。資料は「消した会話から」として残る (資料ごと消した方は残らない)
    assert client.get("/api/notes", params={"q": "残す資料の会話"}).json()
    assert client.get("/api/notes", params={"q": "資料ごと消す会話"}).json()
    calls = client.get("/api/materials", params={"q": "残す資料の会話"}).json()["calls"]
    assert len(calls) == 1 and calls[0]["conversation_deleted"] is True
    assert client.get("/api/materials", params={"q": "資料ごと消す会話"}).json()["calls"] == []
    # 資料を 1 つ消す
    assert client.delete(f"/api/materials/{calls[0]['id']}").json() == {"ok": True}
    assert client.delete(f"/api/materials/{calls[0]['id']}").status_code == 404
    assert client.delete("/api/conversations/999999").status_code == 404


def test_clear_materials_of_a_conversation(client, llm):
    conversation = chat(client)
    llm.script += [tool_turn("fake__look", {"what": "clear"}), text_turn("見ました")]
    send(client, conversation, "見て")
    idle(client, conversation)
    assert client.delete(f"/api/conversations/{conversation}/materials").json() == {"removed": 1}
    assert client.get(f"/api/conversations/{conversation}/materials").json()["calls"] == []
    assert client.get(f"/api/conversations/{conversation}").status_code == 200   # 会話は残る


def test_assistant_name_changes_the_instructions(client, llm):
    assert client.get("/api/preferences").json() == {"assistant_name": "ジャービス"}
    assert client.put("/api/preferences", json={"assistant_name": "  フライデー \n"}).json() == {"assistant_name": "フライデー"}
    assert client.get("/api/status").json()["assistant_name"] == "フライデー"
    conversation = chat(client)
    llm.script += [text_turn("はい")]
    send(client, conversation, "名前は？")
    idle(client, conversation)
    system = llm.requests[-1]["messages"][0]["content"]
    assert "「フライデー」" in system and "ジャービス" not in system
    assert client.app.state.speech.extra_words == ["フライデー"]
    assert client.put("/api/preferences", json={"assistant_name": "x" * 21}).status_code == 422
    assert client.put("/api/preferences", json={"assistant_name": "   "}).status_code == 400
    client.put("/api/preferences", json={"assistant_name": "ジャービス"})


def test_fetched_structure_appears_in_3d(client, llm):
    conversation = chat(client)
    llm.script += [tool_turn("fake__make_shape", {"name": "ti"}), tool_turn("fake__make_shape", {"name": "ti"}, "call_2"),
                   text_turn("持ってきました")]
    send(client, conversation, "Ti の構造を持ってきて")
    idle(client, conversation)
    cards = [i for i in items(client, conversation) if i["type"] == "structure"]
    assert [c["data"] for c in cards] == [{"app": "fake", "structure_id": "s-ti"}]   # 同じ構造のカードは 1 枚
    # 単位胞が 2 原子なので、自動で 3×3×3 に繰り返して返す (40 原子以上)
    view = client.get("/api/views/fake/structures/s-ti").json()
    assert view["repeat"] == [3, 3, 3] and view["n_atoms"] == 54 and len(view["positions"]) == 54
    assert client.get("/api/views/fake/structures/s-ti", params={"repeat": "1,1,1"}).json()["n_atoms"] == 2
    assert client.get("/api/views/fake/structures/s-ti", params={"repeat": "0,1,1"}).status_code == 422
    latest = client.get("/api/structures/latest", params={"conversation_id": conversation}).json()
    assert latest[0]["structure_id"] == "s-ti" and latest[0]["tools"] == ["make_shape"]
    materials = client.get(f"/api/conversations/{conversation}/materials").json()
    assert materials["structures"][0]["structure_id"] == "s-ti"


def test_structures_from_claude_code_show_in_every_conversation(client):
    # Claude Code の入口 (会話の番号なし) で取ってきた構造は、どの会話の空間にも候補として出る
    db = main.database.connect()
    try:
        db.execute("INSERT INTO materials (conversation_id, app, tool, args, result, jobs, created_at, structures) "
                   "VALUES (NULL, 'fake', 'make_shape', '{}', '{}', '[]', '2099-01-01', '[\"s-cli\"]')")
    finally:
        db.close()
    assert client.get("/api/structures/latest").json()[0]["structure_id"] == "s-cli"
    conversation = chat(client)
    assert client.get("/api/structures/latest", params={"conversation_id": conversation}).json()[0]["structure_id"] == "s-cli"


def test_work_directory_is_kept_inside_its_roots(client):
    made = client.post("/api/works", json={"name": "Ti の相転移"})
    assert made.status_code == 200, made.text
    work = made.json()
    assert work["path"] == str((DATA / "works" / "Ti の相転移").resolve()) and Path(work["path"]).is_dir()
    outside = client.post("/api/works", json={"name": "外", "path": str(DATA / "elsewhere")})
    assert outside.status_code == 400 and "下にしてください" in outside.json()["detail"]
    assert client.post("/api/works", json={"name": "a/b"}).status_code == 400
    assert client.post("/api/works", json={"name": "二重", "path": work["path"]}).status_code == 400
    folder = Path(work["path"])
    (folder / "jobs").mkdir()
    (folder / "thermo.csv").write_text("time,temperature\n0,300\n1,310\n2,320\n")
    (folder / "Cu.xyz").write_text('2\nLattice="3.6 0 0 0 3.6 0 0 0 3.6" Properties=species:S:1:pos:R:3 pbc="T T T"\nCu 0 0 0\nCu 1.8 1.8 0\n')
    (folder / "memo.txt").write_text("考察のメモ")
    (folder / "plot.png").write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                                   "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    (DATA / "secret.txt").write_text("外のファイル")
    listing = client.get(f"/api/works/{work['id']}/files").json()
    kinds = {e["name"]: e["kind"] for e in listing["entries"]}
    assert kinds == {"jobs": "dir", "Cu.xyz": "structure", "memo.txt": "text", "plot.png": "image", "thermo.csv": "table"}
    table = client.get(f"/api/works/{work['id']}/file", params={"path": "thermo.csv"}).json()
    assert table["kind"] == "table" and table["table"]["columns"]["temperature"] == [300, 310, 320]
    structure = client.get(f"/api/works/{work['id']}/file", params={"path": "Cu.xyz"}).json()["structure"]
    assert structure["formula"] == "Cu2" and structure["symbols"] == ["Cu", "Cu"] and structure["cell"][0][0] == 3.6
    assert client.get(f"/api/works/{work['id']}/file", params={"path": "memo.txt"}).json()["text"] == "考察のメモ"
    assert client.get(f"/api/works/{work['id']}/image", params={"path": "plot.png"}).headers["content-type"] == "image/png"
    for bad in ("../../secret.txt", "/etc/passwd"):
        assert client.get(f"/api/works/{work['id']}/file", params={"path": bad}).status_code == 400
    assert client.get(f"/api/works/{work['id']}/files", params={"directory": ".."}).status_code == 400


def test_assistant_reads_work_files_and_sees_open_windows(client, llm):
    work = next(w for w in client.get("/api/works").json()["works"] if w["name"] == "Ti の相転移")
    conversation = client.post("/api/conversations", json={"work_id": work["id"]}).json()["id"]   # ファイルを置いた作業で話す
    llm.script += [tool_turn("list_work_files", {}), tool_turn("read_work_file", {"path": "thermo.csv"}, "call_2"),
                   tool_turn("save_to_work", {"app": "fake", "kind": "job", "id": 1}, "call_3"), text_turn("見ました")]
    sent = client.post(f"/api/conversations/{conversation}/messages",
                       json={"text": "温度の変化をどう思う？", "viewing": ["グラフ thermo.csv (横軸 time・縦軸 temperature)"]})
    assert sent.status_code == 200
    idle(client, conversation)
    first = json.dumps(llm.requests[-4]["messages"][1]["content"], ensure_ascii=False)
    assert "[画面で開いているもの]" in first and "thermo.csv" in first
    user_item = next(i for i in items(client, conversation) if i["type"] == "user")
    assert user_item["data"]["text"] == "温度の変化をどう思う？"                     # 会話の表示には出さない
    results = [m["content"] for m in llm.requests[-1]["messages"] if m["role"] == "tool"]
    assert "Cu.xyz" in results[0] and '"max": 320' in results[1].replace(".0", "")
    assert results[2].startswith("[失敗]") and "書き出す道具" in results[2]          # fake には書き出しの道具がない


def test_conversation_needs_an_output_directory(client, llm):
    # 出力先なしでは会話を始められない。出力先のない (前からある) 会話には依頼を送れず、決めると送れる
    assert client.post("/api/conversations", json={}).status_code == 422
    assert client.post("/api/conversations", json={"work_id": 999999}).status_code == 404
    db = main.database.connect()
    try:
        old = db.execute("INSERT INTO conversations (title, created_at, updated_at) VALUES ('古い会話', '2026-11-01', '2026-11-01')").lastrowid
    finally:
        db.close()
    assert client.get(f"/api/conversations/{old}").json()["work"] is None
    refused = client.post(f"/api/conversations/{old}/messages", json={"text": "見て"})
    assert refused.status_code == 409 and "出力先" in refused.json()["detail"]
    work = default_work(client)
    assert client.put(f"/api/conversations/{old}/work", json={"work_id": work}).json()["work"]["id"] == work
    assert client.get(f"/api/conversations/{old}").json()["work"]["id"] == work
    assert client.get("/api/status").json()["work"]["id"] == work            # Claude Code の入口の出力先も合わせる
    llm.script += [text_turn("はい")]
    assert client.post(f"/api/conversations/{old}/messages", json={"text": "見て"}).status_code == 200
    idle(client, old)


def test_tools_run_from_the_screen_without_chat(client, llm):
    # どの道具もチャットなしで画面から使える。関門を通り、やったことと結果は会話に残り、次の依頼でジャービスに伝わる
    tools = {t["name"]: t for t in client.get("/api/tools").json()}
    assert tools["fake__look"]["read_only"] and tools["fake__wipe"]["destructive"] and tools["fake__look"]["app"] == "fake"
    assert tools["write_note"]["app"] == "matdev"
    conversation = chat(client)
    looked = client.post(f"/api/conversations/{conversation}/tools", json={"name": "fake__look", "args": {"what": "画面"}}).json()
    assert looked["ok"] and '"what": "画面"' in looked["result"]
    action = [i for i in items(client, conversation) if i["type"] == "action"][-1]["data"]
    assert action["tool"] == "fake__look" and action["ok"] and "画面" in action["result"] and action["told"] is False
    # 消す道具は会話の確認のカードで止まる (拒否すれば動かない)
    before = fake_state()["wiped"]
    import threading
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(client.post(
        f"/api/conversations/{conversation}/tools", json={"name": "fake__wipe", "args": {}}).json()))
    worker.start()
    card = pending(client, conversation)
    client.post(f"/api/confirmations/{card}", json={"approve": False})
    worker.join(30)
    assert answer["ok"] is False and "拒否" in answer["result"] and fake_state()["wiped"] == before
    assert client.post(f"/api/conversations/{conversation}/tools", json={"name": "fake__look", "args": {"what": 1}}).status_code == 400
    assert client.post(f"/api/conversations/{conversation}/tools", json={"name": "nope", "args": {}}).status_code == 404
    # 次の依頼で、画面でやったこと (結果つき) がジャービスに伝わる
    llm.script += [text_turn("見ました")]
    send(client, conversation, "どう？")
    idle(client, conversation)
    told = json.dumps(llm.requests[-1]["messages"][-1]["content"], ensure_ascii=False)
    assert "[画面での操作]" in told and "fake__look" in told and "結果" in told and "fake__wipe" in told


def test_matsim_default_view_uses_read_only_tools():
    view = configuration.parse(configuration.DEFAULT).app("matsim").view
    assert view["list"]["tool"] == "get_job_results" and view["data"]["tool"] == "get_job_dataset"
    assert view["link"].endswith("/#/jobs/{job_id}")


def test_conversion_drops_claude_only_blocks():
    messages = [{"role": "user", "content": [{"type": "text", "text": "q"}]},
                {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                                  {"type": "text", "text": "a"},
                                                  {"type": "tool_use", "id": "t1", "name": "fake__look", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
    out = to_openai("sys", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["content"] == "a" and out[2]["tool_calls"][0]["function"]["name"] == "fake__look"
    assert "thinking" not in json.dumps(out)


@pytest.mark.parametrize("raw, message", [
    ({"llm": {"provider": "openai"}, "mcpServers": {"a": {"command": "x"}}}, "llm.model"),
    ({"llm": {"provider": "gemini"}, "mcpServers": {"a": {"command": "x"}}}, "provider"),
    ({"mcpServers": {"Bad-Name": {"command": "x"}}}, "アプリの名前"),
    ({"mcpServers": {}}, "アプリが 1 つも"),
    ({"llm": {"modle": "x"}, "mcpServers": {"a": {"command": "x"}}}, "知らない項目"),
    ({"mcpServers": {"a": {"command": "x", "matdev": {"view": {"list": {"tool": "t"}}}}}}, "両方"),
    ({"speech": {"provider": "cloud"}, "mcpServers": {"a": {"command": "x"}}}, "speech.provider"),
    ({"works": {"roots": []}, "mcpServers": {"a": {"command": "x"}}}, "works.roots"),
    ({"mcpServers": {"a": {"command": "x", "matdev": {"view": {"link": "javascript:alert(1)"}}}}}, "https://"),
])
def test_config_errors_are_explained(raw, message):
    with pytest.raises(ValueError, match=message):
        configuration.parse(raw)


def test_paths_file_is_read_without_overriding_the_environment(tmp_path, monkeypatch):
    # paths.env: コメント・引用符・$変数 (上の行のものも)・~ を読み、すでに環境変数にある値は上書きしない
    from matdev import settings
    for key in ("T_VENV", "T_BIN", "T_DIR", "T_KEEP", "T_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("T_KEEP", "from-shell")
    path = tmp_path / "paths.env"
    path.write_text('# コメント\n\nT_VENV=/opt/venv   # 行末のコメント\nT_BIN=$T_VENV/bin/app\n'
                    'T_DIR="/data/with space"\nexport T_KEEP=from-file\nT_HOME=~/works\n')
    found = settings.load_paths(path)
    assert found["T_BIN"] == "/opt/venv/bin/app" and os.environ["T_BIN"] == "/opt/venv/bin/app"
    assert os.environ["T_DIR"] == "/data/with space"
    assert os.environ["T_KEEP"] == "from-shell"
    assert os.environ["T_HOME"] == os.path.expanduser("~/works")
    path.write_text("これは KEY=値 ではない\n")
    with pytest.raises(ValueError):
        settings.load_paths(path)
    for key in ("T_VENV", "T_BIN", "T_DIR", "T_HOME"):
        monkeypatch.delenv(key, raising=False)
