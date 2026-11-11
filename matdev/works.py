"""作業: 作業のディレクトリを決めて、計算の結果や構造をそこに置く。置いたもの (自分で置いたファイルも) を画面と空間で開き、
ジャービスとの議論に使う。

- 会話ごとに出力先の作業を持つ (conversations.work_id。会話を始める前に必ず決める)。
  Claude Code の入口 (会話の番号なし) は、最後に開いた会話の作業 (preferences の active_work) に書き出す
- 作業のディレクトリは設定の works.roots の下にだけ作る。ファイルの読み書きはそのディレクトリの外を指せない
- 書き出しはアプリの道具 (設定の export) を MatDev が呼ぶ。ジャービスには save_to_work (書き出す先は いまの作業 だけ) を渡す
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from pathlib import Path

from .db import now

NAME = re.compile(r"^[^/\\\0\n]{1,60}$")
TEXT_LIMIT = 200_000            # 中身を見せる上限 [バイト]
TABLE_ROWS = 2000               # 表は間引いてこの行数まで
LIST_LIMIT = 500
STRUCTURE_SUFFIXES = {".cif", ".xyz", ".extxyz", ".vasp", ".poscar"}
STRUCTURE_NAMES = {"POSCAR", "CONTCAR"}
TABLE_SUFFIXES = {".csv", ".tsv", ".dat", ".out"}
HTML_SUFFIXES = {".html", ".htm"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".gif": "image/gif"}
TEXT_SUFFIXES = {".txt", ".in", ".log", ".json", ".md", ".dat", ".out", ".py", ".sh", ".yaml", ".yml"}
MAX_STRUCTURE_ATOMS = 5000


class WorkError(Exception):
    """利用者に見せる理由つきの失敗。"""


# ---- 作業 --------------------------------------------------------------------------------------

def _row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "path": row["path"], "created_at": row["created_at"]}


def roots(config) -> list[Path]:
    return [Path(r).expanduser().resolve() for r in config.works.roots]


def create(db: sqlite3.Connection, config, name: str, path: str = "") -> dict:
    """作業を作る。path がなければ最初の場所の下に name のディレクトリを作る。場所の外は断る。"""
    name = " ".join(name.split())
    if not NAME.match(name):
        raise WorkError("作業の名前は 1〜60 文字で、/ を含めないでください")
    allowed = roots(config)
    target = Path(path).expanduser() if path else allowed[0] / name
    target = target.resolve()
    if not any(target == root or target.is_relative_to(root) for root in allowed):
        raise WorkError(f"作業のディレクトリは次の場所の下にしてください: {', '.join(map(str, allowed))}")
    if db.execute("SELECT 1 FROM works WHERE path = ?", (str(target),)).fetchone():
        raise WorkError("そのディレクトリはもう作業になっています")
    target.mkdir(parents=True, exist_ok=True)
    cursor = db.execute("INSERT INTO works (name, path, created_at) VALUES (?, ?, ?)", (name, str(target), now()))
    return get(db, cursor.lastrowid)


def listing(db: sqlite3.Connection) -> list[dict]:
    return [_row(r) for r in db.execute("SELECT * FROM works ORDER BY id DESC").fetchall()]


def get(db: sqlite3.Connection, work_id: int) -> dict:
    row = db.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        raise WorkError("作業が見つかりません")
    return _row(row)


def active(db: sqlite3.Connection) -> dict | None:
    try:
        row = db.execute("SELECT value FROM preferences WHERE key = 'active_work'").fetchone()
    except sqlite3.OperationalError:   # まだ表がない
        return None
    if row is None or row["value"] == "":
        return None
    try:
        return get(db, int(row["value"]))
    except (WorkError, ValueError):
        return None


def for_conversation(db: sqlite3.Connection, conversation_id: int | None) -> dict | None:
    """その会話の出力先。会話の番号がなければ (Claude Code の入口) いまの作業。"""
    if conversation_id is None:
        return active(db)
    row = db.execute("SELECT work_id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None or row["work_id"] is None:
        return None
    try:
        return get(db, row["work_id"])
    except WorkError:
        return None


def assign(db: sqlite3.Connection, conversation_id: int, work_id: int) -> dict:
    """会話の出力先を決める。いまの作業 (Claude Code の出力先) もそれに合わせる。"""
    work = get(db, work_id)
    db.execute("UPDATE conversations SET work_id = ? WHERE id = ?", (work_id, conversation_id))
    set_active(db, work_id)
    return work


def set_active(db: sqlite3.Connection, work_id: int | None) -> None:
    if work_id is not None:
        get(db, work_id)
    db.execute("INSERT INTO preferences (key, value) VALUES ('active_work', ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("" if work_id is None else str(work_id),))


# ---- ファイル ------------------------------------------------------------------------------------

def resolve(work: dict, relative: str) -> Path:
    """作業のディレクトリの中のパスにする。外 (.. やリンクで外に出るもの) は断る。"""
    base = Path(work["path"]).resolve()
    target = (base / (relative or ".")).resolve()
    if target != base and not target.is_relative_to(base):
        raise WorkError("作業のディレクトリの外は開けません")
    return target


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in STRUCTURE_SUFFIXES or path.name in STRUCTURE_NAMES:
        return "structure"
    if suffix in IMAGE_TYPES:
        return "image"
    if suffix in HTML_SUFFIXES:
        return "html"
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix in TEXT_SUFFIXES or not suffix:
        return "text"
    return "other"


def files(work: dict, directory: str = "") -> dict:
    """ディレクトリの中身 (ディレクトリが先、名前の順)。"""
    folder = resolve(work, directory)
    if not folder.is_dir():
        raise WorkError("ディレクトリが見つかりません")
    base = Path(work["path"]).resolve()
    entries = []
    for path in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:LIST_LIMIT]:
        if path.name.startswith("."):
            continue
        relative = path.resolve().relative_to(base).as_posix() if path.resolve().is_relative_to(base) else None
        if relative is None:
            continue   # 外を指すリンクは出さない
        entries.append({"name": path.name, "path": relative, "dir": path.is_dir(),
                        "kind": "dir" if path.is_dir() else kind_of(path), "size": path.stat().st_size if path.is_file() else None})
    return {"work": work, "directory": folder.relative_to(base).as_posix() if folder != base else "", "entries": entries}


def _numeric_table(text: str, delimiter: str | None) -> dict | None:
    """数の表として読む。先頭行が名前なら列名に使う。数が少なすぎれば None (文字として見せる)。"""
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        return None
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)) if delimiter else [line.split() for line in lines]

    def number(value: str):
        try:
            return float(value)
        except ValueError:
            return None
    header = rows[0]
    if all(number(v) is not None for v in header):
        header = [f"列 {i + 1}" for i in range(len(rows[0]))]
    else:
        rows = rows[1:]
    rows = [r for r in rows if len(r) == len(header)]
    if len(rows) < 2:
        return None
    stride = max(1, -(-len(rows) // TABLE_ROWS))
    columns = {name: [number(r[i]) for r in rows[::stride]] for i, name in enumerate(header)}
    numeric = {k: v for k, v in columns.items() if sum(x is not None for x in v) >= len(v) * 0.8}
    return {"columns": numeric, "n_rows": len(rows)} if len(numeric) >= 2 else None


def _structure(path: Path) -> dict:
    from ase.io import read   # 重いので使うときに読む
    try:
        atoms = read(path, index=-1)   # 複数のコマがあるファイルは最後のコマ
    except Exception as exc:
        raise WorkError(f"構造として読めません: {exc}") from exc
    stride = max(1, -(-len(atoms) // MAX_STRUCTURE_ATOMS))
    return {"name": path.name, "formula": atoms.get_chemical_formula(mode="metal"), "n_atoms": len(atoms),
            "shown": len(atoms[::stride]), "symbols": atoms.get_chemical_symbols()[::stride],
            "positions": [[round(float(v), 3) for v in p] for p in atoms.get_positions()[::stride]],
            "cell": [[round(float(v), 4) for v in row] for row in atoms.get_cell()], "repeat": [1, 1, 1]}


def open_file(work: dict, relative: str) -> dict:
    """ファイルを見られる形にする: 構造 (3D 用)、表 (グラフ用)、画像 (URL)、文字 (先頭だけ)。"""
    path = resolve(work, relative)
    if not path.is_file():
        raise WorkError("ファイルが見つかりません")
    kind = kind_of(path)
    info = {"path": relative, "name": path.name, "size": path.stat().st_size, "kind": kind}
    if kind == "structure":
        return info | {"structure": _structure(path)}
    if kind in ("image", "html"):
        return info
    with open(path, "rb") as handle:
        head = handle.read(TEXT_LIMIT)
    if b"\0" in head[:8192]:
        return info | {"kind": "other"}
    text = head.decode("utf-8", errors="replace")
    if kind == "table" or kind == "text":
        delimiter = "," if path.suffix.lower() == ".csv" else "\t" if path.suffix.lower() == ".tsv" else None
        table = _numeric_table(text, delimiter) if kind == "table" or path.suffix.lower() in (".dat", ".out") else None
        if table:
            return info | {"kind": "table", "table": table}
    return info | {"kind": "text", "text": text, "truncated": path.stat().st_size > TEXT_LIMIT}


def html_path(work: dict, relative: str) -> Path:
    path = resolve(work, relative)
    if not path.is_file() or path.suffix.lower() not in HTML_SUFFIXES:
        raise WorkError("HTML のファイルが見つかりません")
    return path


def inside(work: dict, paths: list[str]) -> list[str]:
    """アプリが返したファイルのパスのうち、作業のディレクトリの中にあるものを作業からの相対パスにする (外のものは出さない)。"""
    base = Path(work["path"]).resolve()
    found = []
    for raw in paths:
        path = Path(raw).resolve()
        if path.is_file() and path.is_relative_to(base):
            found.append(path.relative_to(base).as_posix())
    return found


def image_path(work: dict, relative: str) -> tuple[Path, str]:
    path = resolve(work, relative)
    media = IMAGE_TYPES.get(path.suffix.lower())
    if not path.is_file() or media is None:
        raise WorkError("画像が見つかりません")
    return path, media


# ---- 書き出し (アプリの道具で作業のディレクトリへ) ----------------------------------------------------

async def export(app, work: dict, kind: str, item_id: str) -> dict:
    """ジョブは jobs/<アプリ>-<番号>/ に、構造は structures/ に置く。"""
    rule = app.config.export or {}
    tool = rule.get(kind)
    if not tool:
        raise WorkError(f"{app.name} には作業に書き出す道具 ({kind}) がありません")
    folder = resolve(work, f"jobs/{app.name}-{item_id}" if kind == "job" else "structures")
    raw_id = int(item_id) if str(item_id).isdigit() else item_id
    key = "job_id" if kind == "job" else "structure_id"
    return await app.call_json(tool, {key: raw_id, rule.get("dir_arg", "directory"): str(folder)})


# ---- ジャービスの道具 (読むだけと、いまの作業への保存) -----------------------------------------------

TOOLS = [
    {"name": "list_work_files", "eager_input_streaming": True,
     "description": "いまの作業のディレクトリのファイルの一覧。計算の結果 (jobs/)・構造 (structures/)・利用者が置いたファイルがある。",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": {"directory": {"type": "string", "description": "作業のディレクトリからの相対パス。空なら一番上"}}}},
    {"name": "read_work_file", "eager_input_streaming": True,
     "description": "いまの作業のファイルを読む。表は列の名前と要約、構造は組成と格子、文字は先頭を返す。",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["path"],
                      "properties": {"path": {"type": "string", "description": "作業のディレクトリからの相対パス"}}}},
    {"name": "save_to_work", "eager_input_streaming": True,
     "description": "ジョブの結果か構造を、いまの作業のディレクトリに書き出す (ジョブは jobs/、構造は structures/)。議論や見返しに使う。",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["app", "kind", "id"],
                      "properties": {"app": {"type": "string"}, "kind": {"type": "string", "enum": ["job", "structure"]},
                                     "id": {"type": ["integer", "string"]}}}},
]
NAMES = {tool["name"] for tool in TOOLS}


def summarize_for_llm(opened: dict) -> dict:
    """ファイルの中身を、LLM に渡す大きさにまとめる (表は列ごとの範囲、構造は組成と格子)。"""
    if opened["kind"] == "table":
        columns = opened["table"]["columns"]
        stats = {}
        for name, values in columns.items():
            numbers = [v for v in values if v is not None]
            if numbers:
                stats[name] = {"min": min(numbers), "max": max(numbers), "first": numbers[0], "last": numbers[-1]}
        return {k: opened[k] for k in ("path", "kind")} | {"n_rows": opened["table"]["n_rows"], "columns": stats}
    if opened["kind"] == "structure":
        st = opened["structure"]
        return {k: opened[k] for k in ("path", "kind")} | {"formula": st["formula"], "n_atoms": st["n_atoms"], "cell": st["cell"]}
    if opened["kind"] == "text":
        return {k: opened[k] for k in ("path", "kind")} | {"text": opened["text"][:20000], "truncated": opened["truncated"]}
    return {k: opened[k] for k in ("path", "kind", "size")}


async def run_tool(db, toolbox, name: str, args: dict, conversation_id: int | None = None) -> str:
    work = for_conversation(db, conversation_id)
    if work is None:
        raise WorkError("この会話の出力先 (作業) が決まっていません。画面で出力先を決めてください")
    if name == "list_work_files":
        return json.dumps(files(work, args.get("directory", "")), ensure_ascii=False)
    if name == "read_work_file":
        return json.dumps(summarize_for_llm(open_file(work, args["path"])), ensure_ascii=False)
    app = toolbox.apps.get(args["app"])
    if app is None:
        raise WorkError(f"アプリ {args['app']} はつないでいません")
    return json.dumps(await export(app, work, args["kind"], str(args["id"])), ensure_ascii=False)
