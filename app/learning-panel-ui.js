import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk?version=4.0';
import Gdk from 'gi://Gdk?version=4.0';
import Soup from 'gi://Soup?version=3.0';
import Pango from 'gi://Pango';

export const APP_ID = 'io.github.amariichi.ScreenshotTranslatorLearning';
const SCHEMA = 'org.gnome.shell.extensions.screenshot-translator-learning';
const ROOT = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent().get_path();

function label(text, css = '') {
    const widget = new Gtk.Label({label: text, wrap: true, wrap_mode: Pango.WrapMode.WORD_CHAR,
        max_width_chars: 48, width_chars: 1, xalign: 0, hexpand: true, selectable: true});
    if (css)
        widget.add_css_class(css);
    return widget;
}

function box(spacing = 10) {
    return new Gtk.Box({orientation: Gtk.Orientation.VERTICAL, spacing});
}

function clear(container) {
    while (container.get_first_child())
        container.remove(container.get_first_child());
}

function scroll(child) {
    const widget = new Gtk.ScrolledWindow({vexpand: true, hexpand: true,
        hscrollbar_policy: Gtk.PolicyType.NEVER, child});
    return widget;
}

function textField(value, multiline = false) {
    if (!multiline)
        return new Gtk.Entry({text: value, hexpand: true});
    const widget = new Gtk.TextView({wrap_mode: Gtk.WrapMode.WORD_CHAR,
        top_margin: 8, bottom_margin: 8, left_margin: 8, right_margin: 8});
    widget.get_buffer().set_text(value, -1);
    return widget;
}

function fieldText(widget) {
    if (widget instanceof Gtk.Entry)
        return widget.get_text();
    const buffer = widget.get_buffer();
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), false);
}

