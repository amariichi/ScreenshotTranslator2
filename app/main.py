from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from PIL import Image
import io

from .llama_client import LlamaClient
from .config import get_settings

app = FastAPI(title="Screenshot Translator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse("app/static/index.html", media_type="text/html")


@app.post("/api/translate")
async def translate(
    image: UploadFile = File(...),
    prompt: str = Form(""),
    ctx: int | None = Form(None),
):
    settings = get_settings()
    target_ctx = ctx or settings.ctx_size

    try:
        raw = await image.read()
        # Normalize to PNG for predictable base64 size
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail=f"Failed to read image: {exc}") from exc

    client = LlamaClient()
    # Override ctx dynamically
    get_settings().ctx_size = target_ctx
    try:
        markdown = await client.translate_image(png_bytes, prompt or None)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await client.aclose()

    return JSONResponse({"markdown": markdown, "ctx": target_ctx})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/llama-status")
async def llama_status() -> JSONResponse:
    # 1) ログをざっくり見る
    log_path = "llama-server.log"
    log_status = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-400:]
            text = "\n".join(lines).lower()
            has_loading = "loading model" in text
            has_idle = "idle" in text
            has_listening = "listening" in text or "http server" in text
            has_error = "error" in text

            if has_error:
                log_status = "エラー検出 (ログ)"
            elif has_loading and not has_idle:
                log_status = "モデル読み込み中 (ログより)"
            elif has_idle:
                log_status = "準備完了 (ログより)"
            elif has_listening:
                log_status = "起動中（モデル読み込み未確認）(ログより)"
    except FileNotFoundError:
        log_status = None

    # 2) HTTPで確認
    client = LlamaClient()
    status = await client.get_status()
    await client.aclose()

    # ログが具体的なら優先、HTTPのみならHTTP
    if log_status and status:
        if log_status != status:
            return JSONResponse({"status": f"{log_status} / {status}"})
        return JSONResponse({"status": log_status})
    if log_status:
        return JSONResponse({"status": log_status})
    return JSONResponse({"status": status})
