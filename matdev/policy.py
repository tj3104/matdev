"""道具ごとの実行の決まり。LLM の判断に任せず、ここで止める。

どのアプリにも同じ決まり (MCP の道具の印で判断する):
- 設定の confirm に書いた道具、消す・上書きする印 (destructiveHint) の道具は確認を取る
- 設定の allow に書いた道具、読むだけの印 (readOnlyHint) の道具はそのまま動かす
- 印のない道具は確認を取る (アプリに道具が増えても安全な側に倒す)
見積もりの強制 (estimate) は、設定に書いたアプリの道具だけ (gate.py)。
"""
from __future__ import annotations


def needs_confirmation(app, tool) -> bool:
    annotations = tool.annotations
    if tool.name in app.config.confirm or (annotations is not None and annotations.destructive_hint):
        return True
    if tool.name in app.config.allow or (annotations is not None and annotations.read_only_hint):
        return False
    return True


def preview_arguments(arguments: dict, preview_tool) -> dict:
    """投入の引数から、見積もりの道具が受け付ける分だけを取り出す。"""
    accepted = set((preview_tool.input_schema or {}).get("properties", {}))
    return {key: value for key, value in arguments.items() if key in accepted}


def describe_call(name: str, arguments: dict) -> str:
    labels = {"delete_structures": "構造を削除", "delete_jobs": "ジョブを結果ごと削除", "cancel_job": "ジョブを中止",
              "add_structure_file": "このマシンのファイルを読み込む"}
    short = name.split("__", 1)[-1]
    return f"{labels.get(short, name)}: {arguments}"


def describe_submission(name: str, rule: dict, arguments: dict, preview: dict) -> str:
    seconds = preview.get(rule.get("seconds_field", "estimated_seconds"))
    parts = [f"{name.split('__', 1)[-1]}"]
    parts += [f"{key} {preview[key]}" for key in rule.get("summary", []) if key in preview]
    parts.append(f"見積もり {seconds / 60:.1f} 分" if isinstance(seconds, (int, float)) else "見積もりなし")
    return " / ".join(parts)
