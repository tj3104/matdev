"""python -m matdev mcp          Claude Code 用の MCP サーバー
python -m matdev mcp-config   その接続設定を JSON で出す
python -m matdev prompt       Claude Code 用の指示文を出す
python -m matdev doctor       paths.env のパスと venv で動くかを確かめる (--quick でアプリの起動を省く)
"""
import json
import sys

from . import settings
from .config import config_path


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "mcp":
        from .gateway import main as serve
        serve()
    elif command == "mcp-config":
        env = {"MATDEV_DATA_DIR": str(settings.DATA_DIR), "MATSIM_DATA_DIR": settings.MATSIM_DATA_DIR,
               "MATDEV_AUTO_RUN_SECONDS": str(settings.AUTO_RUN_SECONDS), "MATDEV_CONFIG": str(config_path())}
        print(json.dumps({"mcpServers": {"matdev": {"command": sys.executable, "args": ["-m", "matdev", "mcp"],
                                                    "env": env}}}))
    elif command == "prompt":
        from .config import load
        from .gateway import _name
        from .prompt import build_system
        print(build_system(load(), "cli", _name()))   # 呼び名は Web 版の画面で付けたもの
    elif command == "doctor":
        from .doctor import main as check
        raise SystemExit(check())
    else:
        raise SystemExit(__doc__)


main()
