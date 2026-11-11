"""python -m matdev doctor: paths.env のパスと venv で MatDev が動くかを確かめる (./run.sh doctor からも)。

別の環境に持っていったときに、どこを直せばよいかを 1 行ずつ出す。何も書き換えない。
- MatDev の venv に要るパッケージ
- MatSim の venv (matsimapi の MCP サーバー・対話型 MD の Python とそのパッケージ)、MatSim のデータの場所
- ezcal (任意。なければつながないだけ)
- データ・作業の場所に書けるか、設定ファイル (matdev.json) が読めるか
- つなぐアプリを実際に起動して、道具の数を数える
「✗」があると終了コード 1。「△」は任意のもの (なくてもほかは動く)。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import settings

OK, NG, MAYBE = "✓", "✗", "△"


class Report:
    def __init__(self) -> None:
        self.failed = False

    def line(self, mark: str, label: str, detail: str = "", hint: str = "") -> None:
        if mark == NG:
            self.failed = True
        print(f"  {mark} {label}" + (f": {detail}" if detail else ""))
        if hint and mark != OK:
            print(f"      → {hint}")

    @staticmethod
    def section(title: str) -> None:
        print(f"\n[{title}]")


def _executable(path: str) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def _imports(python: str, modules: list[str]) -> dict[str, bool]:
    """別の venv の Python で、パッケージが読み込めるかを調べる (読み込まずに探すだけ)。"""
    code = ("import importlib.util, json, sys; "
            "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in sys.argv[1:]}))")
    try:
        done = subprocess.run([python, "-c", code, *modules], capture_output=True, text=True, timeout=60)
        import json
        return json.loads(done.stdout.strip().splitlines()[-1])
    except Exception:   # 動かない Python
        return {}


def _writable(path: Path) -> tuple[bool, str]:
    target = path
    while not target.exists():   # まだない場所は、作れる親があるか
        target = target.parent
    if os.access(target, os.W_OK):
        return True, "あり" if path.exists() else f"なし (最初に使うときに作る。{target} に書ける)"
    return False, f"{target} に書けません"


async def _connect(report: Report) -> None:
    from .config import load
    from .tools import Toolbox

    config = load()
    toolbox = Toolbox(config.apps)
    try:
        await asyncio.wait_for(toolbox.start(), 120)
        for name, app in toolbox.apps.items():
            if app.error:
                report.line(NG, f"{name} を起動", app.error, f"{app.config.command} {' '.join(app.config.args)} を確かめてください")
            else:
                report.line(OK, f"{name} を起動", f"道具 {len(app.tools)} 個")
                if app.tool("get_system_status") is not None:   # MatSim: NEP を GPUMD と CPU のどちらで計算するか
                    try:
                        engine = (await app.call_json("get_system_status", {})).get("engine") or {}
                    except Exception:   # 古い MatSim など
                        engine = {}
                    if engine:
                        cpu = engine.get("md") == "cpu"
                        report.line(MAYBE if cpu else OK, f"  {name} の計算エンジン",
                                    "CPU (calorine の CPUNEP。MD・静的計算・フォノン分散だけ)" if cpu else "GPUMD",
                                    "GPU で速く計算するなら、MatSim の paths.env の MATSIM_GPUMD_BIN に GPUMD を書いてください")
    except asyncio.TimeoutError:
        report.line(NG, "アプリの起動", "120 秒で終わりませんでした")
    finally:
        await toolbox.stop()


def main() -> int:
    report = Report()
    print("MatDev の環境の確認")

    report.section("パスの設定 (paths.env)")
    if settings.PATHS_FILE.is_file():
        report.line(OK, "paths.env", str(settings.PATHS_FILE))
    else:
        report.line(MAYBE, "paths.env", f"{settings.PATHS_FILE} がありません。既定の場所で動かします",
                    "cp paths.example.env paths.env として、この環境の場所に書き換えてください")

    report.section("MatDev の venv")
    report.line(OK, "Python", f"{sys.executable} ({sys.version.split()[0]})")
    for module, required in (("fastapi", True), ("uvicorn", True), ("anthropic", True), ("mcp", True), ("jsonschema", True),
                             ("openai", False), ("faster_whisper", False)):
        if importlib.util.find_spec(module):
            report.line(OK, module)
        elif required:
            report.line(NG, module, "入っていません", f"{sys.executable} -m pip install -e {settings.BASE_DIR}")
        else:
            report.line(MAYBE, module, "入っていません (OpenAI 形式の LLM / 声の入力で使う)",
                        f"{sys.executable} -m pip install -e {settings.BASE_DIR}")

    report.section("MatSim (計算・構造)")
    report.line(OK if settings.MATSIM_VENV.is_dir() else NG, "MATSIM_VENV", str(settings.MATSIM_VENV),
                "paths.env の MATSIM_VENV に、matsimapi を入れた venv を書いてください")
    report.line(OK if _executable(settings.MATSIMAPI_BIN) else NG, "matsimapi のコマンド", settings.MATSIMAPI_BIN,
                "MatSim の venv に MatSim のフォルダの packages/matsimapi を入れるか、MATDEV_MATSIMAPI_BIN を直してください")
    if _executable(settings.IMD_PYTHON):
        found = _imports(settings.IMD_PYTHON, ["matsimapi", "ase", "calorine", "sevenn"])
        report.line(OK if found else NG, "対話型 MD の Python", settings.IMD_PYTHON, "この Python が動きません")
        for module, use in (("matsimapi", None), ("ase", None), ("calorine", "対話型 MD の NEP (CPU)"),
                            ("sevenn", "対話型 MD の SevenNet")):
            if found.get(module):
                report.line(OK, f"  {module}")
            elif found:
                report.line(MAYBE if use else NG, f"  {module}", f"入っていません{f' ({use}で使う)' if use else ''}",
                            f"{settings.IMD_PYTHON} -m pip install {module}")
    else:
        report.line(NG, "対話型 MD の Python", f"{settings.IMD_PYTHON} がありません", "MATDEV_IMD_PYTHON を直してください")
    ok, detail = _writable(Path(settings.MATSIM_DATA_DIR))
    report.line(OK if ok else NG, "MATSIM_DATA_DIR", f"{settings.MATSIM_DATA_DIR} ({detail})",
                "MatSimApp と同じデータを使うなら、MatSimApp の data の場所を書いてください")

    report.section("ezcal (第一原理計算、任意)")
    if _executable(settings.EZCAL_BIN):
        report.line(OK, "ezcal のコマンド", f"{settings.EZCAL_BIN} (つなぎます)")
    else:
        report.line(MAYBE, "ezcal のコマンド", f"{settings.EZCAL_BIN} がありません (ezcal はつなぎません)",
                    "使うなら paths.env の EZCAL_VENV (または EZCAL_BIN) を書いてください")
    if shutil.which("pw.x") is None and _executable(settings.EZCAL_BIN):
        report.line(MAYBE, "Quantum ESPRESSO (pw.x)", "PATH にありません (ezcal の設定で場所を決めていれば問題なし)")

    report.section("データと作業の場所")
    for label, path in (("MATDEV_DATA_DIR", settings.DATA_DIR), ("MATDEV_WORKS_ROOT", Path(settings.WORKS_ROOT)),
                        ("MATDEV_MODELS_DIR", settings.MODELS_DIR)):
        ok, detail = _writable(path)
        report.line(OK if ok else NG, label, f"{path} ({detail})", f"paths.env の {label} を書ける場所にしてください")

    report.section("設定ファイルと入口")
    try:
        from .config import config_path, load
        config = load()
        where = config_path() if config.path else "なし (既定の設定)"
        report.line(OK, "matdev.json", f"{where} / アプリ: {', '.join(a.name for a in config.apps)} / LLM: {config.llm.provider}")
    except Exception as exc:
        report.line(NG, "matdev.json", str(exc), "matdev.example.json を見て直してください")
        config = None
    report.line(OK, "ポート", f"127.0.0.1:{os.environ.get('MATDEV_PORT', '8041')}")
    report.line(OK, "受ける接続の名前", ", ".join(settings.ALLOWED_HOSTS),
                "スマホから Tailscale で使うなら、MATDEV_ALLOWED_HOSTS にこの PC の Tailscale の名前を足してください")
    claude = os.environ.get("CLAUDE_BIN", "claude")
    report.line(OK if shutil.which(claude) else MAYBE, "Claude Code (claude_matdev.sh で使う、任意)",
                shutil.which(claude) or f"{claude} が見つかりません")
    env_file = settings.BASE_DIR / ".env"
    if env_file.is_file():
        mode = env_file.stat().st_mode & 0o777
        report.line(OK if mode == 0o600 else NG, ".env", f"権限 {mode:o}", "chmod 600 .env")
    else:
        report.line(MAYBE, ".env", "ありません (API キーと月の上限を書く)", "cp .env.example .env && chmod 600 .env")

    if config is not None and "--quick" not in sys.argv:
        report.section("アプリを起動して確かめる")
        asyncio.run(_connect(report))

    print("\n" + ("直すところがあります (✗)。上の → を見てください" if report.failed else "動かせます。./run.sh で起動してください"))
    return 1 if report.failed else 0
