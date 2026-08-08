using System;
using System.Drawing;
using System.Windows.Forms;

namespace OverlayClient;

/// <summary>
/// Minimal tray icon using WinForms NotifyIcon.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    private readonly NotifyIcon _icon;
    private readonly Action _onQuit;
    private readonly Action _onToggleEnabled;
    private readonly Action _onShowTestOverlay;
    private readonly Action<bool> _onToggleSpeech;
    private readonly bool _speechEnabled;

    public TrayIcon(
        Action onQuit,
        Action onToggleEnabled,
        Action onShowTestOverlay,
        Action<bool> onToggleSpeech,
        bool speechEnabled)
    {
        _onQuit = onQuit;
        _onToggleEnabled = onToggleEnabled;
        _onShowTestOverlay = onShowTestOverlay;
        _onToggleSpeech = onToggleSpeech;
        _speechEnabled = speechEnabled;

        _icon = new NotifyIcon
        {
            Text = "Screenshot Translator Overlay",
            Icon = SystemIcons.Application,
            Visible = true,
            ContextMenuStrip = BuildMenu()
        };
    }

    private ContextMenuStrip BuildMenu()
    {
        var menu = new ContextMenuStrip();

        var toggle = new ToolStripMenuItem("Enable/Disable Gesture", null, (_, __) => _onToggleEnabled());

        // Checked rather than a separate mode: the overlay is the point, and
        // speech is something you add on top of it, never instead of it.
        var speak = new ToolStripMenuItem("Speak Translation")
        {
            CheckOnClick = true,
            Checked = _speechEnabled
        };
        speak.CheckedChanged += (sender, __) => _onToggleSpeech(((ToolStripMenuItem)sender!).Checked);

        var test = new ToolStripMenuItem("Show Test Overlay", null, (_, __) => _onShowTestOverlay());
        var quit = new ToolStripMenuItem("Quit", null, (_, __) => _onQuit());

        menu.Items.Add(toggle);
        menu.Items.Add(speak);
        menu.Items.Add(test);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(quit);

        return menu;
    }

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }

    public void ShowBalloon(string message, int timeoutMs = 3000)
    {
        _icon.BalloonTipTitle = "Screenshot Translator";
        _icon.BalloonTipText = message;
        _icon.ShowBalloonTip(timeoutMs);
    }
}
