# バックエンドと読み上げの設定

最初の導入は[README](../README.md#導入)を参照してください。以下のBash例はLinux / WSL2向けです。Windowsでは環境変数を`$env:変数名 = "値"`で設定してから`start.ps1`を実行します。

## メモリとポートの調整

GPUメモリが足りない場合は、コンテキスト長を下げて起動します。

```bash
LLAMA_CTX=4096 ./start.sh
```

```powershell
$env:LLAMA_CTX = "4096"
.\start.ps1
```

`LLAMA_CTX`はモデルサーバーの`-c`へ渡されます。変更を反映するにはモデルサーバーの再起動が必要で、既存サーバーへ接続するアプリ側だけを再起動してもメモリ使用量は変わりません。

既存のモデルサーバーを使う場合:

```bash
SKIP_LLAMACPP=1 LLAMA_SERVER_URL=http://127.0.0.1:8009 ./start.sh
```

| 変数 | 既定 | 用途 |
|---|---|---|
| `WEB_PORT` | `8012` | アプリのポート |
| `LLAMA_PORT` | `8009` | 起動するモデルサーバーのポート |
| `LLAMA_CTX` | `8192` | モデルのコンテキスト長 |
| `LLAMA_PARALLEL` | `1` | 同時処理スロット数 |
| `LLAMA_MODEL` | `models/gemma-4-26B_q4_0-it.gguf` | モデルのパス |
| `LLAMA_MMPROJ` | `models/gemma-4-26B-it-mmproj.gguf` | 同じモデルに対応する画像用ファイル |
| `LLAMA_MODEL_NAME` | `Gemma-4-26B-A4B-It-QAT` | API要求に指定するモデル名 |
| `LLAMA_BIN` | Linux: `./llama.cpp/build/bin/llama-server`、Windows: `llama.cpp-win\llama-server.exe` | 実行ファイル |
| `SKIP_LLAMACPP` | `0` | `1`ならモデルサーバーを起動しない |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8009` | 接続するモデルAPIのベースURL |
| `LLAMA_SPEC_DRAFT_MODEL` | 既定モデル使用時に対応ファイルがあれば自動選択 | MTPヘッドのパス。詳細は[MTP](mtp.md) |
| `LLAMA_SPEC_TYPE` | `draft-mtp` | MTPの方式 |
| `LLAMA_REASONING` | Gemma 4では`off` | モデルのreasoning設定 |
| `LLAMA_THINK_BUDGET` | Gemma 4では`0` | reasoning budget |

WindowsのMTP候補長は`LLAMA_SPEC_DRAFT_N_MAX`で指定でき、既定は`1`です。Linuxの起動スクリプトにはこの変数の指定はありません。学習モードのモデル接続先は、リダイレクトしないローカルHTTPに限ります。

## llama.cppのビルド設定

`build_llama.sh`の`GPU_BACKEND`は`cuda`（既定）、`vulkan`、`hip`、`cpu`を選べます。AMD APUではまずVulkanを使います。必要なSDKやドライバは[公式ビルドガイド](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)で確認してください。

`JOBS`で並列ビルド数、`LLAMA_REPO` / `LLAMA_DIR`でソース取得元・配置先を指定できます。libcurlへの依存を避けるため`LLAMA_CURL=OFF`でビルドします。既存のllama.cppチェックアウトは自動更新しません。モデルやMTPに未対応の古い版なら、ローカル変更を保全してから対応版へ更新してください。

## 別のモデルを使う

モデルと画像用`mmproj`は対応する組を使います。26B-A4B、E4B、Qwen3.5の画像用ファイルは共用できません。同名ファイルが配布されている場合は別名で保存してください。以下は配置名の例です。

```bash
LLAMA_MODEL=models/gemma-4-E4B-it-UD-Q4_K_XL.gguf \
LLAMA_MMPROJ=models/mmproj-F16_gemma4E4B.gguf \
LLAMA_MODEL_NAME=Gemma-4-E4B-It ./start.sh
```

```bash
LLAMA_MODEL=models/Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf \
LLAMA_MMPROJ=models/mmproj-F32.gguf ./start.sh
```

`start.sh`は上記Qwen3.5のモデルパスを使うと、`app/chat_templates/qwen3.5-35b-a3b.chat_template.jinja`を自動適用します。これはQwen公式テンプレートを基にthinkingを無効化したものです。別パスの場合は`LLAMA_CHAT_TEMPLATE_FILE`を明示指定できます。テンプレート指定と`LLAMA_CHAT_TEMPLATE_KWARGS`はLinuxの`start.sh`向けです。Gemma 4の既定構成はモデル内蔵テンプレートを使います。

従来の26B-A4Bモデルを使う例:

```bash
LLAMA_MODEL=models/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf \
LLAMA_MMPROJ=models/mmproj-F16.gguf ./start.sh
```

モデル変更後はOCR、訳、学習メモのJSON出力を確認してください。モデルが違えば、同じ速度や品質になるとは限りません。

## 読み上げの設定

既定はSupertonic 3のONNXモデルをCPUで実行します。起動スクリプトが初回にモデルを取得し、以後はキャッシュを使います。Linuxの音声再生にはPortAudioと、ALSA経由の再生を使う場合は`aplay`が必要です。

| 変数 | 既定 | 設定範囲・用途 |
|---|---|---|
| `SUPERTONIC_VOICE` | `F2` | `M1`〜`M5` / `F1`〜`F5` |
| `SUPERTONIC_SPEED` | `1.05` | `0.7`〜`2.0` |
| `SUPERTONIC_STEPS` | `8` | `5`〜`12` |
| `SUPERTONIC_LANGUAGE` | `en` | パッセージ全体に渡す言語タグ |

既定の`en`は、日本語訳に残る英字も含めて一つの声で読むための設定です。単語ごとの言語自動判定ではありません。`ja`で日本語タグを指定でき、`auto`を指定した場合のみ文字種から日本語/英語のタグを選びます。読み方は文章により変わるため、調整後に音声を確認してください。

従来のKokoroを使う場合は`TTS_ENGINE=kokoro`を指定します。Windowsで日本語G2Pも使う場合は`uv sync --extra ja-tts`が必要で、`pyopenjtalk`のビルドにMSVCが必要です。既定のSupertonicにはこの追加設定は不要です。

### 読み上げモデルのライセンス

アプリのコードはMITですが、[Supertonic 3のモデル](https://huggingface.co/Supertone/supertonic-3)はOpenRAIL-Mです。重みはこのリポジトリでは再配布せず、利用者の環境へ取得します。

モデルのライセンスには用途制限があります。また、生成した情報・コンテンツを公開する際は機械生成であることの明示が求められます。音声を利用・公開する前に、[LICENSEの本文とAttachment A](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE)を確認してください。Kokoroのモデルは別のライセンスです。

## 実験的なvLLMバックエンド

既定はGemma 4 GGUF＋llama.cppです。既存の実験用スクリプトでは、別モデルの`nvidia/diffusiongemma-26B-A4B-it-NVFP4`をvLLMで起動できます。既定構成と翻訳・日本語・JSON出力の品質が同じとは限らず、処理時間も入力と環境に依存します。学習モードの互換性を含めて確認してから使ってください。

Linux、Docker、NVIDIA Container Toolkit、対応するNVIDIA GPUと十分なVRAM・ディスク容量が必要です。[モデルカード](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4)はBlackwell/Hopperを対応環境に挙げています。利用するvLLM版との組合せは[公式レシピ](https://recipes.vllm.ai/Google/diffusiongemma-26B-A4B-it)で確認してください。モデル重みに加え、Dockerイメージやコンパイルキャッシュにも容量を使います。

リポジトリのスクリプトは開発版イメージをdigestで固定しています。最新レシピのvLLM版と一致するとは限りません。既存モデルと同じGPUへ同時にロードできない場合があるため、バックエンドを切り替える前に取得・推論とモデルサーバーを停止してください。

```bash
BACKEND=vllm ./start.sh
```

初回はDockerイメージ・重みの取得、ロード、コンパイルに時間がかかります。スクリプトはウォームアップも実行します。`Ctrl+C`でアプリを終了しても、既定ではコンテナを残します。終了時にコンテナも止める場合:

```bash
STOP_BACKEND_ON_EXIT=1 BACKEND=vllm ./start.sh
```

| 変数 | 既定 | 用途 |
|---|---|---|
| `VLLM_PORT` | `8000` | モデルAPIのポート |
| `VLLM_GPU_MEM_UTIL` | `0.75` | GPUメモリ割当の設定値 |
| `VLLM_MAX_MODEL_LEN` | `8192` | コンテキスト長 |
| `VLLM_MAX_NUM_SEQS` | `4` | 最大同時シーケンス数 |
| `VLLM_IMAGE` | digest固定 | 使用するイメージ |
| `VLLM_NAME` | `dgemma` | コンテナ名 |
| `STOP_BACKEND_ON_EXIT` | `0` | `1`ならアプリ終了時にコンテナも停止・削除 |

個別の制御は`./app/scripts/run_vllm_backend.sh start`、`status`、`stop`です。`stop`は指定名のコンテナを削除し、`start`も停止済みの同名コンテナを作り直します。他の用途のコンテナと同じ名前を使わないでください。既存スクリプトはDockerのポートを公開するため、ネットワークからのアクセス範囲も確認してください。

ログは`docker logs dgemma`で確認します。別名を指定した場合はその名前へ置き換えます。空の回答が返る場合は、起動オプションの`enable_thinking:false`とサーバーの出力形式を確認してください。JSON制約の強制やtemperatureの扱いは、通常の逐次生成モデルと同じとは限りません。
