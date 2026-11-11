# MatDev

相談しながら材料計算を進めるジャービス。計算は MatSimAPI の MCP サーバーに任せる。今は文字版で、声と XR はこのあと載せる。

```
ブラウザ ─ MatDev (会話・確認・ノート・ジョブの見張り) ─ Claude API
                    └─ MCP ─ matsimapi ─ GPUMD / SevenNet
```

入口は 2 つあり、どちらも同じ関門 (`matdev/gate.py`) を通る。

| 入口 | 頭脳 | 費用 | 確認の取り方 |
|---|---|---|---|
| `./claude_matdev.sh` (開発・試験) | 普段の Claude Code | Claude の契約の利用枠 | 端末に出るダイアログ |
| `./run.sh` (Web) | Claude API | API の従量課金 | 画面のカード |

## 設定ファイル (matdev.json): つなぐアプリと LLM

`matdev.example.json` をコピーして `matdev.json` を作る (なければ MatSim と Claude で動く)。

```json
{
  "llm": {"provider": "anthropic", "model": "claude-opus-5-5"},
  "mcpServers": {
    "matsim": {"command": "~/.venv/gpumd312/bin/matsimapi", "args": ["mcp"],
               "env": {"MATSIM_DATA_DIR": "~/matsim/data"},
               "matdev": {"guide": "…", "estimate": {…}, "jobs": {…}, "confirm": […], "allow": […], "view": {…}}},
    "otherapp": {"command": "…", "args": ["…"]}
  }
}
```

- `mcpServers` は Claude Code の `.mcp.json` と同じ書き方。MCP サーバーを持つアプリなら、設定を貼るだけで道具が増える。
  道具の名前は「アプリ名__道具名」(例: `matsim__get_job`)
- `matdev` の欄 (任意): `guide` (指示文に足す説明)、`estimate` (投入の前に見積もりを強制する道具)、`jobs` (終わりを見張る)、
  `confirm` / `allow` (印に関係なく常に確認 / 確認しない道具)。なければ MCP の印 (読むだけ・消す) だけで判断し、印のない道具は確認する
- `matdev.view` (任意): ノートのジョブから結果を見る。LLM を通さずサーバーが道具を直接呼ぶので料金はかからない。

```json
"view": {"list": {"tool": "get_job_results", "args": {"job_id": "{job_id}"}, "items": "datasets[]"},
         "data": {"tool": "get_job_dataset", "args": {"job_id": "{job_id}", "key": "{key}"}, "columns": "data"},
         "link": "https://<PC>:10000/#/jobs/{job_id}"}
```

  `list` の道具はグラフにできる表の一覧 (`items` の先の 1 つずつが `{key, title, x, y, columns}`)、`data` の道具は表 1 つ分
  (`columns` の先が `{列の名前: [値, …]}`) を返す。`link` はアプリの画面を開く URL (グラフの道具がないアプリはこれだけでもよい)。
  呼べるのは読むだけの印 (readOnlyHint) がある道具だけ。MatSim の既定では MatSimApp を `MATDEV_MATSIM_URL` (既定は Tailscale の 10000 番) で開く
- `llm.provider`: `anthropic` (Claude) か `openai` (OpenAI 形式: OpenAI・Azure OpenAI・xAI・ローカルの vLLM や Ollama など)。

```json
{"llm": {"provider": "openai", "model": "qwen3-32b", "base_url": "http://gpu-server:8000/v1",
         "api_key_env": "LOCAL_LLM_KEY", "price_per_mtok": [0, 0]}}
```

  `price_per_mtok` は 1M トークンあたりの [入力, 出力] USD (ローカルは [0, 0])。書かなければ料金表、表にないモデルは最も高い料金で数える。
  `api_key_env` は鍵を読む環境変数の名前 (.env に書く)。鍵の要らない接続先なら空にする
- Web 版と Claude Code の入口 (`claude_matdev.sh`) は同じ設定を読む。場所は `MATDEV_CONFIG` で変えられる

別の LLM・アプリへの切り替えの詳細は `docs/llm_backends.md`。

## Claude Code から使う

```bash
./claude_matdev.sh                # 対話で使う。本番のデータ (MatSim の data/) を使う
```

Bash・ファイル編集・ほかの MCP・設定ファイルの許可は使えず、見える道具は MatDev の窓口だけになる。自動の通知はないので、
終わったかどうかは聞いて確かめる。

