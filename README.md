# Screenshot Translator (Gemma-4-26B-A4B-It)

<img width="640" height="521" alt="Image" src="https://github.com/user-attachments/assets/f24ef322-08b5-48e6-aa54-71b9e06d7401" />

![Image](https://github.com/user-attachments/assets/2939810b-8c72-4e91-9964-d6fac526c736)

このリポジトリには3つの使い方があります。
1) Web UI: クリップボード貼り付け画像を OCR + 英→日翻訳して Markdown 表示
2) Windows 常駐クライアント: 画面上の範囲選択 → スクショ → OCR + 翻訳をオーバーレイ表示
3) Ubuntu Gnome Extension: 画面上の範囲選択 → スクショ → OCR + 翻訳（TTS Once / Monitor で読み上げ対応）

※ Windows 常駐クライアントは **Windows + WSL2** と **Windows 単体（WSL2 不要）** のどちらでも動きます。

※ Ubuntu Gnome Extension は Ubuntu (Gnome Shell) 環境用です。

## Windows で使う場合（WSL2 不要）

Windows 単体でクローンから起動まで完結します。NVIDIA (CUDA) / AMD / Intel / CPU のみ、
いずれにも対応しています。**MSVC や CUDA Toolkit のインストールは不要です。**

```powershell
git clone <このリポジトリの URL>
cd ScreenshotTranslator2

# 1. llama.cpp のビルド済みバイナリを取得（環境に合わせて選択）
.\app\scripts\fetch_llama_win.ps1                    # AMD / Intel / 汎用 (Vulkan)
.\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3  # NVIDIA (Blackwell はこちら)
.\app\scripts\fetch_llama_win.ps1 -Flavor cuda-12.4  # NVIDIA (Ada 以前)

# 2. モデルを models\ に配置（下記「要件」参照）

# 3. 起動（uv sync も自動実行されます）
.\start.ps1
```

詳細（`llama-server.exe` の配置、ビルド済みが使えない場合のソースビルド手順、
トラブルシュート、AMD APU 固有の設定）は **[docs/windows.md](docs/windows.md)** を参照してください。

> Blackwell 世代 (RTX 50 シリーズ / RTX PRO Blackwell) は CUDA 12.8 以降が必要なため、
> `cuda-12.4` ビルドでは動作しません。`cuda-13.3` を選んでください。

## 要件
- GPU: NVIDIA (CUDA) / AMD (Vulkan・ROCm) / CPU のいずれか
  - `app/scripts/build_llama.sh` の `GPU_BACKEND` で切り替えます（既定: `cuda`）
  - AMD APU (Ryzen AI Max+ 395 / Strix Halo など) は `vulkan` を使ってください
- `uv` (Python パッケージマネージャ) がホストにインストール済み
- 下の2つのモデルファイルをローカル `models/` に配置
  - `models/gemma-4-26B_q4_0-it.gguf`（既定）
  - `models/gemma-4-26B-it-mmproj.gguf`（26B-A4B 用、既定）
- 配布元: [google/gemma-4-26B-A4B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf)
  - Google 公式の QAT (quantization-aware training) 版です。約 14.4GB
- 任意: `models/mtp-gemma-4-26B-A4B-it.gguf` を置くと投機的デコードで高速化します
  → [docs/mtp.md](docs/mtp.md)
