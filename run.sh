#!/usr/bin/env bash
# MatDev の起動スクリプト
#   ./run.sh              この PC からのみアクセス可 (http://localhost:8041)
#   ./run.sh --tailscale  Tailscale (tailscale serve) 経由で使う。Cookie を HTTPS 専用にする
#   ./run.sh doctor       paths.env のパス・venv で動くかを確かめる
#
# 各アプリのパスと venv は paths.env に書く (paths.example.env を参照、別の環境では ./setup.sh から)。
# API キーなどは .env に書く (.env.example を参照)。
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env ]]; then
  if [[ "$(stat -c '%a' .env)" != "600" ]]; then
    echo ".env はほかのユーザーから読めない設定にしてください: chmod 600 .env" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
# 各アプリのパスと venv は paths.env (ひな形は paths.example.env)。.env と環境変数にある値が優先
source ./paths.sh
matdev_load_paths
PYTHON="${MATDEV_VENV:-$HOME/.venv/matdev312}/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "MatDev の venv が見つかりません ($PYTHON)。paths.env の MATDEV_VENV を直すか ./setup.sh を実行してください" >&2
  exit 1
fi
if [[ "${1:-}" == "doctor" ]]; then   # paths.env の値で動くかを確かめる
  exec "$PYTHON" -m matdev doctor
fi
if [[ "${1:-}" == "--tailscale" ]]; then
  export MATDEV_COOKIE_SECURE=1
fi
# LAN には公開しない。MatSimAPI の道具は認証なしで計算を動かせるので、入口はここのログインだけにする
exec "$PYTHON" -m uvicorn matdev.main:app --host 127.0.0.1 --port "${MATDEV_PORT:-8041}"
