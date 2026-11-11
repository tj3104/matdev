# LLM の切り替え方法

MatDev の頭脳を替える方法のまとめ。2026-11-03 作成、2026-11-09 更新。

**2026-11-09: 方法 5 を実装した。** 設定ファイル (matdev.json) の `llm.provider` を `openai` にすれば、
OpenAI 形式の LLM (OpenAI・Azure OpenAI・xAI・ローカルの vLLM や Ollama など、別のマシンでもよい) で Web 版が動く。
台本の相手での試験 (`01_test/test_multi.py`) まで。本物の OpenAI 形式の LLM ではまだ試していない。

## 変えないところ

関門 (`matdev/policy.py`、`matdev/gate.py`) は LLM の外にあるので、どの方法でも同じように効く。

| 方法 | 入口 | 課金 | 関門 | 変更の量 |
|---|---|---|---|---|
| 今の形 | `claude_matdev.sh` | Claude の契約の利用枠 | 効く | なし (動作確認済み) |
| 1. Console: Web 版 | `run.sh` | API の従量課金 | 効く | `.env` のみ |
| 2. Console: Claude Code | `claude_matdev.sh` | API の従量課金 | 効く | スクリプトに数行 |
| 3. ローカル: 接続先の変更 | `claude_matdev.sh` | 電気代のみ | 効く | 中継の用意とスクリプト |
| 4. ローカル: 別の MCP 対応アプリ | そのアプリ | 電気代のみ | 窓口の分は効く | アプリの設定 |
| 5. ローカル: Web 版の対応 | `run.sh` | 電気代のみ | 効く | `agent.py` の開発 |

長く使うならローカルは 5 が本命。Console なら 1 が一番簡単。

## 1. Console: Web 版

`.env` に書いて起動する。コードの変更は要らない。

```bash
ANTHROPIC_API_KEY=<Console で発行した MatDev 専用のキー>
MATDEV_MONTHLY_LIMIT_USD=<月の上限>
```

```bash
./run.sh     # http://localhost:8041
```

- Console にも支出の上限を設定する。`MATDEV_MONTHLY_LIMIT_USD` は MatDev の見積もりによる上限
- 実 API では未確認。最初は短い依頼で、回答の表示、道具の呼び出し、2 回目以降の会話を確かめる
- 確かめた範囲: LLM を台本に置き換えた試験

## 2. Console: Claude Code

`claude_matdev.sh` は API の鍵を消してから起動している。次のように切り替えられる形にする。

```bash
if [[ "${MATDEV_USE_API:-0}" != "1" ]]; then
  unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
fi
```

```bash
MATDEV_USE_API=1 ANTHROPIC_API_KEY=<キー> ./claude_matdev.sh
```

- API キーが有効な間は、ログインではなく従量課金になる
- `~/.bashrc` にキーを書かない。普段の Claude Code まで従量課金に変わる
- Web 版の使用量の記録と月の上限は効かない。上限は Console で管理する

## 3. ローカル: Claude Code の接続先を変える

Claude Code を、Anthropic 形式の API を出す中継 (LiteLLM など) へ向け、中継からローカルの LLM を呼ぶ。

```
claude → 中継 (LiteLLM など) → ローカルの LLM
```

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:<中継のポート>
export ANTHROPIC_AUTH_TOKEN=<中継の鍵>
```

- **公式の対象外。** Anthropic は Claude 以外のモデルへの接続をサポートしていない (Claude Code の資料「Other LLM gateways」)
- **`ANTHROPIC_AUTH_TOKEN` も必ず設定する。** `ANTHROPIC_BASE_URL` だけだと、保存された claude.ai のログインが有効なまま中継に送られる
- 今の `claude_matdev.sh` は `ANTHROPIC_AUTH_TOKEN` を消すので、方法 2 と同じように分岐が要る
- `--model` にローカルのモデルの名前を渡す。中継側で名前の対応を決める
- Claude Code の更新で動かなくなることがある

## 4. ローカル: 別の MCP 対応アプリ

Open WebUI、LibreChat など、ローカルの LLM に対応した MCP クライアントに窓口を登録する。登録する内容は次のコマンドで出せる。

```bash
~/.venv/matdev312/bin/python -m matdev mcp-config
```

- 窓口は標準入出力で動くので、アプリが標準入出力の MCP に対応している必要がある。HTTP だけのアプリには変換が要る
- `claude_matdev.sh` の制限 (`--tools ""` など) は効かない。アプリが持つ別の道具は、アプリ側で止める
- 指示文は `python -m matdev prompt` の内容をアプリの設定に入れる
- elicitation に対応していないアプリでは確認が取れず、削除、中止、ファイルの読み込み、長い計算は常に実行されない

## 5. ローカル: Web 版を対応させる

`matdev/agent.py` の LLM を呼ぶ部分を、OpenAI 形式などのローカル用の呼び出しに替える。ローカルの LLM サーバー (vLLM、Ollama など) は別に動かす。

変えるところ:

| 場所 | 内容 |
|---|---|
| `make_client` | ローカルのサーバーの URL に接続する |
| `build_tools` | 道具の定義を接続先の形式に直す |
| `_stream_turn` | 呼び出しと、回答の逐次表示 |
| `_turn` の道具の取り出し | `tool_use` の読み方と、停止の理由の扱い |
| `_dump`、`_history`、`_repair` | 履歴の保存形式。今は Anthropic の形式で保存している |
| `usage.py` | 費用の計算。ローカルでは 0 |

変えないところ: `gate.py`、`policy.py`、`matsim.py`、`notes.py`、`watcher.py`、画面、ログイン。

進め方:

1. `agent.py` の Anthropic 専用の部分を別のファイルに分け、設定で切り替える
2. 既存の `01_test/test_agent.py` を両方の接続先で通す
3. Ti の場面を同じ指示で流し、Claude と比べる

## ローカルの LLM に共通の注意

- 窓口の道具は 32 個ある。小さめの LLM は長い段取りで迷いやすい。少数の道具に絞った版が要るかもしれない
- 道具の呼び出しに対応したモデルを選ぶ
- GPU は RTX 5070 Ti の 16 GB 1 枚で、計算と取り合う。計算中は LLM を CPU に回すなどの決まりが要る
- 中継や LLM のサーバーも `127.0.0.1` だけで待ち受ける
- 切り替えたら `01_test/escape_check.py` を流して、範囲外の操作ができないことを確かめる。ただしこの試験は `claude` コマンド前提なので、方法 4・5 では作り直す

## 使い分けの案

| 段階 | 入口 |
|---|---|
| 開発・試験 | 今の形 (`claude_matdev.sh`) |
| 実 API の確認、毎日の利用 | 1 |
| ローカルの比較 | 3 で手軽に試し、残すなら 5 |
