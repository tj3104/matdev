"""MatDev の設定ファイル (matdev.json)。つなぐアプリ (MCP サーバー) と、頭脳 (LLM) を並べる。

    {
      "llm": {"provider": "anthropic", "model": "claude-opus-5-5"},
      "mcpServers": {
        "matsim": {"command": "$MATSIM_VENV/bin/matsimapi", "args": ["mcp"],
                   "env": {"MATSIM_DATA_DIR": "$MATSIM_DATA_DIR"},
                   "matdev": {"guide": "...", "estimate": {...}, "jobs": {...}, "confirm": [...], "allow": [...]}}
      }
    }

``mcpServers`` は Claude Code の .mcp.json と同じ書き方で、ほかのアプリの MCP サーバーの設定をそのまま貼れる。
``matdev`` の欄は MatDev だけが読む決まり (なければ MCP の印だけで判断する)。値の ``~`` と ``$変数`` は展開する。
ファイルの場所は MATDEV_CONFIG、なければ MatDev のフォルダの matdev.json。ファイルがなければ DEFAULT (MatSim と Claude) で動く。
DEFAULT のパス (MatSim・ezcal の venv、データの場所) は paths.env から決まる (settings.py)。matdev.json でも $MATSIM_VENV などが使える。
"""
from __future__ import annotations

import json
import os
import sys
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import settings

APP_NAME = re.compile(r"^[a-z][a-z0-9_]{0,15}$")   # 道具の名前の頭に付けるので短く、記号なし
PROVIDERS = ("anthropic", "openai")

DEFAULT = {
    "llm": {"provider": "anthropic"},
    "mcpServers": {
        "matsim": {
            "command": settings.MATSIMAPI_BIN, "args": ["mcp"],
            "env": {"MATSIM_DATA_DIR": settings.MATSIM_DATA_DIR},
            "matdev": {
                "guide": ("GPUMD・SevenNet で MD・構造モデリング・解析を動かす。GPU は 1 枚で、ジョブは順番に実行される。"
                          "汎用ポテンシャルの結果は参考値として扱う"),
                "estimate": {"tool": "submit_calculation", "preview": "preview_calculation",
                             "seconds_field": "estimated_seconds", "summary": ["formula", "n_atoms", "total_steps"]},
                "jobs": {"ids": "jobs[].id", "status": "get_job", "id_arg": "job_id", "status_field": "status",
                         "message_field": "message", "done": ["done", "failed", "cancelled"]},
                "confirm": ["cancel_job", "add_structure_file"],
                "allow": ["add_sample_structure", "import_mp_structure", "save_model", "save_final_structure",
                          "submit_analysis", "draft_save", "import_pubchem", "import_crystal"],
                # 作業のディレクトリに結果を書き出す道具。ジャービスには渡さず、MatDev が作業のディレクトリの中にだけ書く
                "export": {"job": "export_job", "structure": "export_structure", "dir_arg": "directory"},
                "view": {"list": {"tool": "get_job_results", "args": {"job_id": "{job_id}"}, "items": "datasets[]"},
                         "data": {"tool": "get_job_dataset", "columns": "data",
                                  "args": {"job_id": "{job_id}", "key": "{key}", "max_rows": 400}},
                         "link": settings.MATSIM_APP_URL + "/#/jobs/{job_id}",
                         # 計算過程の構造の時間変化 (トラジェクトリ)。{symbols, frames: [{index, cell, positions}], n_frames_total}
                         "trajectory": {"tool": "get_job_trajectory", "args": {"job_id": "{job_id}"}},
                         # 終わったジョブの最終構造 (構造緩和の後など) を構造として登録し、次の編集・計算に使う (保存なので関門を通す)
                         "final": {"tool": "save_final_structure", "id_arg": "job_id"},
                         "structure": {"tool": "get_structure_view",
                                       "args": {"structure_id": "{structure_id}", "repeat": "{repeat}"},
                                       "id_arg": "structure_id",
                                       "created_by": {"import_mp_structure": "id", "add_sample_structure": "id",
                                                      "add_structure_file": "id", "save_model": "id",
                                                      "save_final_structure": "id", "draft_save": "id",
                                                      "import_pubchem": "id", "import_crystal": "id"}}},
                # 構造から編集・計算へ (作業台)。入力欄の定義は fields の形 (key・label・type・default・options・show_if)
                "workbench": {"source": "structure", "structure_arg": "structure_id", "kind_arg": "kind",
                              "edit": {"list": "list_modeling_operations", "preview": "preview_model",
                                       "save": "save_model", "structures": "list_structures"},
                              # 編集を重ねて (戻して)、最後に 1 回だけ登録する下書き
                              "draft": {"start": "draft_start", "apply": "draft_apply", "undo": "draft_undo",
                                        "view": "draft_view", "save": "draft_save"},
                              # 有機分子を探して取り込む (PubChem、全方位に真空を空けた箱に入れる)
                              "molecules": {"search": "search_pubchem", "import": "import_pubchem"},
                              # 結晶構造を Materials Project から取り込む (OPTIMADE、API キー不要)
                              "crystals": {"search": "search_crystals", "import": "import_crystal"},
                              "calculate": {"list": "list_calculation_kinds", "describe": "describe_calculation_kind",
                                            "potentials": "list_potentials", "repeat": True,
                                            # MD の構造を記録する間隔 (ステップ) の引数。空なら全体で約 50 コマになるようにする
                                            "traj_arg": "traj_interval"}},
            },
        },
    },
}

