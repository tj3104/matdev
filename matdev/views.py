"""ノートのジョブの結果を見る。LLM を通さず、設定の view に書いた道具をサーバーが直接呼ぶ (料金はかからない)。

    "view": {
      "list": {"tool": "get_job_results", "args": {"job_id": "{job_id}"}, "items": "datasets[]"},
      "data": {"tool": "get_job_dataset", "args": {"job_id": "{job_id}", "key": "{key}"}, "columns": "data"},
      "link": "https://<PC>:10000/#/jobs/{job_id}"
    }

- list の道具は、グラフにできる表の一覧を返す。items の道筋の先の 1 つずつが
  {key, title, x, y, columns} (x は横軸の列、y は縦軸の列 (1 つか一覧)、columns は列の一覧。key 以外は省略可)
- data の道具は、表 1 つ分を返す。columns の道筋の先が {列の名前: [値, ...]}
- args の "{job_id}" "{key}" は値に置き換える (道具が数を受け付けるなら数にする)
- link はアプリの画面を開くための URL
画面を見るだけの操作なので、呼べるのは読むだけの印 (readOnlyHint) がある道具に限る。
"""
from __future__ import annotations

import math

from .gate import pick


class ViewError(Exception):
    """設定や応答の形の問題。理由を利用者に見せる。"""


def _typed(arguments: dict, tool) -> dict:
    """道具の受け付ける型に合わせる (ジョブの番号は数のアプリも文字のアプリもある)。"""
    properties = (tool.input_schema or {}).get("properties", {})
    typed = {}
    for name, value in arguments.items():
        kinds = properties.get(name, {}).get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if isinstance(value, str) and value.lstrip("-").isdigit() and "string" not in kinds and "integer" in kinds:
            value = int(value)
        typed[name] = value
    return typed


def _fill(template, values: dict):
    if isinstance(template, str):
        for name, value in values.items():
            if template == "{" + name + "}":
                return value
        for name, value in values.items():
            template = template.replace("{" + name + "}", str(value))
        return template
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    return template


def link(app, job_id: str) -> str | None:
    view = app.config.view or {}
    return _fill(view["link"], {"job_id": job_id}) if view.get("link") else None


def has_structures(app) -> bool:
    return bool(app.config.view and "structure" in app.config.view)


def final_tool(app) -> dict | None:
    """最終構造を構造にする道具 ({name: アプリ__道具, id_arg})。画面は関門を通す道具の窓口 (/api/conversations/{id}/tools) で呼ぶ。"""
    rule = (app.config.view or {}).get("final")
    if not rule or app.tool(rule["tool"]) is None:
        return None
    return {"name": f"{app.name}__{rule['tool']}", "id_arg": rule.get("id_arg", "job_id")}


def has_trajectory(app) -> bool:
    return bool(app.config.view and "trajectory" in app.config.view)


def has_graphs(app) -> bool:
    return bool(app.config.view and "list" in app.config.view)


async def _call(app, part: str, values: dict):
    rule = app.config.view[part]
    tool = app.tool(rule["tool"])
    if tool is None:
        raise ViewError(f"{app.name} に道具 {rule['tool']} がありません (設定を確かめてください)")
    if not (tool.annotations is not None and tool.annotations.read_only_hint):
        raise ViewError(f"{app.name} の {tool.name} には読むだけの印がないので、画面からは呼びません")
    try:
        arguments = _typed(_fill(rule.get("args", {"job_id": "{job_id}"}), values), tool)
        return await app.call_json(tool.name, arguments), rule
    except ValueError as exc:   # JSON でない応答
        raise ViewError(f"{app.name} の {tool.name} の応答を読めません") from exc


def _names(columns) -> list[dict]:
    named = []
    for column in columns or []:
        if isinstance(column, dict) and "key" in column:
            named.append({"key": str(column["key"]), "label": str(column.get("label") or column["key"])})
        elif isinstance(column, str):
            named.append({"key": column, "label": column})
    return named


