
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';
import Soup from 'gi://Soup?version=3.0';
import Pango from 'gi://Pango';

import { Extension, gettext as _ } from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const SelectionArea = GObject.registerClass({
    Signals: { 'area-selected': { param_types: [GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_INT] } },
}, class SelectionArea extends St.Widget {
    _init() {
        super._init({
            name: 'selection-area',
            reactive: true,
            visible: false,
            style: 'background-color: rgba(0,0,0,0.1);',
            x: 0,
            y: 0,
            width: global.screen_width,
            height: global.screen_height,
        });

        this._startX = 0;
        this._startY = 0;
        this._isSelecting = false;
        this._lasso = new St.Widget({ style_class: 'selection-laso', visible: false });
        this.add_child(this._lasso);

        this.connect('button-press-event', this._onButtonPress.bind(this));
        this.connect('motion-event', this._onMotion.bind(this));
        this.connect('button-release-event', this._onButtonRelease.bind(this));
    }

    _onButtonPress(actor, event) {
        let [x, y] = event.get_coords();
        this._startX = x;
        this._startY = y;
        this._isSelecting = true;
        this._lasso.set_position(x, y);
        this._lasso.set_size(0, 0);
        this._lasso.show();
        return Clutter.EVENT_STOP;
    }

    _onMotion(actor, event) {
        if (!this._isSelecting) return Clutter.EVENT_PROPAGATE;
        let [x, y] = event.get_coords();
        let w = x - this._startX;
        let h = y - this._startY;

        let lx = this._startX;
        let ly = this._startY;
        if (w < 0) { lx = x; w = -w; }
        if (h < 0) { ly = y; h = -h; }

        this._lasso.set_position(lx, ly);
        this._lasso.set_size(w, h);
        return Clutter.EVENT_STOP;
    }

    _onButtonRelease(actor, event) {
        if (!this._isSelecting) return Clutter.EVENT_PROPAGATE;
        this._isSelecting = false;
        this._lasso.hide();
        this.hide();

        let [x, y] = event.get_coords();
        let w = Math.abs(x - this._startX);
        let h = Math.abs(y - this._startY);
        let lx = Math.min(x, this._startX);
        let ly = Math.min(y, this._startY);

        if (w > 10 && h > 10) {
            this.emit('area-selected', lx, ly, w, h);
        }
        return Clutter.EVENT_STOP;
    }
});

export default class ScreenshotTranslatorExtension extends Extension {
    enable() {
        this._settings = this.getSettings();

        // --- State ---
        this._mode = 'overlay'; // 'overlay' or 'monitor' or 'tts-once'
        this._isMonitoring = false;
        this._monitorTimeoutId = null;
        this._httpSession = new Soup.Session();

        // --- UI Setup ---
        this._createIndicator();
        this._selectionArea = new SelectionArea();
        Main.layoutManager.addChrome(this._selectionArea);

        this._selectionArea.connect('area-selected', (obj, x, y, w, h) => {
            if (this._mode === 'monitor') {
                this._startMonitoring(x, y, w, h);
            } else if (this._mode === 'tts-once') {
                this._takeTtsOnceScreenshot(x, y, w, h);
            } else {
                this._takeScreenshot(x, y, w, h);
            }
        });

        // --- Keybinding ---
        Main.wm.addKeybinding(
            'start-capture',
            this._settings,
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.NORMAL,
            () => {
                this._startSelection();
            }
        );

        console.log("ScreenshotTranslator: ENABLED (Merged Mode)");
    }

    disable() {
        this._stopMonitoring();

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
        if (this._selectionArea) {
            this._selectionArea.destroy();
            this._selectionArea = null;
        }
        if (this._resultBox) {
            this._resultBox.destroy();
            this._resultBox = null;
        }
        if (this._session) {
            this._session = null;
        }

        Main.wm.removeKeybinding('start-capture');
        this._settings = null;
    }