- **音声読み上げ (TTS)**:
  - 既定のエンジンは `Supertonic 3` (ONNX / CPU 実行) です。バックエンド起動時にモデル (約386MB) が自動でダウンロードされます。
  - 声は `F2`（日本語女性）、言語は文字種から自動判定されます。日本語の中に英語が混じっていても、1つの声で続けて読み上げます。
  - 音声再生のために、ホスト側に `libportaudio2` や `aplay` (ALSA) が必要です（Ubuntu Desktopなら通常は入っています）。
  - 音声の調整は環境変数で行えます: `SUPERTONIC_VOICE` (`M1`〜`M5` / `F1`〜`F5`、既定 `F2`)、`SUPERTONIC_SPEED` (0.7〜2.0、既定 `1.05`)、`SUPERTONIC_STEPS` (5〜12、既定 `8`)、`SUPERTONIC_LANGUAGE` (既定 `en`)。
  - `SUPERTONIC_LANGUAGE` は Supertonic に渡す言語タグです。Supertonic はパッセージ全体を 1 つのタグで条件付けし、語ごとの言語判定は行いません。`ja` を渡すと、日本語訳に残った英字（製品名・略語など）まで日本語として読まれて発音が崩れるため、**既定を `en`** にしています。日本語の読み上げ品質は `en` でも損なわれません（英字を含まない文でも確認済み）。`ja` に戻す場合は `SUPERTONIC_LANGUAGE=ja`、文字種による自動判定（v7.6.0 以前の挙動）に戻す場合は `SUPERTONIC_LANGUAGE=auto` を指定してください。
  - 従来の `Kokoro-82M` に戻す場合は `TTS_ENGINE=kokoro` を指定してください。この場合のみ日本語 G2P (`misaki[ja]`) が必要で、Windows では既定から外れます（`pyopenjtalk` に wheel が無く MSVC が必要なため）。有効化する場合は `uv sync --extra ja-tts`。
  - **ライセンス注意**: `supertonic` パッケージ本体は MIT ですが、**モデルの重みは BigScience Open RAIL-M** ライセンスで配布されています（Kokoro の Apache-2.0 とは条件が異なります）。
    - 本リポジトリはモデルの重みを再配布していません。初回起動時に [Supertone/supertonic-3](https://huggingface.co/Supertone/supertonic-3) から利用者の環境へ直接ダウンロードされます。本アプリのコード自体は MIT です。
    - このモデルには**用途に基づく制限**（Open RAIL-M ライセンス Attachment A）があり、利用者はこれに従う必要があります。違法行為、未成年者の搾取、有害な虚偽情報の生成・拡散、なりすまし（ディープフェイク）、ハラスメント、医療上の助言・診断結果の解釈、法執行や司法判断への利用などが禁止されています。
    - 生成した音声を公開・配布する場合は、**機械生成である旨の明示**が求められます（同 Attachment A (e)）。
    - 全文は上記モデルページ、またはダウンロード先の `LICENSE` ファイルを参照してください。

## 使い方
1. llama.cpp をビルド
   ```bash
   ./app/scripts/build_llama.sh              # NVIDIA (CUDA)
   GPU_BACKEND=vulkan ./app/scripts/build_llama.sh   # AMD / 汎用
   ```
   - `LLAMA_CURL=OFF` でlibcurl未インストール環境でも通るようにしています。
   - 並列ビルドは `JOBS` 環境変数で上書き可能（既定は `nproc` があればその値、なければ4）。
   - 必要に応じて `LLAMA_REPO` / `LLAMA_DIR` を上書きしてください。
   - Windows ネイティブではビルド不要です（`app/scripts/fetch_llama_win.ps1` で公式ビルド済みバイナリを取得）。
2. モデルを `models/` 配下へ配置 (パスは環境変数で変更可)。
3. サーバー起動
   ```bash
   ./start.sh          # Linux / WSL2
   ```
   ```powershell
   .\start.ps1         # Windows ネイティブ
   ```
   - デフォルト: Gemma 4 26B-A4B (Google QAT `q4_0`) + 同梱 mmproj, llama-server 8009, Web UI 8012, ctx=8192, parallel=1。
   - `models/mtp-gemma-4-26B-A4B-it.gguf` があれば投機的デコードが自動で有効になります（無ければ無効のまま通常動作）。
   - VRAMが少ない場合は起動時に `LLAMA_CTX` を下げて起動できます（例: `LLAMA_CTX=4096 ./start.sh`）。
   - 既存の llama-server を使う場合: `SKIP_LLAMACPP=1 LLAMA_SERVER_URL=http://127.0.0.1:8009 ./start.sh`
   - Gemma 4 既定時は `LLAMA_THINK_BUDGET=0` と `--reasoning off` が自動適用されます。
   - E4B を併用する場合は、26B-A4B 用の `models/mmproj-F16.gguf` と名前が衝突しないよう、E4B 用 projector を任意の別名にして保存してください。例: `models/mmproj-F16_gemma4E4B.gguf`
   - E4B を使う場合は `LLAMA_MODEL=models/gemma-4-E4B-it-UD-Q4_K_XL.gguf LLAMA_MMPROJ=models/mmproj-F16_gemma4E4B.gguf LLAMA_MODEL_NAME=Gemma-4-E4B-It ./start.sh` のように明示指定してください。
   - Qwen3.5 を使う場合は `LLAMA_MODEL=models/Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf LLAMA_MMPROJ=models/mmproj-F32.gguf ./start.sh` のように明示指定してください。
   - Qwen3.5 では Qwen3.5 用の `mmproj` を指定してください。Gemma 4 用の `mmproj` とは共用できません。ファイル名が衝突する場合は任意の別名で保存し、`LLAMA_MMPROJ` にそのパスを指定してください。

## 詳細ドキュメント
- [docs/mtp.md](docs/mtp.md) — MTP（投機的デコード）による高速化。任意機能・GGUF の作り方付き
- [docs/windows.md](docs/windows.md) — WSL2 を使わない Windows 単体構成（CUDA / 非CUDA 両対応）/ AMD APU 設定

## 主な環境変数
- `WEB_PORT` (既定: 8012)
- `LLAMA_PORT` (既定: 8009)
- `LLAMA_MODEL` (既定: `models/gemma-4-26B_q4_0-it.gguf`)
- `LLAMA_MMPROJ` (既定: `models/gemma-4-26B-it-mmproj.gguf`)
- `LLAMA_MODEL_NAME` (既定: `Gemma-4-26B-A4B-It-QAT`)
- `LLAMA_SPEC_DRAFT_MODEL` (MTP ヘッドのパス。既定モデル使用時は `models/mtp-gemma-4-26B-A4B-it.gguf` が存在すれば自動設定)
- `LLAMA_SPEC_TYPE` (既定: `draft-mtp`。`--spec-type` に渡す値)
- `GPU_BACKEND` (`build_llama.sh` 用。`cuda` / `vulkan` / `hip` / `cpu`。既定: `cuda`)
- `LLAMA_CTX` (既定: 8192)
- `LLAMA_PARALLEL` (既定: 1)
- `LLAMA_BIN` (既定: ./llama.cpp/build/bin/llama-server)
- `LLAMA_CHAT_TEMPLATE_FILE` (`--chat-template-file` に渡すテンプレートパス)
- `LLAMA_REASONING` (`--reasoning` に渡す値。Gemma 4 系モデルでは未指定時に `off`)
- `LLAMA_THINK_BUDGET` (`--reasoning-budget` に渡す値。Gemma 4 / Qwen3.5 既定時は自動で `0`)
- `LLAMA_ARG_CHAT_TEMPLATE_FILE` / `LLAMA_ARG_THINK_BUDGET` も互換入力として受け付け
- `SKIP_LLAMACPP`=1 で llama-server 起動をスキップ

### Gemma 4 既定構成
- 既定構成は Google 公式 QAT 版の `gemma-4-26B_q4_0-it.gguf` と `gemma-4-26B-it-mmproj.gguf` です（v7.3.0 で unsloth `UD-Q4_K_XL` から変更）。
  - サイズが 17.1GB → 14.4GB に減り、メモリ帯域律速の環境では約 1.26 倍高速になります。
  - 従来構成に戻す場合: `LLAMA_MODEL=models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf LLAMA_MMPROJ=models/mmproj-F16.gguf ./start.sh`
- projector の型式は `gemma4v` で、旧来の llama.cpp でもそのまま読めます。llama.cpp の更新が必須なのは MTP を使う場合だけです。
- E4B を同居させる場合は、E4B 用 projector を別名で保存し、`LLAMA_MMPROJ` で明示指定してください。
- chat template は Gemma 4 のモデル内蔵 template をそのまま使います。
- 単一ユーザー前提で `--parallel 1` を既定にしています。
- thinking を抑制するため、Gemma 4 系モデルでは `LLAMA_THINK_BUDGET` 未指定時は `0`、`LLAMA_REASONING` 未指定時は `off` を自動適用します。

### Qwen3.5 テンプレート運用（互換）
- 追跡対象テンプレートは `app/chat_templates/qwen3.5-35b-a3b.chat_template.jinja` です。
- 元テンプレートは Qwen 公式 `chat_template.jinja`（Apache-2.0）で、このリポジトリでは `enable_thinking=false` を加えています。
- `models/` は `.gitignore` 対象のため、テンプレートは `models/` ではなく `app/chat_templates/` に置いて管理します。
- Qwen3.5 に切り替えたときだけ、このテンプレートが自動適用されます。
- 実行時に `LAMA_ARG_THINK_BUDGET`（typo）が与えられた場合も互換で受け付けますが、`LLAMA_THINK_BUDGET` の利用を推奨します。

### `LLAMA_CTX` について（VRAM調整）
- `LLAMA_CTX` は **llama.cpp の `llama-server` を起動する際の `-c`** に渡され、主に KV cache のサイズに効くため VRAM 使用量に影響します。
- `LLAMA_CTX` を変更してVRAM使用量を変えたい場合は、**llama-server を起動し直す必要があります**（FastAPI側の環境変数だけ変えてもVRAMは変わりません）。
- `SKIP_LLAMACPP=1` で既存の llama-server を使う場合、その既存プロセスが `-c` で起動された値が有効になります。

## おまけ：vLLM / DiffusionGemma バックエンド（実験的）

既定では **Gemma-4-26B-A4B-It を llama.cpp (GGUF)** で動かします（幅広い GPU・CPU で動作）。
ハイエンドな NVIDIA GPU をお持ちの場合は、**同じベースモデルの拡散版**
`nvidia/diffusiongemma-26B-A4B-it-NVFP4` を **vLLM** 経由で使う、より高速な選択肢があります。
拡散モデルは 256 トークンのブロックを並列生成するため、OCR＋翻訳がおおむね 1 秒未満で完了します。

> **⚠ 実験的です。** この経路は公開前イメージ `vllm/vllm-openai:gemma` に依存します。NVIDIA/vLLM は
> *「supporting vLLM image が正式公開されるまで暫定であり変更されうる」* と明記しています。NVFP4 形式も
> experimental 扱いです。再現性のためイメージは **digest で固定**しています（スクリプト参照）。新しい
> イメージが出たら digest を更新してください。将来 mainline の vLLM が `diffusion_gemma` を取り込めば、
> この特別イメージは不要になる見込みです。

### 要件
- **NVIDIA Blackwell または Hopper GPU**（NVFP4 には FP4 対応ハードが必要）、空き VRAM 約 30 GB
- **Docker** ＋ **NVIDIA Container Toolkit**（`--gpus all` が機能すること）
- モデル重み用に約 13 GB のディスク（初回のみ `~/.cache/huggingface` に取得）

### かんたん起動（ワンコマンド）
```bash
BACKEND=vllm ./start.sh
```
これだけで、vLLM コンテナの起動（初回のDL・ロード＋自動ウォームアップ）→ アプリ (FastAPI, `:8012`)
の起動までを一括で行います。起動後はブラウザで `http://localhost:8012` を開いてください。停止は
**Ctrl + C**（既定では vLLM コンテナは起動したまま＝次回が速い。終了時にコンテナも止めたい場合は
`STOP_BACKEND_ON_EXIT=1 BACKEND=vllm ./start.sh`）。

主な調整用環境変数（任意）：

| 変数 | 既定 | 用途 |
|---|---|---|
| `VLLM_PORT` | `8000` | 待受ポート |
| `VLLM_GPU_MEM_UTIL` | `0.75` | VRAM 使用率上限（OOM 時は下げる） |
| `VLLM_MAX_MODEL_LEN` | `8192` | 最大コンテキスト長 |
| `VLLM_IMAGE` | digest 固定 | 別イメージを使う場合 |
| `STOP_BACKEND_ON_EXIT` | `0` | 終了時に vLLM コンテナも停止する |

### 手動で個別に制御する場合
バックエンド（vLLM コンテナ）だけを起動・停止・確認：
```bash
./app/scripts/run_vllm_backend.sh start     # 冪等。起動の最後に自動ウォームアップ
./app/scripts/run_vllm_backend.sh stop
./app/scripts/run_vllm_backend.sh status
```
初回は重みのダウンロードとモデル読み込みで数分かかります。**起動直後の最初の 1 リクエスト**は
一度きりの CUDA/コンパイル処理のため遅く（約 4〜5 秒）品質も落ちるので、スクリプトは最後に小さな
ウォームアップを自動実行します。

すでに起動済みの外部サーバにアプリを向けるだけなら（`BACKEND` を使わない従来の方法）：
```bash
SKIP_LLAMACPP=1 \
LLAMA_SERVER_URL=http://127.0.0.1:8000 \
LLAMA_MODEL_NAME=nvidia/diffusiongemma-26B-A4B-it-NVFP4 \
./start.sh
```
- スクリプトが付与する `--default-chat-template-kwargs '{"enable_thinking":false}'` は **必須**です。
  これが無いとモデルが回答を「思考(reasoning)」として出力し、アプリ側の `content` が空になります。

### どちらを使うべき？

| | GGUF（既定） | vLLM / NVFP4（任意） |
|---|---|---|
| 対応 GPU | ほぼ全 GPU / CPU | Blackwell / Hopper のみ |
| 遅延（OCR＋翻訳） | 数秒 | 約 0.5 秒（ウォーム後） |
| セットアップ | llama.cpp をビルド | `docker pull` |
| 安定性 | 安定 | 実験的（公開前イメージ） |
| VRAM | 調整可（約 16 GB〜） | 約 30 GB |

品質は同等です（同じ Gemma-4-26B-A4B ベース）。対応ハードでは速度重視で vLLM、可搬性重視で GGUF を
既定のまま、という使い分けがおすすめです。

### 困ったとき
- **起動時に CUDA out of memory** → `VLLM_GPU_MEM_UTIL` を下げる（例 `0.70`）、必要なら
  `VLLM_MAX_MODEL_LEN=4096` も：
  `VLLM_GPU_MEM_UTIL=0.70 VLLM_MAX_MODEL_LEN=4096 ./app/scripts/run_vllm_backend.sh start`
- **翻訳が空で返る** → サーバが `enable_thinking:false` で動いているか確認（スクリプト既定で付与）。
- **最初の 1 回だけ遅い/崩れる** → ウォームアップが実行されたか確認（スクリプトが自動実行）。
- **モデル側ログ** → `docker logs dgemma`

## フロントエンドの使い方
- ブラウザで `http://localhost:8012` にアクセス。
- 画像を **貼り付け** (Ctrl+V) するかドラッグ&ドロップ。
- 任意で追加指示を入力し「再送信」。
- 返ってきた Markdown を「コピー」ボタンで取得可能。
- CSS で横に長い行も折り返して表示。

## アーキテクチャ
- `llama.cpp` の `llama-server --api` を常駐させ、OpenAI 互換 `/v1/chat/completions` でマルチモーダル推論。
- FastAPI (ポート 8012) が画像を PNG に正規化 → llama-server へ base64 画像付きメッセージ送信。
- 応答 Markdown をそのまま表示 (要約禁止プロンプトを付与)。
- Windows 常駐クライアントは `windows/OverlayClient` にあり、Ctrl+Alt押下/離しでROIを取得して `/api/v1/ocr_translate_with_grounding` に送信する。トレイの「Speak Translation」を有効にすると、表示した文字列をそのまま `/api/v1/speak` に渡して読み上げる。

## Windows 常駐クライアント（WPF）
- 参照先: `windows/OverlayClient`
- 前提: Windows 10/11 + .NET 8 SDK（`dotnet --version` で確認）
- WSL2でこのリポジトリをCloneした場合は、windowsフォルダをWindows側にコピーしてビルド・実行してください。
- 先に WSL 側の FastAPI を起動しておく（`./start.sh`、ポート 8012）。
- ビルド:
  ```powershell
  cd windows/OverlayClient
  dotnet build
  ```
- 実行:
  ```powershell
  dotnet run
  ```
- 配布用にまとめる:
  ```powershell
  dotnet publish -c Release -o output
  ```
  - `output/` に実行ファイル一式が出力されます（`settings.json` も同梱）。
- `settings.json` は exe と同じフォルダに置かれ、ビルド出力に同梱されます。
- WSL 側に繋がらない場合は `settings.json` の `server.base_url` を確認してください。
- 既定は **Ctrl+Altを押しながらドラッグし、キーを離すと**矩形ROI指定 → 翻訳実行です（Ctrlのみ/Altのみは設定で変更可）。
- 既定で ROI は **赤枠で一瞬表示**されます（`overlay.preview.show_roi_preview`）。
- Ctrl押下中のリアルタイム枠表示は `overlay.preview.live_preview` で切替できます。
- オーバーレイは「×」で閉じられます（アプリ自体の終了はトレイメニューの Quit）。

## Ubuntu Gnome Extension (Screenshot Translator)
Ubuntu (Gnome Shell) 環境向けの専用拡張機能です。Windows 版とは操作感が異なります。
バージョン19以降、**トップバーのメニューからモード切替**が可能になりました。

### 前提
- Python バックエンド (`./start.sh`) が `127.0.0.1:8012` で起動している必要があります。

### インストール方法
リポジトリ内の拡張機能をローカルの拡張機能ディレクトリにコピーしてインストールします。
**注意**: Wayland 環境での更新不具合を防ぐため、シンボリックリンクではなく**コピー**を推奨しています。

※ `gnome-extension/metadata.json` 内の UUID とディレクトリ名は一致させる必要があります。

```bash
# ディレクトリ作成 (例: screenshot-translator@<your-username>)
# 注意: <your-username> の部分は metadata.json の uuid の @ 以降と一致させてください
mkdir -p ~/.local/share/gnome-shell/extensions/screenshot-translator@<your-username>

# ファイルのコピー (更新時もこのコマンドを実行してください)
cp -r gnome-extension/* ~/.local/share/gnome-shell/extensions/screenshot-translator@<your-username>/
```

### 有効化
インストール後、Gnome Shell を再読み込みする必要があります。
- **Wayland (Ubuntu 標準)**: 一度ログアウトして、再度ログインしてください。
- **X11**: `Alt` + `F2` を押し、`r` を入力して Enter。

再読み込み後、「Extensions (拡張機能)」アプリまたは「Extension Manager」を開き、**Screenshot Translator** を有効にしてください。

### 使い方
画面上部（トップバー）に追加される **辞書アイコン「あ」** (または類似のアイコン) からモードを切り替えて使用します。

1. **モード選択**:
   トップバーのアイコンをクリックし、以下のいずれかを選択します。
   - **Text Overlay Mode (翻訳モード)** [デフォルト]: 選択範囲を翻訳して画面に表示します。
   - **TTS Once Mode (読み上げ・1回)**: 選択範囲を一度だけOCR→翻訳→読み上げします（監視しません）。
   - **TTS Monitor Mode (読み上げモード)**: 選択範囲を定期的に監視し、変化があった箇所を日本語で読み上げます。

2. **キャプチャ開始**:
   - ショートカット: **`Ctrl` + `Alt` + `S`**
   - 画面が少し暗くなり、マウスドラッグで範囲を選択します。

3. **Monitor Mode (読み上げ) の挙動**:
   - 選択後、バックグラウンドで **5秒ごとに** 選択範囲を監視します。
   - 監視中はトップバーのアイコンが赤くなり、メニューに「Stop Monitoring」が表示されます。
   - **新しいテキスト**（チャットの追記やスクロールなど）が検出されると、自動的に日本語で読み上げられます（**Supertonic 3 ONNX** 音声合成エンジンを使用）。
     - **Mixed TTS**: 日本語と英語が混在していても、言語を自動判定して1つの声で途切れずに読み上げます。
     - **Stability**: 翻訳の揺らぎを抑制し、無駄な再読み上げを減らしました。
     - **Full Read**: 初回起動時は、検出されたテキスト全文を読み上げます。
     - **Chunked Read**: 長文は文単位で順次読み上げ、最初の音が早く出るようにしています。
   - 停止するには、再度ショートカットを押して新しい範囲を選ぶか、メニューから「Stop Monitoring」を選択してください。

4. **Monitor Mode のリセット**:
   - 別の範囲を選択したい場合は、再度 **`Ctrl` + `Alt` + `S`** を押してください。古いモニタリングは停止し、新しい範囲で即座に開始されます。

### アンインストール (取り除き方)
拡張機能を削除するには、以下のディレクトリを削除し、Gnome Shell を再読み込みします。

```bash
rm -rf ~/.local/share/gnome-shell/extensions/screenshot-translator@<your-username>
```
その後、ログアウト/ログイン (または `Alt+F2 r`) してください。

### トラブルシューティング
- **更新が反映されない**: Wayland では `Alt+F2 r` が効かないことがあるため、ログアウト/ログインを試してください。
- **Pango エラー**: 古いバージョンがキャッシュされている可能性があります。一度アンインストール操作を行ってから再インストールしてください。


## 新API（WSL側）
- `GET /health`
- `POST /api/v1/ocr_translate_with_grounding`（`clean_image` (必須) と `guide_image` (任意) を multipart で送信）
- `POST /api/v1/ocr_translate_tts_once`（OCR→翻訳→読み上げを1回実行）
- `POST /api/v1/speak`（`{"text": "..."}` を読み上げるだけ。推論はしない。呼び出し側が表示した文字列をそのまま読ませるためのもので、Windows 常駐クライアントの「Speak Translation」が使う）

## 開発メモ
- 依存は仮想環境内 (`uv sync`) のみでインストールされ、ホストには入れません。
- フロントはプレーン HTML/CSS/JS (ビルド不要)。
- Markdown レンダリングは軽量な独自実装で、コード/箇条書き/強調をサポート。

## 既知の注意点
- llama.cpp 初回起動時にモデルをロードするため、1 回目のリクエストは時間がかかります。モデルのロードが完了（ステータスに「準備完了 (ログより) / 起動中（API応答あり・モデル読み込み未確認）」と表示されます。）しても翻訳が実行されない場合は、お手数ですが、再度画像を張り付けてください。
- モデル読み込み中に画像を貼り付けると失敗する場合があります。ステータスが「準備完了」と表示されてから貼り付けてください。
- `LLAMA_CTX` を大きくすると VRAM 使用量が増えます。GPU メモリに合わせて起動時に調整してください。