export class LearningPanel {
    constructor(application, options = {}) {
        const source = Gio.SettingsSchemaSource.new_from_directory(options.schemaDir ??
            GLib.build_filenamev([ROOT, 'gnome-learning-extension', 'schemas']),
        Gio.SettingsSchemaSource.get_default(), false);
        this.settings = options.settings ?? new Gio.Settings({settings_schema: source.lookup(SCHEMA, false)});
        this.http = new Soup.Session({timeout: 70});
        this.closed = false;
        this.busy = false;
        this.polling = false;
        this.sid = this.settings.get_string('session-id');
        this.snapshot = null;
        this.sessions = [];
        this.signatures = {};
        this.uiError = '';
        this.uiInfo = '';
        this._viewEpoch = 0;
        this._emptyTrashEpoch = 0;
        this.followLatest = true;
        this.unreadCount = 0;
        this.historyCards = new Map();
        this.historyOrder = [];
        this.window = new Gtk.ApplicationWindow({application, title: 'Screenshot Translator · 学習',
            default_width: 490, default_height: 650});
        this.window.add_css_class('learning-window');
        Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', true);
        const css = new Gtk.CssProvider();
        css.load_from_string(`
            .learning-window { background: #171b20; color: #f1eee7; }
            .learning-window label { font-family: "Noto Sans CJK JP", sans-serif; }
            .learning-window .muted { color: #b7b8b5; font-size: 12px; }
            .learning-window .status { color: #e5ba76; font-size: 12px; }
            .learning-window .original { color: #cfdbdf; font-size: 16px; }
            .learning-window .translation { color: #fff5df; font-size: 18px; font-weight: 500; }
            .learning-window .card { background: #22282e; border-radius: 10px; padding: 12px; }
            .learning-window .section-title { font-size: 15px; font-weight: 600; }
            .learning-window button { min-height: 28px; }
            .learning-window headerbar { background: #22282e; color: #f1eee7; }
            .learning-window notebook > header { background: #171b20; }
            .learning-window notebook tab { padding: 4px 12px; }
        `);
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
        const header = new Gtk.HeaderBar();
        header.set_title_widget(new Gtk.Label({label: '視聴と学習'}));
        const preferences = new Gtk.Button({label: '記録設定'});
        preferences.connect('clicked', () => this.editSettings());
        header.pack_end(preferences);
        this.window.set_titlebar(header);
        const root = box(6);
        root.set_margin_start(12); root.set_margin_end(12);
        root.set_margin_top(8); root.set_margin_bottom(10);
        this.window.set_child(root);
        const sessionRow = new Gtk.Box({spacing: 8});
        this.sessionPicker = Gtk.DropDown.new_from_strings(['記録を読み込み中…']);
        this.sessionPicker.set_hexpand(true);
        const pickerFactory = new Gtk.SignalListItemFactory();
        pickerFactory.connect('setup', (_factory, item) => item.set_child(new Gtk.Label({
            xalign: 0, ellipsize: Pango.EllipsizeMode.END, max_width_chars: 24, width_chars: 1})));
        pickerFactory.connect('bind', (_factory, item) => {
            const text = item.get_item().get_string();
            item.get_child().set_label(text);
            item.get_child().set_tooltip_text(text);
        });
        this.sessionPicker.set_factory(pickerFactory);
        this.sessionPicker.connect('notify::selected', () => {
            if (this.updatingPicker)
                return;
            const selected = this.sessions[this.sessionPicker.get_selected()];
            if (selected && selected.id !== this.sid)
                this.perform(() => this.selectSession(selected.id));
        });
        const newButton = new Gtk.Button({label: '新規'});
        newButton.connect('clicked', () => this.newSession());
        sessionRow.append(new Gtk.Label({label: '記録'}));
        sessionRow.append(this.sessionPicker); sessionRow.append(newButton);
        const recordMenu = new Gtk.MenuButton({label: '操作'});
        const recordPopover = new Gtk.Popover();
        const recordActions = box(4);
        this.renameButton = new Gtk.Button({label: '名前を変更…'});
        this.folderButton = new Gtk.Button({label: '保存フォルダを開く'});
        this.trashButton = new Gtk.Button({label: '記録を削除…'});
        this.emptyTrashButton = new Gtk.Button({label: 'ゴミ箱を空にする…'});
        for (const [button, action] of [[this.renameButton, () => this.renameSession()],
            [this.folderButton, () => this.openFolder()], [this.trashButton, () => this.trashSession()],
            [this.emptyTrashButton, () => this.emptyTrash()]]) {
            button.connect('clicked', () => { recordPopover.popdown(); action(); });
            recordActions.append(button);
        }
        recordPopover.set_child(recordActions); recordMenu.set_popover(recordPopover);
        sessionRow.append(recordMenu); root.append(sessionRow);
        const statusRow = new Gtk.Box({spacing: 10});
        this.status = label('接続中…', 'status');
        this.summary = new Gtk.Label({label: '履歴0件 / 学習メモ0件', xalign: 1});
        this.summary.add_css_class('muted');
        statusRow.append(this.status); statusRow.append(this.summary); root.append(statusRow);
        const controls = new Gtk.Box({spacing: 8, homogeneous: true});
        this.selectButton = new Gtk.Button({label: '範囲を選ぶ'});
        this.resumeButton = new Gtk.Button({label: '再開'});
        this.pauseButton = new Gtk.Button({label: '停止'});
        this.selectButton.connect('clicked', () => this.command('select'));
        this.resumeButton.connect('clicked', () => this.command('resume'));
        this.pauseButton.connect('clicked', () => this.command('pause'));
        for (const button of [this.selectButton, this.resumeButton, this.pauseButton])
            controls.append(button);
        root.append(controls);
        const tabs = new Gtk.Notebook({vexpand: true});
        this.tabs = tabs;
        this.history = box(12); this.notes = box(12);
        this.historyEarlier = label('以前の履歴は保存した記録から確認できます。', 'muted');
        this.historyEmpty = label('「範囲を選ぶ」で取得を始めます。', 'muted');
        this.history.append(this.historyEarlier); this.history.append(this.historyEmpty);
        this.historyEarlier.set_visible(false);
        this.historyScroll = scroll(this.history);
        this.historyScroll.set_min_content_height(80);
        this.historyAdjustment = this.historyScroll.get_vadjustment();
        this.latestButton = new Gtk.Button({label: '最新へ', halign: Gtk.Align.END, visible: false});
        this.latestButton.connect('clicked', () => {
            this.followLatest = true;
            this.unreadCount = 0;
            delete this.signatures.history;
            if (this.snapshot) this._renderHistory(this.snapshot);
            else this._queueHistoryRestore(null, true);
        });
        const historyPage = box(4);
        historyPage.append(this.historyScroll); historyPage.append(this.latestButton);
        this.historyAdjustment.connect('value-changed', () => {
            if (this.closed || this._settingScroll || this._syncingHistory) return;
            this.followLatest = this._historyAtBottom();
            if (this.followLatest) this.unreadCount = 0;
            this._updateLatestButton();
        });
        this.historyAdjustment.connect('changed', () => {
            if (!this.closed && !this._syncingHistory && this.followLatest && !this._scrollTimer)
                this._queueHistoryRestore(null, true);
        });
        const scrollInput = new Gtk.EventControllerScroll({flags: Gtk.EventControllerScrollFlags.VERTICAL});
        scrollInput.connect('scroll', (_controller, _dx, dy) => {
            if (dy < 0) {
                this.followLatest = false;
                if (this._scrollTimer) GLib.source_remove(this._scrollTimer);
                this._scrollTimer = 0;
            }
            return false;
        });
        this.historyScroll.add_controller(scrollInput);
        tabs.append_page(historyPage, new Gtk.Label({label: '履歴', wrap: false}));
        tabs.append_page(scroll(this.notes), new Gtk.Label({label: '学習メモ', wrap: false}));
        root.append(tabs);
        const analysisRow = new Gtk.Box({spacing: 8});
        this.analysisButton = new Gtk.Button({label: '未処理を生成', hexpand: true});
        this.analysisButton.connect('clicked', () => this.perform(async () => {
            await this.api('POST', `/sessions/${this.sid}/analysis`, {});
        }));
        this.stopAnalysisButton = new Gtk.Button({label: '解説を停止', visible: false});
        this.stopAnalysisButton.connect('clicked', () => this.perform(async () => {
            await this.api('POST', `/sessions/${this.sid}/analysis/stop`, {});
        }));
        this.retryAnalysisButton = new Gtk.Button({label: '失敗分を再試行', visible: false});
        this.retryAnalysisButton.connect('clicked', () => this.perform(async () => {
            await this.api('POST', `/sessions/${this.sid}/analysis`, {retry_failed: true});
        }));
        analysisRow.append(this.analysisButton); analysisRow.append(this.retryAnalysisButton);
        analysisRow.append(this.stopAnalysisButton); root.append(analysisRow);
        this.analysisStatus = label('処理済み0 / 未処理0 / 失敗0', 'muted');
        this.analysisStatus.set_tooltip_text('処理済みには、学習メモの対象がなかった履歴も含みます。');
        root.append(this.analysisStatus);
        const files = new Gtk.Box({spacing: 8});
        this.autoSave = new Gtk.Label({label: '自動保存'});
        this.autoSave.add_css_class('muted');
        this.openRecordButton = new Gtk.Button({label: '保存した記録を開く', hexpand: true});
        this.openRecordButton.connect('clicked', () => this.perform(async () => {
            const path = this.snapshot?.markdown_path;
            if (path) Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), null);
        }));
        this.saveRetryButton = new Gtk.Button({label: '保存を再試行', visible: false});
        this.saveRetryButton.connect('clicked', () => this.perform(() =>
            this.api('POST', `/sessions/${this.sid}/export`, {})));
        files.append(this.autoSave); files.append(this.openRecordButton); files.append(this.saveRetryButton);
        root.append(files);
        this.window.connect('close-request', () => {
            this.command('pause', false);
            this.closed = true;
            if (this.timer) GLib.source_remove(this.timer);
            if (this._scrollTimer) GLib.source_remove(this._scrollTimer);
            this.timer = 0; this._scrollTimer = 0;
            this.http.abort();
            return false;
        });
        this.timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 750, () => {
            this.poll();
            return GLib.SOURCE_CONTINUE;
        });
        this.perform(async () => {
            await this.loadSessions();
            if (!this.sid && this.sessions.length)
                await this.selectSession(this.sessions[0].id);
            if (!this.sessions.length)
                await this.createSession('視聴記録');
        });
        this.window.present();
    }

    async api(method, path, body = null, {timeoutMs = 0} = {}) {
        const base = this.settings.get_string('backend-url').replace(/\/$/, '');
        const uri = GLib.Uri.parse(base, GLib.UriFlags.NONE);
        if (uri.get_scheme() !== 'http' || !['localhost', '127.0.0.1', '::1'].includes(uri.get_host()) || uri.get_userinfo())
            throw new Error('接続先はローカルHTTPサーバーを指定してください。');
        const message = Soup.Message.new(method, `${base}/api/learning${path}`);
        message.set_flags(Soup.MessageFlags.NO_REDIRECT);
        if (body !== null)
            message.set_request_body_from_bytes('application/json',
                new GLib.Bytes(new TextEncoder().encode(JSON.stringify(body))));
        const cancellable = timeoutMs ? new Gio.Cancellable() : null;
        let timeout = timeoutMs ? GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeoutMs, () => {
            timeout = 0; cancellable.cancel(); return GLib.SOURCE_REMOVE;
        }) : 0;
        let bytes;
        try {
            bytes = await new Promise((resolve, reject) => {
                this.http.send_and_read_async(message, GLib.PRIORITY_DEFAULT, cancellable, (session, result) => {
                    try { resolve(session.send_and_read_finish(result)); } catch (error) { reject(error); }
                });
            });
        } finally {
            if (timeout) GLib.source_remove(timeout);
        }
        const output = JSON.parse(new TextDecoder().decode(bytes.get_data()));
        if (message.get_status() < 200 || message.get_status() >= 300)
            throw new Error(typeof output.detail === 'string' ? output.detail : '操作に失敗しました。');
        return output;
    }

    async perform(operation) {
        if (this.busy || this.closed)
            return;
        this.busy = true;
        try { await operation(); this.uiError = ''; } catch (error) {
            this.uiError = error.message;
            if (!this.closed) this.status.set_label(this.uiError);
        } finally {
            this.busy = false;
            this.poll();
        }
    }

    async command(action, analyseOnStop = true) {
        if (!this.sid || this.closed) return;
        const sid = this.sid;
        const epoch = this._commandEpoch = (this._commandEpoch ?? 0) + 1;
        this.uiError = ''; this.uiInfo = '';
        let armError = '';
        if (action === 'pause' && analyseOnStop) {
            this._armingStop = true;
            this.status.set_label('取得を停止しています…');
            this.selectButton.set_sensitive(false); this.resumeButton.set_sensitive(false);
            try {
                await this.api('POST', `/sessions/${sid}/analysis/on-stop`, {}, {timeoutMs: 3000});
            } catch (_error) {
                armError = '自動解説を予約できませんでした。停止後に「未処理を生成」で再試行できます。';
            } finally {
                if (this._commandEpoch === epoch) this._armingStop = false;
            }
        } else {
            this._armingStop = false;
        }
        if (this.closed || this.sid !== sid || this._commandEpoch !== epoch) return;
        this.settings.set_string('control-request', JSON.stringify({action,
            id: GLib.uuid_string_random(), session_id: sid}));
        this.uiError = armError;
        this.status.set_label(armError || (action === 'pause' ? '停止を要求しました…' : '画面取得へ接続中…'));
    }

    async loadSessions() {
        this.sessions = await this.api('GET', '/sessions');
        if (this.sid && !this.sessions.some(session => session.id === this.sid))
            this.sid = '';
        this.updatingPicker = true;
        this.sessionPicker.set_model(Gtk.StringList.new(this.sessions.length ?
            this.sessions.map(session => session.title) : ['記録がありません']));
        this.sessionPicker.set_selected(Math.max(0, this.sessions.findIndex(session => session.id === this.sid)));
        this.updatingPicker = false;
    }

    async selectSession(sid) {
        if (this.snapshot?.capturing || this.snapshot?.analysis.running)
            throw new Error('取得と解説を停止してから記録を切り替えてください。');
        this._viewEpoch++;
        this._emptyTrashEpoch++;
        this._emptyTrashPending = false;
        this.uiInfo = ''; this.uiError = '';
        this.sid = sid;
        this.snapshot = null;
        this.signatures = {};
        this.followLatest = true;
        this.unreadCount = 0;
        this.settings.set_string('session-id', sid);
    }

    async createSession(title) {
        if (this.snapshot?.capturing || this.snapshot?.analysis.running)
            throw new Error('取得と解説を停止してから新しい記録を作成してください。');
        const previousSid = this.sid;
        const session = await this.api('POST', '/sessions', {title});
        if (this.closed || this.sid !== previousSid) return;
        await this.selectSession(session.id);
        await this.loadSessions();
    }

    newSession() {
        const entry = textField('視聴記録');
        this.dialog('新しい視聴記録', [['作品名・エピソード', entry]], async () => {
            await this.createSession(fieldText(entry));
        });
    }

    dialog(title, fields, save, {confirmLabel = '保存', destructive = false, description = '', direct = false} = {}) {
        const dialog = new Gtk.Dialog({title, application: this.window.get_application(), transient_for: this.window,
            modal: true, destroy_with_parent: true,
            default_width: 430});
        const cancel = dialog.add_button('キャンセル', Gtk.ResponseType.CANCEL);
        const confirm = dialog.add_button(confirmLabel, Gtk.ResponseType.OK);
        if (destructive) {
            confirm.add_css_class('destructive-action');
            dialog.set_default_response(Gtk.ResponseType.CANCEL);
            dialog.set_focus(cancel);
        }
        const content = dialog.get_content_area();
        content.set_spacing(8); content.set_margin_start(16); content.set_margin_end(16);
        content.set_margin_top(14); content.set_margin_bottom(14);
        if (description) content.append(label(description));
        for (const [caption, field] of fields) {
            content.append(label(caption));
            if (field instanceof Gtk.TextView) {
                const scroller = scroll(field);
                scroller.set_min_content_height(90);
                content.append(scroller);
            } else content.append(field);
        }
        dialog.connect('response', (_widget, response) => {
            if (response === Gtk.ResponseType.OK) {
                if (direct) save();
                else this.perform(save);
            }
            dialog.destroy();
        });
        dialog.present();
    }

    editSettings() {
        const snap = this.snapshot;
        if (!snap) return;
        const sid = snap.id;
        const title = textField(snap.title), url = textField(snap.source_url);
        const context = textField(snap.user_context, true);
        const known = textField(snap.known_words.join(', '));
        this.dialog('記録と文脈', [['作品名・エピソード', title], ['出典URL（任意）', url],
            ['場面のメモ（任意）', context], ['既知語（カンマ区切り）', known]], async () => {
            await this.api('PATCH', `/sessions/${sid}`, {title: fieldText(title),
                source_url: fieldText(url), user_context: fieldText(context),
                known_words: fieldText(known).split(',').map(word => word.trim()).filter(Boolean)});
            if (!this.closed && this.sid === sid) await this.loadSessions();
        });
    }

    renameSession() {
        const snap = this.snapshot;
        if (!snap) return;
        const sid = snap.id;
        const title = textField(snap.title);
        this.dialog('名前を変更', [['記録名', title]], async () => {
            await this.api('PATCH', `/sessions/${sid}`, {title: fieldText(title)});
            if (!this.closed && this.sid === sid) await this.loadSessions();
        });
    }

    openFolder() {
        const snap = this.snapshot;
        if (!snap) return;
        this.perform(async () => {
            const path = snap.directory_path || Gio.File.new_for_path(snap.markdown_path).get_parent().get_path();
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), null);
        });
    }

    trashSession() {
        const snap = this.snapshot;
        if (!snap) return;
        const sid = snap.id, title = snap.title;
        this.dialog('記録を削除', [], async () => {
            await this.api('POST', `/sessions/${sid}/trash`, {confirm_title: title});
            if (this.closed || this.sid !== sid) return;
            this.sid = ''; this.snapshot = null; this.signatures = {};
            this.settings.set_string('session-id', '');
            await this.loadSessions();
            if (this.sessions.length) await this.selectSession(this.sessions[0].id);
            else await this.createSession('視聴記録');
        }, {confirmLabel: '削除して退避', destructive: true,
            description: `「${title}」を削除します。\n原文・訳・学習メモと関連ファイルを、復元できる退避先へ移します。${snap.trash_path ? '\n退避先: ' + snap.trash_path : ''}`});
    }

    _trashContextCurrent(context) {
        return !this.closed && this.sid === context.sid && this._viewEpoch === context.view &&
            this._emptyTrashEpoch === context.request;
    }

    _trashMessage(context, text, error = false) {
        if (!this._trashContextCurrent(context)) return;
        this.uiError = error ? text : '';
        this.uiInfo = error ? '' : text;
        this.status.set_label(text);
    }

    async emptyTrash() {
        if (this.closed || this._emptyTrashPending) return;
        const context = {sid: this.sid, view: this._viewEpoch, request: ++this._emptyTrashEpoch};
        this._emptyTrashPending = true;
        this.emptyTrashButton.set_sensitive(false);
        this.uiInfo = ''; this.uiError = '';
        try {
            const info = await this.api('GET', '/trash', null, {timeoutMs: 5000});
            if (!this._trashContextCurrent(context)) return;
            if (!Number.isInteger(info.count) || info.count < 0)
                throw new Error('ゴミ箱の情報を確認できませんでした。');
            if (!info.count) {
                this._trashMessage(context, 'ゴミ箱は空です');
                return;
            }
            if (info.busy) {
                this._trashMessage(context, '取得と学習メモ生成を停止してからゴミ箱を空にしてください。', true);
                return;
            }
            if (typeof info.token !== 'string' || !info.token)
                throw new Error('ゴミ箱の確認情報を取得できませんでした。');
            this.dialog('ゴミ箱を空にする', [], () => this._confirmEmptyTrash(context, info), {
                direct: true, destructive: true, confirmLabel: '完全に削除',
                description: `ゴミ箱の${info.count}件を完全に削除します。\n削除後は復元できません。\n現在の記録は対象外です。`,
            });
        } catch (error) {
            this._trashMessage(context, error.message || 'ゴミ箱を確認できませんでした。', true);
        } finally {
            if (context.request === this._emptyTrashEpoch) this._emptyTrashPending = false;
            if (this._trashContextCurrent(context)) this.emptyTrashButton.set_sensitive(true);
        }
    }

    async _confirmEmptyTrash(context, info) {
        if (!this._trashContextCurrent(context) || this._emptyTrashPending) return;
        this._emptyTrashPending = true;
        this.emptyTrashButton.set_sensitive(false);
        try {
            const result = await this.api('POST', '/trash/empty', {confirm_permanent: true, token: info.token});
            if (!this._trashContextCurrent(context)) return;
            if (!Number.isInteger(result.deleted) || result.deleted < 0 || result.remaining !== 0)
                throw new Error('ゴミ箱の削除結果を確認できませんでした。もう一度状態を確認してください。');
            this._trashMessage(context, `ゴミ箱を空にしました（${result.deleted}件）`);
        } catch (error) {
            this._trashMessage(context, error.message || 'ゴミ箱を空にできませんでした。', true);
        } finally {
            if (context.request === this._emptyTrashEpoch) this._emptyTrashPending = false;
            if (this._trashContextCurrent(context)) this.emptyTrashButton.set_sensitive(true);
        }
    }

    editNote(note) {
        const meaning = textField(note.meaning_ja, true), context = textField(note.context_ja, true);
        const register = textField(note.register_ja);
        const uncertain = new Gtk.CheckButton({label: '文脈・意味を要確認', active: note.uncertain});
        const sid = this.sid;
        this.dialog(note.expression, [['意味', meaning], ['この場面での説明', context],
            ['語調・使い方', register], ['確認状態', uncertain]], () =>
            this.api('PATCH', `/sessions/${sid}/notes/${note.id}`, {meaning_ja: fieldText(meaning),
                context_ja: fieldText(context), register_ja: fieldText(register), uncertain: uncertain.get_active()}));
    }

    async poll() {
        if (this.closed || this.polling || this.busy)
            return;
        this.polling = true;
        try {
            if (!this.sid) {
                await this.loadSessions();
                if (this.sessions.length) await this.selectSession(this.sessions[0].id);
                else await this.createSession('視聴記録');
            }
            const sid = this.sid;
            const snap = await this.api('GET', `/sessions/${sid}`);
            if (!this.closed && this.sid === sid) {
                this.snapshot = snap;
                this.render(snap);
            }
        } catch (_error) {
            if (!this.closed)
                this.status.set_label('学習サーバーに接続できません。起動後、自動的に再接続します。');
        } finally { this.polling = false; }
    }

    _historyAtBottom() {
        const adjustment = this.historyAdjustment;
        return adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() <= 28;
    }

    _historyAnchor() {
        const value = this.historyAdjustment.get_value();
        for (const id of this.historyOrder) {
            const card = this.historyCards.get(id)?.widget;
            if (!card) continue;
            const rect = card.get_allocation();
            if (rect.y + rect.height > value)
                return {id, offset: value - rect.y, value};
        }
        return {value};
    }

    _updateLatestButton() {
        this.latestButton.set_label(this.unreadCount ? `新着${this.unreadCount}件 · 最新へ` : '最新へ');
        this.latestButton.set_visible(!this.followLatest);
    }

    _queueHistoryRestore(anchor, follow) {
        if (this._scrollTimer) GLib.source_remove(this._scrollTimer);
        this._scrollTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 60, () => {
            this._scrollTimer = 0;
            if (this.closed) return GLib.SOURCE_REMOVE;
            const adjustment = this.historyAdjustment;
            const limit = Math.max(0, adjustment.get_upper() - adjustment.get_page_size());
            let value = limit;
            if (!follow) {
                const card = anchor?.id ? this.historyCards.get(anchor.id)?.widget : null;
                value = card ? card.get_allocation().y + anchor.offset : (anchor?.value ?? adjustment.get_value());
            }
            this._settingScroll = true;
            adjustment.set_value(Math.max(0, Math.min(limit, value)));
            this._settingScroll = false;
            this.followLatest = follow;
            if (follow) this.unreadCount = 0;
            this._updateLatestButton();
            return GLib.SOURCE_REMOVE;
        });
    }

    _renderHistory(snap) {
        let entries = snap.entries;
        const signature = JSON.stringify([snap.id, snap.entry_count, entries.map(item =>
            [item.id, item.source, item.translation, item.kind, item.speaker, item.captured_at, item.uncertain, item.note])]);
        if (signature === this.signatures.history) return;
        const sameSession = this.historySession === snap.id;
        const follow = !sameSession || this.followLatest;
        const anchor = follow ? null : this._historyAnchor();
        if (!follow && anchor?.id && this.historyCards.has(anchor.id)) {
            // The API returns a rolling recent window. While the reader stays
            // here, keep this card and its following cached cards even after
            // they leave that window. Returning to latest compacts to API data.
            const retainedIds = this.historyOrder.slice(this.historyOrder.indexOf(anchor.id));
            const retained = new Set(retainedIds);
            const incoming = new Map(snap.entries.map(item => [item.id, item]));
            const incomingAnchor = snap.entries.findIndex(item => item.id === anchor.id);
            const prefix = incomingAnchor < 0 ? [] : snap.entries.slice(0, incomingAnchor);
            const prefixIds = new Set(prefix.map(item => item.id));
            entries = [...prefix.filter(item => !retained.has(item.id)),
                ...retainedIds.map(id => incoming.get(id) ?? this.historyCards.get(id).item),
                ...snap.entries.filter(item => !retained.has(item.id) && !prefixIds.has(item.id))];
        }
        const oldIds = new Set(this.historyCards.keys());
        this._syncingHistory = true;
        if (!sameSession) {
            for (const {widget} of this.historyCards.values()) this.history.remove(widget);
            this.historyCards.clear();
            this.historySession = snap.id;
            this.unreadCount = 0;
        }
        const wanted = new Set(entries.map(item => item.id));
        for (const [id, record] of this.historyCards) {
            if (!wanted.has(id)) {
                this.history.remove(record.widget);
                this.historyCards.delete(id);
            }
        }
        this.historyEarlier.set_visible(snap.entry_count > entries.length);
        this.historyEmpty.set_visible(entries.length === 0);
        let previous = this.historyEmpty;
        for (const item of entries) {
            let record = this.historyCards.get(item.id);
            if (!record) {
                record = {widget: box(6)};
                record.widget.add_css_class('card');
                this.historyCards.set(item.id, record);
                this.history.append(record.widget);
            }
            record.item = item;
            const content = JSON.stringify([item.source, item.translation, item.kind, item.speaker,
                item.captured_at, item.uncertain, item.note]);
            if (record.signature !== content) {
                clear(record.widget);
                const kind = {subtitle: '字幕', sms: 'SMS', screen_text: '画面内文字'}[item.kind] ?? item.kind;
                record.widget.append(label(`${new Date(item.captured_at * 1000).toLocaleTimeString()} · ${kind}${item.speaker ? ' · ' + item.speaker : ''}`, 'muted'));
                record.widget.append(label(item.source, 'original'));
                record.widget.append(label(item.translation, 'translation'));
                if (item.uncertain || item.note) record.widget.append(label('要確認: ' + (item.note || '原文・訳を確認してください'), 'status'));
                record.signature = content;
            }
            this.history.reorder_child_after(record.widget, previous);
            previous = record.widget;
        }
        this.historyOrder = entries.map(item => item.id);
        if (!follow && sameSession) this.unreadCount += entries.filter(item => !oldIds.has(item.id)).length;
        this._syncingHistory = false;
        this.followLatest = follow;
        this.signatures.history = signature;
        this._updateLatestButton();
        this._queueHistoryRestore(anchor, follow);
    }

    render(snap) {
        let bridge = {};
        try { bridge = JSON.parse(this.settings.get_string('bridge-state')); } catch (_) { /* absent */ }
        const connected = Date.now() - (bridge.heartbeat_ms ?? 0) < 6000;
        const analysis = snap.analysis;
        const pending = analysis.pending ?? 0;
        const failed = analysis.failed ?? snap.entries.filter(item => item.analysis_status === 'failed').length;
        const done = analysis.done ?? snap.entries.filter(item => item.analysis_status === 'done').length;
        const count = analysis.note_count ?? snap.notes.length;
        const error = this.uiError || snap.last_error || analysis.error || snap.export_error;
        const resumePending = analysis.resume_pending ?? snap.resume_pending;
        this.status.set_label([resumePending ? '取得再開待ち' : snap.capturing ? '取得中' : '停止中',
            connected ? (bridge.overlap ? '取得範囲が重複' : '') : '取得拡張が未接続',
            error ? 'エラー: ' + error : this.uiInfo].filter(Boolean).join(' · '));
        this.status.set_tooltip_text(bridge.message || '');
        this.summary.set_label(`履歴${snap.entry_count}件 / 学習メモ${count}件`);
        const progress = analysis.running ? `学習メモ生成中 ${analysis.processed ?? 0}/${analysis.total_run ?? 0} · ` : '';
        this.analysisStatus.set_label(`${progress}処理済み${done} / 未処理${pending} / 失敗${failed}`);
        this.autoSave.set_label(snap.export_error ? '保存エラー' : '自動保存');
        this.saveRetryButton.set_visible(Boolean(snap.export_error));
        this.openRecordButton.set_sensitive(Boolean(snap.markdown_path));
        this.selectButton.set_sensitive(connected && !analysis.running && !this._armingStop);
        this.resumeButton.set_sensitive(connected && !this._armingStop && (!snap.capturing || Boolean(resumePending)));
        this.pauseButton.set_sensitive(snap.capturing || Boolean(bridge.capturing) ||
            Boolean(bridge.legacy_monitoring) || Boolean(bridge.legacy_busy));
        this.analysisButton.set_sensitive(!snap.capturing && !bridge.legacy_monitoring &&
            !bridge.legacy_busy && !analysis.running && pending > 0);
        this.retryAnalysisButton.set_visible(failed > 0 && !analysis.running);
        this.retryAnalysisButton.set_sensitive(!snap.capturing && !bridge.legacy_monitoring &&
            !bridge.legacy_busy && !analysis.running && failed > 0);
        this.renameButton.set_sensitive(!analysis.running);
        this.trashButton.set_sensitive(!snap.capturing && !analysis.running);
        this.folderButton.set_sensitive(Boolean(snap.directory_path || snap.markdown_path));
        this.emptyTrashButton.set_sensitive(!this._emptyTrashPending);
        this.stopAnalysisButton.set_visible(analysis.running);
        this.stopAnalysisButton.set_sensitive(analysis.running && !analysis.stop_requested);
        this._renderHistory(snap);
        const noteSignature = JSON.stringify([snap.notes, done, pending, failed, analysis.no_notes, analysis.running]);
        if (noteSignature !== this.signatures.notes) {
            clear(this.notes);
            if (!snap.notes.length) {
                const text = analysis.running ? '学習メモを生成中…' :
                    done && !pending && !failed ? '選別が終わりました。学習メモの対象はありませんでした。' :
                    done ? `学習メモはまだありません。処理済み${done}件。` :
                    '原文・訳は取得中に保存し、取得を停止すると学習メモを生成します。';
                this.notes.append(label(text, 'muted'));
            }
            for (const note of snap.notes) {
                const card = box(7); card.add_css_class('card');
                card.append(label(note.expression, 'section-title'));
                card.append(label(note.quote, 'original'));
                card.append(label(note.meaning_ja)); card.append(label(note.context_ja));
                card.append(label(note.register_ja, 'muted'));
                card.append(label((note.edited ? '編集済み' : '下書き') + (note.uncertain ? ' · 文脈・意味を要確認' : ''), 'status'));
                const source = snap.entries.find(entry => entry.id === note.entry_id);
                card.append(label('出典: ' + (source ? new Date(source.captured_at * 1000).toLocaleTimeString() : note.entry_id.slice(0, 8)), 'muted'));
                const edit = new Gtk.Button({label: 'メモを編集', halign: Gtk.Align.START});
                edit.connect('clicked', () => this.editNote(note)); card.append(edit); this.notes.append(card);
            }
            this.signatures.notes = noteSignature;
        }
    }
}
