"""MatDev の通しテスト。LLM だけ台本に置き換え、MCP・MatSimAPI・GPUMD は本物を専用のデータ置き場で動かす。

    ~/.venv/matdev312/bin/python -m pytest 01_test/test_agent.py -v

本番のデータ (MatSim の data/) には触らない。小さい Cu の MD を流すので GPU を少し使う。
"""
import copy
import json
import os
import shutil
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data_test"
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir()
os.environ["MATDEV_DATA_DIR"] = str(DATA / "matdev")
os.environ["MATDEV_WORKS_ROOT"] = str(DATA / "works")   # 作業のディレクトリは試験の中だけ
os.environ["EZCAL_MCP_STATE"] = str(DATA / "ezcal_state")   # ezcal のジョブの記録も試験の中だけ
os.environ["MATSIM_DATA_DIR"] = str(DATA / "matsim")
os.environ["MATDEV_WATCH_SECONDS"] = "0.5"
os.environ["MATDEV_MONTHLY_LIMIT_USD"] = "100"
os.environ["MATDEV_ALLOWED_HOSTS"] = "testserver"

import pytest  # noqa: E402
from anthropic.types.beta import BetaMessage  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from matdev import main, settings  # noqa: E402

H = {"X-Requested-With": "matdev"}
STAGES = [{"ensemble": "nvt", "steps": 300, "temperature": 300}]


def message(*blocks, stop="end_turn"):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": settings.MODEL, "content": list(blocks),
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10,
                  "cache_creation_input_tokens": 0}})


def text(value):
    return {"type": "text", "text": value}


