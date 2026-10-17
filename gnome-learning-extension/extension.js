import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import Soup from 'gi://Soup?version=3.0';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import LegacyCaptureExtension from './legacy-capture.js';

const APP_ID = 'io.github.amariichi.ScreenshotTranslatorLearning';
const FRAME_INTERVAL_MS = 1000;
const HEARTBEAT_INTERVAL_MS = 2000;
const MAX_ERRORS = 3;
const LEGACY_UUID = 'screenshot-translator@amariichi';

export function validRect(rect) {
    return Array.isArray(rect) && rect.length === 4 && rect.every(Number.isInteger) &&
        rect[2] > 10 && rect[3] > 10;
}

export function overlaps(a, b) {
    return validRect(a) && validRect(b) && a[0] < b[0] + b[2] && b[0] < a[0] + a[2] &&
        a[1] < b[1] + b[3] && b[1] < a[1] + a[3];
}

function windowRect(window) {
    const r = window.get_frame_rect();
    return [r.x, r.y, r.width, r.height];
}

function loopbackBase(value) {
    const uri = GLib.Uri.parse(value, GLib.UriFlags.NONE);
    const host = uri.get_host()?.toLowerCase();
    if (!['http', 'https'].includes(uri.get_scheme()) ||
        !['127.0.0.1', 'localhost', '::1'].includes(host) ||
        uri.get_userinfo() || uri.get_query() || uri.get_fragment() ||
        !['', '/'].includes(uri.get_path()))
        throw new Error('接続先は localhost / 127.0.0.1 / ::1 のベース URL にしてください');
    return value.replace(/\/$/, '');
}

const SelectionArea = GObject.registerClass({
    Signals: {
        'area-selected': {param_types: [GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_INT, GObject.TYPE_INT]},
        'cancelled': {},
    },
}, class SelectionArea extends St.Widget {
    _init() {
        super._init({reactive: true, visible: false, x: 0, y: 0,
            style: 'background-color: rgba(0,0,0,0.15);'});
        this._lasso = new St.Widget({style_class: 'learning-selection-outline', visible: false});
        this.add_child(this._lasso);
        this.connect('button-press-event', (_actor, event) => {
            if (event.get_button() === Clutter.BUTTON_SECONDARY) {
                this.cancel();
                this.emit('cancelled');
            } else if (event.get_button() === Clutter.BUTTON_PRIMARY) {
                this._start = event.get_coords();
                this._lasso.set_position(...this._start);
                this._lasso.set_size(1, 1);
                this._lasso.show();
            }
            return Clutter.EVENT_STOP;
        });
        this.connect('motion-event', (_actor, event) => {
            if (this._start) {
                const [x, y] = event.get_coords();
                this._lasso.set_position(Math.min(x, this._start[0]), Math.min(y, this._start[1]));
                this._lasso.set_size(Math.abs(x - this._start[0]), Math.abs(y - this._start[1]));
            }
            return Clutter.EVENT_STOP;
        });
        this.connect('button-release-event', (_actor, event) => {
            if (this._start && event.get_button() === Clutter.BUTTON_PRIMARY) {
                const [x, y] = event.get_coords();
                const rect = [Math.round(Math.min(x, this._start[0])), Math.round(Math.min(y, this._start[1])),
                    Math.round(Math.abs(x - this._start[0])), Math.round(Math.abs(y - this._start[1]))];
                this.cancel();
                if (validRect(rect))
                    this.emit('area-selected', ...rect);
                else
                    this.emit('cancelled');
            }
            return Clutter.EVENT_STOP;
        });
        this.connect('key-press-event', (_actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this.cancel();
                this.emit('cancelled');
            }
            return Clutter.EVENT_STOP;
        });
    }

    open() {
        this.cancel();
        this.set_size(global.stage.width, global.stage.height);
        this.show();
        this._grab = Main.pushModal(this, {actionMode: Shell.ActionMode.POPUP});
        if (this._grab.is_revoked()) {
            this.cancel();
            return false;
        }
        return true;
    }

    cancel() {
        this._start = null;
        this._lasso.hide();
        if (this._grab) {
            Main.popModal(this._grab);
            this._grab = null;
        }
        this.hide();
    }
});