## 画面

暗い作業場 (HUD) 風 (左下の「表示」で明るい表示・端末に合わせるも選べる。端末ごとにブラウザに覚える)。PC (幅 1200 以上) は「会話の一覧 | 会話 | 資料」の 3 列、狭い画面は一覧と資料を横から引き出す。
資料のパネルは「この会話」(関わったジョブとグラフ・書いたノート・道具の呼び出しと結果の全文)、「これまで」(すべての会話と
Claude Code の入口で使ったもの。語で絞れる)、「ノート」。資料は関門 (`gate.py`) を通った道具の呼び出しから `materials.py` が残す。

## 声

「🎙 声」で話すと、この PC の Whisper (`speech.py`) が文字にして依頼として送る (声は外に出さず、録音も残さない)。
上の「読み上げ」を入れると、返事・計算の終わり・実行の確認を端末の読み上げで話す。確認には「実行」か「やめる」で答えられる。
設定ファイルの `speech` で `model` (既定 large-v3-turbo)、`device` (既定 cpu。cuda で速い)、`vocabulary` (聞き取りの手がかりの語) を変えられる。
`"provider": "none"` で切れる。モデルは `data/models` に置く (初回は自動で取得、1.6 GB)。

## 空間 (XR)

上の「空間」で、会話・資料・グラフのパネルを空間に並べた画面を開く。スマホ・PC はドラッグで回し、ピンチやホイールで寄り、パネルを押して操作する
(資料のジョブを押すとグラフが出る。◀ ▶ で表と縦軸を切り替える)。Quest のブラウザなどでは「XR で入る」で部屋に重ねて表示し、
コントローラーか手のつまみで押す。three.js は `matdev/frontend/vendor` に同梱 (外部から読み込まない)。

## 構造の 3D 表示

構造を取ってきた (MP からの取り込みなど) ときや `get_structure` で開いたとき、会話に 3D のカードが出る (ドラッグで回す。単位胞が小さければ自動で繰り返す)。
空間では目の前に浮かぶ。空間は 3 秒ごとに新しい構造を見に行くので、`./claude_matdev.sh` で「MP から Ti の構造を持ってきて」と頼むと、
開いている空間 (スマホ・PC・Quest) にそのまま出る。設定は `view.structure` (MatSim の既定は `get_structure_view`)。

## 作業台 (構造から編集・計算へ)

構造のカードと空間の構造の札の「編集」「計算」で作業台を開く。入力欄はアプリの道具が返す定義 (MatSim の fields) から作る。
編集はプレビューしてから保存 (新しい構造のカードが出る)、計算は見積もってから投入する。保存と投入は関門を通るので、
長い計算は会話の確認のカードで止まり、終わればジョブの見張りが知らせる。画面での操作は、次の依頼でジャービスに伝わる。
設定は `view.structure.edit` (list / preview / save / structures) と `view.structure.calculate` (list / describe / potentials)。
見積もりと投入は `estimate` の道具を使う。

## 計算の結果

計算が終わると、会話に「計算の結果」のカードが出て、グラフ (表・横軸・縦軸を選ぶ)・図・構造の時間変化をその場で見られる
(投入はチャットでも、作業台や道具からでもよい)。同じものは資料の「この会話」のジョブからも開ける。構造の時間変化は、計算過程の
構造をコマとして再生・一時停止・スライダーで送る (設定の `view.trajectory`)。空間 (XR) ではグラフのパネルの「表示 ▼」で「構造の時間変化」を選ぶと、目の前で再生する (普段の画面の「空間で見る」からも)。作業台の MD は「構造を記録する間隔」が空なら全体で約 50 コマ。「最終構造を利用する」で、計算の後の構造 (構造緩和の後など) を次の編集・計算に使える (空間でも同じボタン)。

## 構造を取り込む (結晶・有機分子)

上の「取り込む」で、結晶は Materials Project から、分子は PubChem から取り込む。

結晶 (Materials Project) は OPTIMADE で API キーなしで探す。組成式 (TiO2)・元素の組 (Ti-O)・ID (mp-149) で引け、
候補に空間群・原子数・凸包からの距離が安定な順に出る。取り込むと既定で慣用の単位胞にする (OPTIMADE の MP は基本単位胞で返すため)。
設定は `workbench.crystals` (search / import)。

