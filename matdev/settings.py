"""パスや上限などの設定。

各アプリのパスと venv は paths.env (ひな形は paths.example.env) に書く。読む順は 環境変数 (.env を含む) → paths.env → 下の既定。
別の環境では paths.env を書き換えるだけで動くようにする。確かめるのは python -m matdev doctor。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PATHS_FILE = Path(os.environ.get("MATDEV_PATHS", BASE_DIR / "paths.env"))
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_paths(path: Path = PATHS_FILE) -> dict[str, str]:
    """paths.env を読み、まだ環境変数にない値だけ os.environ に入れる (子プロセスにも渡る)。読んだ値を返す。
    書き方は KEY=値。# の行と空行は飛ばし、~ と $変数 (上の行のものも) を展開する。"""
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key = key.strip()
        if not sep or not _KEY.match(key):
            raise ValueError(f"{path}:{number}: KEY=値 の形ではありません: {raw}")
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()   # 行の終わりのコメント
        value = os.path.expanduser(os.path.expandvars(value))
        found[key] = value
        os.environ.setdefault(key, value)
    return found


load_paths()


def _venv(key: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(key, default)))


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
DATA_DIR = Path(os.environ.get("MATDEV_DATA_DIR", BASE_DIR / "data"))
MATSIM_VENV = _venv("MATSIM_VENV", "~/.venv/gpumd312")      # MatSim (matsimapi・GPUMD・SevenNet・ASE) の venv
EZCAL_VENV = _venv("EZCAL_VENV", "~/.venv/ezcalenv312")     # ezcal の venv
IMD_PYTHON = os.environ.get("MATDEV_IMD_PYTHON", str(MATSIM_VENV / "bin" / "python"))   # 対話型 MD の係 (ASE・NEP・SevenNet)
EZCAL_BIN = os.environ.get("EZCAL_BIN", str(EZCAL_VENV / "bin" / "ezcal"))   # 第一原理計算の CLI (あればつなぐ)
WORKS_ROOT = os.environ.get("MATDEV_WORKS_ROOT", str(Path.home() / "matdev_works"))   # 作業のディレクトリを作る場所の既定
MODELS_DIR = Path(os.environ.get("MATDEV_MODELS_DIR", BASE_DIR / "data" / "models"))   # 音声認識のモデル (1.6 GB)
DB_PATH = DATA_DIR / "matdev.db"

# 計算の道具 (MatSimAPI の MCP サーバー)。MatSim の venv の中のものを子プロセスとして動かす
MATSIMAPI_BIN = os.environ.get("MATDEV_MATSIMAPI_BIN", str(MATSIM_VENV / "bin" / "matsimapi"))
def _sibling_matsim() -> Path:
    """MatSim のデータの既定: MatDev と並べて置いた MatSim のフォルダ (matsim か 11_MatSimApp) の data/。"""
    for name in ("matsim", "11_MatSimApp"):
        if (BASE_DIR.parent / name).is_dir():
            return BASE_DIR.parent / name / "data"
    return BASE_DIR.parent / "matsim" / "data"


MATSIM_DATA_DIR = os.environ.get("MATSIM_DATA_DIR", str(_sibling_matsim()))

# 決めた値を環境変数にも置く (matdev.json の $MATSIM_VENV などと、子プロセスのため)
for _key, _value in {"MATSIM_VENV": MATSIM_VENV, "EZCAL_VENV": EZCAL_VENV, "MATSIM_DATA_DIR": MATSIM_DATA_DIR}.items():
    os.environ.setdefault(_key, str(_value))

MODEL = os.environ.get("MATDEV_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("MATDEV_EFFORT", "high")
MAX_TOKENS = 64000
MAX_ROUNDS = 40              # 1 回の依頼で LLM を呼ぶ回数の上限
MAX_TOOL_RESULT_CHARS = 100_000

AUTO_RUN_SECONDS = float(os.environ.get("MATDEV_AUTO_RUN_SECONDS", 1800))   # これを超える計算は確認を取る
WATCH_SECONDS = float(os.environ.get("MATDEV_WATCH_SECONDS", 5))            # ジョブの終わりを見に行く間隔
_limit = os.environ.get("MATDEV_MONTHLY_LIMIT_USD", "")
MONTHLY_LIMIT_USD = float(_limit) if _limit else None
LIMIT_WARN_RATIO = 0.8

# 1M トークンあたりの USD (入力・出力・キャッシュ読み・キャッシュ書き)。2026-10 時点の公開価格。
# Opus が断ったときに自動で切り替わる先 (fallbacks) も入れておく。表にないモデルは、表で最も高い料金で数える
# (月の上限が効かなくならないよう、少なく見積もらない)。キャッシュ書きは入力の 1.25 倍 (5 分のキャッシュ)。
# Haiku 5.5 のキャッシュ読みは資料になかったので、通常の比率 (入力の 0.1 倍) の見積もり
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# この名前で来た接続だけ受ける (DNS リバインディング対策)。Tailscale で使うときは paths.env に Tailscale の名前を足す
# ノートから MatSimApp のジョブの画面を開くときの入口 (スマホからは Tailscale の URL)
MATSIM_APP_URL = os.environ.get("MATDEV_MATSIM_URL", "http://localhost:8020").rstrip("/")
os.environ.setdefault("MATDEV_MATSIM_URL", MATSIM_APP_URL)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("MATDEV_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

SESSION_COOKIE = "matdev_session"
SESSION_DAYS = 30
COOKIE_SECURE = os.environ.get("MATDEV_COOKIE_SECURE") == "1"
