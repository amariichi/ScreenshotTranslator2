# MTP（投機的デコード）— 任意

MTP (multi-token prediction) ヘッドを併用すると、出力速度が大きく改善します。
**完全に任意**の機能で、ヘッドの GGUF が無ければ `start.sh` / `start.ps1` はそのまま
通常動作します。

## 効果（実測）

RTX PRO 4500 Blackwell / 同一スクリーンショット / prompt 415 tokens。

| 構成 | decode | ドラフト受理率 |
|---|---|---|
| unsloth `UD-Q4_K_XL`（旧既定） | 144.8 tok/s | — |
| Google QAT `q4_0`（現既定） | 182.3 tok/s | — |
| Google QAT `q4_0` + MTP | **230.2 tok/s** | 60.6% |

メモリ帯域が律速となる環境（AMD Ryzen AI Max+ 395 等の APU）ほど、
投機的デコードの恩恵は大きくなります。

## 必要なもの

- **最新の llama.cpp**。MTP のドラフト種別は比較的新しい機能で、古いビルドは
  NextN テンソルを読み込むだけで推論には使いません（`// preserved but unused`）。
  `app/scripts/build_llama.sh` は起動時に checkout のコミットを表示します。
- `uv`（変換用の一時 venv を作ります）
- 空き容量 5GB 程度（safetensors 0.9GB + 生成物 0.9GB + torch ホイール 3GB 弱）

**GPU も C++ ツールチェインも不要**です。変換は CPU 版 torch だけで完結します。
ただし `convert_hf_to_gguf.py` が要るため、llama.cpp の**ソース**が必要です。Windows で
`fetch_llama_win.ps1`（ビルド済みバイナリのみ）を使っている場合はソースが無いので、
`build_mtp_gguf.ps1` が自動で shallow clone します（cmake ビルドはしません）。

モデル本体だけを新しくする分には llama.cpp の更新は不要です。**MTP を使う場合のみ**
最新版が必要です。

## 作り方

Google は assistant ヘッドを safetensors のみで配布しているため、GGUF に変換します。

```bash
./app/scripts/build_mtp_gguf.sh
```

Windows（WSL2 を使わない場合）は PowerShell 版を使います。

```powershell
.\app\scripts\build_mtp_gguf.ps1
```

既定では `google/gemma-4-26B-A4B-it-assistant` を取得し、
`models/mtp-gemma-4-26B-A4B-it.gguf`（F16 / 約 855MB）を生成します。
ファイル名が既定のままなら `start.sh` / `start.ps1` が自動で拾います。

他のモデル用に作る場合：

```bash
MTP_HF_REPO=google/gemma-4-12B-it-assistant \
MTP_OUTFILE=models/mtp-gemma-4-12B-it.gguf \
  ./app/scripts/build_mtp_gguf.sh
```

```powershell
.\app\scripts\build_mtp_gguf.ps1 -HfRepo google/gemma-4-12B-it-assistant `
  -OutFile models\mtp-gemma-4-12B-it.gguf
```

生成物が正しいかは、`general.architecture` が `gemma4-assistant` で、
`embedding_length_out` が本体モデルの `embedding_length` と一致することで確認できます
（26B-A4B なら 2816、12B なら 3840）。

## 手動で変換する場合

スクリプトを使わないときは、次の 2 点に注意してください。

**1. `tokenizer_config.json` にパッチが必要**

Google の assistant リポジトリは `"extra_special_tokens": []` をリストとして
出力していますが、transformers はここに辞書を期待するため、そのままでは
変換が失敗します。

```
AttributeError: 'list' object has no attribute 'keys'
```

空リストを空オブジェクト `{}` に書き換えれば通ります。他への影響はありません。

**2. 変換用の依存は uv の index 設定が必要**

`requirements-convert_hf_to_gguf.txt` は CPU 版 torch を別 index から取るため、
`--index-strategy unsafe-best-match` が要ります。

```bash
uv venv .mtp_venv --python 3.12
uv pip install --python .mtp_venv/bin/python --index-strategy unsafe-best-match \
  -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
.mtp_venv/bin/python llama.cpp/convert_hf_to_gguf.py <assistant-dir> \
  --outfile models/mtp-gemma-4-26B-A4B-it.gguf --outtype f16
```

Windows では venv の Python が `.mtp_venv\Scripts\python.exe` になります。この差と
`python3` コマンドの不在があるため、`build_mtp_gguf.sh` は Git Bash からでも動きません。
Windows では `build_mtp_gguf.ps1` を使ってください。

## 有効化

`start.sh` / `start.ps1` は、既定のモデルを使っていて既定名のヘッドが存在すれば
自動的に有効化します。明示指定する場合：

```bash
LLAMA_SPEC_DRAFT_MODEL=models/mtp-gemma-4-26B-A4B-it.gguf ./start.sh
```

**重要**: `--spec-draft-model` だけでは動きません。`--spec-type` の既定値は `none` の
ため、ヘッドは読み込まれても使われません。`start.sh` は `--spec-type draft-mtp` を
併せて渡します。手動で `llama-server` を起動する場合は必ず両方指定してください。

無効化するには `LLAMA_SPEC_DRAFT_MODEL=` を空で渡すか、ヘッドのファイルを退避します。

## 既知の警告（無害）

起動ログに次の 2 行が出ますが、メモリ見積り時のもので実害はありません。
llama.cpp 自身がコメントで "this warning is normal" と明記しています。

```
E llama_init_from_model: failed to initialize the context: Gemma4Assistant requires ctx_other to be set
W srv load_model: [spec] failed to measure draft model memory: failed to create llama_context from model
```

その後に `common_speculative_init_result: loading draft model ...` が出ていれば
正常に読み込まれています。実際に使われているかは、レスポンスの `timings` に
`draft_n` / `draft_n_accepted` が入るかで確認できます。
