"""作業台: 表示した構造 (アプリの構造、または作業の構造ファイル) から、そのまま編集と計算に進む。画面のフォームから道具を呼ぶ。

設定の matdev.workbench に、構造の渡し方 (source: structure / file)・引数の名前・edit / calculate の道具を書く。
入力欄の定義は fields の形 (key・label・type・default・min・max・options・show_if・advanced)。
見る・確かめるだけの操作 (一覧・入力欄の定義・プレビュー・見積もり) は読むだけの印のある道具に限って直接呼ぶ。
保存と投入は関門 (gate.py) を通す。見積もりの強制・確認・資料・新しい構造のカード・ジョブの見張りは、ジャービスが呼んだときと同じ。
"""
from __future__ import annotations

import json

import jsonschema

# 操作 → (設定の欄, 道具の名前の取り方)。投入と見積もりは estimate の決まりの道具を使う
READ_ACTIONS = {
    "edit_list": ("edit", "list"), "edit_preview": ("edit", "preview"), "structures": ("edit", "structures"),
    "calc_list": ("calculate", "list"), "calc_describe": ("calculate", "describe"),
    "potentials": ("calculate", "potentials"), "calc_preview": ("estimate", "preview"),
    # 下書き: 一時的な場所で編集を重ねる・戻す (構造の一覧は変えない)
    "draft_start": ("draft", "start"), "draft_apply": ("draft", "apply"), "draft_undo": ("draft", "undo"),
    "draft_view": ("draft", "view"),
    "molecule_search": ("molecules", "search"),   # 有機分子を探す (PubChem など)
    "crystal_search": ("crystals", "search"),   # 結晶構造 (Materials Project)
}
WRITE_ACTIONS = {"edit_save": ("edit", "save"), "calc_submit": ("estimate", "tool"), "draft_save": ("draft", "save"),
                 "molecule_import": ("molecules", "import"), "crystal_import": ("crystals", "import")}


class WorkbenchError(Exception):
    """設定や入力の問題。理由を利用者に見せる。"""


def available(app) -> dict:
    """画面に渡す作業台の情報 (使えるか・構造の渡し方・引数の名前)。"""
    rule = app.config.workbench or {}
    calculate = rule.get("calculate") or {}
    return {"edit": bool(rule.get("edit")), "draft": bool(rule.get("draft")), "molecules": bool(rule.get("molecules")),
            "crystals": bool(rule.get("crystals")),
            "calculate": bool(calculate and app.config.estimate),
            "source": rule.get("source", "structure"), "structure_arg": rule.get("structure_arg", "structure_id"),
            "kind_arg": rule.get("kind_arg", "kind"), "potentials": bool(calculate.get("potentials")),
            "repeat": bool(calculate.get("repeat")), "traj_arg": calculate.get("traj_arg")}


def tool_for(app, action: str):
    section, part = READ_ACTIONS.get(action) or WRITE_ACTIONS.get(action) or (None, None)
    if section is None:
        raise WorkbenchError(f"知らない操作です: {action}")
    rule = app.config.estimate if section == "estimate" else (app.config.workbench or {}).get(section)
    name = (rule or {}).get(part)
    tool = app.tool(name) if name else None
    if tool is None:
        raise WorkbenchError(f"{app.name} には作業台の道具 ({section}.{part}) がありません")
    return tool


def check(tool, args: dict) -> None:
    try:
        jsonschema.validate(args, tool.input_schema or {})
    except jsonschema.ValidationError as exc:
        raise WorkbenchError(f"入力が正しくありません: {exc.message}") from exc


async def read(app, action: str, args: dict, in_work=lambda tool_name, args: args):
    """見る・確かめるだけの操作。読むだけの印のない道具は呼ばない。in_work は作業の中のパスに直す関門の働き。"""
    tool = tool_for(app, action)
    if action not in READ_ACTIONS:
        raise WorkbenchError("この操作は保存か投入です")
    if not (tool.annotations is not None and tool.annotations.read_only_hint):
        raise WorkbenchError(f"{app.name} の {tool.name} には読むだけの印がないので、確かめるだけの操作には使えません")
    check(tool, args)
    try:
        return await app.call_json(tool.name, in_work(tool.name, args))
    except ValueError as exc:
        raise WorkbenchError(f"{tool.name} の応答を読めません") from exc


def action_text(action: str, label: str, args: dict, result: str) -> str:
    """会話に残し、次の依頼でジャービスに伝える文。"""
    try:
        data = json.loads(result)
    except ValueError:
        data = {}
    structure = args.get("structure_id") or args.get("structure")
    if action == "draft_save":
        made = data.get("id") if isinstance(data, dict) else None
        return f"画面で編集を重ねて ({label})、構造 {made} として保存しました"
    if action == "molecule_import":
        made = data.get("id") if isinstance(data, dict) else None
        return f"画面で分子 {label} を取り込み、構造 {made} として登録しました (全方位に真空 {args.get('vacuum', 10)} Å)"
    if action == "crystal_import":
        made = data.get("id") if isinstance(data, dict) else None
        return (f"画面で結晶構造 {label} (Materials Project {args.get('entry_id')}) を取り込み、"
                f"構造 {made} として登録しました")
    if action == "edit_save":
        made = data.get("id") if isinstance(data, dict) else None
        return f"画面で構造 {structure} に「{label}」をして、構造 {made} として保存しました"
    jobs = [job.get("id") for job in data.get("jobs", [])] if isinstance(data, dict) else []
    return f"画面で構造 {structure} に「{label}」を投入しました (ジョブ {', '.join(map(str, jobs)) or '?'})"