    _createIndicator() {
        this._indicator = new PanelMenu.Button(0.0, "Screenshot Translator", false);

        // Icon
        this._icon = new St.Icon({
            icon_name: 'accessories-dictionary-symbolic',
            style_class: 'system-status-icon',
        });
        this._indicator.add_child(this._icon);

        // Menu: Overlay Mode
        this._overlayItem = new PopupMenu.PopupMenuItem("Text Overlay Mode");
        this._overlayItem.connect('activate', () => { this._setMode('overlay'); });
        this._indicator.menu.addMenuItem(this._overlayItem);

        // Menu: One-shot TTS Mode
        this._ttsOnceItem = new PopupMenu.PopupMenuItem("TTS Once Mode");
        this._ttsOnceItem.connect('activate', () => { this._setMode('tts-once'); });
        this._indicator.menu.addMenuItem(this._ttsOnceItem);

        // Menu: Monitor Mode
        this._monitorItem = new PopupMenu.PopupMenuItem("TTS Monitor Mode");
        this._monitorItem.connect('activate', () => { this._setMode('monitor'); });
        this._indicator.menu.addMenuItem(this._monitorItem);

        // Separator
        this._indicator.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Status / Stop Item
        this._stopItem = new PopupMenu.PopupMenuItem("Stop Monitoring");
        this._stopItem.connect('activate', () => { this._stopMonitoring(); });
        this._stopItem.actor.visible = false; // Hidden by default
        this._indicator.menu.addMenuItem(this._stopItem);

        // Init Mode UI relies on _mode existing, so we call it now
        this._setMode(this._mode);

        Main.panel.addToStatusArea('screenshot-translator-indicator', this._indicator);
    }

    _setMode(mode) {
        this._mode = mode;
        this._overlayItem.setOrnament(mode === 'overlay' ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        this._ttsOnceItem.setOrnament(mode === 'tts-once' ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        this._monitorItem.setOrnament(mode === 'monitor' ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);

        // If switching mode, better stop any active monitoring to avoid confusion
        if (this._isMonitoring) {
            this._stopMonitoring();
        }
    }

    _updateIndicatorStatus() {
        if (this._isMonitoring) {
            this._icon.style_class = 'system-status-icon'; // Reset first
            this._icon.style = 'color: #ff4444;'; // Red tint for active recording/monitoring
            this._stopItem.actor.visible = true;
        } else {
            this._icon.style = null; // Default
            this._stopItem.actor.visible = false;
        }
    }

    _startSelection() {
        if (this._selectionArea) {
            this._selectionArea.show();
            global.stage.set_key_focus(this._selectionArea);
        }
    }

    _stopMonitoring() {
        if (this._monitorTimeoutId) {
            GLib.source_remove(this._monitorTimeoutId);
            this._monitorTimeoutId = null;
        }
        this._isMonitoring = false;
        this._updateIndicatorStatus();
        console.log("Monitor stopped.");
    }

    // --- Monitor Mode Logic ---

    _startMonitoring(x, y, w, h) {
        this._stopMonitoring(); // Clear any existing

        this._isMonitoring = true;
        this._updateIndicatorStatus();

        console.log("Monitor started.");

        // Initial capture (reset session = true)
        this._isProcessing = false;
        this._takeMonitorScreenshot(x, y, w, h, true);

        // Schedule periodic capture. 5 seconds balances responsiveness against
        // the cost of an OCR + translation round trip; synthesis is not the
        // limiting factor (both TTS engines run well faster than real time).
        this._monitorTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
            if (!this._isMonitoring) return GLib.SOURCE_REMOVE;

            if (this._isProcessing) {
                console.log("Skipping capture: Previous check still pending.");
                return GLib.SOURCE_CONTINUE;
            }
            this._takeMonitorScreenshot(x, y, w, h, false);
            return GLib.SOURCE_CONTINUE;
        });
    }

    _takeMonitorScreenshot(x, y, w, h, isFirst) {
        this._isProcessing = true;
        const cleanPath = GLib.build_filenamev([GLib.get_tmp_dir(), 'clean_monitor.png']);
        const file = Gio.File.new_for_path(cleanPath);

        // Async file creation
        file.replace_async(null, false, Gio.FileCreateFlags.NONE, GLib.PRIORITY_DEFAULT, null, (obj, res) => {
            try {
                const stream = obj.replace_finish(res);
                const screenshot = new Shell.Screenshot();

                screenshot.screenshot_area(x, y, w, h, stream, (screenshot, success) => {
                    stream.close(null);
                    if (success) {
                        this._uploadMonitorImages(cleanPath, x, y, w, h, isFirst);
                    } else {
                        this._isProcessing = false;
                    }
                });
            } catch (e) {
                console.error('Monitor screenshot failed:', e);
                this._isProcessing = false;
            }
        });
    }

