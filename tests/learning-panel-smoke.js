// Actual production GTK + companion + FastAPI fixture, isolated GNOME only.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup?version=3.0';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};
const APP_ID = 'io.github.amariichi.ScreenshotTranslatorLearning';
const ROOT = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent().get_path();

function assert(condition, message) { if (!condition) throw new Error(message); }
async function waitFor(predicate, message) {
    for (let index = 0; index < 160; index++) {
        if (await predicate()) return;
        await Scripting.sleep(75);
    }
    throw new Error(message);
}

export async function run() {
    const extension = Main.extensionManager.lookup('screenshot-translator-learning@amariichi');
    await waitFor(() => extension?.stateObj?._enabled, 'companion unavailable');
    const bridge = extension.stateObj;
    const settings = bridge._settings;
    Main.overview.hide();
    const directory = GLib.dir_make_tmp('learning-panel-smoke-XXXXXX');
    const reportPath = `${directory}/panel.json`;
    const port = 18032;
    const http = new Soup.Session({timeout: 5});
    const api = async (method, path, body = null) => {
        const message = Soup.Message.new(method, `http://127.0.0.1:${port}/api/learning${path}`);
        if (body !== null) message.set_request_body_from_bytes('application/json',
            new GLib.Bytes(new TextEncoder().encode(JSON.stringify(body))));
        const bytes = await new Promise((resolve, reject) => {
            http.send_and_read_async(message, 0, null, (session, result) => {
                try { resolve(session.send_and_read_finish(result)); } catch (error) { reject(error); }
            });
        });
        return {status: message.get_status(), body: JSON.parse(new TextDecoder().decode(bytes.get_data()))};
    };
    const server = Gio.Subprocess.new([`${ROOT}/.venv/bin/python`, `${ROOT}/tests/learning_fixture.py`,
        '--port', String(port), '--data-dir', `${directory}/data`], Gio.SubprocessFlags.NONE);
    let process = null;
    try {
        await waitFor(async () => { try { return (await api('GET', '/fixture-ready')).body.data_dir === `${directory}/data`; } catch (_) { return false; } }, 'fixture server unavailable');
        settings.set_string('backend-url', `http://127.0.0.1:${port}`);
        settings.set_string('session-id', '');
        settings.set_value('capture-rect', new GLib.Variant('ai', [15, 45, 200, 100]));
        settings.set_value('panel-rect', new GLib.Variant('ai', [650, 35, 490, 650]));
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        launcher.setenv('WAYLAND_DISPLAY', 'gnome-shell-test-display', true);
        launcher.setenv('GDK_BACKEND', 'wayland', true);
        launcher.setenv('GTK_A11Y', 'none', true);
        launcher.unsetenv('AT_SPI_BUS_ADDRESS');
        process = launcher.spawnv(['gjs', '-m', `${ROOT}/tests/learning-panel-driver.js`, reportPath]);
        await waitFor(() => bridge._panelWindow, 'production GTK window not tracked');
        const actions = Gio.DBusActionGroup.get(Gio.DBus.session, APP_ID, '/io/github/amariichi/ScreenshotTranslatorLearning');
        const command = name => actions.activate_action('smoke', new GLib.Variant('s', name));
        const probe = async () => {
            command('probe'); await Scripting.sleep(80);
            if (!GLib.file_test(reportPath, GLib.FileTest.EXISTS)) return {};
            const result = JSON.parse(new TextDecoder().decode(GLib.file_get_contents(reportPath)[1]));
            assert(!result.error, result.error);
            return result;
        };
        let state;
        await waitFor(async () => { state = await probe(); return state.snapshot?.id; }, 'panel did not create/load a session');
        assert(!state.snapshot.capturing, 'opening panel started capture');
        await Scripting.sleep(1000);
        const initial = bridge._panelWindow.get_frame_rect();
        console.log('Initial production panel geometry: ' + JSON.stringify({actual: [initial.x, initial.y, initial.width, initial.height],
            saved: settings.get_value('panel-rect').deep_unpack(),
            maximized: [bridge._panelWindow.maximized_horizontally, bridge._panelWindow.maximized_vertically]}));
        await waitFor(() => bridge._panelWindow.get_frame_rect().x >= 600, 'window placement not restored');
        command('resume'); command('resume');
        await waitFor(async () => { state = await probe(); return state.snapshot?.entry_count >= 2 && state.snapshot.capturing; }, 'resume did not show translated fixture');
        assert(!state.sensitive.analyse, 'analysis button enabled while capturing');
        GLib.file_set_contents(`${directory}/translation.png`, await bridge._captureArea([0, 0, 1280, 720]));
        const rejected = await api('POST', `/sessions/${state.sid}/analysis`, {});
        assert(rejected.status === 409, 'server allowed live explanation');
        const sid = state.sid;
        command('pause');
        await waitFor(async () => { state = await probe(); return state.snapshot && !state.snapshot.capturing; }, 'pause not reflected in UI');
        await waitFor(async () => { state = await probe(); return state.snapshot?.notes.length === 1 && !state.snapshot.analysis.running; }, 'automatic paused notes did not render');
        command('edit-dialog'); await Scripting.sleep(250);
        command('edit-save');
        await waitFor(async () => { state = await probe(); return state.snapshot?.notes[0]?.edited; }, 'editing draft through GTK dialog did not persist');
        assert(state.snapshot.notes[0].meaning_ja.includes('自分で確認'), 'edited meaning missing');
        const markdown = new TextDecoder().decode(GLib.file_get_contents(state.snapshot.markdown_path)[1]);
        assert(markdown.includes('自分で確認'), 'Markdown missing edited note');
        command('notes-tab'); await Scripting.sleep(250);
        // Capture only this synthetic test compositor, never the user desktop.
        const png = await bridge._captureArea([0, 0, 1280, 720]);
        GLib.file_set_contents(`${directory}/panel.png`, png);
        console.log(`Learning UI screenshot and private fixture records: ${directory}`);

        // Production GTK at a short height, with synthetic long subtitles/SMS and
        // an unbroken word. Only the driver's temporary view uses these entries.
        bridge._panelWindow.move_resize_frame(false, 650, 35, 490, 500);
        command('synthetic-layout');
        await waitFor(async () => { state = await probe(); return state.layout.rowCount === 24 &&
            state.layout.window[1] <= 510 && state.layout.page > 80 && state.layout.upper > state.layout.page * 3; },
        'short GTK layout did not allocate a useful history viewport');
        await Scripting.sleep(250); state = await probe();
        assert(!state.layout.currentPanePresent && !state.layout.personalMemoButtonPresent,
            'removed current pane or personal memo button is still present');
        assert(state.layout.history[1] >= 120, 'history remains cramped at 490x500');
        assert(state.layout.issues.length === 0, 'long history labels clipped: ' + JSON.stringify(state.layout.issues));
        assert(state.layout.footer[1] >= 0 && state.layout.footer[1] + state.layout.footer[3] <= state.layout.window[1] + 2,
            'record-open control is outside the short window');
        assert(state.summary === '履歴406件 / 学習メモ2件' && state.analysisText.includes('処理済み21') &&
            state.analysisText.includes('未処理382') && state.analysisText.includes('失敗3'), 'history/note/analysis counts are conflated');
        assert(state.layout.originals.every((texts, index) => texts.some(text => text.endsWith(`SOURCE_END_${index}`)) &&
            texts.some(text => text.endsWith(`訳終端${index}`))), 'long original/translation tail missing');
        await waitFor(async () => { state = await probe(); return state.layout.follow &&
            state.layout.upper - state.layout.page - state.layout.value < 3; }, 'initial newest-follow failed');
        command('synthetic-append');
        await waitFor(async () => { state = await probe(); return state.layout.rowCount === 25 &&
            state.layout.follow && state.layout.upper - state.layout.page - state.layout.value < 3; },
        'following reader did not follow the new subtitle');
        command('history-read-old'); await Scripting.sleep(200); state = await probe();
        assert(!state.layout.follow, 'reading older history did not suspend following');
        const oldValue = state.layout.value, oldAnchor = state.layout.anchor;
        command('synthetic-append'); await Scripting.sleep(250); state = await probe();
        assert(!state.layout.follow && state.layout.latestVisible && state.layout.unread >= 1 &&
            Math.abs(state.layout.value - oldValue) < 3 && state.layout.anchor.id === oldAnchor.id,
        'new subtitle moved a reader away from old history');
        command('synthetic-drop-append'); await Scripting.sleep(250); state = await probe();
        assert(!state.layout.follow && state.layout.anchor.id === oldAnchor.id &&
            Math.abs(state.layout.anchor.offset - oldAnchor.offset) < 3,
        'rolling history window lost the visible card anchor');
        command('history-read-first'); await Scripting.sleep(150); state = await probe();
        const firstAnchor = state.layout.anchor;
        command('synthetic-drop-append'); await Scripting.sleep(250); state = await probe();
        assert(!state.layout.follow && state.layout.anchor.id === firstAnchor.id &&
            Math.abs(state.layout.anchor.offset - firstAnchor.offset) < 3 &&
            !state.snapshot.entries.some(item => item.id === firstAnchor.id),
        'reading card was discarded when it left the API recent window');
        command('latest');
        await waitFor(async () => { state = await probe(); return state.layout.follow && !state.layout.latestVisible &&
            state.layout.unread === 0 && state.layout.rowCount === state.snapshot.entries.length &&
            state.layout.upper - state.layout.page - state.layout.value < 3; },
        'latest button did not restore following');
        GLib.file_set_contents(`${directory}/history-short.png`, await bridge._captureArea([0, 0, 1280, 720]));
        command('synthetic-running'); await Scripting.sleep(120); state = await probe();
        assert(state.sensitive.resume && !state.sensitive.trash && state.analysisText.includes('学習メモ生成中 7/24'),
            'analysis progress or resume control is incorrect');
        command('synthetic-resume-pending'); await Scripting.sleep(120); state = await probe();
        assert(state.status.includes('取得再開待ち'), 'pending resume was not explained');
        command('synthetic-failed-only'); await Scripting.sleep(120); state = await probe();
        assert(!state.sensitive.analyse && state.sensitive.retry && state.sensitive.retryVisible &&
            state.status.includes('エラー') && state.analysisText.includes('未処理0'), 'failed entries became automatic pending work');
        command('synthetic-no-notes'); await Scripting.sleep(120); state = await probe();
        assert(state.summary === `履歴${state.snapshot.entry_count}件 / 学習メモ0件` && !state.sensitive.analyse &&
            !state.sensitive.retryVisible, 'successful zero-note selection is shown as unfinished work');
        command('restore-real'); command('history-tab');
        await waitFor(async () => { state = await probe(); return state.snapshot?.id === sid && state.snapshot.entry_count < 100; },
            'real isolated fixture view was not restored');

        bridge._panelWindow.move_resize_frame(false, 15, 45, 490, 650);
        await Scripting.sleep(400);
        command('resume'); await Scripting.sleep(250);
        assert(!bridge._capturing, 'overlap allowed production panel self-capture');
        bridge._panelWindow.move_resize_frame(false, 640, 35, 520, 650);
        await Scripting.sleep(450);
        command('new-dialog'); await Scripting.sleep(250); command('new-save');
        await waitFor(async () => { state = await probe(); return state.snapshot?.title === 'Synthetic episode B'; }, 'new session dialog failed');
        assert(state.sid !== sid && state.snapshot.entry_count === 0, 'new session mixed histories');
        const renamedSid = state.sid, fixedFilename = state.snapshot.markdown_path;
        assert(state.layout.recordActions.some(text => text.includes('名前を変更')) &&
            state.layout.recordActions.includes('保存フォルダを開く') &&
            state.layout.recordActions.some(text => text.includes('削除')), 'record actions are missing');
        command('rename-dialog'); await Scripting.sleep(150); command('rename-save');
        await waitFor(async () => { state = await probe(); return state.snapshot?.title === 'Synthetic episode B renamed'; },
            'rename dialog did not update the selected record');
        assert(state.sid === renamedSid && state.snapshot.markdown_path === fixedFilename,
            'rename changed record identity or backing filename');
        command('new-dialog'); await Scripting.sleep(150); command('new-trash-save');
        await waitFor(async () => { state = await probe(); return state.snapshot?.title === 'Synthetic trash record'; },
            'private disposable record was not created');
        const disposable = state.snapshot;
        assert(disposable.markdown_path.startsWith(`${directory}/data/`), 'trash test escaped private fixture data');
        command('trash-dialog'); await Scripting.sleep(150); state = await probe();
        assert(state.layout.dialog?.text.includes(disposable.title) && state.layout.dialog.text.includes('復元') &&
            state.layout.dialog.text.includes('原文・訳・学習メモ'), 'trash confirmation lacks exact target or recoverability');
        command('trash-cancel'); await Scripting.sleep(150);
        assert((await api('GET', `/sessions/${disposable.id}`)).status === 200, 'cancel trashed the selected record');
        command('trash-dialog'); await Scripting.sleep(150); command('trash-confirm');
        await waitFor(async () => { state = await probe(); return state.sid === renamedSid &&
            state.snapshot?.title === 'Synthetic episode B renamed'; }, 'trash did not select the retained record');
        assert((await api('GET', `/sessions/${disposable.id}`)).status === 404, 'explicitly confirmed fixture record remains active');
        assert(!GLib.file_test(disposable.markdown_path, GLib.FileTest.EXISTS) &&
            GLib.file_test(disposable.trash_path, GLib.FileTest.IS_DIR), 'fixture record was not moved into recoverable trash');

        // Permanent cleanup is exercised only against this server's /tmp data.
        // The current record, original translated record, and a separate guard
        // record must remain active and keep their Markdown files.
        const trashPreview = await api('GET', '/trash');
        assert(trashPreview.status === 200 && trashPreview.body.path === `${directory}/data/.trash` &&
            trashPreview.body.count >= 1, 'trash test did not resolve its isolated archive root');
        const archiveFixture = async title => {
            const created = (await api('POST', '/sessions', {title})).body;
            assert(created.markdown_path.startsWith(`${directory}/data/`), 'fixture archive escaped private data');
            const archived = await api('POST', `/sessions/${created.id}/trash`, {confirm_title: created.title});
            assert(archived.status === 200, 'cannot prepare isolated archive');
            return created;
        };
        command('empty-trash');
        await waitFor(async () => { state = await probe(); return state.layout.dialog?.title === 'ゴミ箱を空にする'; },
            'empty-trash confirmation did not open');
        assert(state.layout.dialog.text.includes(`${trashPreview.body.count}件`) &&
            state.layout.dialog.text.includes('復元できません') && state.layout.dialog.text.includes('現在の記録は対象外') &&
            state.layout.dialog.defaultResponse === -6, 'permanent-delete confirmation is incomplete or not cancel-default');
        const postsBeforeCancel = state.trashCalls.post;
        command('empty-trash-cancel'); await Scripting.sleep(120); state = await probe();
        assert(state.trashCalls.post === postsBeforeCancel &&
            (await api('GET', '/trash')).body.count === trashPreview.body.count, 'cancel sent a destructive request');

        const independent = (await api('POST', '/sessions', {title: 'Synthetic independent guard'})).body;
        assert(independent.markdown_path.startsWith(`${directory}/data/`), 'guard record escaped fixture');
        let guardSequence = 0;
        const guardCapture = active => api('POST', `/sessions/${independent.id}/control`,
            {active, controller_id: 'private-trash-smoke', control_seq: ++guardSequence});
        await guardCapture(true);
        command('empty-trash');
        await waitFor(async () => { state = await probe(); return !state.trashPending && Boolean(state.uiError); },
            'busy preview was not reported');
        assert(!state.layout.dialog && state.status.includes('停止してから'), 'busy preview offered permanent deletion');
        await guardCapture(false);

        command('empty-trash');
        await waitFor(async () => { state = await probe(); return state.layout.dialog?.title === 'ゴミ箱を空にする'; },
            'confirmation after busy preview did not open');
        await guardCapture(true);
        command('empty-trash-confirm');
        await waitFor(async () => { state = await probe(); return !state.trashPending && Boolean(state.uiError); },
            'POST busy error was not shown');
        assert((await api('GET', '/trash')).body.count === trashPreview.body.count, 'busy POST changed archive contents');
        await guardCapture(false);

        command('empty-trash');
        await waitFor(async () => { state = await probe(); return state.layout.dialog?.title === 'ゴミ箱を空にする'; },
            'stale-token confirmation did not open');
        await archiveFixture('Synthetic archive added after confirmation');
        command('empty-trash-confirm');
        await waitFor(async () => { state = await probe(); return !state.trashPending && Boolean(state.uiError); },
            'changed-content error was not shown');
        const populatedTrash = (await api('GET', '/trash')).body;
        assert(populatedTrash.count === trashPreview.body.count + 1, 'stale confirmation deleted a new archive');

        command('empty-trash');
        await waitFor(async () => { state = await probe(); return state.layout.dialog?.title === 'ゴミ箱を空にする'; },
            'valid permanent-delete confirmation did not open');
        assert(state.layout.dialog.text.includes(`${populatedTrash.count}件`), 'confirmation used an old count');
        command('empty-trash-confirm');
        await waitFor(async () => { state = await probe(); return !state.trashPending &&
            state.uiInfo === `ゴミ箱を空にしました（${populatedTrash.count}件）`; }, 'permanent cleanup success was not shown');
        assert((await api('GET', '/trash')).body.count === 0, 'confirmed fixture trash was not emptied');
        for (const retained of [sid, renamedSid, independent.id])
            assert((await api('GET', `/sessions/${retained}`)).status === 200, 'empty trash removed an active/unrelated record');
        assert(GLib.file_test(fixedFilename, GLib.FileTest.EXISTS) &&
            GLib.file_test(independent.markdown_path, GLib.FileTest.EXISTS) &&
            GLib.file_test(disposable.directory_path, GLib.FileTest.IS_DIR), 'active record files were removed');
        const postsBeforeEmpty = state.trashCalls.post;
        command('empty-trash');
        await waitFor(async () => { state = await probe(); return !state.trashPending && state.uiInfo === 'ゴミ箱は空です'; },
            'empty-trash informational state was not shown');
        assert(!state.layout.dialog && state.trashCalls.post === postsBeforeEmpty, 'empty trash prompted or posted a deletion');

        command('empty-trash-delay-get');
        await waitFor(async () => { state = await probe(); return state.holdingTrashGet; }, 'delayed preview fixture did not hold');
        command(`select-id:${sid}`);
        await waitFor(async () => { state = await probe(); return state.snapshot?.id === sid && !state.holdingTrashGet; },
            'record switch during preview did not complete');
        assert(!state.layout.dialog && !state.uiInfo.includes('ゴミ箱'), 'old preview polluted the newly selected record');
        command(`select-id:${renamedSid}`);
        await waitFor(async () => { state = await probe(); return state.snapshot?.id === renamedSid; }, 'cannot return to retained record');
        await archiveFixture('Synthetic delayed-response archive');
        command('empty-trash-delay-post');
        await waitFor(async () => { state = await probe(); return state.layout.dialog?.title === 'ゴミ箱を空にする'; },
            'delayed-result confirmation did not open');
        command('empty-trash-confirm');
        await waitFor(async () => { state = await probe(); return state.holdingTrashPost; }, 'delayed cleanup fixture did not hold');
        command(`select-id:${sid}`);
        await waitFor(async () => { state = await probe(); return state.snapshot?.id === sid && !state.holdingTrashPost; },
            'record switch during cleanup did not complete');
        assert(!state.uiInfo.includes('ゴミ箱') && !state.layout.dialog, 'old cleanup result polluted another record');
        assert((await api('GET', '/trash')).body.count === 0 &&
            (await api('GET', `/sessions/${independent.id}`)).status === 200, 'delayed fixture cleanup damaged a retained record');
        command(`select-id:${renamedSid}`);
        await waitFor(async () => { state = await probe(); return state.snapshot?.id === renamedSid; }, 'cannot restore selection after cleanup');
        command('empty-trash-delay-get');
        await waitFor(async () => { state = await probe(); return state.holdingTrashGet; }, 'close-time preview fixture did not hold');
        command('close');
        await waitFor(() => !bridge._panelWindow, 'panel close not tracked');
        const savedRect = settings.get_value('panel-rect').deep_unpack();
        process = launcher.spawnv(['gjs', '-m', `${ROOT}/tests/learning-panel-driver.js`, reportPath]);
        await waitFor(() => bridge._panelWindow, 'panel reopen failed');
        await waitFor(() => Math.abs(bridge._panelWindow.get_frame_rect().x - savedRect[0]) < 4, 'geometry not restored after actual reopen');
        // Restore a maximized window too, using the actual current Mutter API.
        if (bridge._panelWindow.maximize.length === 0) bridge._panelWindow.maximize();
        else bridge._panelWindow.maximize(3);
        await Scripting.sleep(300);
        bridge._restorePanel(bridge._panelWindow, savedRect);
        await waitFor(() => !bridge._panelWindow.maximized_horizontally &&
            !bridge._panelWindow.maximized_vertically &&
            Math.abs(bridge._panelWindow.get_frame_rect().x - savedRect[0]) < 4,
        'maximized window not restored');
        await waitFor(async () => { state = await probe(); return state.snapshot?.title === 'Synthetic episode B renamed'; }, 'selected session not restored');
        command('close');
        await new Promise(resolve => process.wait_async(null, () => resolve()));
        process = null;
        console.log('Learning production GTK/FastAPI/GNOME integration passed (short/long history, scroll anchoring, progress, rename/trash/confirmed-empty)');
    } finally {
        process?.send_signal(15);
        server.send_signal(15);
        http.abort();
    }
}
