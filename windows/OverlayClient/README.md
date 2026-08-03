# WPF Overlay Skeleton (net8.0-windows)

このフォルダは「オーバーレイUI + トレイ常駐 + ROIキャプチャ + WSL API呼び出し」の実装です。
- 返却されたbboxに合わせて翻訳をオーバーレイ表示します（Copy/Close/Esc対応）。
- 部分クリック透過は WM_NCHITTEST で実装（操作領域のみヒット）。

## 前提
- Windows 10/11 + .NET 8 SDK
- FastAPI が起動済み（既定: http://127.0.0.1:8012）
  - **Windows 単体（WSL2 不要）**: リポジトリルートで `.\start.ps1` → [../../docs/windows.md](../../docs/windows.md)
  - **WSL2 構成**: WSL 側で `./start.sh`
  - どちらでも接続先は `http://127.0.0.1:8012` で同じです（`settings.json` の変更不要）

## ビルド
```powershell
dotnet build
```

## 実行
```powershell
dotnet run
```
 
## 配布用にまとめる
```powershell
dotnet publish -c Release -o output
```
`output/` に実行ファイル一式が出力されます（`settings.json` も同梱）。

トレイメニューから「Show Test Overlay」で表示確認できます。
既定は Ctrl+Alt を押した位置と離した位置で矩形ROIを作り → 翻訳実行です（ドラッグ不要）。
終了はトレイメニューの「Quit」を使います（オーバーレイの「×」は表示だけ閉じます）。
Ctrlのみ/Altのみは `settings.json` の `gesture.modifier` を変更すれば使えます。

## settings.json
exeと同じディレクトリに settings.json を置くと読み込みます（パース失敗時はデフォルト）。
このフォルダの settings.json がビルド出力にコピーされます。
接続先は `server.base_url` を変更してください。
入力キーは `gesture.modifier` で切り替えできます（`ctrl_alt` / `ctrl` / `alt`）。
小さい領域をキャプチャしたい場合は `roi.min_width` / `roi.min_height` を調整してください（既定: 64）。

## ROIプレビュー（赤枠）
- 既定で ROI を赤枠で一瞬表示します（`overlay.preview.show_roi_preview`）。
- `duration_ms` で表示時間を調整できます。
- Ctrl押下中のリアルタイム枠表示は `overlay.preview.live_preview` で切替できます。

## デバッグ方法（認識確認）
- `settings.json` の `logging.level` を `debug` にすると、`overlay_debug.log` に認識ログ（ROI座標/失敗）が出力されます。
