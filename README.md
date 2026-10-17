# Screenshot Translator

画面内の英語を、ローカルのGemma 4で読み取り、日本語へ翻訳するアプリです。画像を貼り付けるWeb UI、範囲を選ぶオーバーレイ、字幕を保存しながら読む学習モードを使えます。

| 使い方 | 対応環境 | できること |
|---|---|---|
| Web UI | Windows / Linux / WSL2で起動したサーバー＋ブラウザ | 画像を貼り付けて翻訳し、Markdownをコピー |
| Windows常駐クライアント | Windows 10/11（x64） | 選択範囲の訳をオーバーレイ表示、任意で読み上げ |
| Ubuntuの統合拡張 | GNOME Shell 46〜50 | 一つのアイコンから通常翻訳・読み上げ・学習モードを選択 |

**7.8.0では学習モードを追加しました。** 原文と訳を履歴に残し、取得を止めると難しい表現の文脈付きメモを生成します。記録の切替・改名・削除にも対応します。学習パネルはUbuntuのGNOME/GTK4向けです。

<img width="640" alt="Web UIで画像を貼り付け、日本語訳を表示した例" src="https://github.com/user-attachments/assets/f24ef322-08b5-48e6-aa54-71b9e06d7401" />

## 導入

まず翻訳用サーバー（バックエンド）を起動し、続けて使いたい画面を開きます。以下のコマンドは、特に指定がない限りリポジトリのルートで実行してください。

### 1. 必要なツールとソースを用意する

