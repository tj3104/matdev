"""ezcal (構造ファイル 1 つで第一原理計算を回す CLI) を MatDev につなぐ MCP サーバー。

    ~/.venv/matdev312/bin/python -m matdev.adapters.ezcal_mcp      (標準入出力の MCP サーバー)

ezcal 本体とその venv には手を入れず、ezcal のコマンドを子プロセスで動かす (EZCAL_BIN、既定 ~/.venv/ezcalenv312/bin/ezcal)。
- QE は CPU を使い切るので、ジョブは 1 つずつ順番に流す。状態は EZCAL_MCP_STATE (既定 ~/.ezcal/matdev_jobs) の jobs.json に残し、
  MatDev を立ち上げ直しても続きを追える (計算の子プロセスは切り離して動かす)
- 出力先 (outdir) と構造ファイルの場所は、MatDev の関門が会話の作業のディレクトリで決める (設定の matdev.work)
- 渡せる選択肢は OPTIONS に書いたものだけ。任意の Python を動かす --calc-script などは渡さない
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

EZCAL = os.environ.get("EZCAL_BIN", str(Path.home() / ".venv/ezcalenv312/bin/ezcal"))
STATE = Path(os.environ.get("EZCAL_MCP_STATE", str(Path.home() / ".ezcal" / "matdev_jobs")))
READ = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE = ToolAnnotations(destructiveHint=True)

TASKS = {
    "scf": "自己無撞着場 (SCF) 計算。全エネルギー・フェルミ準位・ギャップ",
    "relax": "原子位置の最適化 (セルは固定)",
    "vc-relax": "原子位置とセルの最適化 (格子定数が出る)",
    "bands": "バンド構造 (scf を前段で自動実行)",
    "dos": "全状態密度と部分状態密度 (scf・nscf を前段で自動実行)",
    "charge": "電荷密度と原子電荷 (Loewdin・Bader)",
    "auto": "一通り: vc-relax → scf → nscf → dos → bands",
    "md-mlip": "機械学習ポテンシャルで MD / MC (SevenNet など)",
    "md-qe": "QE で第一原理 MD",
}
# 渡してよい選択肢: 名前 → 型 ("float" / "int" / "str" / "bool" / "int3")
OPTIONS = {
    "ecutwfc": "float", "ecutrho": "float", "kmesh": "int3", "kspacing": "float", "functional": "str",
    "occupations": "str", "smearing": "str", "degauss": "float", "conv_thr": "float", "nbnd": "int",
    "spin": "bool", "magmom": "str", "afm": "str", "hubbard_u": "str", "vdw": "str", "np": "int", "npool": "int",
    "line_density": "float", "band_scheme": "str", "emin": "float", "emax": "float", "relax": "bool",
    "fixed_cell": "bool", "charge": "bool", "primitive": "bool", "plot": "str",
    # MD / MC
    "mode": "str", "temperature": "float", "steps": "int", "ensemble": "str", "timestep": "float", "pressure": "float",
    "supercell": "int3", "seed": "int", "thermostat": "str", "init_temperature": "float", "mlip_backend": "str",
    "model": "str", "device": "str", "engine": "str",
}
SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.,=/+\-:; ]{1,80}$")
LABELS = {"scf": "SCF", "relax": "構造最適化 (原子)", "vc-relax": "構造最適化 (原子とセル)", "bands": "バンド構造",
          "dos": "状態密度 (DOS)", "charge": "電荷密度・原子電荷", "auto": "一通り (auto)", "md-mlip": "MLIP の MD / MC",
          "md-qe": "第一原理 MD"}
GROUPS = {"md-mlip": "MD", "md-qe": "MD"}
Task = Literal["scf", "relax", "vc-relax", "bands", "dos", "charge", "auto", "md-mlip", "md-qe"]   # 画面では選択肢になる

# 画面の入力欄の定義 (MatDev の作業台の形: key・label・type・default・options・min・max・optional・advanced)
def _select(key, label, values, default, advanced=False):
    return {"key": key, "label": label, "type": "select", "default": default, "advanced": advanced,
            "options": [{"value": v, "label": v} for v in values]}


DFT_FIELDS = [
    _select("functional", "汎関数", ["pbe", "pbesol", "pz"], "pbe"),
    {"key": "kspacing", "label": "k 点の間隔", "type": "number", "default": 0.25, "min": 0.05, "max": 1.0, "unit": "1/Å",
     "hint": "小さいほど k 点が多く精度が上がる"},
    {"key": "ecutwfc", "label": "カットオフ (空なら擬ポテンシャルの推奨)", "type": "number", "default": None, "min": 10, "max": 300,
     "unit": "Ry", "optional": True},
    {"key": "spin", "label": "スピン分極", "type": "bool", "default": False},
    {"key": "magmom", "label": "初期磁化 (例: Fe=0.6)", "type": "text", "default": "", "optional": True, "show_if": {"spin": True},
     "max_length": 80},
    {"key": "hubbard_u", "label": "DFT+U (例: Fe=4.0)", "type": "text", "default": "", "optional": True, "advanced": True,
     "max_length": 80},
    _select("occupations", "占有の扱い", ["smearing", "fixed", "tetrahedra"], "smearing", advanced=True),
    {"key": "degauss", "label": "スメアリング幅", "type": "number", "default": 0.02, "min": 0.001, "max": 0.1, "unit": "Ry",
     "advanced": True, "show_if": {"occupations": "smearing"}},
    {"key": "np", "label": "MPI の並列数", "type": "int", "default": 8, "min": 1, "max": 128, "advanced": True},
    _select("plot", "図の出し方", ["both", "matplotlib", "plotly"], "both", advanced=True),
]
DEFAULT_PLOT = "both"   # 図は png (matplotlib) と、回せる html (Plotly) の両方を出す
TASK_FIELDS = {
    "scf": DFT_FIELDS, "relax": DFT_FIELDS, "vc-relax": DFT_FIELDS, "dos": DFT_FIELDS, "charge": DFT_FIELDS,
    "bands": DFT_FIELDS + [_select("band_scheme", "高対称線の決め方", ["materials_project", "latimer_munro",
                                                                    "setyawan_curtarolo", "seekpath"], "materials_project")],
    "auto": DFT_FIELDS + [{"key": "relax", "label": "先に構造最適化", "type": "bool", "default": True},
                          {"key": "fixed_cell", "label": "セルは固定", "type": "bool", "default": False, "show_if": {"relax": True}},
                          {"key": "charge", "label": "電荷密度も出す", "type": "bool", "default": False}],
    "md-mlip": [_select("mode", "モード", ["md", "mcmc", "mcmd", "mcrelax", "kmc"], "md"),
                _select("plot", "図の出し方", ["both", "matplotlib", "plotly"], "both", advanced=True),
                {"key": "temperature", "label": "温度", "type": "number", "default": 300, "min": 1, "max": 5000, "unit": "K"},
                {"key": "steps", "label": "ステップ数", "type": "int", "default": 1000, "min": 1, "max": 10000000},
                _select("ensemble", "アンサンブル", ["nvt", "npt", "nve", "nph"], "nvt"),
                {"key": "timestep", "label": "時間刻み", "type": "number", "default": 1.0, "min": 0.1, "max": 10, "unit": "fs"},
                {"key": "supercell", "label": "スーパーセル (例: 2 2 2)", "type": "text", "default": "", "optional": True,
                 "max_length": 20},
                _select("mlip_backend", "MLIP", ["sevennet", "mace", "chgnet", "orb", "matgl", "emt"], "sevennet"),
                _select("device", "計算機", ["auto", "cuda", "cpu"], "auto", advanced=True)],
    "md-qe": [_select("ensemble", "アンサンブル", ["nve", "nvt", "npt", "nph"], "nvt"),
              {"key": "temperature", "label": "温度", "type": "number", "default": 300, "min": 1, "max": 5000, "unit": "K"},
              {"key": "steps", "label": "ステップ数", "type": "int", "default": 50, "min": 1, "max": 100000},
              {"key": "timestep", "label": "時間刻み", "type": "number", "default": 2.0, "min": 0.1, "max": 10, "unit": "fs"},
              {"key": "supercell", "label": "スーパーセル (例: 2 2 2)", "type": "text", "default": "", "optional": True,
               "max_length": 20}] + DFT_FIELDS,
}

server = MCPServer("ezcal", instructions=(
    "ezcal で第一原理計算 (Quantum ESPRESSO) と MLIP の MD を流す。構造は作業のディレクトリにある cif / POSCAR / xyz を渡す。"
    "投入の前に preview_task で構造と推奨条件を確かめる。ジョブは 1 つずつ順番に動く。結果は作業のディレクトリの ezcal/ に置かれる"))
_lock = threading.Lock()


# ---- ジョブの記録 ---------------------------------------------------------------------------------

def _load() -> dict:
    try:
        return json.loads((STATE / "jobs.json").read_text())
    except (OSError, ValueError):
        return {"next": 1, "jobs": {}}


def _save(state: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / "jobs.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(STATE / "jobs.json")


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # 終わったが親が回収していない子 (zombie) は終わったものとする
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def _summary(job: dict) -> dict | None:
    try:
        return json.loads((Path(job["rundir"]) / "summary.json").read_text())
    except (OSError, ValueError):
        return None


def _tick() -> None:
    """動いているジョブの終わりを確かめ、空いていれば次を始める。"""
    with _lock:
        state = _load()
        jobs = state["jobs"]
        running = [j for j in jobs.values() if j["status"] == "running"]
        for job in running:
            if _alive(job.get("pid")):
                continue
            summary = _summary(job)
            ok = bool(summary and summary.get("ok", True) and (Path(job["rundir"]) / "summary.json").exists())
            job["status"] = "done" if ok else "failed"
            job["message"] = "" if ok else _tail(job, 6)
            job["finished_at"] = time.time()
        if not any(j["status"] == "running" for j in jobs.values()):
            queued = sorted((j for j in jobs.values() if j["status"] == "queued"), key=lambda j: j["id"])
            if queued:
                _start(queued[0])
        _save(state)


def _start(job: dict) -> None:
    Path(job["rundir"]).parent.mkdir(parents=True, exist_ok=True)
    log = open(job["log"], "w")
    # 作業のディレクトリで動かす (そこに qe_config.yaml を置けば作業ごとに並列数などを変えられる)。MatDev とは切り離す
    process = subprocess.Popen(job["command"], cwd=job["cwd"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    job.update(status="running", pid=process.pid, started_at=time.time())
    threading.Thread(target=process.wait, daemon=True).start()   # 終わった子を回収する


def _tail(job: dict, lines: int = 20) -> str:
    try:
        text = Path(job["log"]).read_text(errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return "\n".join(text.strip().splitlines()[-lines:])


def _ticker() -> None:
    while True:
        try:
            _tick()
        except Exception as exc:  # 見張りは止めない
            print(f"[ezcal_mcp] {type(exc).__name__}: {exc}", flush=True)
        time.sleep(5)


# ---- 入力 ---------------------------------------------------------------------------------------

def _structure(path: str) -> str:
    target = Path(path).expanduser()
    if not target.is_file():
        raise ValueError(f"構造ファイルが見つかりません: {path}")
    return str(target)


def _arguments(options: dict | None) -> list[str]:
    args = []
    for key, value in (options or {}).items():
        if value is None or value == "":
            continue   # 空の欄は ezcal の既定にまかせる
        kind = OPTIONS.get(key)
        if kind is None:
            raise ValueError(f"選択肢 {key} は使えません (使えるもの: {', '.join(sorted(OPTIONS))})")
        flag = "--" + key.replace("_", "-")
        if kind == "bool":
            if key in ("relax", "primitive"):
                args.append(flag if value else ("--no-relax" if key == "relax" else "--as-is"))
            elif value:
                args.append(flag)
        elif kind == "int3":
            if isinstance(value, str):   # 画面からは "2 2 2" や "2,2,2" で来る
                parts = re.split(r"[\s,]+", value.strip())
                value = [int(v) for v in parts] if all(v.isdigit() for v in parts) else value
            if not (isinstance(value, list) and len(value) == 3 and all(isinstance(v, int) and v > 0 for v in value)):
                raise ValueError(f"{key} は正の整数 3 つで指定してください")
            args += [flag, *map(str, value)]
        elif kind in ("float", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} は数で指定してください")
            args += [flag, str(int(value) if kind == "int" else float(value))]
        else:
            if not isinstance(value, str) or not SAFE_TEXT.match(value):
                raise ValueError(f"{key} に使えない文字があります")
            args += [flag, value]
    return args


def _public(job: dict) -> dict:
    out = {k: job.get(k) for k in ("id", "task", "structure", "status", "message", "rundir", "options")}
    summary = _summary(job) if job["status"] in ("done", "failed") else None
    if summary:   # 長い一覧は除いて要点だけ
        out["summary"] = {k: v for k, v in summary.items() if not isinstance(v, (list, dict)) or k in ("steps", "averages", "results")}
    if job["status"] in ("running", "failed"):
        out["log_tail"] = _tail(job, 12)
    return out


# ---- 道具 ---------------------------------------------------------------------------------------

@server.tool(annotations=READ)
def list_tasks() -> list[dict]:
    """ezcal で流せる計算の種類 (key・名前・分類・説明)。key を preview_task・submit_task の task に渡す。"""
    return [{"key": key, "label": LABELS[key], "group": GROUPS.get(key, "第一原理 (QE)"), "note": note} for key, note in TASKS.items()]


@server.tool(annotations=READ)
def describe_task(task: Task) -> dict:
    """計算の種類の入力欄の定義 (options に渡せる key・既定値・範囲・選択肢)。渡せる選択肢の全体は options_all。"""
    if task not in TASKS:
        raise ValueError(f"task は {', '.join(TASKS)} のどれかです")
    return {"key": task, "label": LABELS[task], "note": TASKS[task], "fields": TASK_FIELDS[task], "options_all": OPTIONS,
            "no_repeat": True}


@server.tool(annotations=READ)
def preview_task(task: Task, structure: str, options: dict | None = None) -> dict:
    """投入せずに確かめる: 構造の対称性・推奨 k メッシュ・擬ポテンシャル・カットオフと、実行するコマンド。"""
    if task not in TASKS:
        raise ValueError(f"task は {', '.join(TASKS)} のどれかです")
    path = _structure(structure)
    command = [EZCAL, task, path, *_arguments(options)]
    info = subprocess.run([EZCAL, "info", path], capture_output=True, text=True, timeout=180)
    text = re.sub(r"\x1b\[[0-9;]*m", "", info.stdout + info.stderr).strip()
    facts = {}
    block = re.search(r"\{.*?\n\}", text, re.S)   # 構造の要点は JSON で出る
    if block:
        try:
            facts = json.loads(block.group(0))
        except ValueError:
            facts = {}
    return {"task": task, "structure": path, "formula": facts.get("full_formula") or facts.get("formula"),
            "natoms": facts.get("natoms"), "spacegroup": facts.get("spacegroup"), "info": text[-4000:],
            "command": " ".join(command[1:]),
            "notes": ["第一原理計算の所要時間は見積もれません。構造の大きさと k 点の数で数分〜数時間かかります"]}


@server.tool()
def submit_task(task: Task, structure: str, outdir: str, options: dict | None = None, name: str = "") -> dict:
    """計算を順番待ちに入れて、すぐに戻る。結果は outdir/<名前>/ に summary.json・report.md・図と一緒に置かれる。"""
    if task not in TASKS:
        raise ValueError(f"task は {', '.join(TASKS)} のどれかです")
    path = _structure(structure)
    options = {"plot": DEFAULT_PLOT, **(options or {})}
    if options["plot"] not in ("both", "matplotlib", "plotly", "none"):
        raise ValueError("plot は both / matplotlib / plotly / none のどれかです")
    extra = _arguments(options)
    if name and not re.match(r"^[A-Za-z0-9_.\-]{1,60}$", name):
        raise ValueError("name は英数字と _ . - の 60 文字までです")
    with _lock:
        state = _load()
        job_id = state["next"]
        state["next"] += 1
        label = name or f"{Path(path).stem}_{task}_{job_id}"
        root = Path(outdir).expanduser()
        job = {"id": job_id, "task": task, "structure": path, "options": options or {}, "status": "queued", "message": "",
               "rundir": str(root / label), "cwd": str(root.parent if root.name == "ezcal" else root),
               "log": str(STATE / f"job{job_id}.log"), "created_at": time.time(),
               "command": [EZCAL, task, path, *extra, "--outdir", str(root), "--name", label]}
        state["jobs"][str(job_id)] = job
        _save(state)
    _tick()
    return {"jobs": [{"id": job_id}], "rundir": job["rundir"]}


@server.tool(annotations=READ)
def get_job(job_id: int) -> dict:
    """ジョブの状態 (queued / running / done / failed / cancelled)。終わっていれば summary.json の要点も返す。"""
    _tick()
    job = _load()["jobs"].get(str(job_id))
    if job is None:
        raise ValueError("ジョブが見つかりません")
    return _public(job)


@server.tool(annotations=READ)
def list_jobs(limit: int = 20) -> list[dict]:
    """新しい順のジョブの一覧。"""
    _tick()
    jobs = sorted(_load()["jobs"].values(), key=lambda j: -j["id"])[:limit]
    return [{k: j.get(k) for k in ("id", "task", "structure", "status", "rundir")} for j in jobs]


# 結果の表 (csv) の題と、既定で描く列
TABLE_TITLES = {"dos.csv": "状態密度 (DOS)", "bands.csv": "バンド構造", "energy_log.csv": "MD・MC の記録", "convergence.csv": "収束"}
MAX_ROWS = 2000


def _job(job_id: int) -> dict:
    job = _load()["jobs"].get(str(job_id))
    if job is None:
        raise ValueError("ジョブが見つかりません")
    return job


def _tables(rundir: Path) -> list[Path]:
    found = [*sorted(rundir.glob("*.csv")), *sorted((rundir / "plots").glob("*.csv"))]
    return [path for path in found if path.is_file()]


def _header(path: Path) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.readline().strip().split(",")


def _default_y(name: str, columns: list[str]) -> list[str]:
    """既定で描く列。バンドは全部の線、DOS は全体と部分、記録はエネルギー。"""
    rest = columns[1:]
    if name == "bands.csv":
        return rest
    if name == "dos.csv":
        return [c for c in rest if c == "dos" or c.startswith("pdos")] or rest[:1]
    for want in ("energy_eV", "e_total_eV", "temperature_K"):
        if want in rest:
            return [want]
    return rest[:1]


@server.tool(annotations=READ)
def get_job_results(job_id: int) -> dict:
    """ジョブの結果の一覧: グラフにできる表 (datasets: key・title・x・y・columns)、図 (images)、Plotly の図 (html)、要点 (highlights)。"""
    job = _job(job_id)
    rundir = Path(job["rundir"])
    datasets = []
    for path in _tables(rundir):
        columns = _header(path)
        if len(columns) < 2:
            continue
        key = path.relative_to(rundir).as_posix()
        datasets.append({"key": key, "title": TABLE_TITLES.get(path.name, path.stem), "x": columns[0],
                         "y": _default_y(path.name, columns), "columns": [{"key": c, "label": c} for c in columns]})
    plots = rundir / "plots"
    images = [str(p) for p in sorted(plots.glob("*.png"))] if plots.is_dir() else []
    html = [str(p) for p in sorted(plots.glob("*.html"))] if plots.is_dir() else []
    summary = _summary(job) or {}
    highlights = {k: v for k, v in summary.items() if isinstance(v, (int, float, str)) and k not in ("rundir", "workdir")}
    return {"status": job["status"], "rundir": str(rundir), "datasets": datasets, "images": images, "html": html,
            "highlights": highlights}


@server.tool(annotations=READ)
def get_job_dataset(job_id: int, key: str) -> dict:
    """表 1 つ分 (key は get_job_results の datasets のもの)。行が多いときは等間隔に間引く。"""
    rundir = Path(_job(job_id)["rundir"]).resolve()
    path = (rundir / key).resolve()
    if not path.is_relative_to(rundir) or path.suffix != ".csv" or not path.is_file():
        raise ValueError("表が見つかりません")
    with open(path, encoding="utf-8", errors="replace") as handle:
        rows = [line.strip().split(",") for line in handle if line.strip()]
    header, body = rows[0], rows[1:]
    stride = max(1, -(-len(body) // MAX_ROWS))

    def number(value: str):
        try:
            return float(value)
        except ValueError:
            return None
    data = {name: [number(r[i]) if i < len(r) else None for r in body[::stride]] for i, name in enumerate(header)}
    return {"key": key, "n_rows": len(body), "data": {k: v for k, v in data.items() if any(x is not None for x in v)}}


@server.tool(annotations=DESTRUCTIVE)
def cancel_job(job_id: int) -> dict:
    """順番待ちか実行中のジョブを止める。実行中なら QE のプロセスごと止める。"""
    with _lock:
        state = _load()
        job = state["jobs"].get(str(job_id))
        if job is None:
            raise ValueError("ジョブが見つかりません")
        if job["status"] == "running" and _alive(job.get("pid")):
            try:
                os.killpg(job["pid"], signal.SIGTERM)
            except OSError:
                pass
        if job["status"] in ("queued", "running"):
            job.update(status="cancelled", finished_at=time.time())
        _save(state)
    return _public(job)


def main() -> None:
    threading.Thread(target=_ticker, name="ezcal-ticker", daemon=True).start()
    server.run("stdio")


if __name__ == "__main__":
    main()
