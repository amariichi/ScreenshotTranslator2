# 従来GNOME拡張を単独で使う

このガイドは学習パネルを使わず、従来のOverlay/TTSだけを使う場合のものです。通常翻訳と学習の両方を使う場合は、[READMEの統合拡張の手順](../README.md#ubuntu通常翻訳と学習モードを切り替える)を使ってください。旧拡張と統合拡張を同時に有効にしないでください。

## 前提

GNOME Shell 46〜50を対象とします。実動確認はGNOME 50.1 / Waylandです。他の対象版やX11での動作確認は環境ごとに必要です。`gnome-shell --version`でShellの版を確認してください。

バックエンドは別ターミナルの`./start.sh`で起動しておきます。既定のアプリ接続先は`http://127.0.0.1:8012`です。

## インストールと更新

リポジトリのルートで実行します。

```bash
./gnome-extension/install.sh
```

対応Shell版を確認し、拡張ファイルをユーザーの拡張フォルダへコピーして設定スキーマをコンパイルします。既存設定は維持します。以前のインストール先がシンボリックリンクなら、リンク自体を退避してからコピーに切り替えます。ソースのユーザー名を書き換える必要はありません。

Waylandでは通常のログアウト・再ログイン後に有効化してください。X11では`Alt+F2`→`r`によるShell再読み込みも使えます。インストーラー自身は再起動や自動有効化を行いません。

```bash
gnome-extensions enable screenshot-translator@amariichi
gnome-extensions info screenshot-translator@amariichi
```

統合拡張が有効なら、先に`gnome-extensions disable screenshot-translator-learning@amariichi`で無効化してください。

## 操作

トップバーのアイコンからモードを選びます。

| メニュー | 動作 |
|---|---|
| `Text Overlay Mode` | 選択範囲を一度翻訳して表示 |
| `TTS Once Mode` | 選択範囲を一度OCR・翻訳して読み上げ |
| `TTS Monitor Mode` | 約5秒ごとに範囲を確認し、新しい内容を読み上げ |

`Ctrl+Alt+S`で画面をドラッグ選択します。選択は`Esc`／右クリックで取り消せます。Monitorは「Stop Monitoring」で止められ、ショートカットから別の範囲を選び直すこともできます。読み上げの声・速度・言語設定は[設定ガイド](configuration.md#読み上げの設定)を参照してください。

## 困ったとき

- **OUT OF DATE／更新が反映されない**: 対応版を再インストールし、通常のログアウト・再ログインを行います。ファイル更新やenableだけで、実行中のShellの古いコードが置き換わるとは限りません。
- **翻訳・TTSが動かない**: `http://127.0.0.1:8012/health`と起動ターミナルを確認します。拡張ログは`journalctl -b /usr/bin/gnome-shell --no-pager`で確認できます。ログには処理内容が含まれ得るため、共有前に確認してください。
- **新しい拡張が見つからない**: ファイルを配置したまま再ログインしてから有効化してください。

開発者コンソールの操作は[GNOME公式ガイド](https://gjs.guide/extensions/development/debugging.html#looking-glass)を参照してください。通常の導入・更新にEval操作は不要です。

## 取り除く

```bash
gnome-extensions uninstall screenshot-translator@amariichi
```

その後、通常のログアウト・再ログインを行います。テスト方法は[開発ガイド](development.md#gnomeとgtkの隔離テスト)にあります。
