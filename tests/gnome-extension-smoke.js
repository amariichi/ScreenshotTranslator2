// Run in an isolated GNOME 50 session, never the user's current desktop:
// dbus-run-session gnome-shell-test-tool --headless \
//   --extension /path/to/screenshot-translator@amariichi.shell-extension.zip \
//   "$PWD/tests/gnome-extension-smoke.js"
import Clutter from 'gi://Clutter';
import Soup from 'gi://Soup?version=3.0';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

export const METRICS = {};

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

async function waitFor(predicate, message) {
    for (let i = 0; i < 100; i++) {
        if (predicate())
            return;
        await Scripting.sleep(50);
    }
    throw new Error(message);
}

export async function run() {
    const extension = Main.extensionManager.lookup('screenshot-translator@amariichi');
    await waitFor(() => extension?.stateObj?._enabled, 'Extension did not enable');
    const app = extension.stateObj;
    assert(app._indicator, 'Panel indicator is missing');
    Main.overview.hide();
    await Scripting.sleep(200);

    // Exercise real modal input and both cancellation routes.
    app._startSelection();
    assert(app._selectionArea.visible && app._selectionArea._grab, 'Selection did not grab input');
    app._selectionArea._onKeyPress(null, { get_key_symbol: () => Clutter.KEY_Escape });
    assert(!app._selectionArea.visible && !app._selectionArea._grab, 'Selection did not cancel');
    app._startSelection();
    app._selectionArea._onButtonPress(null, { get_button: () => Clutter.BUTTON_SECONDARY });
    assert(!app._selectionArea.visible && !app._selectionArea._grab, 'Right-click did not cancel');

    // A real screenshot must be a PNG of the selected size, not an empty file.
    const png = await app._captureArea(20, 30, 240, 120);
    assert(png[0] === 137 && png[1] === 80 && png[2] === 78 && png[3] === 71, 'Capture is not PNG');
    const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
    assert(view.getUint32(16) === 240 && view.getUint32(20) === 120, 'Wrong PNG dimensions');

    // Local HTTP fixture: no LLM, OCR or audio playback is needed for this test.
    const uploads = [];
    const server = new Soup.Server();
    server.add_handler(null, (serverObject, message) => {
        const body = message.get_request_body().flatten().get_data();
        const decoded = new TextDecoder().decode(body);
        assert(decoded.includes('name="clean_image"'), 'Multipart screenshot is missing');
        assert(decoded.includes('name="options"'), 'Multipart options are missing');
        assert(body.some((value, i) => value === 137 && body[i + 1] === 80 &&
            body[i + 2] === 78 && body[i + 3] === 71), 'Upload has no PNG bytes');
        uploads.push(message.get_uri().get_path());
        message.set_status(200, null);
        message.set_response('application/json', Soup.MemoryUse.COPY,
            JSON.stringify({ ja_translation: 'GNOME 50での翻訳テストです。' }));
    });
    server.listen_local(0, Soup.ServerListenOptions.IPV4_ONLY);
    const originalUrl = app._settings.get_string('backend-url');
    app._settings.set_string('backend-url', server.get_uris()[0].to_string() + 'api/v1/ocr_translate_with_grounding');
    try {
        await app._takeScreenshot(20, 30, 240, 120);
        assert(uploads.length === 1 && uploads[0].endsWith('ocr_translate_with_grounding'),
            'Overlay did not upload exactly once to the configured backend');
        assert(app._resultBox?.visible, 'Translation result is missing');
        const scrollView = app._resultBox.get_last_child();
        const label = scrollView.get_child().get_first_child();
        assert(label.text === 'GNOME 50での翻訳テストです。', 'Response was not displayed');
        app._resultBox.get_first_child().get_last_child().emit('clicked', Clutter.BUTTON_PRIMARY);
        assert(!app._resultBox, 'Close button did not dismiss the result');

        // Verify all capture modes send valid PNGs through the same finish path.
        try {
            await app._takeTtsOnceScreenshot(20, 30, 240, 120);
            assert(uploads.length === 2 && uploads[1].endsWith('ocr_translate_tts_once'),
                'TTS Once did not upload to the configured backend');
            app._startMonitoring(20, 30, 240, 120);
            await waitFor(() => uploads.length === 3 && !app._isProcessing, 'Monitor did not upload');
            assert(uploads[2].endsWith('monitor_update'), 'Monitor used the wrong endpoint');
            app._stopMonitoring();
            assert(!app._monitorTimeoutId && !app._isMonitoring, 'Monitor did not stop');
        } finally {
            app._stopMonitoring();
        }

        // Disabling while a response is pending must not recreate shell actors.
        let pending;
        server.add_handler('/pending', (serverObject, message) => {
            message.pause();
            pending = message;
        });
        app._settings.set_string('backend-url', server.get_uris()[0].to_string() + 'pending');
        const work = app._takeScreenshot(20, 30, 240, 120);
        await waitFor(() => pending, 'Pending request did not reach server');
        app._startSelection();
        app.disable();
        pending.set_status(200, null);
        pending.set_response('application/json', Soup.MemoryUse.COPY, '{"ja_translation":"late response"}');
        pending.unpause();
        await work;
        assert(!app._resultBox && !app._selectionArea && !app._httpSession, 'Disable left resources alive');
        app.enable();
        assert(app._indicator && app._enabled, 'Re-enable failed');
    } finally {
        app._settings?.set_string('backend-url', originalUrl);
        server.disconnect();
    }
    console.log('Screenshot Translator: GNOME runtime smoke checks passed');
}