分子 (PubChem) は、PubChem の分子を名前 (英語)・CID・SMILES で探して取り込む。3D の配座を全方位に真空 (既定 10 Å) を空けた箱に入れて
MatSim の構造にする (会話に 3D のカードが出て、編集・計算・その場で MD に進める)。分子だけの箱を作り直すのは編集の
「真空層を全方位に」。ほかの構造 (表面など) に置くのは編集の「分子を置く」で、置く分子の影をつまんで動かす・回して位置と向きを決め、
「適用」で下書きに重ねる。設定は `workbench.molecules` (search / import)。

## 作業 (作業のディレクトリ)

会話を始める前に、その会話の出力先 (作業) を選ぶか作る (上の 📁 で変えられる)。終わった計算の結果は `jobs/<アプリ>-<番号>/`、取ってきた・作った構造は `structures/` に置かれる。
資料の「作業」(空間では資料の「ファイル」) でディレクトリをたどり、ファイルをウインドウで開いて並べられる。開いているウインドウは
次の依頼でジャービスに伝わり、ジャービスは `list_work_files`・`read_work_file` で中身を読める。
作業のディレクトリを作ってよい場所は設定の `works.roots` (既定 `~/matdev_works`)。それ以外の場所は断る。

## ezcal (第一原理計算)

`~/.venv/ezcalenv312/bin/ezcal` があれば、既定でつなぐ (MCP の窓口は `matdev/adapters/ezcal_mcp.py`)。scf・relax・vc-relax・バンド・DOS・
電荷・auto と MLIP の MD を、会話の出力先 (作業) の `ezcal/` に結果を置いて流す。構造は作業のファイル (structures/ の cif など) を渡す。
ジョブは 1 つずつ順番で、投入は毎回確認する。作業のディレクトリに `qe_config.yaml` を置けば、並列数などを作業ごとに変えられる。

## 道具 (チャットなしで使う)

上の「道具」で、つないだアプリの道具をどれでも画面から使える (入力欄は道具の定義から作る)。実行は関門を通るので、確認や見積もりは
ジャービスが使うときと同じ。やったことと結果は会話に「⚙」で残り、次にジャービスに頼むときに伝わる。
構造からの計算は作業台がわかりやすい: MatSim の構造のカードの「計算」、作業の構造ファイルや構造のカードの「ezcal で計算」。

## 会話・資料の削除と呼び名

会話は一覧の ✕ で消す (ノートと使用量は残る。資料も消すかを選べる)。資料は「この会話」の道具の結果を開いて 1 つずつ、またはまとめて消せる。
アシスタントの呼び名は左下の「名前」で変える (既定はジャービス)。

## 起動

```bash
./run.sh                 # http://localhost:8041
./run.sh --tailscale     # スマホから Tailscale (tailscale serve) 経由で使う。Cookie を HTTPS 専用にする
./run.sh doctor          # paths.env のパス・venv で動くかを確かめる
```

最初に開くとアカウントを 1 つ作る画面が出る。スマホから使うときは `tailscale serve` で 8041 番を HTTPS に通し、
`paths.env` の `MATDEV_ALLOWED_HOSTS` にこの PC の Tailscale の名前を足す。8041 番を LAN やインターネットに直接開かない。

## 別の環境で使う (paths.env)

各アプリのパスと venv は `paths.env` の 1 か所に書く (ひな形は `paths.example.env`)。`run.sh`・`claude_matdev.sh`・
`python -m matdev` がこれを読む。シェルの環境変数と `.env` にある値が優先する。秘密 (API キー) は `.env` に書く。

| 項目 | 内容 |
|---|---|
| `MATDEV_VENV` | MatDev 本体の venv (`setup.sh` が作る) |
| `MATSIM_VENV` | MatSim (matsimapi・GPUMD・SevenNet・ASE) の venv。MCP サーバーと対話型 MD の Python はここから決まる |
| `EZCAL_VENV` | ezcal の venv (任意。なければ ezcal をつながない) |
| `MATDEV_MATSIMAPI_BIN`・`MATDEV_IMD_PYTHON`・`EZCAL_BIN`・`CLAUDE_BIN` | venv の中の決まった場所と違うときだけ |
| `MATSIM_DATA_DIR`・`MATDEV_WORKS_ROOT`・`MATDEV_DATA_DIR`・`MATDEV_MODELS_DIR`・`MATDEV_CONFIG` | データ・作業・音声モデル・設定ファイルの場所 |
| `MATDEV_PORT`・`MATDEV_ALLOWED_HOSTS`・`MATDEV_MATSIM_URL` | 入口 (ポート・受ける接続の名前・MatSimApp の画面) |

