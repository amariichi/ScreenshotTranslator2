from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from PIL import Image
import io
import json
import os
import difflib
from typing import Any, Dict, Tuple, Optional

from .llama_client import LlamaClient
from .config import get_settings
from .tts import tts_engine
from .diff_engine import DiffEngine

# Global session state
class SessionState:
    last_ocr_text: str = ""

session_state = SessionState()

app = FastAPI(title="Screenshot Translator", version="7.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

_PROMPT_ECHO_MARKERS = (
    "自然文は必ず日本語で全文訳してください",
    "要約・省略禁止",
    "コードや数式は原文のままとします",
)

_BAD_OUTPUT_MARKERS = (
    "the user wants me to",
    "i need to",
    "let's process",
    "i will translate",
    "input text:",
    "start of content to translate",
    "the input text is",
    "<channel|>",
    "<|channel",
    "<|think|>",
)

_META_OUTPUT_MARKERS = (
    "the user wants me to",
    "i need to",
    "let's process",
    "i will translate",
    "input text:",
    "start of content to translate",
    "the input text is",
    "<channel|>",
    "<|channel",
    "<|think|>",
)


def _looks_like_english_output(text: str) -> bool:
    if not text:
        return False
    latin = sum(1 for c in text if "A" <= c <= "Z" or "a" <= c <= "z")
    ja = sum(1 for c in text if "\u3040" <= c <= "\u30FF" or "\u4E00" <= c <= "\u9FFF")
    if latin == 0:
        return False
    return ja == 0 or (latin >= 20 and latin > ja * 4)


def _is_repetitive_output(text: str) -> bool:
    if not text:
        return False
    cleaned = " ".join(text.split())
    if len(cleaned) < 200:
        return False
    segments = cleaned.replace("。", "\n").split("\n")
    counts: Dict[str, int] = {}
    for seg in segments:
        s = seg.strip()
        if len(s) < 20:
            continue
        counts[s] = counts.get(s, 0) + 1
        if counts[s] >= 3:
            return True
    return False


def _should_retry_webui_output(text: str) -> bool:
    if not text:
        return True
    lowered = text.strip().lower()
    if any(marker in lowered for marker in _PROMPT_ECHO_MARKERS):
        return True
    if any(marker in lowered for marker in _BAD_OUTPUT_MARKERS):
        return True
    if _looks_like_english_output(lowered):
        return True
    return _is_repetitive_output(lowered)


def _looks_like_meta_output(text: str) -> bool:
    if not text:
        return False
    lowered = text.strip().lower()
    return any(marker in lowered for marker in _META_OUTPUT_MARKERS)


def _looks_like_duplicate_ocr(old_text: str, new_text: str, new_content: str) -> bool:
    if not old_text or not new_text or not new_content:
        return False
    if len(new_text) < 200:
        return False
    long_text = len(new_text) >= 300
    # If "new" content is most of the OCR result, treat this as a jittered re-OCR.
    ratio_threshold = 0.9 if long_text else 0.8
    if len(new_content) < int(len(new_text) * ratio_threshold):
        return False
    ratio = difflib.SequenceMatcher(None, old_text, new_text).ratio()
    sim_threshold = 0.9 if long_text else 0.95
    return ratio > sim_threshold


def _log_webui_output(markdown: str, prompt: str) -> None:
    if os.getenv("LOG_WEBUI_RAW", "") != "1" and not _should_log_webui_output(markdown):
        return
    try:
        with open("webui_raw.log", "a", encoding="utf-8") as f:
            f.write("\n---\n")
            f.write(f"prompt={prompt!r}\n")
            f.write("output=\n")
            f.write(markdown)
            f.write("\n")
    except Exception:
        pass


@app.get("/")
async def root() -> FileResponse:
    return FileResponse("app/static/index.html", media_type="text/html")


@app.post("/api/translate")
async def translate(
    image: UploadFile = File(...),
    prompt: str = Form(""),
    # Compatibility: older frontends may still send ctx
    ctx: int | None = Form(None),
):
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
    try:
        markdown = await client.translate_image(png_bytes, prompt or None)
        if _should_retry_webui_output(markdown):
            retry_prompt = (prompt or "").strip()
            retry_suffix = (
                "\n\n重要: すべての内容を日本語に正確に翻訳してください。"
                "なお、コードはそのまま出力してください。"
                "翻訳結果以外の説明文、自己言及、方針説明、思考過程、特殊トークンは一切出力しないでください。"
                "たとえば The user wants me to, I need to, Let's process, I will translate, <channel|> のような文字列は禁止です。"
                "同じ文の繰り返しは禁止です。"
                "行や段落を重複させたり、並べ替えたり、別の行同士を結合したりしないでください。"
                "コードやコードに見える行は原文のまま維持してください。"
                "翻訳するべきか迷う行は、無理に言い換えず原文を優先してください。"
                "途中で繰り返し始めたら停止せず、残りの内容を続けてください。"
                "出力は翻訳済み本文だけにしてください。"
            )
            retry_prompt = (retry_prompt + retry_suffix).strip() if retry_prompt else retry_suffix.strip()
            markdown = await client.translate_image(
                png_bytes,
                retry_prompt,
                max_tokens=3000,
                temperature=0.0,
                top_p=0.8,
            )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await client.aclose()

    _ = ctx  # ignore; server ctx is set at llama-server start
    return JSONResponse({"markdown": markdown})


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


def _read_upload_as_png(file: UploadFile) -> Tuple[bytes, int, int]:
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"Empty upload: {file.filename}")
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {file.filename}: {exc}") from exc
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    w, h = img.size
    return buf.getvalue(), w, h


