using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace OverlayClient;

public sealed class AppSettings
{
    [JsonPropertyName("server")] public ServerSettings Server { get; set; } = new();
    [JsonPropertyName("gesture")] public GestureSettings Gesture { get; set; } = new();
    [JsonPropertyName("roi")] public RoiSettings Roi { get; set; } = new();
    [JsonPropertyName("overlay")] public OverlaySettings Overlay { get; set; } = new();
    [JsonPropertyName("fallback")] public FallbackSettings Fallback { get; set; } = new();
    [JsonPropertyName("logging")] public LoggingSettings Logging { get; set; } = new();

    public static AppSettings CreateDefault() => new();

    public static AppSettings Load(string path)
    {
        var json = File.ReadAllText(path);
        var opt = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
        var obj = JsonSerializer.Deserialize<AppSettings>(json, opt);
        if (obj == null) throw new InvalidOperationException("settings.json parse failed");
        return obj;
    }

    public void Save(string path)
    {
        var opt = new JsonSerializerOptions { WriteIndented = true };
        File.WriteAllText(path, JsonSerializer.Serialize(this, opt));
    }
}

public sealed class ServerSettings
{
    [JsonPropertyName("base_url")] public string BaseUrl { get; set; } = "http://127.0.0.1:8012";
    [JsonPropertyName("health_path")] public string HealthPath { get; set; } = "/health";
    [JsonPropertyName("inference_path")] public string InferencePath { get; set; } = "/api/v1/ocr_translate_with_grounding";
    [JsonPropertyName("request_timeout_sec")] public int RequestTimeoutSec { get; set; } = 60;
    [JsonPropertyName("translation_only")] public bool TranslationOnly { get; set; } = true;
    [JsonPropertyName("return_roi_fallback")] public bool ReturnRoiFallback { get; set; } = false;
    [JsonPropertyName("retry")] public RetrySettings Retry { get; set; } = new();
}

public sealed class GestureSettings
{
    [JsonPropertyName("enabled")] public bool Enabled { get; set; } = true;
    [JsonPropertyName("mode")] public string Mode { get; set; } = "ctrl_hold";
    [JsonPropertyName("modifier")] public string Modifier { get; set; } = "ctrl_alt";
}

public sealed class RoiSettings
{
    [JsonPropertyName("margin_ratio")] public double MarginRatio { get; set; } = 0.35;
    [JsonPropertyName("min_width")] public int MinWidth { get; set; } = 64;
    [JsonPropertyName("min_height")] public int MinHeight { get; set; } = 64;
    [JsonPropertyName("max_width")] public int MaxWidth { get; set; } = 1920;
    [JsonPropertyName("max_height")] public int MaxHeight { get; set; } = 1080;
    [JsonPropertyName("clip_to_screen")] public bool ClipToScreen { get; set; } = true;
    [JsonPropertyName("use_centroid_for_min_size_expand")] public bool UseCentroidForMinSizeExpand { get; set; } = true;
}

public sealed class OverlaySettings
{
    [JsonPropertyName("topmost")] public bool Topmost { get; set; } = true;
    [JsonPropertyName("header")] public OverlayHeaderSettings Header { get; set; } = new();
    [JsonPropertyName("text")] public OverlayTextSettings Text { get; set; } = new();
    [JsonPropertyName("interaction")] public OverlayInteractionSettings Interaction { get; set; } = new();
    [JsonPropertyName("appearance")] public OverlayAppearanceSettings Appearance { get; set; } = new();
    [JsonPropertyName("preview")] public OverlayPreviewSettings Preview { get; set; } = new();
}

public sealed class FallbackSettings
{
    [JsonPropertyName("enabled")] public bool Enabled { get; set; } = true;
    [JsonPropertyName("confidence_threshold")] public double ConfidenceThreshold { get; set; } = 0.5;
    [JsonPropertyName("bbox_min_width")] public int BboxMinWidth { get; set; } = 80;
    [JsonPropertyName("bbox_min_height")] public int BboxMinHeight { get; set; } = 40;
    [JsonPropertyName("bbox_max_area_ratio")] public double BboxMaxAreaRatio { get; set; } = 0.9;
    [JsonPropertyName("prefer_roi_fallback_from_server")] public bool PreferRoiFallbackFromServer { get; set; } = true;
    [JsonPropertyName("force_use_roi")] public bool ForceUseRoi { get; set; } = false;
}

public sealed class LoggingSettings
{
    [JsonPropertyName("level")] public string Level { get; set; } = "info";
    [JsonPropertyName("debug_save_images")] public bool DebugSaveImages { get; set; } = false;
    [JsonPropertyName("debug_image_dir")] public string DebugImageDir { get; set; } = ".debug_images";
}

public sealed class RetrySettings
{
    [JsonPropertyName("enabled")] public bool Enabled { get; set; } = true;
    [JsonPropertyName("max_attempts")] public int MaxAttempts { get; set; } = 1;
    [JsonPropertyName("backoff_ms")] public int BackoffMs { get; set; } = 300;
}

public sealed class OverlayHeaderSettings
{
    [JsonPropertyName("height_px")] public int HeightPx { get; set; } = 28;
    [JsonPropertyName("show_status")] public bool ShowStatus { get; set; } = true;
    [JsonPropertyName("buttons")] public OverlayHeaderButtons Buttons { get; set; } = new();
}

public sealed class OverlayHeaderButtons
{
    [JsonPropertyName("copy")] public bool Copy { get; set; } = true;
    [JsonPropertyName("close")] public bool Close { get; set; } = true;
}

public sealed class OverlayTextSettings
{
    [JsonPropertyName("wrap")] public bool Wrap { get; set; } = true;
    [JsonPropertyName("vertical_scrollbar")] public string VerticalScrollbar { get; set; } = "auto";
    [JsonPropertyName("selectable")] public bool Selectable { get; set; } = true;
    [JsonPropertyName("font_size")] public double FontSize { get; set; } = 14;
}

public sealed class OverlayInteractionSettings
{
    [JsonPropertyName("partial_clickthrough")] public bool PartialClickthrough { get; set; } = true;
    [JsonPropertyName("interactive_whitelist")] public string[] InteractiveWhitelist { get; set; } =
        ["Button", "ScrollBar", "ScrollViewer", "TextBox", "Thumb"];
    [JsonPropertyName("close_on_esc")] public bool CloseOnEsc { get; set; } = true;
    [JsonPropertyName("replace_existing")] public bool ReplaceExisting { get; set; } = true;
}

public sealed class OverlayAppearanceSettings
{
    [JsonPropertyName("background_opacity")] public double BackgroundOpacity { get; set; } = 0.66;
    [JsonPropertyName("corner_radius")] public int CornerRadius { get; set; } = 6;
}

public sealed class OverlayPreviewSettings
{
    [JsonPropertyName("show_roi_preview")] public bool ShowRoiPreview { get; set; } = true;
    [JsonPropertyName("duration_ms")] public int DurationMs { get; set; } = 700;
    [JsonPropertyName("live_preview")] public bool LivePreview { get; set; } = true;
}