def use(name, args, tool_id="toolu_1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


class FakeStream:
    def __init__(self, response, hang):
        self.response, self.hang = response, hang

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def events():
            for block in self.response.content:
                if block.type == "text":
                    yield types.SimpleNamespace(type="text", text=block.text)
        return events()

    async def get_final_message(self):
        return self.response


class FakeClient:
    """台本の応答を順に返し、受け取った依頼を記録する。"""

    def __init__(self):
        self.script, self.requests = [], []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self.stream))

    def stream(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        assert self.script, "台本が足りません"
        return FakeStream(self.script.pop(0), False)


@pytest.fixture(scope="module")
def llm():
    return FakeClient()


@pytest.fixture(scope="module")
def client(llm):
    main.client_factory = lambda: llm
    with TestClient(main.app) as test_client:
        response = test_client.post("/api/auth/setup", json={"username": "tester", "password": "password-123"}, headers=H)
        assert response.status_code == 200
        test_client.headers.update(H)
        yield test_client


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


def new_chat(client):
    return client.post("/api/conversations", json={"work_id": default_work(client)}).json()["id"]


def items(client, chat):
    return client.get(f"/api/conversations/{chat}").json()["items"]


def idle(client, chat):
    return wait(lambda: not client.get(f"/api/conversations/{chat}").json()["busy"])


def send(client, chat, value):
    assert client.post(f"/api/conversations/{chat}/messages", json={"text": value}).status_code == 200


def matsim(client):
    return client.app.state.toolbox.apps["matsim"]


def call(client, name, args):
    return client.portal.call(matsim(client).call_json, name, args)


def listing(client, name):
    """一覧の道具の結果を読む。MCP は要素ごとに別のブロックで返すので、改行でつないだ JSON が並ぶ。"""
    raw = client.portal.call(matsim(client).call, name, {}).strip()
    decoder, found, position = json.JSONDecoder(), [], 0
    while position < len(raw):
        value, position = decoder.raw_decode(raw, position)
        found.append(value)
        while position < len(raw) and raw[position].isspace():
            position += 1
    return found


def pending_card(client, chat):
    def found():
        data = client.get(f"/api/conversations/{chat}").json()
        return data["pending"][0] if data["pending"] else None
    return wait(found)


def check_history(requests):
    """どの依頼でも、道具の呼び出しの直後に結果があり、履歴は追記だけであること。"""
    for request in requests:
        messages = request["messages"]
        for index, entry in enumerate(messages):
            uses = [b["id"] for b in entry["content"] if entry["role"] == "assistant" and b.get("type") == "tool_use"]
            if uses:
                following = messages[index + 1]["content"]
                assert {b["tool_use_id"] for b in following if b.get("type") == "tool_result"} == set(uses)


def test_requires_login():
    with TestClient(main.app) as anonymous:
        assert anonymous.get("/api/conversations").status_code == 401
        assert anonymous.get("/api/status").json()["user"] is None


def test_status_lists_tools(client):
    status = client.get("/api/status").json()
    assert status["apps"][0] == {"name": "matsim", "tools": 41, "error": None, "graphs": True, "link": True,
                                 "structures": True, "workbench": {"edit": True, "draft": True, "molecules": True, "crystals": True, "calculate": True, "source": "structure",
                                                                   "structure_arg": "structure_id", "kind_arg": "kind",
                                                                   "potentials": True, "repeat": True, "traj_arg": "traj_interval"},
                                 "export": ["job", "structure"]}  # wait_for_job を除く
    assert status["apps"][1]["name"] == "ezcal" and status["apps"][1]["tools"] == 9 and status["apps"][1]["error"] is None
    assert status["tools"] == 50 and status["llm"].startswith("anthropic")


def test_csrf_header_required(client):
    response = client.post("/api/conversations", headers={"X-Requested-With": "other"})
    assert response.status_code == 403


def test_plain_reply_keeps_thinking_block(client, llm):
    chat = new_chat(client)
    llm.script += [message(THINKING, text("こんにちは")), message(text("二回目"))]
    send(client, chat, "やあ")
    idle(client, chat)
    send(client, chat, "続き")
    idle(client, chat)
    second = llm.requests[-1]
    assert second["model"] == "claude-opus-5-5" and second["fallbacks"] == "default"
    assert second["thinking"] == {"type": "adaptive"}
    assert second["messages"][1]["content"][0] == THINKING
    assert [t["name"] for t in second["tools"]] == sorted(t["name"] for t in second["tools"])
    assert [i["data"].get("text") for i in items(client, chat) if i["type"] == "assistant"] == ["こんにちは", "二回目"]


def test_read_tool_runs_without_confirmation(client, llm):
    chat = new_chat(client)
    llm.script += [message(use("matsim__list_calculation_kinds", {}), stop="tool_use"), message(text("24 種類です"))]
    send(client, chat, "何ができる?")
    idle(client, chat)
    result = llm.requests[-1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "is_error" not in result
    assert "transition" in result["content"]
    check_history(llm.requests)


def test_matsim_structure_in_3d(client, llm):
    # 構造を作った道具の呼び出しから 3D のカードが出て、表示用のデータは本物の MatSim (読むだけの道具) から来る
    chat = new_chat(client)
    llm.script += [message(use("matsim__add_sample_structure", {"key": "Cu"})), message(text("登録しました"))]
    send(client, chat, "Cu の試験構造を出して")
    idle(client, chat)
    card = next(i for i in items(client, chat) if i["type"] == "structure")
    view = client.get(f"/api/views/matsim/structures/{card['data']['structure_id']}").json()
    assert set(view["symbols"]) == {"Cu"} and view["n_atoms"] >= 40 and len(view["cell"]) == 3
    assert len(view["positions"]) == view["shown"]


def test_invalid_input_is_not_run(client, llm):
    chat = new_chat(client)
    llm.script += [message(use("matsim__get_job", {"job_id": "abc"}), stop="tool_use"), message(text("直します"))]
    send(client, chat, "ジョブを見て")
    idle(client, chat)
    result = llm.requests[-1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "入力が正しくありません" in result["content"]


def test_short_job_runs_then_reports_and_writes_note(client, llm):
    structure = call(client, "add_sample_structure", {"key": "Cu"})["id"]
    chat = new_chat(client)
    args = {"kind": "md", "structure_id": structure, "stages": STAGES, "repeat": [3, 3, 3], "name": "テスト", "traj_interval": 50}
    note = {"title": "Cu の短い MD", "purpose": "通しの確認", "conditions": "Cu 108 原子 300 K", "findings": "完了した",
            "job_ids": []}
    llm.script += [message(use("matsim__submit_calculation", args), stop="tool_use"), message(text("投入しました")),
                   message(use("write_note", note), stop="tool_use"), message(text("完了を確認しました"))]
    send(client, chat, "Cu の MD を流して")
    wait(lambda: any(i["data"].get("text") == "完了を確認しました" for i in items(client, chat)), 120)
    idle(client, chat)
    log = items(client, chat)
    assert any("投入します" in i["data"].get("text", "") for i in log if i["type"] == "notice")
    finished = [i["data"]["text"] for i in log if i["type"] == "notice" and "計算が終わりました" in i["data"]["text"]]
    assert len(finished) == 1 and ": done" in finished[0], finished
    cards = [i["data"] for i in log if i["type"] == "job"]   # 会話の中で結果を開くカード
    assert len(cards) == 1 and cards[0]["status"] == "done" and cards[0]["open"] is True
    job = cards[0]["job_id"]
    assert client.get(f"/api/views/matsim/jobs/{job}", params={"conversation_id": chat}).json()["trajectory"] is True
    motion = client.get(f"/api/views/matsim/jobs/{job}/trajectory").json()   # 計算過程の構造の時間変化
    assert len(motion["frames"]) >= 2 and len(motion["frames"][0]["positions"]) == len(motion["symbols"]) == 108
    final = client.get(f"/api/views/matsim/jobs/{job}", params={"conversation_id": chat}).json()["final"]
    assert final == {"name": "matsim__save_final_structure", "id_arg": "job_id"}
    saved = client.post(f"/api/conversations/{chat}/tools", json={"name": final["name"], "args": {"job_id": int(job)}}).json()
    assert saved["ok"], saved   # 計算の後の構造を、次の計算に使える構造として登録する (会話に 3D のカード)
    made = str(json.loads(saved["result"])["id"])
    assert any(i["type"] == "structure" and i["data"]["structure_id"] == made for i in items(client, chat))
    assert not any("自動の通知" in i["data"].get("text", "") for i in log if i["type"] == "user")
    notes = client.get("/api/notes", params={"q": "Cu 確認"}).json()
    assert notes[0]["title"] == "Cu の短い MD"
    assert client.get("/api/notes", params={"q": "存在しない語"}).json() == []
    check_history(llm.requests)


def test_results_and_structures_go_to_the_work_directory(client, llm):
    # 作業を決めると、作った構造は structures/ に、終わった計算の結果は jobs/matsim-<番号>/ に置かれる (本物の MatSim)
    work = client.post("/api/works", json={"name": "置き場の試験"}).json()
    folder = Path(work["path"])
    structure = call(client, "add_sample_structure", {"key": "Cu"})["id"]   # 関門の外 (試験の準備)
    chat = client.post("/api/conversations", json={"work_id": work["id"]}).json()["id"]
    llm.script += [message(use("matsim__add_sample_structure", {"key": "Cu"}), stop="tool_use"), message(text("出しました"))]
    send(client, chat, "Cu を出して")
    idle(client, chat)
    made = next(i for i in items(client, chat) if i["type"] == "structure")["data"]["structure_id"]
    assert {p.suffix for p in (folder / "structures").glob(f"structure{made}_*")} == {".cif", ".xyz", ".vasp"}
    assert any("structures/ に置きました" in i["data"].get("text", "") for i in items(client, chat) if i["type"] == "notice")
    quick = {"kind": "static", "structure_id": structure, "potential_id": 1,
             "options": {"relax": False, "elastic": False, "cohesive": False}}
    llm.script += [message(text("終わりました"))]
    submitted = client.post("/api/workbench/matsim", json={"action": "calc_submit", "args": quick, "conversation_id": chat,
                                                           "label": "静的計算"}).json()
    job_id = submitted["result"]["jobs"][0]["id"]
    wait(lambda: (folder / "jobs" / f"matsim-{job_id}" / "job.json").exists(), 120)
    assert (folder / "jobs" / f"matsim-{job_id}" / "run.in").exists()
    listing = client.get(f"/api/works/{work['id']}/files", params={"directory": f"jobs/matsim-{job_id}"}).json()
    assert any(e["name"] == "run.in" for e in listing["entries"])
    # 画面の「作業に置く」(手で置く)
    again = client.post("/api/works/save", json={"app": "matsim", "kind": "structure", "id": str(structure)})
    assert again.status_code == 200 and list((folder / "structures").glob(f"structure{structure}_*"))
    # 書き出しの道具はジャービスに見せない
    names = {name for name, _, _ in client.app.state.toolbox.qualified()}
    assert "matsim__export_job" not in names and "matsim__export_structure" not in names


def test_ezcal_runs_in_the_conversation_work(client, llm):
    # ezcal (本物の QE) で Si の scf: 構造は作業の中の相対パス、出力先は関門が会話の作業にする。投入は毎回確認
    from ase.build import bulk
    from ase.io import write
    work = client.post("/api/works", json={"name": "ezcal の試験"}).json()
    folder = Path(work["path"])
    (folder / "structures").mkdir(exist_ok=True)
    write(folder / "structures" / "Si.cif", bulk("Si", "diamond", a=5.43))
    chat = client.post("/api/conversations", json={"work_id": work["id"]}).json()["id"]
    submit = next(tool for name, _, tool in client.app.state.toolbox.qualified() if name == "ezcal__submit_task")
    assert "outdir" not in submit.input_schema["properties"]                 # 出力先はジャービスに決めさせない
    llm.script += [message(use("ezcal__preview_task", {"task": "scf", "structure": "../../secret.cif"}), stop="tool_use"),
                   message(use("ezcal__preview_task", {"task": "scf", "structure": "structures/Si.cif"}, "toolu_2"),
                           stop="tool_use"),
                   message(use("ezcal__submit_task", {"task": "scf", "structure": "structures/Si.cif"}, "toolu_3"),
                           stop="tool_use"),
                   message(text("投入しました")), message(text("Si の scf が終わりました"))]
    start = len(llm.requests)
    send(client, chat, "Si の scf を ezcal で流して")
    card = pending_card(client, chat)
    client.post(f"/api/confirmations/{card}", json={"approve": True})
    wait(lambda: any(i["data"].get("text") == "Si の scf が終わりました" for i in items(client, chat)), 300)
    results = [b for m in llm.requests[start + 2]["messages"] for b in (m["content"] if isinstance(m["content"], list) else [])
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert results[0].get("is_error") and "作業のディレクトリの外" in json.dumps(results[0], ensure_ascii=False)
    assert json.loads(results[1]["content"])["natoms"] == 2 and json.loads(results[1]["content"])["formula"] == "Si2"
    summaries = list((folder / "ezcal").glob("*/summary.json"))
    assert len(summaries) == 1 and json.loads(summaries[0].read_text())["ok"] is True
    assert any("計算が終わりました" in i["data"].get("text", "") for i in items(client, chat) if i["type"] == "notice")


def test_ezcal_workbench_from_a_structure_file(client, llm):
    # ezcal の作業台: 作業の構造ファイルから、種類・入力欄・見積もり・投入 (毎回確認) まで、チャットなしで
    import threading
    from ase.build import bulk
    from ase.io import write
    work = client.post("/api/works", json={"name": "ezcal の作業台"}).json()
    folder = Path(work["path"])
    (folder / "structures").mkdir(exist_ok=True)
    write(folder / "structures" / "Si.cif", bulk("Si", "diamond", a=5.43))
    chat = client.post("/api/conversations", json={"work_id": work["id"]}).json()["id"]
    status = {a["name"]: a for a in client.get("/api/status").json()["apps"]}
    assert status["ezcal"]["workbench"]["source"] == "file" and status["ezcal"]["workbench"]["kind_arg"] == "task"

    def bench(action, args, **extra):
        return client.post("/api/workbench/ezcal", json={"action": action, "args": args, "conversation_id": chat, **extra})

    kinds = bench("calc_list", {}).json()["result"]
    assert any(k["key"] == "scf" for k in kinds)
    fields = bench("calc_describe", {"task": "scf"}).json()["result"]["fields"]
    assert any(f["key"] == "kspacing" for f in fields)
    preview = bench("calc_preview", {"task": "scf", "structure": "structures/Si.cif"}).json()["result"]
    assert preview["natoms"] == 2 and preview["formula"] == "Si2"
    assert bench("calc_preview", {"task": "scf", "structure": "../../x.cif"}).status_code == 502   # 作業の外は断る
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(bench("calc_submit", {
        "task": "dos", "structure": "structures/Si.cif", "options": {"kspacing": 0.5}}, label="DOS").json()))
    worker.start()
    card = pending_card(client, chat)                                       # 見積もれないので毎回確認
    client.post(f"/api/confirmations/{card}", json={"approve": True})
    worker.join(60)
    assert answer["result"]["jobs"] and "DOS" in answer["summary"]
    job_id = answer["result"]["jobs"][0]["id"]
    llm.script += [message(text("Si の DOS を確認しました"))]
    wait(lambda: any(i["data"].get("text") == "Si の DOS を確認しました" for i in items(client, chat)), 600)
    assert any(i["type"] == "action" and "DOS" in i["data"]["text"] for i in items(client, chat))
    # 結果は MatSim と同じく「グラフ」で見られる: 表 (csv)・図 (png)・Plotly の図 (html、作業のファイルとして)
    view = client.get(f"/api/views/ezcal/jobs/{job_id}", params={"conversation_id": chat}).json()
    dos = next(d for d in view["datasets"] if d["key"] == "plots/dos.csv")
    assert dos["x"] == "E-EF" and "dos" in dos["y"]
    assert view["work_id"] == work["id"] and any(p.endswith("plots/dos.png") for p in view["images"])
    html = next(p for p in view["html"] if p.endswith("plots/dos.html"))
    table = client.get(f"/api/views/ezcal/jobs/{job_id}/data", params={"key": "plots/dos.csv"}).json()
    assert len(table["columns"]["dos"]) > 100
    framed = client.get(f"/api/works/{work['id']}/html", params={"path": html})
    assert framed.status_code == 200 and framed.headers["content-security-policy"].startswith("sandbox allow-scripts")
    assert framed.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'none'" in client.get("/api/status").headers["content-security-policy"]   # ほかはいつもどおり
    assert all(not p.startswith("/") for p in view["images"] + view["html"])   # 作業からの相対パスだけ (外の場所は出さない)


def test_interactive_md_keeps_atoms_apart_and_saves(client):
    # 対話型 MD (NEP89 を CPU で): つまんだ原子を隣の原子の真上へ引っぱり続けても、共有結合半径の和 × 0.7 より近づかない
    import numpy as np
    from ase.build import bulk
    from ase.geometry import find_mic
    from ase.io import write
    from starlette.websockets import WebSocketDisconnect
    engines = client.get("/api/imd/engines").json()["engines"]
    default = next(e for e in engines if e["default"])
    assert "NEP89" in default["label"] and any(e["id"] == "emt" for e in engines)
    work = client.post("/api/works", json={"name": "対話型 MD の試験"}).json()
    write(Path(work["path"]) / "Ti96.xyz", bulk("Ti", "hcp", a=2.95, c=4.68).repeat((4, 4, 3)), format="extxyz")
    chat = client.post("/api/conversations", json={"work_id": work["id"]}).json()["id"]
    with pytest.raises(WebSocketDisconnect):   # 別の場所から開いた画面は断る
        with client.websocket_connect("/api/imd", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    with client.websocket_connect("/api/imd", headers={"origin": "http://testserver"}) as ws:
        ws.send_json({"op": "start", "conversation_id": chat, "file": "Ti96.xyz", "engine": default["engine"], "temperature": 300})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["n_atoms"] == 96 and "NEP89" in ready["engine"]
        cell = np.array(ready["cell"])
        closest, limit = 9.0, 0.7 * 2 * 1.60
        for i in range(60):
            frame = ws.receive_json()
            if i % 3 == 0:
                ws.send_json({"op": "pull", "index": 0, "target": frame["positions"][1]})
            x = np.array(frame["positions"])
            closest = min(closest, find_mic(x[1:] - x[0], cell, True)[1].min())
        assert frame["blocked"] and closest > limit - 0.05, closest
        ws.send_json({"op": "release"})
        ws.send_json({"op": "set", "temperature": 600})
        for _ in range(5):
            frame = ws.receive_json()
        assert frame["pulling"] is None and frame["target_temperature"] == 600
        ws.send_json({"op": "save"})
        saved = next(m for m in iter(ws.receive_json, None) if m["type"] == "saved")
        assert saved["path"].startswith("structures/imd_") and (Path(work["path"]) / saved["path"]).is_file()
        ws.send_json({"op": "stop"})
    assert any(i["type"] == "action" and "対話型 MD" in i["data"]["text"] for i in items(client, chat))
    with client.websocket_connect("/api/imd", headers={"origin": "http://testserver"}) as ws:   # 作業の外は断る
        ws.send_json({"op": "start", "conversation_id": chat, "file": "../../x.xyz", "engine": default["engine"]})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"op": "stop"})


def test_workbench_draft_stacks_edits_and_saves_once(client):
    # 編集の下書き: 重ねる → 一つ前に戻す → 重ねる → 最後に 1 回だけ保存 (構造の一覧に増えるのは 1 件だけ)
    structure = call(client, "add_sample_structure", {"key": "Cu"})["id"]
    chat = new_chat(client)
    before = len(listing(client, "list_structures"))

    def bench(action, args, **extra):
        return client.post("/api/workbench/matsim", json={"action": action, "args": args, "conversation_id": chat, **extra})

    state = bench("draft_start", {"structure_id": structure}).json()["result"]
    token = state["draft"]
    assert state["step"] == 0 and state["formula"] == "Cu4" and state["view"]["positions"]
    state = bench("draft_apply", {"draft": token, "op": "supercell", "options": {}}).json()["result"]
    assert state["step"] == 1 and state["n_atoms"] == 32
    state = bench("draft_apply", {"draft": token, "op": "substitute",
                                  "options": {"from": "Cu", "to": "Ni", "amount": "count", "count": 4}}).json()["result"]
    assert state["formula"] == "Cu28Ni4"
    state = bench("draft_undo", {"draft": token}).json()["result"]
    assert state["step"] == 1 and state["formula"] == "Cu32"
    state = bench("draft_apply", {"draft": token, "op": "vacancy",
                                  "options": {"target": "*", "amount": "count", "count": 1}}).json()["result"]
    assert state["n_atoms"] == 31 and [s["label"] for s in state["steps"]] == ["スーパーセル", "空孔"]
    assert len(listing(client, "list_structures")) == before                  # 下書きのあいだは登録しない
    saved = bench("draft_save", {"draft": token}, label="スーパーセル → 空孔")
    assert saved.status_code == 200, saved.text
    made = str(saved.json()["result"]["id"])
    assert len(listing(client, "list_structures")) == before + 1
    log = items(client, chat)
    assert any(i["type"] == "structure" and i["data"]["structure_id"] == made for i in log)
    assert any(i["type"] == "action" and "スーパーセル → 空孔" in i["data"]["text"] for i in log)
    assert bench("draft_apply", {"draft": "0" * 16, "op": "supercell", "options": {}}).status_code == 502   # ない下書き


def test_pubchem_molecule_is_boxed_and_placed_on_a_surface(client):
    # 有機分子を PubChem で探して取り込む (全方位に真空) → 別の構造に置く (位置・向き・重なる原子の削除)。PubChem に通信する
    chat = new_chat(client)

    def bench(action, args, **extra):
        return client.post("/api/workbench/matsim", json={"action": action, "args": args, "conversation_id": chat, **extra})

    assert client.get("/api/status").json()["apps"][0]["workbench"]["molecules"]
    found = bench("molecule_search", {"query": "benzene"}).json()["result"]
    found = found if isinstance(found, list) else [found]   # 1 件の一覧は 1 つの値で届く
    assert found[0]["cid"] == 241 and found[0]["formula"] == "C6H6"
    assert bench("molecule_search", {"query": "CCO"}).json()["result"]["name"] == "Ethanol"   # SMILES
    imported = bench("molecule_import", {"cid": 241, "vacuum": 6, "name": "Benzene"}, label="Benzene")
    assert imported.status_code == 200, imported.text
    molecule = imported.json()["result"]["id"]
    view = client.get(f"/api/views/matsim/structures/{molecule}?repeat=1,1,1").json()
    assert view["n_atoms"] == 12 and all(min(p[k] for p in view["positions"]) > 5.9 for k in range(3))   # 真空 6 Å
    log = items(client, chat)
    assert any(i["type"] == "structure" and i["data"]["structure_id"] == str(molecule) for i in log)
    assert any(i["type"] == "action" and "分子 Benzene を取り込み" in i["data"]["text"] for i in log)

    host = call(client, "add_sample_structure", {"key": "Cu"})["id"]
    token = bench("draft_start", {"structure_id": host}).json()["result"]["draft"]
    bench("draft_apply", {"draft": token, "op": "supercell", "options": {}})
    bench("draft_apply", {"draft": token, "op": "vacuum", "options": {"axis": "z", "thickness": 15}})
    state = bench("draft_apply", {"draft": token, "op": "place_molecule",
                                  "options": {"molecule": molecule, "z": 12, "rx": 90}}).json()["result"]
    assert state["formula"] == "Cu32C6H6" and state["n_atoms"] == 44
    state = bench("draft_undo", {"draft": token}).json()["result"]
    state = bench("draft_apply", {"draft": token, "op": "place_molecule",   # 中央 (欄が空) に置き、重なる Cu を消す
                                  "options": {"molecule": molecule, "rmd": 2.0}}).json()["result"]
    assert state["n_atoms"] < 44 and any("消しました" in w for w in state["warnings"])
    state = bench("draft_apply", {"draft": token, "op": "place_molecule",   # 同じ所にもう 1 つ置くと重なる
                                  "options": {"molecule": molecule}}).json()["result"]
    assert any("近すぎる" in w for w in state["warnings"]), state["warnings"]
    boxed = bench("draft_start", {"structure_id": molecule}).json()["result"]["draft"]
    state = bench("draft_apply", {"draft": boxed, "op": "vacuum_all", "options": {"vacuum": 4}}).json()["result"]
    assert state["n_atoms"] == 12
    assert bench("molecule_search", {"query": "zzzznotamolecule"}).status_code == 502


def test_crystal_is_found_and_imported_from_materials_project(client):
    # 結晶構造を Materials Project から OPTIMADE で探して取り込む (API キーなし。Materials Project に通信する)
    chat = new_chat(client)

    def bench(action, args, **extra):
        return client.post("/api/workbench/matsim", json={"action": action, "args": args, "conversation_id": chat, **extra})

    found = bench("crystal_search", {"query": "Si"}).json()["result"]
    assert found[0]["id"] == "mp-149" and found[0]["spacegroup"] == "Fd-3m (227)" and found[0]["e_above_hull"] == 0
    imported = bench("crystal_import", {"entry_id": "mp-149", "conventional": True}, label="Si Fd-3m")
    assert imported.status_code == 200, imported.text
    made = imported.json()["result"]
    assert made["n_atoms"] == 8 and made["spacegroup"] == "Fd-3m (227)"   # 慣用の単位胞 (基本単位胞は 2 原子)
    log = items(client, chat)
    cards = [i["data"]["structure_id"] for i in log if i["type"] == "structure"]
    assert cards == [str(made["id"])]   # カードは登録した構造の番号 (Materials Project の ID ではない)
    assert any(i["type"] == "action" and "mp-149" in i["data"]["text"] for i in log)
    system = bench("crystal_search", {"query": "Ti-O"}).json()["result"]
    assert {c["formula"] for c in system} >= {"O2Ti", "OTi"}   # 元素の組は、その元素だけの組成すべて
    assert bench("crystal_search", {"query": "TiQ2"}).status_code == 502   # 元素記号でない


def test_workbench_edits_and_calculates_from_a_structure(client, llm):
    # 表示した構造から作業台で: 編集をプレビュー → 保存 (新しい構造のカード) → 計算を見積もり → 投入 (見張り)。
    # 画面での操作は、次の依頼 (ここでは計算の終わりの自動の通知) の頭でジャービスに伝わる
    structure = call(client, "add_sample_structure", {"key": "Cu"})["id"]
    chat = new_chat(client)

    def bench(action, args, **extra):
        return client.post("/api/workbench/matsim", json={"action": action, "args": args, **extra})

    ops = bench("edit_list", {}).json()["result"]
    assert any(op["key"] == "supercell" and op["fields"] for op in ops["ops"])
    supercell = {"op": "supercell", "structure_id": structure, "options": {}}
    assert bench("edit_preview", supercell).json()["result"]["n_atoms"] > 4
    assert bench("edit_save", supercell).status_code == 400              # 会話の外では保存しない
    assert bench("edit_preview", {"op": 1}).status_code == 400           # 入力の形を確かめる
    assert bench("format_disk", {}).status_code == 400                   # 決まった操作だけ
    saved = bench("edit_save", {**supercell, "name": "作業台の試験"}, conversation_id=chat, label="スーパーセル")
    assert saved.status_code == 200, saved.text
    made = str(saved.json()["result"]["id"])
    log = items(client, chat)
    assert any(i["type"] == "structure" and i["data"]["structure_id"] == made for i in log)
    assert any(i["type"] == "action" and "スーパーセル" in i["data"]["text"] for i in log)

    kinds = bench("calc_list", {}).json()["result"]
    assert any(k["key"] == "static" for k in kinds)
    assert bench("calc_describe", {"kind": "static"}).json()["result"]["fields"]
    quick = {"kind": "static", "structure_id": int(made), "potential_id": 1,
             "options": {"relax": False, "elastic": False, "cohesive": False}}
    assert bench("calc_preview", quick).json()["result"]["estimated_seconds"] < 60
    llm.script += [message(text("終わりを確認しました"))]
    submitted = bench("calc_submit", quick, conversation_id=chat, label="静的計算")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["result"]["jobs"]
    wait(lambda: any(i["data"].get("text") == "終わりを確認しました" for i in items(client, chat)), 120)
    told = json.dumps(llm.requests[-1]["messages"][-1]["content"], ensure_ascii=False)
    assert "[画面での操作]" in told and "スーパーセル" in told and "静的計算" in told
    assert all(i["data"]["told"] for i in items(client, chat) if i["type"] == "action")


def test_long_job_needs_confirmation_and_denial_stops_it(client, llm):
    structure = listing(client, "list_structures")[0]["id"]
    before = len(listing(client, "list_jobs"))
    chat = new_chat(client)
    args = {"kind": "md", "structure_id": structure, "stages": STAGES, "repeat": [3, 3, 3]}
    llm.script += [message(use("matsim__submit_calculation", args), stop="tool_use"), message(text("やめました"))]
    settings.AUTO_RUN_SECONDS, saved = 0, settings.AUTO_RUN_SECONDS
    try:
        send(client, chat, "長い計算を流して")
        card = pending_card(client, chat)
        assert len(listing(client, "list_jobs")) == before
        assert client.post(f"/api/confirmations/{card}", json={"approve": False}).status_code == 200
        idle(client, chat)
    finally:
        settings.AUTO_RUN_SECONDS = saved
    assert len(listing(client, "list_jobs")) == before
    result = llm.requests[-1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "拒否" in result["content"]


def test_delete_needs_confirmation_and_approval_runs_it(client, llm):
    job = listing(client, "list_jobs")[0]["id"]
    chat = new_chat(client)
    llm.script += [message(use("matsim__delete_jobs", {"ids": [job]}), stop="tool_use"), message(text("消しました"))]
    send(client, chat, "消して")
    card = pending_card(client, chat)
    assert any(j["id"] == job for j in listing(client, "list_jobs"))
    assert client.post(f"/api/confirmations/{card}", json={"approve": True}).status_code == 200
    idle(client, chat)
    assert not any(j["id"] == job for j in listing(client, "list_jobs"))
    assert client.post(f"/api/confirmations/{card}", json={"approve": True}).status_code == 409


def test_file_read_needs_confirmation(client, llm):
    chat = new_chat(client)
    llm.script += [message(use("matsim__add_structure_file", {"path": "/etc/hostname"}), stop="tool_use"), message(text("了解"))]
    send(client, chat, "読み込んで")
    card = pending_card(client, chat)
    client.post(f"/api/confirmations/{card}", json={"approve": False})
    idle(client, chat)


def test_stop_during_confirmation_repairs_history(client, llm):
    chat = new_chat(client)
    llm.script += [message(THINKING, use("matsim__cancel_job", {"job_id": 999}), stop="tool_use")]
    send(client, chat, "止めて")
    pending_card(client, chat)
    client.post(f"/api/conversations/{chat}/stop")
    idle(client, chat)
    assert client.get(f"/api/conversations/{chat}").json()["pending"] == []
    llm.script += [message(text("次の依頼です"))]
    send(client, chat, "別の話")
    idle(client, chat)
    check_history(llm.requests)
    assert llm.requests[-1]["messages"][-1]["content"][0]["text"] == "別の話"


def test_refusal_and_truncation_run_nothing(client, llm):
    chat = new_chat(client)
    llm.script += [message(use("matsim__delete_jobs", {"ids": [1]}), stop="refusal")]
    send(client, chat, "a")
    idle(client, chat)
    llm.script += [message(use("matsim__delete_jobs", {"ids": [1]}), stop="max_tokens")]
    send(client, chat, "b")
    idle(client, chat)
    assert client.get(f"/api/conversations/{chat}").json()["pending"] == []
    assert sum(i["type"] == "notice" for i in items(client, chat)) == 2


def test_monthly_limit_blocks(client, llm):
    calls = len(llm.requests)
    settings.MONTHLY_LIMIT_USD, saved = 0.000001, settings.MONTHLY_LIMIT_USD
    try:
        chat = new_chat(client)
        send(client, chat, "上限の確認")
        idle(client, chat)
    finally:
        settings.MONTHLY_LIMIT_USD = saved
    assert len(llm.requests) == calls
    assert any("上限" in i["data"].get("text", "") for i in items(client, chat) if i["type"] == "notice")
    assert client.get("/api/usage").json()["calls"] > 0


def test_limit_is_required(client, llm):
    calls = len(llm.requests)
    settings.MONTHLY_LIMIT_USD, saved = None, settings.MONTHLY_LIMIT_USD
    try:
        chat = new_chat(client)
        send(client, chat, "上限なし")
        idle(client, chat)
    finally:
        settings.MONTHLY_LIMIT_USD = saved
    assert len(llm.requests) == calls


def test_unknown_host_is_rejected(client):
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400


def test_secrets_do_not_reach_child_process(monkeypatch):
    from matdev.config import AppConfig
    from matdev.tools import child_env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_TOKEN", "x")
    monkeypatch.setenv("MATDEV_MONTHLY_LIMIT_USD", "1")
    env = child_env(AppConfig(name="a", command="x", env={"APP_SETTING": "1"}))
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MY_TOKEN", "MATDEV_MONTHLY_LIMIT_USD"} & set(env)
    assert "PATH" in env and env["APP_SETTING"] == "1"


def test_scrub_hides_keys():
    from matdev.agent import scrub
    assert "sk-ant" not in scrub("key=sk-ant-api03-abc_DEF-123456789 end")
    assert "sk-proj" not in scrub("key=sk-proj-abcdefghijklmnop1234 end")


def test_automatic_turn_cannot_submit_without_confirmation(client, llm):
    structure = listing(client, "list_structures")[0]["id"]
    chat = new_chat(client)
    before = len(listing(client, "list_jobs"))
    args = {"kind": "md", "structure_id": structure, "stages": STAGES, "repeat": [3, 3, 3]}
    llm.script += [message(use("matsim__submit_calculation", args), stop="tool_use"), message(text("見送ります"))]
    client.portal.call(_notify, client, chat)
    card = pending_card(client, chat)
    assert len(listing(client, "list_jobs")) == before
    client.post(f"/api/confirmations/{card}", json={"approve": False})
    idle(client, chat)


async def _notify(client, chat):
    client.app.state.agent.enqueue(chat, "[自動の通知] 終わりました", notice=True)


def test_every_model_is_charged():
    from matdev.usage import prices_for
    for model in settings.PRICES:
        assert prices_for(model)[0] > 0
    assert prices_for("claude-unknown-9") == max(settings.PRICES.values(), key=lambda p: p[0] + p[1])
    assert prices_for("claude-haiku-5-5")[0] < prices_for("claude-opus-5-5")[0]


def test_auth_error_is_explained(client, llm):
    import anthropic
    import httpx
    chat = new_chat(client)
    original = llm.beta.messages.stream

    def fail(**kwargs):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
    llm.beta.messages.stream = fail
    try:
        send(client, chat, "こんにちは")
        idle(client, chat)
    finally:
        llm.beta.messages.stream = original
    assert any("鍵が使えません" in i["data"].get("text", "") for i in items(client, chat))
