// Runs the production GTK window in the isolated smoke session. Test actions only.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk?version=4.0';
import Pango from 'gi://Pango';
import {APP_ID, LearningPanel} from '../app/learning-panel-ui.js';

const output = ARGV[0];
const app = new Gtk.Application({application_id: APP_ID});
let panel;
let lastError = '';
let synthetic = null;
let realSnapshot = null;
let realRender;
const trashCalls = {get: 0, post: 0};
let delayTrashGet = 0, delayTrashPost = 0;
let holdingTrashGet = false, holdingTrashPost = false;

function descendants(widget) {
    if (!widget) return [];
    const output = [widget];
    for (let child = widget.get_first_child(); child; child = child.get_next_sibling())
        output.push(...descendants(child));
    return output;
}

function bounds(widget) {
    const [ok, rect] = widget.compute_bounds(panel.window);
    return ok ? [rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()] : [];
}

function layoutReport() {
    const adjustment = panel.historyAdjustment;
    const issues = [];
    for (const [id, record] of panel.historyCards) {
        for (const widget of descendants(record.widget).filter(child => child instanceof Gtk.Label)) {
            const allocation = widget.get_allocation();
            const [width, height] = widget.get_layout().get_pixel_size();
            if (allocation.width > 0 && (width > allocation.width + 2 || height > allocation.height + 2 ||
                widget.get_ellipsize() !== Pango.EllipsizeMode.NONE))
                issues.push({id, text: widget.get_label().slice(-100),
                    allocation: [allocation.width, allocation.height], textSize: [width, height]});
        }
    }
    const dialog = app.get_windows().find(window => window instanceof Gtk.Dialog);
    return {window: [panel.window.get_width(), panel.window.get_height()],
        history: [panel.historyScroll.get_width(), panel.historyScroll.get_height()],
        footer: bounds(panel.openRecordButton),
        value: adjustment.get_value(), upper: adjustment.get_upper(), page: adjustment.get_page_size(),
        follow: panel.followLatest, unread: panel.unreadCount, latestVisible: panel.latestButton.get_visible(),
        anchor: panel._historyAnchor(), issues, rowCount: panel.historyCards.size,
        originals: [...panel.historyCards.values()].map(record =>
            descendants(record.widget).filter(widget => widget instanceof Gtk.Label).map(widget => widget.get_label())),
        currentPanePresent: Boolean(panel.current),
        personalMemoButtonPresent: descendants(panel.window).some(widget => widget instanceof Gtk.Button &&
            widget.get_label() === '個人メモ'),
        recordActions: [panel.renameButton, panel.folderButton, panel.trashButton, panel.emptyTrashButton].map(widget => widget.get_label()),
        dialog: dialog ? {title: dialog.get_title(),
            defaultResponse: dialog.get_default_widget() ? dialog.get_response_for_widget(dialog.get_default_widget()) : null,
            text: descendants(dialog)
            .filter(widget => widget instanceof Gtk.Label).map(widget => widget.get_label()).join('\n')} : null};
}

function report(extra = {}) {
    GLib.file_set_contents(output, JSON.stringify({sid: panel.sid, snapshot: synthetic ?? panel.snapshot,
        status: panel.status.get_label(), summary: panel.summary.get_label(), watching: Boolean(panel._watch),
        analysisText: panel.analysisStatus.get_label(), uiError: panel.uiError, uiInfo: panel.uiInfo,
        trashPending: Boolean(panel._emptyTrashPending), trashCalls, holdingTrashGet, holdingTrashPost, title: panel.window.get_title(), error: lastError,
        sensitive: {resume: panel.resumeButton.get_sensitive(), pause: panel.pauseButton.get_sensitive(),
            analyse: panel.analysisButton.get_sensitive(), trash: panel.trashButton.get_sensitive(),
            retry: panel.retryAnalysisButton.get_sensitive(),
            retryVisible: panel.retryAnalysisButton.get_visible()}, layout: layoutReport(), ...extra}));
}