def _extract_first_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("<json>") and text.endswith("</json>"):
        inner = text[len("<json>") : -len("</json>")].strip()
        return json.loads(inner)

    if "```json" in text:
        start = text.find("```json")
        end = text.find("```", start + 7)
        if start != -1 and end != -1 and end > start:
            inner = text[start + 7 : end].strip()
            return json.loads(inner)

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    brace = 0
    start_idx = None
    for i, ch in enumerate(text):
        if ch == "{":
            if brace == 0:
                start_idx = i
            brace += 1
        elif ch == "}":
            if brace > 0:
                brace -= 1
                if brace == 0 and start_idx is not None:
                    candidate = text[start_idx : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        continue

    raise ValueError("Could not extract valid JSON from model output")


def _maybe_log_raw_output(raw: str) -> None:
    if os.getenv("OCR_JSON_DEBUG", "0") != "1":
        return
    try:
        path = os.path.join(os.getcwd(), "ocr_json_debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(raw)
            f.write("\n----\n")
    except Exception:
        pass


def _extract_field_from_raw(raw: str, key: str) -> str | None:
    try:
        obj = _extract_first_json(raw)
        val = obj.get(key)
        if isinstance(val, str):
            return val
    except Exception:
        pass

    # Fallback: regex search for a JSON-like "key": "value"
    # We want to capture until the NEXT key or end of object, to handle unescaped quotes inside value.
    # Heuristic: Look for ", "next_key": or " }
    import re

    # 1. Find the start of the key-value pair
    # Match: "key" : "
    start_pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*"', re.DOTALL)
    m_start = start_pattern.search(raw)
    if not m_start:
        return None
    
    start_idx = m_start.end()
    rest = raw[start_idx:]

    # 2. Find the likely end of the value. 
    # We look for a pattern that looks like the start of a NEW key: `",\s*"[\w]+"\s*:`
    # OR the end of the JSON object: `"\s*}`
    # Note: This regex finds the *delimiter* starting with the closing quote.
    end_pattern = re.compile(r'"\s*(?:,\s*"[\w]+"\s*:|})', re.DOTALL)
    
    m_end = end_pattern.search(rest)
    if m_end:
        # The content is everything up to the quote that starts the delimiter
        val = rest[:m_end.start()]
    else:
        # If no clear delimiter found:
        # 1. Try naive greedy match for last quote (if it exists)
        m_simple = re.search(r'(.*?)"', rest, re.DOTALL)
        if m_simple:
            val = m_simple.group(1)
        else:
            # 2. If NO quote found, assume truncation and return everything until the end
            val = rest

    val = val.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return val


def _extract_translation_text(raw: str) -> str | None:
    ja = _extract_field_from_raw(raw, "ja_translation")
    if ja:
        return ja.strip()

    text = raw.strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("```") or text.startswith("<json>"):
        return None
    if _looks_like_meta_output(text) or _looks_like_english_output(text):
        return None
    return text


async def _retry_full_schema_translation(
    guide_png: bytes,
    clean_png: bytes,
    return_roi_fallback: bool,
    timeout_sec: int,
    prompt: str,
) -> str:
    client = LlamaClient()
    try:
        raw = await client.ocr_translate_with_grounding(
            guide_png=guide_png,
            clean_png=clean_png,
            return_roi_fallback=return_roi_fallback,
            timeout_sec=timeout_sec,
            extra_instruction=prompt.strip() or None,
            translation_only=False,
        )
    finally:
        await client.aclose()
    return (_extract_translation_text(raw) or "").strip()


def _validate_bbox(b: Dict[str, Any], w: int, h: int) -> Tuple[int, int, int, int]:
    x1 = int(b.get("x1"))
    y1 = int(b.get("y1"))
    x2 = int(b.get("x2"))
    y2 = int(b.get("y2"))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Invalid bbox ordering")
    if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        raise ValueError("BBox out of image bounds")
    return x1, y1, x2, y2


@app.post("/api/v1/ocr_translate_with_grounding")
async def ocr_translate_with_grounding(
    clean_image: UploadFile = File(...),
    guide_image: Optional[UploadFile] = File(None),
    options: str = Form(default="{}"),
    prompt: str = Form(default=""),
) -> JSONResponse:
    try:
        opt = json.loads(options) if options else {}
        if not isinstance(opt, dict):
            raise ValueError("options must be JSON object")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options JSON: {exc}") from exc

    return_roi_fallback = bool(opt.get("return_roi_fallback", True))
    timeout_sec = int(opt.get("timeout_sec", 90))
    translation_only = bool(opt.get("translation_only", False))

    clean_png, w, h = _read_upload_as_png(clean_image)
    if guide_image:
        guide_png, _, _ = _read_upload_as_png(guide_image)
    else:
        guide_png = clean_png
    if w > 1920 or h > 1080:
        raise HTTPException(status_code=400, detail="clean_image exceeds 1920x1080 limit")

    client = LlamaClient()
    try:
        raw = await client.ocr_translate_with_grounding(
            guide_png=guide_png,
            clean_png=clean_png,
            return_roi_fallback=return_roi_fallback,
            timeout_sec=timeout_sec,
            extra_instruction=prompt.strip() or None,
            translation_only=translation_only,
        )
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await client.aclose()

    try:
        obj = _extract_first_json(raw)
    except Exception as exc:
        _maybe_log_raw_output(raw)
        ja = _extract_translation_text(raw)
        if translation_only:
            if ja:
                fallback_obj = {
                    "ja_translation": ja.strip(),
                    "notes": f"json_parse_failed: {exc}",
                }
                return JSONResponse(fallback_obj)
            try:
                ja_full = await _retry_full_schema_translation(
                    guide_png=guide_png,
                    clean_png=clean_png,
                    return_roi_fallback=return_roi_fallback,
                    timeout_sec=timeout_sec,
                    prompt=prompt,
                )
            except Exception:
                ja_full = ""
            if ja_full:
                return JSONResponse(
                    {
                        "ja_translation": ja_full,
                        "notes": f"translation_only_retry_from_full_schema: {exc}",
                    }
                )
            raise HTTPException(
                status_code=500,
                detail="Failed to extract a valid translated result from model output. Please retry.",
            ) from exc
        # Fallback: try to salvage ja_translation/ocr_text from broken JSON
        ocr = _extract_field_from_raw(raw, "ocr_text")
        fallback_obj = {
            "target_bbox": {"x1": 0, "y1": 0, "x2": w, "y2": h},
            "ocr_text": ocr or "",
            "ja_translation": (ja or raw).strip(),
            "notes": f"json_parse_failed: {exc}",
        }
        return JSONResponse(fallback_obj)

    if translation_only:
        ja_translation = str(obj.get("ja_translation", "")).strip()
        notes = str(obj.get("notes", "")).strip()
        if not ja_translation:
            ja_translation = (_extract_translation_text(raw) or "").strip()
        if not ja_translation:
            try:
                ja_translation = await _retry_full_schema_translation(
                    guide_png=guide_png,
                    clean_png=clean_png,
                    return_roi_fallback=return_roi_fallback,
                    timeout_sec=timeout_sec,
                    prompt=prompt,
                )
            except Exception:
                ja_translation = ""
            notes = (notes + " | retried_with_full_schema").strip(" |")
        if not ja_translation:
            raise HTTPException(status_code=500, detail="Missing key: ja_translation")
        if _looks_like_meta_output(ja_translation):
            try:
                ja_fallback = await _retry_full_schema_translation(
                    guide_png=guide_png,
                    clean_png=clean_png,
                    return_roi_fallback=return_roi_fallback,
                    timeout_sec=timeout_sec,
                    prompt=prompt,
                )
            except Exception:
                ja_fallback = ""
            if ja_fallback and not _looks_like_meta_output(ja_fallback):
                ja_translation = ja_fallback
                notes = (notes + " | meta_output_retried_with_full_schema").strip(" |")
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Model returned meta/instruction text instead of a clean translation. Please retry.",
                )
        return JSONResponse({"ja_translation": ja_translation, "notes": notes})

    try:
        _validate_bbox(obj.get("target_bbox", {}), w=w, h=h)
    except Exception as exc:
        if "roi_fallback" in obj:
            obj["target_bbox"] = {"x1": 0, "y1": 0, "x2": w, "y2": h}
            obj.setdefault("notes", "")
            obj["notes"] = (obj["notes"] + f" | bbox_invalid: {exc}").strip(" |")
        else:
            raise HTTPException(status_code=500, detail=f"Invalid target_bbox: {exc}") from exc

    for key in ("target_bbox", "ocr_text", "ja_translation"):
        if key not in obj:
            raise HTTPException(status_code=500, detail=f"Missing key: {key}")

    return JSONResponse(obj)


@app.post("/api/v1/monitor_update")
async def monitor_update(
    clean_image: UploadFile = File(...),
    guide_image: Optional[UploadFile] = File(None), # Optional optimization
    reset_session: bool = Form(False),
    options: str = Form(default="{}"),
) -> JSONResponse:
    if reset_session:
        session_state.last_ocr_text = ""
        # interrupt previous speech
        tts_engine.speak("", interrupt=True) 

    try:
        opt = json.loads(options) if options else {}
    except Exception:
        opt = {}

    timeout_sec = int(opt.get("timeout_sec", 60))
    clean_png, w, h = _read_upload_as_png(clean_image)
    
    # Optimization: If guide image is missing, use clean image (single upload)
    if guide_image:
        guide_png, _, _ = _read_upload_as_png(guide_image)
    else:
        guide_png = clean_png

    # DEBUG: Save image to check what we received
    try:
        with open("debug_monitor_latest.png", "wb") as f:
            f.write(clean_png)
    except Exception as e:
        print(f"Failed to save debug image: {e}")

    client = LlamaClient()
    try:
        raw = await client.ocr_translate_with_grounding(
            guide_png=guide_png,
            clean_png=clean_png,
            return_roi_fallback=True,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
    finally:
        await client.aclose()

    # 2. Extract Text
    try:
        obj = _extract_first_json(raw)
        current_text = obj.get("ja_translation", "")
        if not current_text:
            current_text = obj.get("ocr_text", "")
            
    except Exception:
         current_text = _extract_field_from_raw(raw, "ja_translation") or ""
         if not current_text:
             current_text = _extract_field_from_raw(raw, "ocr_text") or ""
            
    if not current_text:
        print("[Monitor] No text detected in image.")
        return JSONResponse({"status": "no_text_detected"})
    
    print(f"[Monitor] Detected Text len={len(current_text)}: {current_text[:50].replace('\n', ' ')}...")

    # 3. Diff & TTS
    new_content = DiffEngine.detect_new_content(session_state.last_ocr_text, current_text)
    
    if new_content:
        if tts_engine.is_busy() and _looks_like_duplicate_ocr(
            session_state.last_ocr_text, current_text, new_content
        ):
            print("[Monitor] Skipping duplicate long OCR jitter while speaking.")
            should_update_state = False
            return JSONResponse({"status": "ok", "new_content_len": 0})
        # Check if already in pipeline (Active or Queued)
        if tts_engine.is_content_active(new_content):
            print(f"[Monitor] Skipping {new_content[:20]}... (Already active)")
            should_update_state = False
        else:
             if tts_engine.is_busy():
                 print(f"[Monitor] Buffering {new_content[:20]}...")
                 # Buffer the text, but DO NOT update last_ocr_text yet.
                 # We want monitors to keep comparing against the currently speaking text (A),
                 # until A finishes and B actually starts.
                 tts_engine.set_next_text(new_content)
                 should_update_state = False
             else:
                 print(f"[Monitor] Speaking {new_content[:20]}...")
                 tts_engine.speak(new_content, interrupt=True)
                 should_update_state = True
    else:
        should_update_state = False

    # Update state
    # Only update if we accepted the new content for IMMEDIATE speech, or if reset/first run.
    # If we buffered it, we keep the old state (A) so DiffEngine calculates diff against A,
    # preventing "B" from being lost if it's merely buffered.
    if should_update_state or reset_session or not session_state.last_ocr_text:
         session_state.last_ocr_text = current_text

    return JSONResponse({"status": "ok", "new_content_len": len(new_content)})


@app.post("/api/v1/ocr_translate_tts_once")
async def ocr_translate_tts_once(
    clean_image: UploadFile = File(...),
    guide_image: Optional[UploadFile] = File(None),
    options: str = Form(default="{}"),
) -> JSONResponse:
    try:
        opt = json.loads(options) if options else {}
    except Exception:
        opt = {}

    timeout_sec = int(opt.get("timeout_sec", 60))
    clean_png, w, h = _read_upload_as_png(clean_image)
    if guide_image:
        guide_png, _, _ = _read_upload_as_png(guide_image)
    else:
        guide_png = clean_png

    client = LlamaClient()
    try:
        raw = await client.ocr_translate_with_grounding(
            guide_png=guide_png,
            clean_png=clean_png,
            return_roi_fallback=True,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)
    finally:
        await client.aclose()

    try:
        obj = _extract_first_json(raw)
        current_text = obj.get("ja_translation", "")
        if not current_text:
            current_text = obj.get("ocr_text", "")
    except Exception:
        current_text = _extract_field_from_raw(raw, "ja_translation") or ""
        if not current_text:
            current_text = _extract_field_from_raw(raw, "ocr_text") or ""

    if not current_text:
        return JSONResponse({"status": "no_text_detected"})

    tts_engine.speak(current_text, interrupt=True)
    return JSONResponse(
        {
            "status": "ok",
            "text": current_text,
            "len": len(current_text),
        }
    )