新しい環境での手順:

```bash
# MatSim (と、使うなら ezcal) は、それぞれのプロジェクトの手順で venv に入れておく
./setup.sh               # paths.env を作って止まる → この環境の場所に書き換える
./setup.sh               # MatDev の venv を作って入れ、.env を作り、doctor で確かめる
# .env に ANTHROPIC_API_KEY と MATDEV_MONTHLY_LIMIT_USD を書く
./run.sh
```

`doctor` は、MatDev の venv のパッケージ、MatSim の venv (matsimapi・ase・calorine・sevenn)、ezcal、データの場所に書けるか、
設定ファイル、`.env` の権限を見て、つなぐアプリを実際に起動して道具の数を数える。直す所は「✗」と「→」で出る (`--quick` で起動を省く)。
`matdev.json` の中でも `$MATSIM_VENV`・`$MATSIM_DATA_DIR`・`$MATDEV_MATSIM_URL` が使える (`matdev.example.json`)。

MatSim が使うもの (GPUMD・Atomsk・NEP89 のファイル・計算エンジンの選び方) は MatSim の `paths.env` (MatSim のフォルダの `paths.example.env`) に
書く (別の場所なら MatDev の `paths.env` の `MATSIM_PATHS`)。GPUMD がない環境では、MatSim は NEP を CPU (calorine) で計算する
(MD・静的計算・フォノン分散。`doctor` に「matsim の計算エンジン: CPU」と出る)。

## 実行の決まり

LLM の判断に任せず、サーバーが `matdev/policy.py` で止める。

| 操作 | 扱い |
|---|---|
| 読むだけの道具、構造の追加、解析 | そのまま実行 |
| 計算の投入 | サーバーが必ず見積もりを取る。30 分までは内容を表示して実行、超えると確認 |
| 削除、ジョブの中止、このマシンのファイルの読み込み、知らない道具 | 常に確認 |

自動の通知から動いているターンでの投入は、短い計算でも確認を取る。

## セキュリティ

- 8041 番は 127.0.0.1 だけで待ち受け、`MATDEV_ALLOWED_HOSTS` の名前の接続だけ受ける
- API キーなどの環境変数は MCP サーバーに渡さない。Anthropic の鍵の形の文字列は LLM に送る前に消す
- `.env` が 600 でなければ起動しない。月の上限がなければ依頼を受け付けない
- 道具の結果の中の指示には従わないよう指示文に書いている。ただし確認の関門はサーバー側にある
- 会話とノートは `data/matdev.db` に暗号化なしで保存する (`data/` は 700)。LLM に送った内容は Anthropic のサーバーを通る

## 終了の知らせ

`matdev/watcher.py` がジョブを 5 秒ごとに見る。同じ投入のジョブが全部終わると、ジャービスが結果を読んで報告し、ノートに残す。

## 設定

`.env.example` を参照。月の上限 `MATDEV_MONTHLY_LIMIT_USD` は必須で、80% で知らせ、100% で新しい依頼を止める。費用の表示は
MatDev の見積もりなので、確実に止める上限は Anthropic Console 側にも設定する。

## 試験

```bash
~/.venv/matdev312/bin/python -m pytest 01_test/test_agent.py    # LLM だけ台本。MatSimAPI と GPUMD は本物
~/.venv/matdev312/bin/python -m pytest 01_test/test_gateway.py  # Claude Code 用の窓口の関門
~/.venv/matdev312/bin/python -m pytest 01_test/test_multi.py    # 複数のアプリと OpenAI 形式 (設定ファイルを切り替えるので、ほかとは別に流す)
~/.venv/matdev312/bin/python 01_test/escape_check.py            # 本物の Claude Code で範囲外の操作を試す。Claude の利用枠を使う
CHROMIUM_PATH=~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
  ~/.venv/yumeguri312/bin/python 01_test/test_ui.py             # 画面
```
