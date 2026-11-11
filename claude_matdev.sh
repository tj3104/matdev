#!/usr/bin/env bash
# MatDev を Claude Code から試す (普段のログインのまま。Console の課金は要らない)。
#   ./claude_matdev.sh                対話で使う
#   ./claude_matdev.sh -p "質問"      1 回だけ。確認が要る操作はすべて実行されない
#
# 道具は MatDev の窓口だけにする。--tools "" で Bash・ファイル編集などを消し、--strict-mcp-config でほかの MCP を消し、
# --setting-sources "" で許可の設定やフックを読まない。ANTHROPIC_API_KEY は外して API 課金にならないようにする。
# 確認は MatDev が人に直接聞くので、窓口の道具は Claude Code 側では許可しておく。
set -euo pipefail
cd "$(dirname "$0")"
source ./paths.sh   # 各アプリのパスと venv (paths.env)
matdev_load_paths
PYTHON="${MATDEV_VENV:-$HOME/.venv/matdev312}/bin/python"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
exec "${CLAUDE_BIN:-claude}" --tools "" --mcp-config "$("$PYTHON" -m matdev mcp-config)" --strict-mcp-config --setting-sources "" \
  --allowedTools "mcp__matdev__*" --disable-slash-commands --system-prompt "$("$PYTHON" -m matdev prompt)" "$@"