# ezcal (第一原理計算の CLI) は、入っていればつなぐ。MatDev の中の MCP の窓口から動かす (adapters/ezcal_mcp.py)
if Path(settings.EZCAL_BIN).exists():
    DEFAULT["mcpServers"]["ezcal"] = {
        "command": sys.executable,   # MatDev の venv の Python (mcp が入っている)
        "args": ["-m", "matdev.adapters.ezcal_mcp"],
        "env": {"EZCAL_BIN": settings.EZCAL_BIN, "EZCAL_MCP_STATE": str(settings.DATA_DIR / "ezcal_jobs")},   # ジョブの記録は MatDev の data に
        "matdev": {
            "guide": ("ezcal で Quantum ESPRESSO の第一原理計算 (scf・relax・vc-relax・バンド・DOS・電荷・auto) と MLIP の MD を流す。"
                      "構造は作業のディレクトリのファイル (structures/ の cif など) を作業からの相対パスで渡す。"
                      "MatSim の構造は作業に置かれた cif を使う。CPU を使い 1 つずつ順番に動く。所要時間は見積もれないので投入は毎回確認になる。"
                      "結果は作業の ezcal/ に summary.json・report.md・図として置かれる"),
            "estimate": {"tool": "submit_task", "preview": "preview_task", "seconds_field": "estimated_seconds",
                         "summary": ["task", "formula", "natoms"]},
            "jobs": {"ids": "jobs[].id", "status": "get_job", "id_arg": "job_id", "status_field": "status",
                     "message_field": "message", "done": ["done", "failed", "cancelled"]},
            "confirm": ["cancel_job"],
            # 結果のグラフ (csv)・図 (png)・Plotly の図 (html)
            "view": {"list": {"tool": "get_job_results", "args": {"job_id": "{job_id}"}, "items": "datasets[]"},
                     "data": {"tool": "get_job_dataset", "args": {"job_id": "{job_id}", "key": "{key}"}, "columns": "data"},
                     "images": "images[]", "html": "html[]"},
            "work": {"submit_task": {"dir": "outdir", "subdir": "ezcal", "paths": ["structure"]},
                     "preview_task": {"paths": ["structure"]}},
            # 作業の構造ファイルから計算へ (作業台)
            "workbench": {"source": "file", "structure_arg": "structure", "kind_arg": "task",
                          "calculate": {"list": "list_tasks", "describe": "describe_task"}},
        },
    }


