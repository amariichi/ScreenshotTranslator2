using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace OverlayClient;

public sealed class ApiClient : IDisposable
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions = new() { PropertyNameCaseInsensitive = true };

    public ApiClient()
    {
        _http = new HttpClient();
    }

    public async Task<bool> CheckHealthAsync(ServerSettings settings, CancellationToken ct)
    {
        var url = settings.BaseUrl.TrimEnd('/') + settings.HealthPath;
        try
        {
            using var resp = await _http.GetAsync(url, ct).ConfigureAwait(false);
            return resp.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    public async Task<InferenceResult> RequestInferenceAsync(
        ServerSettings settings,
        byte[] cleanPng,
        CancellationToken ct)
    {
        var url = settings.BaseUrl.TrimEnd('/') + settings.InferencePath;
        var content = new MultipartFormDataContent();
        var clean = new ByteArrayContent(cleanPng);
        clean.Headers.ContentType = new MediaTypeHeaderValue("image/png");
        content.Add(clean, "clean_image", "clean.png");

        var optionsJson = JsonSerializer.Serialize(new
        {
            return_roi_fallback = settings.ReturnRoiFallback,
            translation_only = settings.TranslationOnly,
            timeout_sec = settings.RequestTimeoutSec
        });
        content.Add(new StringContent(optionsJson, Encoding.UTF8, "text/plain"), "options");

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(TimeSpan.FromSeconds(settings.RequestTimeoutSec));

        using var resp = await _http.PostAsync(url, content, cts.Token).ConfigureAwait(false);
        var raw = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
        if (!resp.IsSuccessStatusCode)
        {
            return new InferenceResult { Raw = raw, Error = $"HTTP {resp.StatusCode}" };
        }

        try
        {
            var parsed = JsonSerializer.Deserialize<InferenceResponse>(raw, _jsonOptions);
            return new InferenceResult { Parsed = parsed, Raw = raw };
        }
        catch (Exception ex)
        {
            return new InferenceResult { Raw = raw, Error = ex.Message };
        }
    }

    public void Dispose()
    {
        _http.Dispose();
    }
}
