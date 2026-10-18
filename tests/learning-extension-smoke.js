// Isolated shell only. This test never captures the user's desktop or operates a video.
// Pack gnome-learning-extension/ and run:
// dbus-run-session gnome-shell-test-tool --headless \
//   --extra-filter io.github.amariichi.ScreenshotTranslatorLearning \
//   --extension /tmp/learning.shell-extension.zip "$PWD/tests/learning-extension-smoke.js"
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Soup from 'gi://Soup?version=3.0';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
import {ExtensionType} from 'resource:///org/gnome/shell/misc/extensionUtils.js';

export const METRICS = {};
const UUID = 'screenshot-translator-learning@amariichi';
const APP_ID = 'io.github.amariichi.ScreenshotTranslatorLearning';
const ROOT = Gio.File.new_for_uri(import.meta.url).get_parent().get_parent().get_path();

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

async function waitFor(predicate, message) {
    for (let i = 0; i < 160; i++) {
        if (predicate())
            return;
        await Scripting.sleep(50);
    }
    throw new Error(message);
}

function bodyText(message) {
    return new TextDecoder().decode(message.get_request_body().flatten().get_data());
}

function reply(message, data, status = 200) {
    message.set_status(status, null);
    message.set_response('application/json', Soup.MemoryUse.COPY, JSON.stringify(data));
}