function syntheticEntry(index) {
    return {id: `synthetic-long-${index}`, kind: index % 3 ? 'subtitle' : 'sms', speaker: null,
        source: `Synthetic line ${index}: ` +
            'We should talk things through before making a decision, even when the answer seems obvious. '.repeat(4) +
            'UnbrokenWordForWrapping'.repeat(10) + ` SOURCE_END_${index}`,
        translation: `合成字幕${index}：` +
            '答えが明らかに思えても、決める前にお互いの考えをよく話し合っておこう。'.repeat(6) + ` 訳終端${index}`,
        uncertain: false, note: '', captured_at: 1792200000 + index,
        analysis_status: 'pending', context: []};
}

function showSynthetic() {
    panel.snapshot = synthetic;
    realRender(synthetic);
}

app.connect('activate', () => {
    panel = new LearningPanel(app);
    const realApi = panel.api.bind(panel);
    panel.api = async (...args) => {
        const [method, path] = args;
        const get = method === 'GET' && path === '/trash';
        const post = method === 'POST' && path === '/trash/empty';
        if (get) trashCalls.get++;
        if (post) trashCalls.post++;
        const delay = get ? delayTrashGet : post ? delayTrashPost : 0;
        if (get) delayTrashGet = 0;
        if (post) delayTrashPost = 0;
        const result = await realApi(...args);
        if (delay) {
            if (get) holdingTrashGet = true;
            if (post) holdingTrashPost = true;
            await new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
                resolve(); return GLib.SOURCE_REMOVE;
            }));
            if (get) holdingTrashGet = false;
            if (post) holdingTrashPost = false;
        }
        return result;
    };
    realRender = panel.render.bind(panel);
    panel.render = snapshot => { if (!synthetic) realRender(snapshot); };
    const realPoll = panel.poll.bind(panel);
    panel.poll = () => synthetic ? Promise.resolve() : realPoll();
    const action = new Gio.SimpleAction({name: 'smoke', parameter_type: new GLib.VariantType('s')});
    action.connect('activate', (_action, parameter) => {
        try {
            const command = parameter.get_string()[0];
            if (command === 'probe') report();
            else if (command === 'drop-watch') { panel.http.abort(); report(); }
            else if (command === 'resume') { panel.resumeButton.emit('clicked'); report(); }
            else if (command === 'pause') { panel.pauseButton.emit('clicked'); report(); }
            else if (command === 'analyse') { panel.analysisButton.emit('clicked'); report(); }
            else if (command === 'retry-failed') { panel.retryAnalysisButton.emit('clicked'); report(); }
            else if (command === 'notes-tab') { panel.tabs.set_current_page(1); report(); }
            else if (command === 'history-tab') { panel.tabs.set_current_page(0); report(); }
            else if (command === 'new-dialog') { panel.newSession(); report(); }
            else if (command === 'new-save' || command === 'new-trash-save' || command === 'rename-save') {
                const dialog = app.get_windows().find(window => window instanceof Gtk.Dialog);
                const entry = descendants(dialog).find(widget => widget instanceof Gtk.Entry);
                entry.set_text(command === 'new-save' ? 'Synthetic episode B' :
                    command === 'new-trash-save' ? 'Synthetic trash record' : 'Synthetic episode B renamed');
                dialog.response(Gtk.ResponseType.OK); report();
            } else if (command === 'rename-dialog') { panel.renameSession(); report(); }
            else if (command === 'trash-dialog') { panel.trashSession(); report(); }
            else if (command === 'empty-trash') { panel.emptyTrashButton.emit('clicked'); report(); }
            else if (command === 'empty-trash-delay-get') { delayTrashGet = 500; panel.emptyTrash(); report(); }
            else if (command === 'empty-trash-delay-post') { delayTrashPost = 500; panel.emptyTrash(); report(); }
            else if (command === 'empty-trash-cancel' || command === 'empty-trash-confirm') {
                const dialog = app.get_windows().find(window => window instanceof Gtk.Dialog);
                dialog.response(command === 'empty-trash-confirm' ? Gtk.ResponseType.OK : Gtk.ResponseType.CANCEL); report();
            } else if (command.startsWith('select-id:')) {
                const sid = command.slice('select-id:'.length);
                panel.perform(() => panel.selectSession(sid)); report();
            }
            else if (command === 'trash-cancel' || command === 'trash-confirm') {
                const dialog = app.get_windows().find(window => window instanceof Gtk.Dialog);
                dialog.response(command === 'trash-confirm' ? Gtk.ResponseType.OK : Gtk.ResponseType.CANCEL); report();
            } else if (command === 'edit-dialog') { panel.editNote(panel.snapshot.notes[0]); report(); }
            else if (command === 'edit-save') {
                const dialog = app.get_windows().find(window => window instanceof Gtk.Dialog);
                const text = descendants(dialog).find(widget => widget instanceof Gtk.TextView);
                text.get_buffer().set_text('拍手を促す表現。自分で確認して編集。', -1);
                dialog.response(Gtk.ResponseType.OK); report();
            } else if (command === 'synthetic-layout') {
                realSnapshot = panel.snapshot;
                synthetic = JSON.parse(JSON.stringify(realSnapshot));
                synthetic.entries = Array.from({length: 24}, (_value, index) => syntheticEntry(index));
                synthetic.entry_count = 406;
                synthetic.current = [{source: 'SYNTHETIC CURRENT PANE', translation: '現在ペインの誤表示検出用'}];
                synthetic.analysis = {...synthetic.analysis, pending: 382, failed: 3, done: 21,
                    total: 406, note_count: 2, with_notes: 2, no_notes: 19, running: false, error: ''};
                panel.signatures = {}; panel.followLatest = true; panel.tabs.set_current_page(0);
                showSynthetic(); report();
            } else if (command === 'history-read-old') {
                panel.historyAdjustment.set_value(panel.historyAdjustment.get_upper() * 0.35);
                report();
            } else if (command === 'history-read-first') {
                const first = panel.historyCards.get(panel.historyOrder[0]).widget;
                panel.historyAdjustment.set_value(first.get_allocation().y + 10);
                report();
            } else if (command === 'synthetic-append' || command === 'synthetic-drop-append') {
                const index = synthetic.entry_count;
                if (command === 'synthetic-drop-append') synthetic.entries = synthetic.entries.slice(4);
                synthetic.entries.push(syntheticEntry(index));
                synthetic.entry_count++; synthetic.analysis.pending++;
                showSynthetic(); report();
            } else if (command === 'latest') { panel.latestButton.emit('clicked'); report(); }
            else if (command === 'synthetic-running') {
                synthetic.capturing = false; synthetic.resume_pending = false;
                synthetic.analysis = {...synthetic.analysis, resume_pending: false, running: true, stop_requested: false,
                    processed: 7, total_run: 24};
                showSynthetic(); report();
            } else if (command === 'synthetic-resume-pending') {
                synthetic.capturing = true; synthetic.resume_pending = true;
                synthetic.analysis.stop_requested = true; synthetic.analysis.resume_pending = true;
                showSynthetic(); report();
            } else if (command === 'synthetic-failed-only') {
                synthetic.capturing = false; synthetic.resume_pending = false;
                synthetic.analysis = {...synthetic.analysis, resume_pending: false, running: false, pending: 0, failed: 3,
                    done: 403, error: '合成例：解説に失敗しました。明示的に再試行できます。'};
                showSynthetic(); report();
            } else if (command === 'synthetic-no-notes') {
                synthetic.notes = []; synthetic.capturing = false; synthetic.resume_pending = false;
                synthetic.analysis = {...synthetic.analysis, resume_pending: false, running: false, pending: 0, failed: 0,
                    done: 406, with_notes: 0, no_notes: 406, note_count: 0, error: ''};
                showSynthetic(); panel.tabs.set_current_page(1); report();
            } else if (command === 'restore-real') {
                synthetic = null; panel.snapshot = realSnapshot; panel.signatures = {};
                panel.followLatest = true; panel.unreadCount = 0;
                realRender(realSnapshot); panel.poll(); report();
            } else if (command === 'close') panel.window.close();
            else throw new Error('unknown smoke command');
        } catch (error) { lastError = error.message; report(); }
    });
    app.add_action(action);
});
app.run([]);