async def datasets(app, job_id: str) -> list[dict]:
    """グラフにできる表の一覧。"""
    return (await overview(app, job_id))["datasets"]


async def overview(app, job_id: str) -> dict:
    """グラフにできる表の一覧と、図 (images)・Plotly の図 (html) のファイルのパス (設定の view.images / view.html の道筋)。"""
    if not has_graphs(app):
        return {"datasets": [], "images": [], "html": []}
    result, rule = await _call(app, "list", {"job_id": job_id})
    view = app.config.view or {}
    files = {kind: [p for p in pick(result, view[kind]) if isinstance(p, str)] if view.get(kind) else []
             for kind in ("images", "html")}
    found = []
    for item in pick(result, rule.get("items", "datasets[]")):
        if not isinstance(item, dict) or "key" not in item:
            continue
        y = item.get("y")
        found.append({"key": str(item["key"]), "title": str(item.get("title") or item["key"]),
                      "x": item.get("x"), "y": [y] if isinstance(y, str) else list(y or []),
                      "columns": _names(item.get("columns"))})
    return {"datasets": found, **files}


MAX_FRAMES = 200


async def trajectory(app, job_id: str) -> dict:
    """計算過程の構造の時間変化 (3D で動かす用)。{symbols, frames: [{index, cell, positions}], n_frames_total}"""
    if not has_trajectory(app):
        raise ViewError(f"{app.name} には構造の時間変化を見る設定 (view.trajectory) がありません")
    data, _ = await _call(app, "trajectory", {"job_id": job_id})
    frames = [f for f in (data.get("frames") or [])[:MAX_FRAMES]
              if isinstance(f, dict) and isinstance(f.get("positions"), list) and isinstance(f.get("cell"), list)]
    symbols = data.get("symbols") or []
    if frames and not all(len(f["positions"]) == len(symbols) for f in frames):
        raise ViewError(f"{app.name} の時間変化の応答で、原子の数がそろっていません")
    return {"app": app.name, "job_id": job_id, "symbols": symbols, "frames": frames,
            "n_frames_total": data.get("n_frames_total", len(frames)), "n_atoms": data.get("n_atoms", len(symbols))}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


FEW_ATOMS = 40   # これより少ない単位胞は、形が分かるように繰り返して見せる


async def structure(app, structure_id: str, repeat: list[int] | None = None) -> dict:
    """構造の 3D 表示用のデータ (元素・座標 Å・セル)。repeat がなければ、原子が少ないときは自動で繰り返す。"""
    if not has_structures(app):
        raise ViewError(f"{app.name} には構造を見る設定 (view.structure) がありません")
    data, _ = await _call(app, "structure", {"structure_id": structure_id, "repeat": repeat or [1, 1, 1]})
    if repeat is None and isinstance(data.get("n_atoms"), int) and 0 < data["n_atoms"] < FEW_ATOMS:
        k = min(4, math.ceil((FEW_ATOMS / data["n_atoms"]) ** (1 / 3)))
        data, _ = await _call(app, "structure", {"structure_id": structure_id, "repeat": [k, k, k]})
    if not all(isinstance(data.get(key), list) for key in ("symbols", "positions", "cell")):
        raise ViewError(f"{app.name} の応答に構造 (symbols・positions・cell) がありません")
    return {"app": app.name, "structure_id": structure_id, **{k: data.get(k) for k in (
        "name", "formula", "symbols", "positions", "cell", "n_atoms", "shown", "repeat")}}


async def table(app, job_id: str, key: str) -> dict:
    """表 1 つ分。数でない値は null にする。"""
    if not has_graphs(app):
        raise ViewError(f"{app.name} にはグラフの設定 (view) がありません")
    result, rule = await _call(app, "data", {"job_id": job_id, "key": key})
    found = pick(result, rule.get("columns", "data"))
    columns = found[0] if found else None
    if not isinstance(columns, dict):
        raise ViewError(f"{app.name} の応答に表がありません")
    return {"key": key, "columns": {str(name): [_number(v) for v in values]
                                    for name, values in columns.items() if isinstance(values, list)}}