    async _uploadMonitorImages(cleanPath, x, y, w, h, isFirst) {
        // Use monitor_update endpoint
        const url = 'http://127.0.0.1:8012/api/v1/monitor_update';
        const file = Gio.File.new_for_path(cleanPath);

        try {
            // Async file read
            const cleanBytes = await new Promise((resolve, reject) => {
                file.load_contents_async(null, (obj, res) => {
                    try {
                        const [success, contents] = obj.load_contents_finish(res);
                        if (success) resolve(contents);
                        else reject(new Error("Failed to load contents"));
                    } catch (e) { reject(e); }
                });
            });

            const boundary = "------------------------" + Date.now().toString(16);
            const encoder = new TextEncoder();
            const parts = [];

            const addPart = (name, filename, contentType, data) => {
                parts.push(encoder.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"` + (filename ? `; filename="${filename}"` : '') + `\r\n` + (contentType ? `Content-Type: ${contentType}\r\n` : '') + `\r\n`));
                parts.push(data instanceof Uint8Array ? data : encoder.encode(data));
                parts.push(encoder.encode("\r\n"));
            };

            addPart('clean_image', 'clean.png', 'image/png', cleanBytes);

            addPart('reset_session', null, null, isFirst ? 'true' : 'false');
            addPart('options', null, null, JSON.stringify({ timeout_sec: 60, translation_only: true, return_roi_fallback: false }));

            parts.push(encoder.encode(`--${boundary}--\r\n`));
            const glibBytes = new GLib.Bytes(this._concatBuffers(parts));
            const msg = Soup.Message.new('POST', url);
            msg.set_request_body_from_bytes(`multipart/form-data; boundary=${boundary}`, glibBytes);

            await this._httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null);
            // We don't check result content for UI, just log success

        } catch (e) {
            console.error('Monitor upload failed:', e);
            // If connection fails, stop monitoring to avoid zombie state
            if (e.message && (e.message.indexOf('Connection refused') !== -1 || e.message.indexOf('Network is unreachable') !== -1)) {
                console.log("Connection lost. Auto-stopping monitor.");
                this._stopMonitoring();
                Main.notify("Monitor Stopped", "Connection to backend lost.");
            }
        } finally {
            this._isProcessing = false;
        }
    }

    // --- Overlay Mode Logic (Legacy/Standard) ---

    async _takeScreenshot(x, y, w, h) {
        const cleanPath = GLib.build_filenamev([GLib.get_tmp_dir(), 'clean.png']);

        // Show scanning feedback if possible? (Optional)

        try {
            const file = Gio.File.new_for_path(cleanPath);
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            const screenshot = new Shell.Screenshot();

            screenshot.screenshot_area(x, y, w, h, stream, (screenshot, success) => {
                stream.close(null);
                if (success) {
                    this._processAndSend(cleanPath, x, y, w, h);
                } else {
                    Main.notify('Screenshot failed');
                }
            });
        } catch (e) {
            Main.notify('Screenshot error', e.message);
        }
    }

    async _processAndSend(cleanPath, x, y, w, h) {
        try {
            const [success, cleanBytes] = GLib.file_get_contents(cleanPath);
            if (!success) {
                Main.notify('Error', 'Failed to read screenshot file');
                return;
            }
            this._uploadImages(cleanBytes, cleanBytes, x, y, w, h);

        } catch (e) {
            Main.notify('Processing Error', e.message);
        }
    }

    // --- TTS Once Mode ---

    async _takeTtsOnceScreenshot(x, y, w, h) {
        const cleanPath = GLib.build_filenamev([GLib.get_tmp_dir(), 'clean_tts_once.png']);
        try {
            const file = Gio.File.new_for_path(cleanPath);
            const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            const screenshot = new Shell.Screenshot();

            screenshot.screenshot_area(x, y, w, h, stream, (screenshot, success) => {
                stream.close(null);
                if (success) {
                    this._processAndSendTtsOnce(cleanPath, x, y, w, h);
                } else {
                    Main.notify('Screenshot failed');
                }
            });
        } catch (e) {
            Main.notify('Screenshot error', e.message);
        }
    }

    async _processAndSendTtsOnce(cleanPath, x, y, w, h) {
        try {
            const [success, cleanBytes] = GLib.file_get_contents(cleanPath);
            if (!success) {
                Main.notify('Error', 'Failed to read screenshot file');
                return;
            }
            this._uploadTtsOnce(cleanBytes, cleanBytes, x, y, w, h);
        } catch (e) {
            Main.notify('Processing Error', e.message);
        }
    }