export default class LearningCaptureExtension extends Extension {
    enable() {
        const old = Main.extensionManager.lookup(LEGACY_UUID);
        if (old?.stateObj?._enabled || global.settings.get_strv('enabled-extensions').includes(LEGACY_UUID))
            return this._migrateLegacy(old);
        this._enableUnified();
    }

    async _migrateLegacy(old) {
        this._migrationPending = true;
        Main.extensionManager.disableExtension(LEGACY_UUID);
        for (let count = 0; count < 100 && (old?.enabled || old?.stateObj?._enabled) && this._migrationPending; count++)
            await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30, () => {
                resolve(); return GLib.SOURCE_REMOVE;
            }));
        if (!this._migrationPending) return;
        this._migrationPending = false;
        if (old?.enabled || old?.stateObj?._enabled)
            throw new Error('従来拡張の停止を確認できません。二重取得を防ぐため開始しません。');
        this._enableUnified();
    }

    _enableUnified() {
        this._settings = this.getSettings();
        this._enabled = true;
        this._epoch = 0;
        this._controllerId = GLib.uuid_string_random();
        this._controlSeq = 0;
        this._sequence = 0;
        this._generation = null;
        this._capturing = false;
        this._desiredActive = false;
        this._requests = new Set();
        this._signals = [];
        this._windowSignals = [];
        this._seenCommands = new Set();
        this._ownWindows = [];
        this._activeMode = 'learning';
        this._legacy = null;
        this._sessionId = this._settings.get_string('session-id');
        this._http = new Soup.Session({timeout: 45, idle_timeout: 5});
        this._message = '学習パネルを開き、取得範囲を選択してください';
        this._readBase();
        this._createIndicator();
        this._selectionArea = new SelectionArea();
        Main.layoutManager.addChrome(this._selectionArea);
        this._selectionArea.connect('area-selected', (_area, ...rect) => {
            this._settings.set_value('capture-rect', new GLib.Variant('ai', rect));
            void this._resume();
        });
        this._selectionArea.connect('cancelled', () => this._setMessage('範囲選択をキャンセルしました'));
        this._selectionArea.connect('destroy', () => { this._selectionArea = null; });
        this._connect(this._settings, 'changed::control-request', () => this._onCommand());
        this._connect(this._settings, 'changed::session-id', () => {
            void this._pause('セッションを切り替えました');
            this._sessionId = this._settings.get_string('session-id');
            this._publish();
        });
        this._connect(this._settings, 'changed::backend-url', () => {
            void this._pause('接続先を変更したため停止しました');
            this._readBase();
        });
        this._connect(this._settings, 'changed::capture-rect', () => {
            if (this._capturing || this._desiredActive)
                void this._pause('取得範囲を変更しました。再開してください');
            this._publish();
        });
        this._connect(Main.layoutManager, 'monitors-changed', () => {
            void this._pause('画面構成を変更したため、範囲を再確認してください');
            if (this._panelWindow)
                this._restorePanel(this._panelWindow);
        });
        this._connect(global.display, 'window-created', () => this._scanWindows());
        this._scanWindows();
        this._captureTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FRAME_INTERVAL_MS, () => {
            void this._captureTick();
            return GLib.SOURCE_CONTINUE;
        });
        this._stateTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
            this._scanWindows();
            this._checkSafety();
            if (Date.now() - (this._lastHeartbeat || 0) >= HEARTBEAT_INTERVAL_MS)
                void this._heartbeat();
            this._publish();
            return GLib.SOURCE_CONTINUE;
        });
        // Old control-request values are intentionally never replayed at enable.
        this._publish();
    }

    _connect(object, signal, handler) {
        this._signals.push([object, object.connect(signal, handler)]);
    }

    _readBase() {
        try {
            this._base = loopbackBase(this._settings.get_string('backend-url'));
        } catch (error) {
            this._base = null;
            this._message = error.message;
            console.error('Learning bridge URL:', error);
        }
    }

    _createIndicator() {
        this._indicator = new PanelMenu.Button(0, 'Screenshot Translator');
        this._indicator.connect('destroy', () => {
            this._statusItem = null;
            this._indicator = null;
        });
        this._indicator.add_child(new St.Icon({icon_name: 'accessories-dictionary-symbolic',
            style_class: 'system-status-icon'}));
        this._legacyItem = new PopupMenu.PopupSubMenuMenuItem('従来モード');
        this._indicator.menu.addMenuItem(this._legacyItem);
        this._legacy = new LegacyCaptureExtension({...this.metadata, uuid: LEGACY_UUID,
            'settings-schema': 'org.gnome.shell.extensions.screenshot-translator'});
        this._legacy.enable({menu: this._legacyItem.menu,
            onMode: () => { this._activeMode = 'legacy'; void this._pause('従来モードを選択しました'); },
            beforeCapture: async () => {
                this._activeMode = 'legacy';
                await this._pause('従来モードで取得します');
            }});
        this._learningItem = new PopupMenu.PopupSubMenuMenuItem('学習モード');
        this._indicator.menu.addMenuItem(this._learningItem);
        const openPanel = new PopupMenu.PopupMenuItem('学習パネルを開く');
        openPanel.connect('activate', () => this._openPanel());
        this._learningItem.menu.addMenuItem(openPanel);
        for (const [text, action] of [['取得範囲を選択…', () => this._startSelection()],
            ['取得を再開', () => this._resume()], ['取得を停止', () => this._stopAll()]]) {
            const item = new PopupMenu.PopupMenuItem(text);
            item.connect('activate', () => { void action(); });
            this._learningItem.menu.addMenuItem(item);
        }
        const stopAll = new PopupMenu.PopupMenuItem('すべての取得を停止');
        stopAll.connect('activate', () => { void this._stopAll(); });
        this._indicator.menu.addMenuItem(stopAll);
        this._statusItem = new PopupMenu.PopupMenuItem('学習パネルは start-learning.sh で開きます', {reactive: false});
        this._statusItem.label.connect('destroy', () => { this._statusItem = null; });
        this._indicator.menu.addMenuItem(this._statusItem);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    _openPanel() {
        const command = this._settings.get_strv('panel-command');
        if (!command.length) {
            Main.notify('Screenshot Translator', 'start-learning.sh でパネルを開いてください');
            return;
        }
        try { Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE); }
        catch (error) { Main.notify('Screenshot Translator', '学習パネルを開けませんでした'); console.error(error); }
    }

    _activateLearning() {
        this._activeMode = 'learning';
        this._legacy?.suspend();
    }

    async _stopAll() {
        this._legacy?.suspend();
        await this._pause('すべての取得を停止しました');
    }

    _onCommand() {
        try {
            const command = JSON.parse(this._settings.get_string('control-request'));
            if (typeof command.id !== 'string' || !command.id || command.id.length > 128 ||
                this._seenCommands.has(command.id))
                return;
            this._seenCommands.add(command.id);
            if (this._seenCommands.size > 64)
                this._seenCommands.delete(this._seenCommands.values().next().value);
            if (command.session_id !== this._sessionId) {
                this._setMessage('古いセッションの操作を無視しました');
                return;
            }
            if (command.action === 'select')
                void this._startSelection();
            else if (command.action === 'resume')
                void this._resume();
            else if (command.action === 'pause')
                void this._stopAll();
        } catch (error) {
            console.error('Learning control request:', error);
            this._setMessage('操作要求を読み取れませんでした');
        }
    }

    async _startSelection() {
        this._activateLearning();
        const pause = this._pause('字幕帯または動画の範囲をドラッグしてください（Esc で中止）');
        const epoch = this._epoch;
        await pause;
        if (!this._enabled || epoch !== this._epoch)
            return;
        this._scanWindows();
        if (!this._panelWindow || !this._sessionId) {
            this._setMessage('先に学習パネルを開き、セッションを選んでください');
            return;
        }
        this._selectionArea?.open();
    }

    _rect() {
        return this._settings.get_value('capture-rect').deep_unpack();
    }

    _onScreen(rect) {
        return validRect(rect) && rect[0] >= 0 && rect[1] >= 0 &&
            rect[0] + rect[2] <= global.stage.width && rect[1] + rect[3] <= global.stage.height;
    }

    _isOwnWindow(window) {
        const seen = new Set();
        while (window && !seen.has(window)) {
            if (window.get_gtk_application_id() === APP_ID || window.get_wm_class() === APP_ID)
                return true;
            seen.add(window);
            window = window.get_transient_for();
        }
        return false;
    }

    _scanWindows() {
        if (!this._enabled)
            return;
        const windows = global.get_window_actors().map(actor => actor.meta_window);
        const roots = windows.filter(window => this._isOwnWindow(window));
        this._ownWindows = roots;
        const panel = roots.find(window => window.get_window_type() === Meta.WindowType.NORMAL &&
            !window.get_transient_for()) || null;
        if (panel === this._panelWindow)
            return;
        this._disconnectWindow();
        this._panelWindow = panel;
        if (!panel) {
            if (this._capturing || this._desiredActive)
                void this._pause('学習パネルが閉じられたため停止しました');
            return;
        }
        const saved = this._settings.get_value('panel-rect').deep_unpack();
        // Initial Wayland configure/present may override a move during window-created.
        // Restore after mapping has settled, and do not overwrite the saved rectangle first.
        if (validRect(saved)) {
            this._restoreTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
                this._restoreTimer = null;
                if (this._enabled && this._panelWindow === panel) {
                    this._restorePanel(panel, saved);
                    this._scheduleGeometrySave(panel);
                }
                return GLib.SOURCE_REMOVE;
            });
        }
        for (const signal of ['position-changed', 'size-changed', 'notify::minimized']) {
            this._windowSignals.push([panel, panel.connect(signal, () => {
                this._checkSafety();
                if (!this._restoreTimer && this._restoringPanel !== panel)
                    this._scheduleGeometrySave(panel);
            })]);
        }
        this._windowSignals.push([panel, panel.connect('unmanaged', () => {
            void this._pause('学習パネルが閉じられたため停止しました');
            this._disconnectWindow();
            this._panelWindow = null;
            this._ownWindows = [];
        })]);
        if (!this._restoreTimer)
            this._scheduleGeometrySave(panel);
        this._publish();
    }

    _scheduleGeometrySave(panel) {
        if (this._restoringPanel === panel)
            return;
        if (this._geometryTimer)
            GLib.source_remove(this._geometryTimer);
        this._geometryTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            this._geometryTimer = null;
            if (this._enabled && this._panelWindow === panel && this._restoringPanel !== panel) {
                this._settings.set_value('panel-rect', new GLib.Variant('ai', windowRect(panel)));
                this._publish();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _restorePanel(window, saved = this._settings.get_value('panel-rect').deep_unpack()) {
        if (!validRect(saved) || Main.layoutManager.monitors.length === 0)
            return;
        let bestMonitor = Main.layoutManager.primaryIndex;
        let bestArea = -1;
        Main.layoutManager.monitors.forEach((monitor, index) => {
            const width = Math.max(0, Math.min(saved[0] + saved[2], monitor.x + monitor.width) - Math.max(saved[0], monitor.x));
            const height = Math.max(0, Math.min(saved[1] + saved[3], monitor.y + monitor.height) - Math.max(saved[1], monitor.y));
            if (width * height > bestArea) {
                bestArea = width * height;
                bestMonitor = index;
            }
        });
        const area = Main.layoutManager.getWorkAreaForMonitor(bestMonitor);
        const width = Math.min(saved[2], area.width);
        const height = Math.min(saved[3], area.height);
        const x = Math.max(area.x, Math.min(saved[0], area.x + area.width - width));
        const y = Math.max(area.y, Math.min(saved[1], area.y + area.height - height));
        // Do not let initial maximization/configure events overwrite the saved
        // normal rectangle. Mutter does not unmaximize in move_resize_frame().
        for (const timer of ['_restoreTimer', '_restoreSettleTimer', '_geometryTimer']) {
            if (this[timer])
                GLib.source_remove(this[timer]);
            this[timer] = null;
        }
        this._restoringPanel = window;
        let ticks = 0;
        let moved = false;
        let previous = null;
        let stableTicks = 0;
        const finish = save => {
            this._restoreSettleTimer = null;
            this._restoringPanel = null;
            if (save)
                this._scheduleGeometrySave(window);
            return GLib.SOURCE_REMOVE;
        };
        try {
            if (window.maximized_horizontally || window.maximized_vertically) {
                // Local GNOME 50 / Meta-18 GI: unmaximize(self), no flags.
                // Older introspected APIs expose a flags argument instead.
                if (window.unmaximize.length === 0)
                    window.unmaximize();
                else
                    window.unmaximize(Meta.MaximizeFlags.BOTH);
            }
        } catch (error) {
            console.error('Learning panel unmaximize:', error);
            this._setMessage('パネルの配置を復元できませんでした。保存済みの位置は保全しています');
            finish(false);
            return;
        }
        this._restoreSettleTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
            if (!this._enabled || this._panelWindow !== window)
                return finish(false);
            if (++ticks > 25) {
                // Bounded wait: preserve the old rectangle rather than persisting
                // a maximized or still-changing initial Wayland configuration.
                this._setMessage('パネル配置の復元待ちを終了しました。保存済みの位置は保全しています');
                return finish(false);
            }
            if (window.maximized_horizontally || window.maximized_vertically)
                return GLib.SOURCE_CONTINUE;
            try {
                if (!moved) {
                    window.move_resize_frame(false, x, y, width, height);
                    moved = true;
                    return GLib.SOURCE_CONTINUE;
                }
                const rect = windowRect(window);
                const placed = Math.abs(rect[0] - x) <= 2 && Math.abs(rect[1] - y) <= 2;
                stableTicks = placed && previous && rect.every((value, index) => value === previous[index])
                    ? stableTicks + 1 : 0;
                previous = rect;
                if (stableTicks >= 3)
                    return finish(true);
            } catch (error) {
                console.error('Learning panel restore:', error);
                this._setMessage('パネルの配置を復元できませんでした。保存済みの位置は保全しています');
                return finish(false);
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _disconnectWindow() {
        for (const timer of ['_restoreTimer', '_restoreSettleTimer', '_geometryTimer']) {
            if (this[timer])
                GLib.source_remove(this[timer]);
            this[timer] = null;
        }
        for (const [object, id] of this._windowSignals || []) {
            try { object.disconnect(id); } catch (error) { console.error('Learning window disconnect:', error); }
        }
        this._windowSignals = [];
        this._restoringPanel = null;
    }

    _visibleWindow(window) {
        return !window.minimized && window.located_on_workspace(global.workspace_manager.get_active_workspace());
    }

    _overlap() {
        const rect = this._rect();
        // Refresh here, not only in the periodic scan: a newly mapped dialog must
        // be excluded before a screenshot upload can leave this process.
        return global.get_window_actors().some(actor => {
            const window = actor.meta_window;
            return this._isOwnWindow(window) && this._visibleWindow(window) && overlaps(rect, windowRect(window));
        });
    }

    _safetyError() {
        if (!this._panelWindow || !this._visibleWindow(this._panelWindow))
            return '学習パネルが同じ画面・ワークスペースに表示されていないため停止しました';
        if (!this._onScreen(this._rect()))
            return '取得範囲を画面内で選択してください';
        if (this._overlap())
            return '翻訳パネルと取得範囲が重なっています。パネルを移動して再開してください';
        return null;
    }

    _checkSafety() {
        const reason = this._safetyError();
        if (reason && (this._capturing || this._desiredActive))
            void this._pause(reason);
        return !reason;
    }

    _invalidate() {
        this._epoch++;
        this._capturing = false;
        this._desiredActive = false;
        this._generation = null;
        this._frameBusy?.cancellable.cancel();
        this._heartbeatBusy?.cancellable.cancel();
        this._frameBusy = null;
        this._heartbeatBusy = null;
        this._lastHash = null;
        this._selectionArea?.cancel();
    }

    _endpoint(base, sessionId, route) {
        if (!base || !sessionId)
            throw new Error('ローカル接続先と学習セッションを確認してください');
        return `${base}/api/learning/sessions/${encodeURIComponent(sessionId)}/${route}`;
    }

    async _resume() {
        if (!this._enabled || this._capturing || this._desiredActive)
            return;
        this._activateLearning();
        this._scanWindows();
        const reason = this._safetyError();
        if (reason || !this._sessionId || !this._base) {
            this._setMessage(reason || 'ローカル接続先と学習セッションを確認してください');
            return;
        }
        this._invalidate();
        const epoch = this._epoch;
        this._desiredActive = true;
        this._setMessage('取得を開始しています…');
        try {
            const response = await this._post('control', {active: true, controller_id: this._controllerId,
                control_seq: ++this._controlSeq});
            if (!this._enabled || epoch !== this._epoch || !this._desiredActive)
                return;
            if (!response.capturing || !Number.isInteger(response.generation))
                throw new Error('取得を開始できません。解説の実行状態を確認してください');
            this._generation = response.generation;
            this._capturing = true;
            this._sequence = 0;
            this._frameErrors = 0;
            this._heartbeatErrors = 0;
            this._lastHeartbeat = 0;
            this._setMessage('取得中 · 解説は停止後に実行できます');
            if (this._checkSafety()) {
                void this._heartbeat();
                void this._captureTick();
            }
        } catch (error) {
            if (this._enabled && epoch === this._epoch) {
                this._invalidate();
                this._setMessage(`開始できません: ${error.message}`);
                console.error('Learning resume:', error);
            }
        }
    }

    async _pause(message) {
        const base = this._base;
        const sessionId = this._sessionId;
        this._invalidate();
        const epoch = this._epoch;
        this._setMessage(message);
        if (!base || !sessionId || !this._enabled)
            return;
        try {
            await this._post('control', {active: false, controller_id: this._controllerId,
                control_seq: ++this._controlSeq}, {base, sessionId});
        } catch (error) {
            if (this._enabled && epoch === this._epoch) {
                console.error('Learning pause (server lease is fallback):', error);
                this._setMessage(`${message}（サーバー停止の確認待ち。リース失効で停止します）`);
            }
        }
    }

    async _heartbeat() {
        if (!this._enabled || !this._capturing || this._heartbeatBusy)
            return;
        const epoch = this._epoch;
        const token = {cancellable: new Gio.Cancellable()};
        this._heartbeatBusy = token;
        this._lastHeartbeat = Date.now();
        try {
            await this._post('heartbeat', {generation: this._generation, controller_id: this._controllerId}, token);
            if (epoch === this._epoch)
                this._heartbeatErrors = 0;
        } catch (error) {
            if (this._enabled && epoch === this._epoch && !token.cancellable.is_cancelled()) {
                console.error('Learning heartbeat:', error);
                if (error.status === 409 || ++this._heartbeatErrors >= MAX_ERRORS)
                    void this._pause('取得の接続確認に失敗したため停止しました。再開してください');
            }
        } finally {
            if (this._heartbeatBusy === token)
                this._heartbeatBusy = null;
        }
    }

    async _captureTick() {
        if (!this._enabled || !this._capturing || this._frameBusy || !this._checkSafety())
            return;
        const token = {cancellable: new Gio.Cancellable()};
        this._frameBusy = token;
        const epoch = this._epoch;
        const generation = this._generation;
        const capturedAt = Date.now() / 1000;
        try {
            const bytes = await this._captureArea(this._rect());
            if (!this._enabled || epoch !== this._epoch || !this._capturing || !this._checkSafety())
                return;
            const hash = GLib.compute_checksum_for_data(GLib.ChecksumType.SHA256, bytes);
            if (hash === this._lastHash)
                return;
            const sequence = ++this._sequence;
            const boundary = `Learning-${GLib.uuid_string_random()}`;
            const encoder = new TextEncoder();
            const parts = [];
            const add = (name, data, file = false) => {
                parts.push(encoder.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"${file ? '; filename="frame.png"\r\nContent-Type: image/png' : ''}\r\n\r\n`));
                parts.push(file ? data : encoder.encode(String(data)));
                parts.push(encoder.encode('\r\n'));
            };
            add('image', bytes, true);
            add('generation', generation);
            add('sequence', sequence);
            add('captured_at', capturedAt);
            parts.push(encoder.encode(`--${boundary}--\r\n`));
            const body = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
            let offset = 0;
            for (const part of parts) {
                body.set(part, offset);
                offset += part.length;
            }
            const response = await this._request(this._endpoint(this._base, this._sessionId, 'frames'),
                `multipart/form-data; boundary=${boundary}`, body, token.cancellable);
            if (this._enabled && epoch === this._epoch && this._capturing) {
                if (response.generation === generation && response.sequence === sequence &&
                    ['busy', 'stale'].includes(response.status)) {
                    this._setMessage(response.status === 'busy' ? '翻訳処理の空きを待っています…' : '次の画面を取得しています…');
                    return;
                }
                if (response.generation !== generation || response.sequence !== sequence ||
                    !['ok', 'duplicate'].includes(response.status))
                    throw new Error('古い、または不正なフレーム応答を受信しました');
                // Failed/stale frames never poison the unchanged-image cache.
                this._lastHash = hash;
                this._frameErrors = 0;
                this._setMessage('取得中 · 解説は停止後に実行できます');
            }
        } catch (error) {
            if (this._enabled && epoch === this._epoch && !token.cancellable.is_cancelled()) {
                console.error('Learning frame:', error);
                if (error.status === 409 || (error.status >= 400 && error.status < 500 && error.status !== 429) ||
                    ++this._frameErrors >= MAX_ERRORS)
                    void this._pause(`取得に失敗したため停止しました: ${error.message}`);
                else
                    this._setMessage(`取得を再試行します (${this._frameErrors}/${MAX_ERRORS}): ${error.message}`);
            }
        } finally {
            if (this._frameBusy === token)
                this._frameBusy = null;
        }
    }

    _captureArea([x, y, width, height]) {
        return new Promise((resolve, reject) => {
            const stream = Gio.MemoryOutputStream.new_resizable();
            const screenshot = new Shell.Screenshot();
            try {
                screenshot.screenshot_area(x, y, width, height, stream, (source, result) => {
                    try {
                        const [success] = source.screenshot_area_finish(result);
                        if (!success)
                            throw new Error('選択範囲の画面取得に失敗しました');
                        stream.close(null);
                        resolve(stream.steal_as_bytes().get_data());
                    } catch (error) {
                        reject(error);
                    } finally {
                        if (!stream.is_closed())
                            stream.close(null);
                    }
                });
            } catch (error) {
                if (!stream.is_closed())
                    stream.close(null);
                reject(error);
            }
        });
    }

    _post(route, data, {base = this._base, sessionId = this._sessionId, cancellable = null} = {}) {
        return this._request(this._endpoint(base, sessionId, route), 'application/json',
            new TextEncoder().encode(JSON.stringify(data)), cancellable);
    }

    _request(url, contentType, body, cancellable = null) {
        const cancel = cancellable || new Gio.Cancellable();
        const session = this._http;
        this._requests.add(cancel);
        return new Promise((resolve, reject) => {
            const message = Soup.Message.new('POST', url);
            // Never follow a redirect to another host, even if localhost responds with one.
            message.set_flags(message.get_flags() | Soup.MessageFlags.NO_REDIRECT);
            message.set_request_body_from_bytes(contentType, new GLib.Bytes(body));
            session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, cancel, (source, result) => {
                this._requests.delete(cancel);
                try {
                    const bytes = source.send_and_read_finish(result);
                    const status = message.get_status();
                    if (status < 200 || status >= 300) {
                        const error = new Error(`HTTP ${status}`);
                        error.status = status;
                        throw error;
                    }
                    resolve(JSON.parse(new TextDecoder().decode(bytes.get_data())));
                } catch (error) {
                    reject(error);
                }
            });
        });
    }

    _setMessage(message) {
        this._message = message;
        this._publish();
    }

    _publish() {
        if (!this._settings)
            return;
        this._settings.set_string('bridge-state', JSON.stringify({version: 1, heartbeat_ms: Date.now(),
            capturing: this._capturing, message: this._message, overlap: this._overlap(),
            rect: this._rect(), panel_rect: this._panelWindow ? windowRect(this._panelWindow) : [],
            session_id: this._sessionId, active_mode: this._activeMode,
            legacy_monitoring: Boolean(this._legacy?._isMonitoring),
            legacy_busy: Boolean(this._legacy?._pendingHttp)}));
        if (this._enabled && this._statusItem)
            this._statusItem.label.text = this._capturing ? '取得中' : '停止中 · 学習パネルで操作';
    }

    _bestEffortPause() {
        if (!this._base || !this._sessionId)
            return;
        // Independent short-lived session: aborting the main session must not cancel the stop request.
        const session = new Soup.Session({timeout: 3});
        const message = Soup.Message.new('POST', this._endpoint(this._base, this._sessionId, 'control'));
        message.set_flags(message.get_flags() | Soup.MessageFlags.NO_REDIRECT);
        message.set_request_body_from_bytes('application/json', new GLib.Bytes(new TextEncoder().encode(
            JSON.stringify({active: false, controller_id: this._controllerId, control_seq: ++this._controlSeq}))));
        session.send_and_read_async(message, GLib.PRIORITY_DEFAULT, null, (source, result) => {
            try { source.send_and_read_finish(result); }
            catch (error) { console.error('Learning disable pause (lease fallback):', error); }
            finally { session.abort(); }
        });
    }

    disable() {
        this._migrationPending = false;
        if (!this._enabled)
            return;
        this._enabled = false;
        this._legacy?.disable();
        this._legacy = null;
        this._invalidate();
        this._message = '学習キャプチャ拡張は停止中です';
        this._publish();
        this._bestEffortPause();
        for (const cancel of this._requests)
            cancel.cancel();
        this._requests.clear();
        this._http?.abort();
        for (const timer of ['_captureTimer', '_stateTimer', '_geometryTimer', '_restoreTimer', '_restoreSettleTimer']) {
            if (this[timer])
                GLib.source_remove(this[timer]);
            this[timer] = null;
        }
        for (const [object, id] of this._signals)
            object.disconnect(id);
        this._signals = [];
        this._disconnectWindow();
        this._selectionArea?.cancel();
        this._selectionArea?.destroy();
        this._indicator?.destroy();
        this._selectionArea = null;
        this._indicator = null;
        this._statusItem = null;
        this._panelWindow = null;
        this._ownWindows = [];
        this._http = null;
        this._settings = null;
    }
}
