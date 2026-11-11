"""試験用の小さなアプリ (MCP サーバー)。MatSim 以外のアプリをつなげることを確かめる。

    python 01_test/fake_app.py      (標準入出力の MCP サーバー)

道具: look (読むだけ)・wipe (消す)・poke (印なし)・estimate_task / start_task / task_status (見積もりとジョブの見張り)・
      task_tables / task_table (ノートから見るグラフ)・make_shape / shape_view (構造の 3D 表示)
"""
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("fake")
STATE = os.environ.get("FAKE_STATE", "/tmp/fake_app_state.json")


def _load():
    try:
        with open(STATE) as handle:
            return json.load(handle)
    except OSError:
        return {"tasks": {}, "wiped": 0, "poked": 0}


def _save(state):
    with open(STATE, "w") as handle:
        json.dump(state, handle)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def look(what: str = "all") -> dict:
    """状態を見る。"""
    return {"what": what, **_load()}


@server.tool(annotations=ToolAnnotations(destructiveHint=True))
def wipe() -> dict:
    """消す。"""
    state = _load()
    state["wiped"] += 1
    _save(state)
    return {"wiped": state["wiped"]}


@server.tool()
def poke() -> dict:
    """印のない道具。"""
    state = _load()
    state["poked"] += 1
    _save(state)
    return {"poked": state["poked"]}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def estimate_task(size: int) -> dict:
    """見積もり。"""
    return {"seconds": size * 10.0, "size": size}


@server.tool()
def start_task(size: int, label: str = "") -> dict:
    """作業を始める (すぐ終わる)。"""
    state = _load()
    task_id = str(len(state["tasks"]) + 1)
    state["tasks"][task_id] = "finished"
    _save(state)
    return {"task": {"id": task_id}, "label": label}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def task_status(task_id: str) -> dict:
    """作業の状態。"""
    return {"state": _load()["tasks"].get(task_id, "unknown"), "note": ""}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def task_tables(task_id: str) -> dict:
    """作業の結果の表の一覧。"""
    return {"tables": [{"key": "curve", "title": "曲線", "x": "t", "y": "v",
                        "columns": [{"key": "t", "label": "時間 (s)"}, {"key": "v", "label": "値"}, "w"]},
                       {"title": "key のない表は無視される"}]}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def task_table(task_id: str, key: str) -> dict:
    """表 1 つ分。"""
    if key != "curve":
        raise ValueError("表が見つかりません")
    return {"table": {"t": [0, 1, 2, 3], "v": [1.0, 4.0, None, 9.5], "w": ["a", 2, 3, 4]}}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
def make_shape(name: str) -> dict:
    """構造を作る (登録する)。"""
    return {"shape": {"id": f"s-{name}"}}


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def shape_view(shape_id: str, repeat: list[int]) -> dict:
    """構造の 3D 表示用のデータ。単位胞は 2 原子。"""
    k = repeat[0] * repeat[1] * repeat[2]
    return {"name": shape_id, "formula": "Ab", "symbols": ["Cu", "O"] * k, "positions": [[0, 0, 0], [1, 1, 1]] * k,
            "cell": [[2 * repeat[0], 0, 0], [0, 2 * repeat[1], 0], [0, 0, 2 * repeat[2]]], "n_atoms": 2 * k,
            "shown": 2 * k, "repeat": repeat}


if __name__ == "__main__":
    server.run("stdio")
