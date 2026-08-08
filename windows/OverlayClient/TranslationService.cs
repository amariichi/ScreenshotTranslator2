using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;

namespace OverlayClient;

public sealed class TranslationService
{
    private readonly AppSettings _settings;
    private readonly ApiClient _apiClient;
    private readonly OverlayManager _overlayManager;
    private readonly DebugLogger _logger;
    private int _inFlight;

    public TranslationService(AppSettings settings, ApiClient apiClient, OverlayManager overlayManager, DebugLogger logger)
    {
        _settings = settings;
        _apiClient = apiClient;
        _overlayManager = overlayManager;
        _logger = logger;
    }

    public void HandleRoi(RectInt roi)
    {
        if (Interlocked.Exchange(ref _inFlight, 1) == 1)
            return;

        _ = Task.Run(async () =>
        {
            try
            {
                await ExecuteAsync(roi).ConfigureAwait(false);
            }
            finally
            {
                Interlocked.Exchange(ref _inFlight, 0);
            }
        });
    }

    private async Task ExecuteAsync(RectInt roi)
    {
        if (_logger.Enabled)
            _logger.Log($"ROI computed: x={roi.X}, y={roi.Y}, w={roi.Width}, h={roi.Height}");

        Application.Current.Dispatcher.Invoke(() => _overlayManager.ShowDebugRoi(roi));

        byte[] cleanPng = ScreenCapture.CaptureRectToPngBytes(roi);

        if (_settings.Logging.DebugSaveImages)
        {
            try
            {
                Directory.CreateDirectory(_settings.Logging.DebugImageDir);
                var stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss_fff");
                File.WriteAllBytes(Path.Combine(_settings.Logging.DebugImageDir, $"clean_{stamp}.png"), cleanPng);
            }
            catch
            {
                // ignore debug save failures
            }
        }

        InferenceResult result = new() { Error = "not_started" };
        int maxAttempts = _settings.Server.Retry.Enabled ? Math.Max(1, _settings.Server.Retry.MaxAttempts) : 1;
        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(_settings.Server.RequestTimeoutSec));
            result = await _apiClient.RequestInferenceAsync(_settings.Server, cleanPng, cts.Token).ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(result.Error))
                break;
            _logger.Log($"Inference attempt {attempt} failed: {result.Error}");
            if (attempt < maxAttempts)
                await Task.Delay(_settings.Server.Retry.BackoffMs).ConfigureAwait(false);
        }

        var display = BuildDisplay(result, roi);

        Application.Current.Dispatcher.Invoke(() =>
        {
            _overlayManager.ShowOverlay(display.Bbox, display.Status, display.Text);
        });

        await SpeakDisplayedTextAsync(result, display).ConfigureAwait(false);
    }

    /// <summary>
    /// Reads out the same string the overlay just showed. BuildDisplay picks it
    /// using settings the backend cannot see, so the text has to travel back
    /// rather than be chosen there, or the two disagree on every fallback path.
    /// </summary>
    private async Task SpeakDisplayedTextAsync(InferenceResult result, DisplayPayload display)
    {
        if (!_settings.Speech.Enabled)
            return;
        // A null Parsed means BuildDisplay fell back to the raw response body;
        // reading that aloud is noise, not a translation.
        if (result.Parsed == null || string.IsNullOrWhiteSpace(display.Text))
            return;

        // SpeakAsync owns the deadline (speech.timeout_sec); no second one here.
        var error = await _apiClient.SpeakAsync(_settings, display.Text, CancellationToken.None).ConfigureAwait(false);
        if (error != null)
            _logger.Log($"Speak request failed: {error}");
    }

    private DisplayPayload BuildDisplay(InferenceResult result, RectInt roi)
    {
        if (result.Parsed == null)
        {
            var text = string.IsNullOrWhiteSpace(result.Raw) ? "Translation failed." : result.Raw;
            return new DisplayPayload(roi, "Fallback", text);
        }

        var parsed = result.Parsed;
        if (_settings.Fallback.ForceUseRoi)
        {
            string roiText = parsed.JaTranslation ?? string.Empty;
            if (_settings.Fallback.PreferRoiFallbackFromServer && parsed.RoiFallback?.JaTranslation is { Length: > 0 } roiFallback)
                roiText = roiFallback;
            return new DisplayPayload(roi, "ROI", roiText);
        }
        if (parsed.TargetBbox == null && !string.IsNullOrWhiteSpace(parsed.JaTranslation))
            return new DisplayPayload(roi, "OK", parsed.JaTranslation);

        bool confidenceOk = !parsed.Confidence.HasValue || parsed.Confidence.Value >= _settings.Fallback.ConfidenceThreshold;
        bool bboxOk = TryValidateBbox(parsed.TargetBbox, roi, out var bboxScreen);

        if (confidenceOk && bboxOk)
        {
            return new DisplayPayload(bboxScreen, "OK", parsed.JaTranslation ?? string.Empty);
        }

        string fallbackText = parsed.JaTranslation ?? string.Empty;
        if (_settings.Fallback.PreferRoiFallbackFromServer && parsed.RoiFallback?.JaTranslation is { Length: > 0 } roiFallbackText)
            fallbackText = roiFallbackText;

        string status = confidenceOk ? "Fallback" : "Low confidence";
        return new DisplayPayload(roi, status, fallbackText);
    }

    private bool TryValidateBbox(BBox? bbox, RectInt roi, out RectInt screenRect)
    {
        screenRect = roi;
        if (bbox == null)
            return false;
        if (bbox.Width <= 0 || bbox.Height <= 0)
            return false;
        if (bbox.X1 < 0 || bbox.Y1 < 0 || bbox.X2 > roi.Width || bbox.Y2 > roi.Height)
            return false;
        if (bbox.Width < _settings.Fallback.BboxMinWidth || bbox.Height < _settings.Fallback.BboxMinHeight)
            return false;
        double areaRatio = (double)(bbox.Width * bbox.Height) / Math.Max(1, roi.Area);
        if (areaRatio > _settings.Fallback.BboxMaxAreaRatio)
            return false;

        screenRect = new RectInt(roi.X + bbox.X1, roi.Y + bbox.Y1, bbox.Width, bbox.Height);
        return true;
    }

    private readonly record struct DisplayPayload(RectInt Bbox, string Status, string Text);
}
