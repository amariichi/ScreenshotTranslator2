# Unified Screenshot Translator indicator

Version 2 hosts both the legacy Overlay/TTS menu and GTK learning capture behind
one indicator. Enable this UUID only; keep the old translator UUID disabled while
retaining its files and settings. The canonical legacy implementation is shared
from `gnome-extension/legacy-capture.js` and bundled into this extension. UUID:
`screenshot-translator-learning@amariichi`; GTK app ID:
`io.github.amariichi.ScreenshotTranslatorLearning`.

Run `./install.sh` to copy and compile the extension. Any existing target is moved
to a timestamped backup outside GNOME's extensions directory. Installation never
enables the extension or restarts GNOME. Enable explicitly with:

```sh
gnome-extensions enable screenshot-translator-learning@amariichi
```

The installer bundles both settings schemas and records an explicit panel-launch
argv. It never starts capture or the panel. The unified menu offers
`従来モード`, `学習モード` (including `学習パネルを開く`) and `すべての取得を停止`.
Switching acquisition paths stops/cancels the previous one; the legacy settings
and shortcut remain unchanged. If the old UUID is active, unified enable waits
for it to stop before creating its one indicator.

Launch the panel with the repository's `start-learning.sh`. The learning indicator
can select a capture rectangle and resume/pause. Capture and panel rectangles are
independent settings. A panel move/resize is saved; subsequent panels are restored
within the available monitor work area. Capture is paused if the panel is closed,
minimized, on another workspace, or overlaps the capture area. Own transient GTK
dialogs are included in the overlap check. The frame is checked again before upload.

Only loopback HTTP(S) base URLs are accepted. Redirects are never followed.
Screenshots remain in memory; this extension stores no image files. It sends at
most one frame request at a time, skips an identical image only after a successful
`ok`/`duplicate` acknowledgement, and retries failed/busy/stale images. Three
consecutive failures pause capture. A separate heartbeat renews the backend lease.
Capture runs every 500ms independently of inference; only the latest pending PNG
is retained and sent when the current request finishes. Stop, range/session/base
changes and disable discard pending captures and invalidate late callbacks.
Local generations invalidate cancelled/old callbacks; ordered control requests
protect rapid stop/resume. Disable cancels requests/timers and sends a short
best-effort pause; backend lease expiry covers a lost process.

The panel uses this settings contract:
`control-request` is JSON `{id,action,session_id}` (`select`, `resume`, `pause`).
`bridge-state` is JSON `{version:1,heartbeat_ms,capturing,message,overlap,rect,
panel_rect,session_id}`. The panel must treat stale bridge heartbeats as disconnected.

## Isolated runtime verification

```sh
mkdir -p /tmp/learning-extension-check
./gnome-learning-extension/pack.sh /tmp/learning-extension-check
dbus-run-session gnome-shell-test-tool --headless \
  --extra-filter io.github.amariichi.ScreenshotTranslatorLearning \
  --extension /tmp/learning-extension-check/screenshot-translator-learning@amariichi.shell-extension.zip \
  "$PWD/tests/learning-extension-smoke.js"
```

The test starts a synthetic GTK fixture explicitly pinned to the test Wayland
socket and talks to a local fixture, not an LLM. `--disable-animations` is omitted:
on GNOME 50 it can race the automation startup-complete listener. Test-tool's XDG
settings/data are temporary and isolated from the user's settings. Runtime
validation was performed on GNOME 50.1; other declared GNOME versions and mixed-DPI
multi-monitor arrangements still require testing.

The v1 module already imported by a live GNOME 50 session cannot be replaced by
disable/enable; its public `ReloadExtension` is deprecated and always fails.
Without an authorized new login, keep the old UUID disabled and use the current
learning panel. New unified menus are verified in an isolated compositor and
load on a later normal login. The installer never restarts GNOME or enables Eval.
