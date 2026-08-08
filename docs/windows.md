# Windows ネイティブ構成（WSL2 不要）

Windows 単体でバックエンドとクライアントの両方を動かす手順です。
**NVIDIA (CUDA) 環境と非 CUDA 環境（AMD / Intel / CPU のみ）の両方に対応**しています。

WSL2 を使う従来の構成もそのまま利用できます（[../README.md](../README.md)）。

> **検証状況**: 依存パッケージの Windows 対応状況とリリース資産名は実際に確認済みですが、
> PowerShell スクリプト自体の実機実行は未検証です。

## 0. 前提

- Windows 10 / 11 (x64)
- [uv](https://docs.astral.sh/uv/) — `winget install astral-sh.uv` または `pip install uv`
- Git
- クライアントもビルドする場合: .NET 8 SDK

**MSVC / Visual Studio Build Tools は不要です。** llama.cpp は公式のビルド済み
バイナリを取得し、Python 依存もすべて wheel が存在するものだけを使います
（唯一の例外は任意機能の日本語 G2P。手順 3 を参照）。

## 1. クローン

```powershell
git clone <このリポジトリの URL>
cd ScreenshotTranslator2
```

## 2. llama.cpp の取得

ビルドは不要です。環境に合わせて flavor を選びます。

```powershell
# AMD (Ryzen AI Max+ 395 / Strix Halo など) / Intel / 汎用
.\app\scripts\fetch_llama_win.ps1

# NVIDIA (CUDA 13.x) ... Blackwell 世代はこちら
.\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3

# NVIDIA (CUDA 12.x) ... Ada 以前
.\app\scripts\fetch_llama_win.ps1 -Flavor cuda-12.4

# AMD ROCm を使いたい場合
.\app\scripts\fetch_llama_win.ps1 -Flavor hip-radeon

# GPU なし
.\app\scripts\fetch_llama_win.ps1 -Flavor cpu
```

`.\llama.cpp-win\llama-server.exe` に展開されます。`start.ps1` の既定パスです。

> **Blackwell 世代（RTX 50 シリーズ / RTX PRO Blackwell）は CUDA 12.8 以降が必要です。**
> 配布されている `cuda-12.4` ビルドでは動作しません。`-Flavor cuda-13.3` を使ってください。
> 利用可能な flavor はリリースごとに変わるため、`-Flavor` に無効な値を渡すと
> スクリプトがそのリリースで選べる一覧を表示します。

**CUDA を選んだ場合**、CUDA ランタイム (`cudart-*.zip`, 約 391MB) も自動で取得・展開
されます。これが無いと `llama-server.exe` は DLL 不足で起動しません。
CUDA Toolkit を別途インストールする必要はありません。

**AMD で Vulkan と ROCm のどちらを選ぶか**: まず `vulkan` を試してください。
ドライバ要件が緩く、ダウンロードも 34MB と小さいためです。`hip-radeon` (325MB) は
ROCm 対応 GPU でのみ動作します。

### llama-server.exe の配置場所

`start.ps1` は既定で **`.\llama.cpp-win\llama-server.exe`** を探します。
`fetch_llama_win.ps1` はここに展開するので、通常は意識する必要はありません。

自分でダウンロードした場合やソースからビルドした場合は、次のどちらかにします。

- **A. 既定の場所に置く** — zip の中身一式を `.\llama.cpp-win\` に展開する
  （`llama-server.exe` 単体ではなく、**同梱の `.dll` も全部**必要です）
- **B. `LLAMA_BIN` で場所を指定する**

```powershell
$env:LLAMA_BIN = "C:\tools\llama.cpp\llama-server.exe"
.\start.ps1
```

zip はフラット構造で、`llama-server.exe` と `ggml*.dll` / `mtmd.dll` などが
同じ階層に入っています。**exe だけを移動すると DLL が見つからず起動しません。**

### ビルド済みバイナリが使えない場合：ソースからビルド

ビルド済みが動かない環境（古いドライバ、特殊な GPU 構成など）では
ソースビルドに切り替えます。**この場合のみ** ツールチェインが必要です。

必要なもの:

- Visual Studio 2022 Build Tools（「C++ によるデスクトップ開発」ワークロード）
- CMake
- バックエンドに応じて:
  - Vulkan → [Vulkan SDK](https://vulkan.lunarg.com/)
  - CUDA → CUDA Toolkit（**Blackwell 世代は 12.8 以降**）
  - ROCm → AMD HIP SDK for Windows

「x64 Native Tools Command Prompt for VS 2022」または Developer PowerShell から:

```powershell
git clone --depth=1 https://github.com/ggml-org/llama.cpp llama.cpp
cd llama.cpp

# バックエンドは1つ選ぶ
cmake -B build -DGGML_VULKAN=ON -DLLAMA_CURL=OFF    # AMD / Intel / 汎用
# cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF    # NVIDIA
# cmake -B build -DGGML_HIP=ON  -DLLAMA_CURL=OFF    # AMD ROCm

cmake --build build --config Release -j
```

**出力先に注意**: Windows の Visual Studio ジェネレータはマルチ構成のため、
Linux の `build/bin/` ではなく **`build\bin\Release\`** に出ます。

```powershell
cd ..
$env:LLAMA_BIN = "llama.cpp\build\bin\Release\llama-server.exe"
.\start.ps1
```

毎回指定するのが面倒なら、`build\bin\Release\` の中身一式を `.\llama.cpp-win\` に
コピーすれば既定パスで拾われます。

なお `app/scripts/build_llama.sh` は bash 用です。Git Bash から
`GPU_BACKEND=vulkan ./app/scripts/build_llama.sh` としても動きますが、
出力先の差異があるため、Windows では上記の手動手順を推奨します。

## 3. Python 依存

`start.ps1` が `uv sync` を自動実行するので、通常は何もしなくて構いません。

**既定の構成に C++ コンパイラは不要です。** 既定の TTS エンジン `Supertonic 3` は
自前で G2P を行うため、日本語辞書も `pyopenjtalk` も必要としません。依存（onnxruntime /
sounddevice / soundfile / supertonic とその依存一式）はすべて Windows 用 wheel が
存在します。Windows でも Linux と同じ日本語読み上げ品質になります。

`Supertonic 3` のモデル（約 386MB）は初回起動時に自動ダウンロードされます。

**旧エンジン Kokoro を使う場合のみ（任意）**: `TTS_ENGINE=kokoro` を指定すると
`Kokoro-82M`（約 330MB）を使います。この場合に限り日本語 G2P の `misaki[ja]` が
効いてきますが、これは `pyopenjtalk` に依存し、pyopenjtalk は全バージョンで wheel を
配布しておらず Windows ではビルドに MSVC が必要です。そのため `pyproject.toml` では
Windows のみ既定の依存から除外しています。

**Kokoro は misaki なしでも動作します**（`app/tts_engines.py` が不在を検出して
フォールバックします）。Kokoro で日本語の読み上げ品質を上げたい場合のみ、
Visual Studio Build Tools を入れた上で：

```powershell
uv sync --extra ja-tts
```

## 4. モデルの配置

`models\` に置きます。ファイル名が既定と一致していれば設定は不要です。

| ファイル | サイズ | 必須 |
|---|---|---|
| `models\gemma-4-26B_q4_0-it.gguf` | 14.4 GB | ○ |
| `models\gemma-4-26B-it-mmproj.gguf` | 1.2 GB | ○ |
| `models\mtp-gemma-4-26B-A4B-it.gguf` | 0.9 GB | 任意（高速化） |

配布元: [google/gemma-4-26B-A4B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf)

14GB を安定して落とすには `hf` CLI が便利です（レジューム対応）。

```powershell
mkdir models -Force
uv tool run --from huggingface_hub hf download `
  google/gemma-4-26B-A4B-it-qat-q4_0-gguf `
  gemma-4-26B_q4_0-it.gguf gemma-4-26B-it-mmproj.gguf `
  --local-dir models
```

MTP ヘッド（任意）は Windows 単体でも作れます。

```powershell
.\app\scripts\build_mtp_gguf.ps1
```

GPU も MSVC も不要で、CPU 版 torch だけで変換します（空き容量 5GB 程度）。
`convert_hf_to_gguf.py` は手順 2 のビルド済み zip に入っていないため、llama.cpp の
ソースが無ければスクリプトが自動で shallow clone します（cmake ビルドはしません）。
詳細と手動手順は [mtp.md](mtp.md) を参照してください。

既に Linux / WSL2 側で生成済みなら、`models\` に `.gguf` を 1 個コピーするだけでも
構いません（ファイル名が既定のままなら `start.ps1` が自動で拾います）。

## 5. 起動

```powershell
.\start.ps1
```

- llama-server が 8009、Web UI が 8012 で起動します
- ブラウザで `http://127.0.0.1:8012` を開くとクリップボード貼り付けの Web UI が使えます
- `models\mtp-*.gguf` があれば投機的デコードが自動で有効になります

`start.sh` と同じ環境変数が使えます。

```powershell
$env:LLAMA_CTX = "4096"     # VRAM が厳しいとき
.\start.ps1
```

既に llama-server を別途起動している場合：

```powershell
$env:SKIP_LLAMACPP = "1"
$env:LLAMA_SERVER_URL = "http://127.0.0.1:8009"
.\start.ps1
```

## 6. 常駐オーバーレイクライアント

`settings.json` の `base_url` は `http://127.0.0.1:8012` のままで**変更不要**です
（WSL2 経由でもネイティブでも同じアドレスになります）。

```powershell
cd windows\OverlayClient
dotnet run
```

`Ctrl+Alt` を押した位置と離した位置で矩形を作ると、その範囲を翻訳して
オーバーレイ表示します。詳細は [../windows/OverlayClient/README.md](../windows/OverlayClient/README.md)。

---

## AMD APU 固有の設定（Ryzen AI Max+ 395 / Strix Halo など）

### GPU メモリ割り当て

このクラスの APU は**容量ではなくメモリ帯域**が速度を決めます。
BIOS の UMA 設定、または AMD ドライバの **Variable Graphics Memory** で
iGPU に十分なメモリを割り当ててください。

既定構成の必要量は **約 16.5 GB** + KV cache です。
128GB 機で 64GB 割り当てなら十分な余裕があります。

### メモリが足りない場合：Gemma 4 12B

12B（QAT q4_0、約 7.2GB）に切り替えられます。

```powershell
$env:LLAMA_MODEL = "models\gemma-4-12b-it-qat-q4_0.gguf"
$env:LLAMA_MMPROJ = "models\mmproj-gemma-4-12b-it-qat-q4_0.gguf"
$env:LLAMA_MODEL_NAME = "Gemma-4-12B-It-QAT"
$env:LLAMA_SPEC_DRAFT_MODEL = "models\mtp-gemma-4-12B-it-qat-Q4_0.gguf"
.\start.ps1
```

配布元: [google/gemma-4-12B-it-qat-q4_0-gguf](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf)
（MTP ヘッドも同リポジトリに GGUF で同梱されています）

**ただし品質面の注意があります。** 同一画像での比較で、12B は金額の変換を 2 箇所
誤りました（`$3.1bn` → 「30億ドル」、`$13.2bn` → 「133億ドル」）。26B は同条件で
誤りゼロでした。このアプリのシステムプロンプトは数値の厳密な保持を最優先で
要求しているため、**メモリに余裕があるなら 26B を推奨**します。

なお 12B の mmproj は `gemma4uv`（vision + audio 統合型）で、26B の `gemma4v`
（ViT タワー型）とは形式が異なります。12B を使う場合は比較的新しい llama.cpp が
必要です（手順 2 のビルド済みバイナリなら問題ありません）。

---

## 代替構成：FastAPI は WSL2、llama-server だけ Windows

WSL2 環境から移行する途中の疎通確認に便利です。コード変更なしで試せます。

Windows 側:
```powershell
.\llama.cpp-win\llama-server.exe --host 0.0.0.0 --port 8009 `
  -m models\gemma-4-26B_q4_0-it.gguf -c 8192 -ngl 999 --jinja --flash-attn on `
  --mmproj models\gemma-4-26B-it-mmproj.gguf --reasoning off --reasoning-budget 0
```

WSL2 側（`<host-ip>` は Windows ホストの IP）:
```bash
SKIP_LLAMACPP=1 LLAMA_SERVER_URL=http://<host-ip>:8009 ./start.sh
```

## トラブルシュート

**`llama-server.exe` が DLL エラーで起動しない（CUDA）**
`cudart-*.zip` の展開漏れです。`.\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3`
を実行し直してください。

**CUDA ビルドを入れたのに GPU が使われない / 起動に失敗する（RTX 50 系）**
Blackwell 世代は CUDA 12.8 以降が必要です。`cuda-12.4` ではなく `cuda-13.3` を
使ってください。

**`uv sync` が pyopenjtalk のビルドで失敗する**
`--extra ja-tts` を付けていませんか。既定では Windows で除外されます。これは旧エンジン
Kokoro 用の任意依存で、既定の Supertonic には不要です。明示的に有効化する場合のみ
Visual Studio Build Tools が必要です。

**`setup_tts.py` が `Failed to install UniDic` と出す**
UniDic は `misaki[ja]` に付随するため、Windows 既定構成では入りません。これは
`TTS_ENGINE=kokoro` を指定したときだけ実行される処理です。処理は続行され、
TTS も動作します。無視して構いません。

**読み上げの声を変えたい / 速度を変えたい**
`SUPERTONIC_VOICE`（`M1`〜`M5` / `F1`〜`F5`、既定 `F2`）と `SUPERTONIC_SPEED`
（0.7〜2.0、既定 `1.05`）を設定してください。
既定より少し速くする例（未設定なら `1.05` で動きます）:

```powershell
$env:SUPERTONIC_VOICE = "F3"
$env:SUPERTONIC_SPEED = "1.2"
```

**スクリプトが実行ポリシーで拒否される**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
