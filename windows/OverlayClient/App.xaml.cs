using System;
using System.IO;
using System.Text.Json;
using System.Windows;

namespace OverlayClient;

public partial class App : Application
{
    public AppSettings Settings { get; private set; } = AppSettings.CreateDefault();
    private TrayIcon? _tray;
    private GestureCoordinator? _gesture;
    private ApiClient? _apiClient;
    private OverlayManager? _overlayManager;
    private TranslationService? _translation;
    private DebugLogger? _logger;

    protected override void OnStartup(StartupEventArgs e)
    {
        Win32Helpers.TryEnablePerMonitorDpiAwareness();
        base.OnStartup(e);

        // Load settings.json next to exe (if exists)
        try
        {
            var path = Path.Combine(AppContext.BaseDirectory, "settings.json");
            if (File.Exists(path))
                Settings = AppSettings.Load(path);
        }
        catch
        {
            // Keep default settings if parsing fails
        }

        _overlayManager = new OverlayManager(Settings);
        _apiClient = new ApiClient();
        _logger = new DebugLogger(Settings);
        _translation = new TranslationService(Settings, _apiClient, _overlayManager, _logger);
        _gesture = new GestureCoordinator(Settings, _translation, _logger);

        _tray = new TrayIcon(
            onQuit: () => Shutdown(),
            onToggleEnabled: ToggleGesture,
            onShowTestOverlay: ShowTestOverlay,
            onToggleSpeech: ToggleSpeech,
            speechEnabled: Settings.Speech.Enabled
        );

        _gesture.Start();
        _ = CheckHealthAsync();
    }

    private void ShowTestOverlay()
    {
        var win = new OverlayWindow(Settings);
        win.SetContent(
            status: "Test",
            text: "これはテスト表示です。横は折り返し、縦はスクロールします。\n" +
                  "Copy/Closeを確認してください。"
        );
        win.Left = 200;
        win.Top = 200;
        win.Width = 500;
        win.Height = 260;
        win.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _gesture?.Dispose();
        _apiClient?.Dispose();
        _tray?.Dispose();
        base.OnExit(e);
    }

    private async System.Threading.Tasks.Task CheckHealthAsync()
    {
        if (_apiClient == null)
            return;

        using var cts = new System.Threading.CancellationTokenSource(System.TimeSpan.FromSeconds(3));
        var ok = await _apiClient.CheckHealthAsync(Settings.Server, cts.Token).ConfigureAwait(false);
        if (!ok)
        {
            _tray?.ShowBalloon("WSL server is not responding. Start FastAPI (port 8012).");
        }
    }

    private void ToggleGesture()
    {
        Settings.Gesture.Enabled = !Settings.Gesture.Enabled;
        var status = Settings.Gesture.Enabled ? "Gesture enabled." : "Gesture disabled.";
        _tray?.ShowBalloon(status);
    }

    // Runtime only, like ToggleGesture: settings.json stays the startup default.
    private void ToggleSpeech(bool enabled)
    {
        Settings.Speech.Enabled = enabled;
        _tray?.ShowBalloon(enabled ? "Speak translation: on." : "Speak translation: off.");
    }
}