@dataclass
class AppConfig:
    """つなぐアプリ 1 つ。"""
    name: str
    command: str
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    guide: str = ""
    estimate: dict | None = None     # 投入の前に見積もりを強制する道具 (tool / preview / seconds_field / summary)
    jobs: dict | None = None         # 終わりを見張るジョブ (ids / status / id_arg / status_field / done)
    confirm: list = field(default_factory=list)   # 印に関係なく常に確認する道具
    allow: list = field(default_factory=list)     # 印がなくても確認せずに動かす道具
    view: dict | None = None         # ノートのジョブの結果を見る (list / data の道具でグラフ、link でアプリの画面)
    export: dict | None = None       # 作業のディレクトリへ書き出す道具 (job / structure / dir_arg)。ジャービスには見せない
    work: dict | None = None         # 道具ごとに、会話の作業のディレクトリで決める引数 {道具: {dir: 出力先の引数, paths: [作業の中のパスの引数]}}
    workbench: dict | None = None    # 作業台 (画面のフォームで編集・計算): source (structure / file)・引数の名前・edit / calculate の道具


@dataclass
class LLMConfig:
    """頭脳。provider は anthropic (Claude) か openai (OpenAI 形式: OpenAI・Azure・xAI・ローカルの vLLM や Ollama など)。"""
    provider: str = "anthropic"
    model: str = ""
    base_url: str | None = None      # openai 形式の接続先 (ローカル LLM なら http://<マシン>:8000/v1 など)
    api_key_env: str = ""            # 鍵を読む環境変数の名前 (.env に書く)。ローカルで鍵が要らなければ空
    price_per_mtok: list | None = None   # [入力, 出力] USD/1M トークン。ローカルなら [0, 0]。なければ料金表 (なければ最も高い料金)
    effort: str = ""                 # anthropic だけ
    max_tokens: int = 0


VOCABULARY = ["ジャービス", "MatSim", "GPUMD", "NEP89", "SevenNet", "PLUMED", "Atomsk", "hcp", "bcc", "fcc",
              "相転移", "昇温", "降温", "格子定数", "拡散係数", "融点", "ケルビン", "オングストローム", "ジョブ"]


@dataclass
class SpeechConfig:
    """耳 (音声認識)。provider は whisper (この PC で動かす) か none (使わない)。読み上げは端末の機能を使う。"""
    provider: str = "whisper"
    model: str = "large-v3-turbo"
    device: str = "cpu"              # GPU は計算と取り合うので既定は CPU。cuda にすると速い (約 7 倍)
    compute_type: str = "int8"       # cpu なら int8、cuda なら float16
    language: str = "ja"
    vocabulary: list = field(default_factory=lambda: list(VOCABULARY))   # 聞き取りの手がかりにする語


@dataclass
class WorksConfig:
    """作業のディレクトリを作ってよい場所。作業のディレクトリはこの下にだけ作る (画面から任意の場所に書かせない)。"""
    roots: list = field(default_factory=lambda: [settings.WORKS_ROOT])


@dataclass
class Config:
    llm: LLMConfig
    apps: list[AppConfig]
    path: Path | None = None
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    works: WorksConfig = field(default_factory=WorksConfig)

    def app(self, name: str) -> AppConfig:
        return next(app for app in self.apps if app.name == name)


def _expand(value):
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def config_path() -> Path:
    return Path(os.environ.get("MATDEV_CONFIG", settings.BASE_DIR / "matdev.json"))


