# Screenshot Translator (Qwen3-VL-30B-A3B)

<img width="640" height="521" alt="Image" src="https://github.com/user-attachments/assets/f24ef322-08b5-48e6-aa54-71b9e06d7401" />

クリップボードから貼り付けたスクリーンショットを llama.cpp (Qwen3-VL-30B-A3B-Instruct + mmproj-F32) で OCR + 英→日翻訳し、Markdown で表示するシンプルな Web アプリです。

## 要件
- CUDA 対応 GPU (例: CUDA 13 / nvcc 13.0.88)
- `uv` (Python パッケージマネージャ) がホストにインストール済み
- 下の２つのモデルファイルを[リンク先からダウンロード](https://huggingface.co/unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF/tree/main)して、ローカル（./models/フォルダを作ってその中）に配置
  - [`models/Qwen3-VL-30B-A3B-Instruct-UD-Q4_K_XL.gguf`](https://huggingface.co/unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF/blob/main/Qwen3-VL-30B-A3B-Instruct-UD-Q4_K_XL.gguf)
  - [`models/mmproj-F32.gguf`](https://huggingface.co/unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF/blob/main/mmproj-F32.gguf)

## 使い方
1. llama.cpp を CUDA ビルド
   ```bash
   ./app/scripts/build_llama.sh
   ```
   - `GGML_CUDA=ON` のみでビルドし、`LLAMA_CURL=OFF` でlibcurl未インストール環境でも通るようにしています。
   - 並列ビルドは `JOBS` 環境変数で上書き可能（既定は `nproc` があればその値、なければ4）。
   - 必要に応じて `LLAMA_REPO` / `LLAMA_DIR` を上書きしてください。
2. モデルを `models/` 配下へ配置 (パスは環境変数で変更可)。
3. サーバー起動
   ```bash
   ./start.sh
   ```
   - デフォルト: llama-server 8009, Web UI 8012, ctx=8192。
   - VRAMが少ない場合は起動時に `LLAMA_CTX` を下げて起動できます（例: `LLAMA_CTX=4096 ./start.sh`）。
   - 既存の llama-server を使う場合: `SKIP_LLAMACPP=1 LLAMA_SERVER_URL=http://127.0.0.1:8009 ./start.sh`

## 主な環境変数
- `WEB_PORT` (既定: 8012)
- `LLAMA_PORT` (既定: 8009)
- `LLAMA_MODEL` (既定: models/Qwen3-VL-30B-A3B-Instruct-UD-Q4_K_XL.gguf)
- `LLAMA_MMPROJ` (既定: models/mmproj-F32.gguf)
- `LLAMA_CTX` (既定: 8192)
- `LLAMA_BIN` (既定: ./llama.cpp/build/bin/llama-server)
- `SKIP_LLAMACPP`=1 で llama-server 起動をスキップ

### `LLAMA_CTX` について（VRAM調整）
- `LLAMA_CTX` は **llama.cpp の `llama-server` を起動する際の `-c`** に渡され、主に KV cache のサイズに効くため VRAM 使用量に影響します。
- `LLAMA_CTX` を変更してVRAM使用量を変えたい場合は、**llama-server を起動し直す必要があります**（FastAPI側の環境変数だけ変えてもVRAMは変わりません）。
- `SKIP_LLAMACPP=1` で既存の llama-server を使う場合、その既存プロセスが `-c` で起動された値が有効になります。

## フロントエンドの使い方
- 画像を **貼り付け** (Ctrl+V) するかドラッグ&ドロップ。
- 任意で追加指示を入力し「再送信」。
- 返ってきた Markdown を「コピー」ボタンで取得可能。
- CSS で横に長い行も折り返して表示。

## アーキテクチャ
- `llama.cpp` の `llama-server --api` を常駐させ、OpenAI 互換 `/v1/chat/completions` でマルチモーダル推論。
- FastAPI (ポート 8012) が画像を PNG に正規化 → llama-server へ base64 画像付きメッセージ送信。
- 応答 Markdown をそのまま表示 (要約禁止プロンプトを付与)。

## 開発メモ
- 依存は仮想環境内 (`uv sync`) のみでインストールされ、ホストには入れません。
- フロントはプレーン HTML/CSS/JS (ビルド不要)。
- Markdown レンダリングは軽量な独自実装で、コード/箇条書き/強調をサポート。

## 既知の注意点
- llama.cpp 初回起動時にモデルをロードするため、1 回目のリクエストは時間がかかります。モデルのロードが完了（ステータスに「準備完了 (ログより) / 起動中（API応答あり・モデル読み込み未確認）」と表示されます。）しても翻訳が実行されない場合は、お手数ですが、再度画像を張り付けてください。
- モデル読み込み中に画像を貼り付けると失敗する場合があります。ステータスが「準備完了」と表示されてから貼り付けてください。
- `LLAMA_CTX` を大きくすると VRAM 使用量が増えます。GPU メモリに合わせて起動時に調整してください。
