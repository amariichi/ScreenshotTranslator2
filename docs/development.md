# 開発ガイド

## 構成

- llama.cppの`llama-server`が画像付きのOpenAI互換`/v1/chat/completions`を提供します。既定ポートは8009です。
- FastAPIは画像をPNGへ正規化してモデルへ送り、Web UIと各クライアント向けAPIを提供します。既定ポートは8012です。
- Web UIは`app/static/`のHTML/CSS/JSで、フロントエンドのビルドは不要です。
- Windowsクライアントは`windows/OverlayClient/`、GNOME拡張とGTKパネルは`gnome-learning-extension/`と`app/learning-panel*.js`です。
- 学習モードは`app/learning*.py`に処理・保存・モデル要求を分けています。保存形式と処理上限は[学習モード](learning.md)を参照してください。

## 主なAPI

バックエンドはWindows / Linuxのどちらでも同じ経路を提供します。通常起動後、`http://127.0.0.1:8012/docs`で完全な定義を確認できます。

| 経路 | 用途 |
|---|---|
| `GET /health` | アプリの生存確認。モデルの準備完了を保証するものではない |
| `POST /api/v1/ocr_translate_with_grounding` | `clean_image`必須・`guide_image`任意のmultipart画像翻訳 |
| `POST /api/v1/ocr_translate_tts_once` | 一度のOCR・翻訳・読み上げ |
| `POST /api/v1/speak` | JSONの`text`を読み上げ。翻訳推論は行わない |
| `/api/learning/...` | 記録、取得、学習メモ、ゴミ箱の管理 |

学習APIはOriginヘッダーのあるブラウザ要求を拒否します。モデルの接続先もローカルHTTPに制限します。既存TTS等を使わない専用サーバーは`app.learning_app:app`です。

## バックエンドのテスト

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_learning*.py' -v
```

一時ディレクトリと合成モデルを使うため、実LLMや実際の記録は不要です。CIではPython/JS/shell/PowerShell構文、GSettingsスキーマ、版整合性、学習バックエンド、Windows WPFビルドも確認します。

## GNOMEとGTKの隔離テスト

GNOME 50の`gnome-shell-test-tool`で、ユーザーのデスクトップとは別のD-Bus/Waylandセッションを起動します。合成画面とローカルfixtureを使い、実LLM・音声品質は検証しません。

```bash
mkdir -p /tmp/screenshot-translator-test
gnome-extensions pack --force --extra-source=legacy-capture.js \
  --out-dir /tmp/screenshot-translator-test gnome-extension
dbus-run-session -- gnome-shell-test-tool --headless \
  --extension /tmp/screenshot-translator-test/screenshot-translator@amariichi.shell-extension.zip \
  "$PWD/tests/gnome-extension-smoke.js"
```

統合拡張と実GTKパネル:

```bash
./gnome-learning-extension/pack.sh /tmp/screenshot-translator-test
dbus-run-session -- gnome-shell-test-tool --headless \
  --extra-filter io.github.amariichi.ScreenshotTranslatorLearning \
  --extension /tmp/screenshot-translator-test/screenshot-translator-learning@amariichi.shell-extension.zip \
  "$PWD/tests/learning-panel-smoke.js"
```

`tests/learning-extension-smoke.js`へ置き換えると統合拡張の取得・制御を単独検証できます。GNOME 50では`--disable-animations`を加えると試験開始が競合するため付けません。GNOMEの版ごとの変更は[公式移行ガイド](https://gjs.guide/extensions/upgrading/gnome-shell-50.html)で確認してください。

テスト出力、画像、記録、ログ、DB、モデル、ビルド成果物はコミットしないでください。実データによる検証と公開用の合成fixtureを分け、ログを共有するときも内容を確認してください。