    async _uploadTtsOnce(cleanBytes, guideBytes, x, y, w, h) {
        const url = 'http://127.0.0.1:8012/api/v1/ocr_translate_tts_once';
        try {
            const boundary = "------------------------" + Date.now().toString(16);
            const encoder = new TextEncoder();
            const parts = [];

            const addPart = (name, filename, contentType, data) => {
                parts.push(encoder.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"` + (filename ? `; filename="${filename}"` : '') + `\r\n` + (contentType ? `Content-Type: ${contentType}\r\n` : '') + `\r\n`));
                parts.push(data instanceof Uint8Array ? data : encoder.encode(data));
                parts.push(encoder.encode("\r\n"));
            };

            addPart('clean_image', 'clean.png', 'image/png', cleanBytes);
            addPart('options', null, null, JSON.stringify({ translation_only: true, return_roi_fallback: false }));

            parts.push(encoder.encode(`--${boundary}--\r\n`));

            const glibBytes = new GLib.Bytes(this._concatBuffers(parts));
            const msg = Soup.Message.new('POST', url);
            msg.set_request_body_from_bytes(`multipart/form-data; boundary=${boundary}`, glibBytes);

            const bytes = await this._httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null);
            const status = msg.get_status();
            if (status !== 200) {
                throw new Error(`Server returned ${status} ${msg.get_reason_phrase()}`);
            }
        } catch (e) {
            Main.notify('TTS Error', e.message);
        }
    }

    async _uploadImages(cleanBytes, guideBytes, x, y, w, h) {
        const url = 'http://127.0.0.1:8012/api/v1/ocr_translate_with_grounding';
        // ... Similar upload logic but for translator ...
        try {
            const boundary = "------------------------" + Date.now().toString(16);
            const encoder = new TextEncoder();
            const parts = [];

            const addPart = (name, filename, contentType, data) => {
                parts.push(encoder.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"` + (filename ? `; filename="${filename}"` : '') + `\r\n` + (contentType ? `Content-Type: ${contentType}\r\n` : '') + `\r\n`));
                parts.push(data instanceof Uint8Array ? data : encoder.encode(data));
                parts.push(encoder.encode("\r\n"));
            };

            addPart('clean_image', 'clean.png', 'image/png', cleanBytes);
            addPart('options', null, null, JSON.stringify({ translation_only: true, return_roi_fallback: false }));

            parts.push(encoder.encode(`--${boundary}--\r\n`));

            const glibBytes = new GLib.Bytes(this._concatBuffers(parts));
            const msg = Soup.Message.new('POST', url);
            msg.set_request_body_from_bytes(`multipart/form-data; boundary=${boundary}`, glibBytes);

            const bytes = await this._httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null);
            const status = msg.get_status();

            if (status !== 200) {
                throw new Error(`Server returned ${status} ${msg.get_reason_phrase()}`);
            }

            const responseBody = new TextDecoder().decode(bytes.get_data());
            const json = JSON.parse(responseBody);
            this._showResult(json, x, y, w, h);

        } catch (e) {
            Main.notify('Translation Error', e.message);
        }
    }

    // --- Helpers ---

    _concatBuffers(buffers) {
        let totalLength = 0;
        for (let b of buffers) totalLength += b.length;
        let result = new Uint8Array(totalLength);
        let offset = 0;
        for (let b of buffers) {
            result.set(b, offset);
            offset += b.length;
        }
        return result;
    }

    _showResult(json, x, y, w, h) {
        if (this._resultBox) {
            this._resultBox.destroy();
            this._resultBox = null;
        }

        const text = json.ja_translation || json.ocr_text || "No result";

        this._resultBox = new St.BoxLayout({
            style_class: 'result-container',
            vertical: true,
            x: x,
            y: y + 20
        });

        if (x + 300 > global.screen_width) this._resultBox.set_position(global.screen_width - 320, y);
        const maxHeight = 600;
        if (y + maxHeight > global.screen_height) this._resultBox.set_position(x, global.screen_height - (maxHeight + 20));

        const scrollView = new St.ScrollView({
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            style_class: 'result-scrollview'
        });

        // Match selection size (clamped)
        let boxW = Math.max(200, Math.min(960, w));
        let boxH = Math.max(100, Math.min(600, h - 40));

        scrollView.set_width(boxW);
        scrollView.set_height(boxH);

        const label = new St.Label({
            text: text,
            style_class: 'result-text'
        });

        label.clutter_text.line_wrap = true;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        label.clutter_text.set_width(boxW);

        const scrollContent = new St.BoxLayout({ vertical: true });
        scrollContent.add_child(label);
        scrollView.set_child(scrollContent);

        const closeBtn = new St.Button({
            child: new St.Label({ text: "X" }),
            style_class: 'close-button'
        });
        closeBtn.connect('clicked', () => {
            this._resultBox.destroy();
            this._resultBox = null;
        });

        const header = new St.BoxLayout();
        header.add_child(new St.Label({ text: "Translation", style_class: 'result-title' }));
        header.add_child(closeBtn);

        this._resultBox.add_child(header);
        this._resultBox.add_child(scrollView);

        Main.layoutManager.addChrome(this._resultBox);
    }
}
