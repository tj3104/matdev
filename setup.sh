#!/usr/bin/env bash
# 別の環境で MatDev を使えるようにする (最初に 1 回)。何度実行してもよい (あるものは作り直さない)。
#   ./setup.sh
#
# 1. paths.env がなければ paths.example.env からコピーして止まる。この環境の venv とデータの場所に書き換えてから、もう一度実行する
# 2. MatDev の venv (paths.env の MATDEV_VENV) がなければ作り、requirements.lock の版で MatDev を入れる
# 3. .env がなければ .env.example からコピーする (600)。API キーと月の上限 (MATDEV_MONTHLY_LIMIT_USD) を書く
# 4. ./run.sh doctor で、つなぐアプリ (MatSim・ezcal) まで動くかを確かめる
#
# MatSim (matsimapi) と ezcal は、それぞれのプロジェクトの手順で venv に入れておく。ここでは場所を指すだけで、入れない。
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f paths.env ]]; then
  cp paths.example.env paths.env
  echo "paths.env を作りました。この環境の venv とデータの場所に書き換えてから、もう一度 ./setup.sh を実行してください"
  exit 0
fi
source ./paths.sh
matdev_load_paths
VENV="${MATDEV_VENV:-$HOME/.venv/matdev312}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "MatDev の venv を作ります: $VENV"
  if command -v uv >/dev/null; then
    uv venv --python 3.12 "$VENV"
  else
    python3 -m venv "$VENV"
  fi
fi
echo "MatDev を入れます (requirements.lock の版)"
if command -v uv >/dev/null; then
  uv pip install --python "$VENV/bin/python" -r requirements.lock -e ".[test]"
else
  "$VENV/bin/python" -m pip install -q -r requirements.lock -e ".[test]"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ".env を作りました。ANTHROPIC_API_KEY と MATDEV_MONTHLY_LIMIT_USD を書いてください"
fi
chmod 600 .env
mkdir -p "${MATDEV_DATA_DIR:-data}" && chmod 700 "${MATDEV_DATA_DIR:-data}"

echo
exec ./run.sh doctor