共通で **Git** と **[uv](https://docs.astral.sh/uv/)** が必要です。Pythonは3.10以降を使います。Python依存パッケージは起動スクリプトが`.venv`へ同期します。GPUを使う場合は、対応するドライバも用意してください。

```sh
git clone https://github.com/amariichi/ScreenshotTranslator2.git
cd ScreenshotTranslator2
```

- **Linux / WSL2**: モデル実行用のllama.cppをビルドするため、CMakeとC++コンパイラが必要です。CUDAを使う場合は対応するCUDA Toolkit、Vulkanを使う場合はVulkan開発環境も用意してください。詳細は[llama.cppのビルドガイド](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)を参照してください。
- **Windows単体**: llama.cppのビルド済みバイナリを使えます。通常はMSVC・CUDA Toolkitの導入は不要です。常駐クライアントを使う場合は.NET 8 SDKも必要です。
- **Ubuntuの学習モード**: 追加で`gjs`、GTK4、Soup3、`glib-compile-schemas`が必要です。GNOMEの版は`gnome-shell --version`で確認できます。実動確認はGNOME 50.1 / Waylandで行っています。

Linuxで読み上げを使う場合は、PortAudio（`libportaudio2`）などの音声出力環境も必要です。[音声設定](docs/configuration.md#読み上げの設定)を参照してください。

### 2. モデルを配置する

[Google公式の配布ページ（Files）](https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/tree/main)から次の2ファイルを取得し、`models`フォルダを作成して配置してください。画像用の`mmproj`も必須です。

```text
models/
  gemma-4-26B_q4_0-it.gguf
  gemma-4-26B-it-mmproj.gguf
```

モデル本体は約14.4GB、画像用ファイルは約1.2GBです。実行時には追加のRAM/VRAMが必要で、速度と必要メモリは環境により変わります。CPUのみでも実行できますが、リアルタイム用途の速度は保証しません。

任意の高速化機能MTPは、まず通常構成で起動してから追加できます。手順は[MTPガイド](docs/mtp.md)を参照してください。

### 3. OSに合わせてバックエンドを起動する

#### Linux / WSL2

環境に合うビルドを一つ選びます。

```bash
./app/scripts/build_llama.sh                       # NVIDIA / CUDA
# GPU_BACKEND=vulkan ./app/scripts/build_llama.sh   # AMD / Intel / Vulkan
# GPU_BACKEND=cpu ./app/scripts/build_llama.sh      # CPUのみ
```

ビルドが終わったら起動します。

```bash
./start.sh
```

#### Windows単体（WSL2不要）

PowerShellで、環境に合うバイナリを一つ取得します。

```powershell
.\app\scripts\fetch_llama_win.ps1                    # AMD / Intel / 汎用Vulkan
# .\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3 # NVIDIA Blackwell向け
# .\app\scripts\fetch_llama_win.ps1 -Flavor cuda-12.4 # NVIDIA Ada以前向け
# .\app\scripts\fetch_llama_win.ps1 -Flavor cpu       # CPUのみ
```

続けて起動します。Blackwellでは`cuda-12.4`を選ばないでください。配布バイナリの選択やトラブル対処は[Windowsガイド](docs/windows.md)にあります。

```powershell
.\start.ps1
```

初回起動では、Python依存と読み上げ用のSupertonic 3モデル（約386MB）も取得します。ネット接続と追加の空き容量が必要です。起動ターミナルを開いたまま、モデルの読み込み完了を待ってください。既定はモデルサーバーが`8009`、アプリが`8012`です。終了は起動ターミナルで`Ctrl+C`を押します。

読み上げモデルにはアプリ本体と異なる利用条件があります。音声を利用・公開する前に[ライセンスの注意](docs/configuration.md#読み上げモデルのライセンス)を確認してください。

## 使う画面を選ぶ

### Web UI：画像を一度ずつ翻訳する

1. バックエンドを起動したPCのブラウザで **http://127.0.0.1:8012** を開きます。
2. 画像を`Ctrl+V`で貼り付けるか、ドラッグ＆ドロップします。
3. 必要なら追加指示を入力して「再送信」します。結果は「Markdownをコピー」で取り出せます。

### Windows：選択範囲の訳をオーバーレイ表示する

バックエンドを起動したまま、別のPowerShellをリポジトリのルートで開きます。

```powershell
dotnet run --project windows/OverlayClient/OverlayClient.csproj
```

翻訳したい範囲の角で`Ctrl+Alt`を押し、ポインターを反対側の角へ移動してキーを離すと翻訳します。トレイメニューの「Speak Translation」で読み上げを切り替え、「Quit」で常駐アプリを終了します。

バックエンドはWindowsの`start.ps1`、またはWSL2の`start.sh`で起動できます。WSL2でクローンした場合も、WPFクライアントはWindows側へコピーして起動してください。[設定・配布方法](windows/OverlayClient/README.md)も参照できます。

### Ubuntu：通常翻訳と学習モードを切り替える

両方を使える統合拡張をインストールします。バックエンドとは別のターミナルで実行してください。

```bash
./gnome-learning-extension/install.sh
```

新しい拡張が見つからない場合や更新が反映されない場合は、一度ログアウトして再ログインしてください。その後、旧拡張を無効にし、統合拡張を有効にします。

```bash
gnome-extensions disable screenshot-translator@amariichi
gnome-extensions enable screenshot-translator-learning@amariichi
```

旧拡張を入れていなければ、最初の`disable`は不要です。二つを同時に有効にせず、統合版のアイコンから使い分けます。再ログインした場合はバックエンドも起動し直してください。インストールや有効化だけでは画面取得は始まりません。

**一度の翻訳・読み上げには「従来モード」**を使います。トップバーのアイコンから「従来モード」を開き、`Text Overlay Mode`、`TTS Once Mode`、`TTS Monitor Mode`を選びます。「取得範囲を選択…」または`Ctrl+Alt+S`でドラッグ選択します。Monitorは約5秒ごとに確認し、新しい内容を読み上げます。終了は「Stop Monitoring」または「すべての取得を停止」です。選択は`Esc`／右クリックで取り消せます。

**字幕やメッセージを保存して学ぶには「学習モード」**を使います。

1. アイコンの「学習モード」→「学習パネルを開く」を選びます。ターミナルからは`./start-learning.sh`でも開けます。
2. 「新規」で作品ごとの記録を作り、パネルを動画の外へ移動します。
3. 「範囲を選ぶ」で字幕やメッセージの領域を選ぶと取得が始まり、原文と訳が履歴に追加されます。
4. パネルの「停止」で取得を止めると、未処理の難しい表現について学習メモを生成します。**動画の一時停止とは別操作です。** 「再開」すると翻訳を優先しますが、実行中の解説要求1件が終わるまで待つ場合があります。
5. 「保存した記録を開く」でMarkdownを確認できます。記録と学習メモは自動保存されます。

保存先はリポジトリ内の **`.learning-data/`** です。`日時-作品名-ID.md`に原文・訳・学習メモをまとめ、履歴DBも同じフォルダに保存します。画像は保存せず、このフォルダはGit対象外です。原文と私的メモを含むため、共有する前に内容を確認してください。

パネルが取得範囲に重なる、閉じる、最小化する、別のワークスペースへ移ると、安全のため取得を停止します。学習メモは誤り得る下書きで、パネルから編集できます。記録管理やゴミ箱の操作は[学習モードの使い方](docs/learning.md)を参照してください。従来拡張だけを使いたい場合は[従来GNOME拡張ガイド](docs/gnome.md)を使ってください。

## 困ったとき

- **接続できない**：起動ターミナルのエラーと、`http://127.0.0.1:8012/health`を確認してください。モデル読み込み中なら、完了後に画像を送り直します。
- **GPUメモリ不足**：`LLAMA_CTX`を小さくしてモデルサーバーを起動し直します。例は[設定ガイド](docs/configuration.md#メモリとポートの調整)にあります。
- **学習モードで取得できない**：統合拡張が有効か、パネルが取得範囲に重なっていないかを確認してください。拡張が未接続なら[学習モードの起動手順](docs/learning.md#起動)を確認します。
- **GNOME拡張の更新が反映されない**：インストール後に通常のログアウト・再ログインを行ってください。

## 詳細ガイド

- [学習モード](docs/learning.md)：保存、編集、重複抑制、停止・再開、記録とゴミ箱の管理
- [Windows](docs/windows.md)：バイナリ、MTP、WSL2、AMD APU、トラブル対処
- [従来GNOME拡張](docs/gnome.md)：単独導入、モード操作、更新・削除
- [設定](docs/configuration.md)：環境変数、別モデル、TTS、実験的vLLMバックエンド
- [MTP](docs/mtp.md)：任意の高速化と有効になっているかの確認
- [開発](docs/development.md)：API、テスト、構成

本アプリのコードは[MITライセンス](LICENSE)です。既定構成では画像の読み取り・翻訳・音声合成をローカルで行います。OCRや訳、皮肉・俗語の意味、話者判定は誤ることがあります。必要な箇所は原文と照合してください。