def _check_view(name: str, view: dict) -> None:
    if not isinstance(view, dict):
        raise ValueError(f"{name}: view は {{...}} で書いてください")
    structure = view.get("structure")
    if structure is not None and not (isinstance(structure, dict) and "tool" in structure):
        raise ValueError(f"{name}: view.structure に tool がありません")
    if ("list" in view) != ("data" in view):
        raise ValueError(f"{name}: view の list と data は両方書いてください")
    if "list" not in view and "link" not in view and "structure" not in view:
        raise ValueError(f"{name}: view には list と data (グラフ)、または link (アプリの画面) を書いてください")
    for part in ("list", "data"):
        if part in view and not (isinstance(view[part], dict) and "tool" in view[part]):
            raise ValueError(f"{name}: view.{part} に tool がありません")
    link = view.get("link")
    if link is not None and not str(link).startswith(("http://", "https://")):
        raise ValueError(f"{name}: view.link は http:// か https:// で始めてください")


def parse(raw: dict, path: Path | None = None) -> Config:
    """設定の中身を確かめて読む。間違いは理由をつけて ValueError にする。"""
    llm_raw = dict(raw.get("llm") or {})
    unknown = set(llm_raw) - set(LLMConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"llm に知らない項目があります: {', '.join(sorted(unknown))}")
    llm = LLMConfig(**_expand(llm_raw))
    if llm.provider not in PROVIDERS:
        raise ValueError(f"llm.provider は {' / '.join(PROVIDERS)} のどれかです")
    if llm.provider == "anthropic":
        llm.model = os.environ.get("MATDEV_MODEL") or llm.model or settings.MODEL
        llm.api_key_env = llm.api_key_env or "ANTHROPIC_API_KEY"
        llm.effort = llm.effort or settings.EFFORT
    elif not llm.model:
        raise ValueError("llm.model を書いてください (openai 形式ではモデル名が必須)")
    llm.max_tokens = llm.max_tokens or settings.MAX_TOKENS
    servers = raw.get("mcpServers") or {}
    if not servers:
        raise ValueError("mcpServers にアプリが 1 つもありません")
    apps = []
    for name, server in servers.items():
        if not APP_NAME.match(name):
            raise ValueError(f"アプリの名前 {name!r} は英小文字で始まる 16 文字までの英小文字・数字・_ にしてください")
        if "command" not in server:
            raise ValueError(f"{name}: command がありません (いまは標準入出力の MCP サーバーだけ)")
        extra = server.get("matdev") or {}
        view = extra.get("view")
        if view is not None:
            _check_view(name, view)
        apps.append(AppConfig(name=name, command=_expand(server["command"]), args=_expand(server.get("args", [])),
                              env={k: str(v) for k, v in _expand(server.get("env", {})).items()},
                              guide=extra.get("guide", ""), estimate=extra.get("estimate"), jobs=extra.get("jobs"),
                              confirm=list(extra.get("confirm", [])), allow=list(extra.get("allow", [])),
                              view=_expand(view), export=extra.get("export"), work=extra.get("work"),
                              workbench=extra.get("workbench")))
    speech_raw = dict(raw.get("speech") or {})
    unknown = set(speech_raw) - set(SpeechConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"speech に知らない項目があります: {', '.join(sorted(unknown))}")
    speech = SpeechConfig(**speech_raw)
    if speech.provider not in ("whisper", "none"):
        raise ValueError("speech.provider は whisper か none です")
    works_raw = dict(raw.get("works") or {})
    if set(works_raw) - {"roots"}:
        raise ValueError("works に書けるのは roots (作業のディレクトリを作ってよい場所の一覧) だけです")
    works = WorksConfig(**({"roots": _expand(works_raw["roots"])} if "roots" in works_raw else {}))
    if not works.roots or not all(isinstance(r, str) for r in works.roots):
        raise ValueError("works.roots には場所を 1 つ以上書いてください")
    return Config(llm=llm, apps=apps, path=path, speech=speech, works=works)


def load() -> Config:
    path = config_path()
    if path.exists():
        return parse(json.loads(path.read_text()), path)
    return parse(DEFAULT)