export async function run() {
    printerr('Learning smoke run begins');
    const extension = Main.extensionManager.lookup(UUID);
    await waitFor(() => extension?.stateObj?._enabled, 'Learning companion did not enable');
    const app = extension.stateObj;
    const settings = app._settings;
    const originalSettings = Object.fromEntries(['backend-url', 'session-id', 'capture-rect', 'panel-rect']
        .map(key => [key, settings.get_value(key)]));
    Main.overview.hide();
    await Scripting.sleep(300);
    assert(app._indicator, 'Missing explicit learning indicator');
    assert(app._legacyItem.label.text === '従来モード' && app._learningItem.label.text === '学習モード',
        'Both mode menus are not present');
    assert(!app._legacy._indicator && !Main.panel.statusArea['screenshot-translator-indicator'],
        'Embedded legacy mode created a second indicator');
    assert(!app._capturing && !app._legacy._isMonitoring, 'Enabling started acquisition');
    const legacySettings = app._legacy._settings;
    const originalLegacyUrl = legacySettings.get_string('backend-url');

    const requests = [];
    const frames = [];
    const legacyUploads = [];
    let serverCapturing = false;
    let generation = 0;
    const controlSequences = new Map();
    let controller = '';
    let rejectFrames = 0;
    let busyFrames = 0;
    let holdFrame = false;
    let pendingFrame = null;
    let holdControl = false;
    let pendingControl = null;
    const server = new Soup.Server();
    server.add_handler(null, (_server, message) => {
        const route = message.get_uri().get_path().split('/').pop();
        requests.push(route);
        if (route === 'control') {
            const data = JSON.parse(bodyText(message));
            assert(data.controller_id && Number.isInteger(data.control_seq), 'Control lacks identity/sequence');
            if (data.control_seq > (controlSequences.get(data.controller_id) ?? -1) &&
                (!serverCapturing || controller === data.controller_id)) {
                controlSequences.set(data.controller_id, data.control_seq);
                generation++;
                serverCapturing = data.active;
                controller = data.controller_id;
            }
            if (holdControl && data.active) {
                pendingControl = {message, generation, capturing: serverCapturing};
                message.pause();
            } else {
                reply(message, {capturing: serverCapturing, generation});
            }
        } else if (route === 'heartbeat') {
            const data = JSON.parse(bodyText(message));
            assert(data.controller_id && Number.isInteger(data.generation), 'Heartbeat lacks identity');
            reply(message, {}, data.generation === generation && serverCapturing ? 200 : 409);
        } else if (route === 'frames') {
            const text = bodyText(message);
            const value = name => text.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
            const data = {generation: Number(value('generation')), sequence: Number(value('sequence')),
                marker: ['pending-B', 'pending-C'].find(marker => text.includes(marker)) ?? 'A'};
            assert(Number(value('captured_at')) > 1e9, 'Missing capture timestamp');
            assert(text.includes('name="image"; filename="frame.png"'), 'Missing multipart image');
            const bytes = message.get_request_body().flatten().get_data();
            assert(bytes.some((value, i) => value === 137 && bytes[i + 1] === 80 &&
                bytes[i + 2] === 78 && bytes[i + 3] === 71), 'Frame has no PNG bytes');
            frames.push(data);
            if (holdFrame) {
                pendingFrame = {message, data};
                message.pause();
            } else if (rejectFrames-- > 0) {
                reply(message, {error: 'temporary test failure'}, 503);
            } else {
                reply(message, {status: busyFrames-- > 0 ? 'busy' : 'ok', ...data});
            }
        } else if (['ocr_translate_with_grounding', 'ocr_translate_tts_once', 'monitor_update'].includes(route)) {
            assert(bodyText(message).includes('name="clean_image"'), 'Legacy upload lost original contract');
            legacyUploads.push(route);
            reply(message, {ja_translation: '統合メニューの従来モードです。'});
        } else {
            reply(message, {error: 'unexpected route'}, 404);
        }
    });
    server.listen_local(0, Soup.ServerListenOptions.IPV4_ONLY);
    let panelProcess = null;
    try {
        settings.set_string('backend-url', server.get_uris()[0].to_string().replace(/\/$/, ''));
        settings.set_string('session-id', 'smoke-session');
        settings.set_value('capture-rect', new GLib.Variant('ai', [20, 50, 200, 100]));
        settings.set_value('panel-rect', new GLib.Variant('ai', [650, 160, 420, 420]));
        await Scripting.sleep(200);

        // Never start without the own GTK panel, even with a stored valid capture rectangle.
        await app._resume();
        assert(!app._capturing, 'Capture started without its panel');
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        // Mutter does not overwrite the inherited WAYLAND_DISPLAY for child processes.
        // Pin the fixture to this test compositor, never the user's display.
        launcher.setenv('WAYLAND_DISPLAY', 'gnome-shell-test-display', true);
        launcher.setenv('GDK_BACKEND', 'wayland', true);
        launcher.setenv('GTK_A11Y', 'none', true);
        launcher.unsetenv('AT_SPI_BUS_ADDRESS');
        panelProcess = launcher.spawnv(['gjs', '-c', `
            imports.gi.versions.Gtk = '4.0';
            const {Gtk} = imports.gi;
            const app = new Gtk.Application({application_id: '${APP_ID}'});
            app.connect('activate', () => {
                const w = new Gtk.ApplicationWindow({application: app, title: 'Learning smoke fixture',
                    default_width: 420, default_height: 420});
                w.set_child(new Gtk.Label({label: 'Synthetic local test panel'}));
                w.present();
            });
            app.run([]);
        `]);
        await waitFor(() => app._panelWindow, 'Own GTK Meta.Window was not detected');
        await waitFor(() => app._panelWindow.get_frame_rect().x >= 500,
            'Panel placement was not restored independently');
        await Scripting.sleep(300);

        await app._startSelection();
        assert(app._selectionArea.visible && app._selectionArea._grab, 'Modal range selector did not open');
        app._selectionArea.cancel();
        assert(!app._selectionArea.visible && !app._selectionArea._grab, 'Range selector did not release modal input');
        const png = await app._captureArea([20, 50, 200, 100]);
        assert(png[0] === 137 && png[1] === 80 && png[2] === 78 && png[3] === 71, 'Actual capture is not PNG');
        const dimensions = new DataView(png.buffer, png.byteOffset, png.byteLength);
        assert(dimensions.getUint32(16) === 200 && dimensions.getUint32(20) === 100,
            'Actual PNG has wrong logical dimensions');

        // Pin an already captured synthetic compositor image for deterministic dedup/race cases.
        // The normal capture function itself was verified immediately above.
        const realCapture = app._captureArea.bind(app);
        app._captureArea = async () => png;
        await app._resume();
        await waitFor(() => frames.length >= 1 && !app._frameBusy, 'First frame did not complete');
        const afterFirst = frames.length;
        await app._captureTick();
        assert(frames.length === afterFirst, 'Unchanged accepted image was uploaded twice');
        const heartbeatCount = requests.filter(route => route === 'heartbeat').length;
        await waitFor(() => requests.filter(route => route === 'heartbeat').length > heartbeatCount,
            'Stable-image skipping did not keep the lease alive');
        await app._pause('test pause');
        assert(!app._capturing && !serverCapturing, 'Pause did not stop both sides');

        // Inference remains serial, while capture replaces exactly one pending image.
        const tagged = marker => new Uint8Array([...png, ...new TextEncoder().encode(marker)]);
        holdFrame = true;
        const beforePending = frames.length;
        await app._resume();
        await waitFor(() => pendingFrame, 'First held image did not reach server');
        app._captureArea = async () => tagged('pending-B');
        await app._captureTick();
        const pendingB = app._pendingFrame;
        assert(pendingB && frames.length === beforePending + 1, 'Capture did not queue behind serial inference');
        app._captureArea = async () => tagged('pending-C');
        await app._captureTick();
        assert(app._pendingFrame && app._pendingFrame !== pendingB && frames.length === beforePending + 1,
            'Pending image was not replaced, or a second request escaped');
        holdFrame = false;
        reply(pendingFrame.message, {status: 'ok', ...pendingFrame.data});
        pendingFrame.message.unpause(); pendingFrame = null;
        await waitFor(() => frames.length === beforePending + 2 && !app._frameBusy,
            'Newest pending image was not drained immediately');
        assert(frames.at(-1).marker === 'pending-C' && !app._pendingFrame,
            'Obsolete pending image was uploaded');
        await app._pause('pending case complete');

        // A(in flight) -> B(pending) -> A(latest) removes obsolete B.
        app._captureArea = async () => png;
        holdFrame = true;
        await app._resume();
        await waitFor(() => pendingFrame, 'Held A did not reach server');
        app._captureArea = async () => tagged('pending-B'); await app._captureTick();
        app._captureArea = async () => png; await app._captureTick();
        assert(!app._pendingFrame, 'Returning to in-flight A retained obsolete B');
        await app._pause('invalidate queued image');
        assert(!app._pendingFrame && !app._captureBusy, 'Stop retained a pending/capturing image');
        holdFrame = false;
        reply(pendingFrame.message, {status: 'ok', ...pendingFrame.data});
        pendingFrame.message.unpause(); pendingFrame = null;

        // A accepted -> B in flight -> A latest must still queue A.
        await app._resume();
        await waitFor(() => app._lastHash && !app._frameBusy, 'Accepted A missing');
        holdFrame = true;
        app._captureArea = async () => tagged('pending-B');
        const sendingB = app._captureTick();
        await waitFor(() => pendingFrame, 'Held B did not reach server');
        app._captureArea = async () => png; await app._captureTick();
        assert(app._pendingFrame?.hash === app._lastHash, 'A was incorrectly skipped using accepted hash');
        holdFrame = false;
        reply(pendingFrame.message, {status: 'ok', ...pendingFrame.data});
        pendingFrame.message.unpause(); pendingFrame = null;
        await sendingB;
        await waitFor(() => !app._frameBusy && frames.at(-1).marker === 'A', 'Newest A was not restored');
        await app._pause('serial pending cases complete');

        // An uncancellable screenshot callback must not enqueue into a changed range.
        let releaseCapture = null;
        app._captureArea = () => new Promise(resolve => { releaseCapture = resolve; });
        const beforeLateCapture = frames.length;
        await app._resume();
        await waitFor(() => releaseCapture, 'Slow screenshot was not started');
        settings.set_value('capture-rect', new GLib.Variant('ai', [25, 50, 200, 100]));
        await waitFor(() => !app._capturing, 'Range change did not invalidate capture');
        releaseCapture(png);
        await Scripting.sleep(100);
        assert(!app._pendingFrame && !app._captureBusy && frames.length === beforeLateCapture,
            'Old screenshot callback queued/sent an image after range change');
        settings.set_value('capture-rect', new GLib.Variant('ai', [20, 50, 200, 100]));
        app._captureArea = async () => png;

        // Changing records discards a queued image as well as the old HTTP callback.
        holdFrame = true;
        await app._resume();
        await waitFor(() => pendingFrame, 'Session-change held request missing');
        app._captureArea = async () => tagged('pending-C'); await app._captureTick();
        assert(app._pendingFrame, 'Session-change fixture did not queue an image');
        settings.set_string('session-id', 'different-smoke-session');
        await waitFor(() => !app._capturing, 'Session change did not stop capture');
        holdFrame = false;
        reply(pendingFrame.message, {status: 'ok', ...pendingFrame.data});
        pendingFrame.message.unpause(); pendingFrame = null;
        await Scripting.sleep(100);
        assert(!app._pendingFrame && !app._lastHash, 'Old session response or queued image survived');
        settings.set_string('session-id', 'smoke-session');
        app._captureArea = async () => png;

        // Failure and busy responses must retry the same image, rather than caching it.
        rejectFrames = 1;
        busyFrames = 1;
        const beforeRetry = frames.length;
        await app._resume();
        await waitFor(() => frames.length > beforeRetry && !app._frameBusy, 'Failure fixture did not run');
        assert(!app._lastHash, 'Failed image was marked accepted');
        await app._captureTick();
        assert(!app._lastHash, 'Busy image was marked accepted');
        await app._captureTick();
        assert(app._lastHash && frames.length >= beforeRetry + 3, 'Same image was not retried after failure/busy');
        await app._pause('test pause');

        // Repeated command id is idempotent; rapid resume/pause ignores a late active result.
        settings.set_string('control-request', JSON.stringify({id: 'repeat', action: 'pause', session_id: 'smoke-session'}));
        await Scripting.sleep(100);
        const controls = requests.filter(route => route === 'control').length;
        app._onCommand();
        await Scripting.sleep(100);
        assert(requests.filter(route => route === 'control').length === controls, 'Command id replay was not ignored');
        holdControl = true;
        const resume = app._resume();
        await waitFor(() => pendingControl, 'Delayed control did not reach server');
        await app._pause('pause after pending resume');
        holdControl = false;
        reply(pendingControl.message, {capturing: pendingControl.capturing, generation: pendingControl.generation});
        pendingControl.message.unpause();
        await resume;
        pendingControl = null;
        assert(!app._capturing && !serverCapturing, 'Old resume response restarted capture');

        // Pause while a frame is pending cancels it; late callback cannot restart or cache it.
        holdFrame = true;
        await app._resume();
        await waitFor(() => pendingFrame, 'Delayed frame did not reach server');
        await app._pause('pause pending frame');
        holdFrame = false;
        reply(pendingFrame.message, {status: 'ok', ...pendingFrame.data});
        pendingFrame.message.unpause();
        pendingFrame = null;
        await Scripting.sleep(200);
        assert(!app._capturing && !app._lastHash, 'Late frame response changed stopped state/cache');

        // Real Meta.Window movement pauses immediately, persists geometry and blocks further starts.
        await app._resume();
        await waitFor(() => app._capturing, 'Resume before overlap failed');
        app._panelWindow.move_resize_frame(false, 20, 50, 420, 420);
        await waitFor(() => !app._capturing, 'Moving panel into ROI did not pause');
        await Scripting.sleep(400);
        assert(JSON.parse(settings.get_string('bridge-state')).overlap, 'Overlap was not exposed to panel');
        const saved = settings.get_value('panel-rect').deep_unpack();
        assert(saved[0] === 20, 'Moved panel geometry was not persisted');
        await app._resume();
        assert(!app._capturing, 'Overlapping panel was allowed to self-capture');
        app._panelWindow.move_resize_frame(false, 650, 160, 440, 430);
        await Scripting.sleep(400);
        assert(settings.get_value('panel-rect').deep_unpack()[2] >= 420, 'Resize was not persisted');

        settings.set_string('backend-url', 'https://example.com');
        assert(!app._base && !app._capturing, 'Remote image backend was accepted');
        settings.set_string('backend-url', server.get_uris()[0].to_string().replace(/\/$/, ''));
        await app._resume();
        await waitFor(() => app._capturing, 'Local backend could not resume');
        app._captureArea = realCapture;
        app.disable();
        await waitFor(() => !serverCapturing, 'Disable did not send best-effort pause');
        assert(!app._captureTimer && !app._stateTimer && !app._geometryTimer &&
            !app._selectionArea && !app._indicator && !app._http, 'Disable left bridge resources alive');
        app.enable();
        assert(app._enabled && !app._capturing, 'Re-enable replayed old capture command');
        legacySettings.set_string('backend-url', server.get_uris()[0].to_string() + 'api/v1/ocr_translate_with_grounding');
        const legacy = app._legacy;
        assert(legacy._settings.get_string('backend-url') === legacySettings.get_string('backend-url'),
            'Unified mode did not retain the legacy settings path');
        await legacy._startSelection();
        assert(legacy._selectionArea.visible && !app._capturing, 'Legacy selector did not stop learning');
        legacy._selectionArea.cancel();
        await legacy._takeScreenshot(20, 50, 200, 100);
        assert(legacyUploads.includes('ocr_translate_with_grounding') && legacy._resultBox,
            'Embedded overlay did not translate/display');
        legacy._setMode('tts-once');
        await legacy._takeTtsOnceScreenshot(20, 50, 200, 100);
        assert(legacyUploads.includes('ocr_translate_tts_once'), 'Embedded TTS Once failed');
        legacy._setMode('monitor');
        legacy._startMonitoring(20, 50, 200, 100);
        await waitFor(() => legacyUploads.includes('monitor_update'), 'Embedded TTS monitor failed');
        await app._resume();
        assert(!legacy._isMonitoring && !legacy._monitorTimeoutId && !legacy._resultBox,
            'Switching to learning left legacy capture/result alive');
        await waitFor(() => app._capturing, 'Learning did not resume after legacy');
        await legacy._startSelection();
        assert(!app._capturing && !serverCapturing && legacy._selectionArea.visible,
            'Switching back to legacy left learning capture alive');
        legacy._selectionArea.cancel();
        await app._stopAll();
        assert(!app._capturing && !legacy._isMonitoring, 'Stop All left a capture loop');

        app._indicator.menu.open();
        app._learningItem.menu.open();
        await Scripting.sleep(150);
        GLib.file_set_contents(`${ROOT}/.agent/icon-unify-20261017/unified-learning-menu-synthetic.png`,
            await app._captureArea([0, 0, 1280, 720]));
        app._learningItem.menu.close();
        app._legacyItem.menu.open();
        await Scripting.sleep(150);
        GLib.file_set_contents(`${ROOT}/.agent/icon-unify-20261017/unified-legacy-menu-synthetic.png`,
            await app._captureArea([0, 0, 1280, 720]));
        app._indicator.menu.close();

        // Related legacy extension is deliberately enabled first, then the unified
        // host migrates it to one icon without changing any unrelated extension.
        Main.extensionManager.disableExtension(UUID);
        await waitFor(() => !app._enabled, 'Manager did not disable unified host');
        const oldPath = `${GLib.dir_make_tmp('translator-legacy-fixture-XXXXXX')}/screenshot-translator@amariichi`;
        GLib.mkdir_with_parents(`${oldPath}/schemas`, 0o700);
        for (const filename of ['extension.js', 'legacy-capture.js', 'metadata.json', 'stylesheet.css',
            'schemas/gschemas.compiled'])
            GLib.file_set_contents(`${oldPath}/${filename}`,
                GLib.file_get_contents(`${ROOT}/gnome-extension/${filename}`)[1]);
        const old = Main.extensionManager.createExtensionObject('screenshot-translator@amariichi',
            Gio.File.new_for_path(oldPath), ExtensionType.PER_USER);
        await Main.extensionManager.loadExtension(old);
        Main.extensionManager.enableExtension(old.uuid);
        await waitFor(() => old.stateObj?._enabled, 'Standalone legacy fixture did not enable');
        Main.extensionManager.enableExtension(UUID);
        await waitFor(() => app._enabled && !old.stateObj._enabled, 'Related legacy UUID was not migrated');
        assert(!Main.panel.statusArea['screenshot-translator-indicator'] && app._indicator,
            'Migration left two translator indicators');
        assert(!app._capturing && !app._legacy._isMonitoring, 'Migration auto-started capture');
        console.log('Unified one-icon menus/legacy Overlay+TTS/handoff/stop-default/migration checks passed');
        console.log('Learning companion: actual PNG/control/lease/dedup/retry/busy/races/overlap/geometry/disable passed');
    } finally {
        if (pendingFrame) {
            reply(pendingFrame.message, {status: 'stale', ...pendingFrame.data});
            pendingFrame.message.unpause();
        }
        if (pendingControl) {
            reply(pendingControl.message, {capturing: false, generation});
            pendingControl.message.unpause();
        }
        if (app._enabled) {
            await app._pause('smoke complete');
            app.disable();
            await Scripting.sleep(100);
        }
        legacySettings.set_string('backend-url', originalLegacyUrl);
        panelProcess?.force_exit();
        // Restore isolated settings with listeners disconnected; never send a stop
        // for this synthetic session to an unrelated localhost server.
        for (const [key, value] of Object.entries(originalSettings))
            settings.set_value(key, value);
        server.disconnect();
    }
}
