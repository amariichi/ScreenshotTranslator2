import base64
import httpx
from typing import Optional

from .config import get_settings


class LlamaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_base = settings.api_base.rstrip("/")
        self.ctx_size = settings.ctx_size
        self.model = settings.model_name
        self.system_prompt = settings.system_prompt
        self._client = httpx.AsyncClient(timeout=300)

    async def translate_image(
        self,
        image_bytes: bytes,
        prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> str:
        settings = get_settings()
        prompt_text = prompt or (
            "すべての内容を日本語に正確に翻訳してください。なお、コードはそのまま出力してください。"
        )
        img_b64 = base64.b64encode(image_bytes).decode()
        image_url = f"data:image/png;base64,{img_b64}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": settings.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "max_tokens": max_tokens if max_tokens is not None else 3000,
            "temperature": temperature if temperature is not None else 0.0,
            "stop": None,
            "stream": False,
            "n": 1,
            "presence_penalty": 0.0,
            "frequency_penalty": 0,
            "logit_bias": {},
            "top_p": top_p if top_p is not None else 0.8,
            "min_p": 0.05,
            "repetition_penalty": 1.0,
            "top_k": 20,
        }

        url = f"{self.api_base}/v1/chat/completions"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise RuntimeError(f"Unexpected response: {data}") from exc

    async def ocr_translate_with_grounding(
        self,
        guide_png: bytes,
        clean_png: bytes,
        return_roi_fallback: bool,
        timeout_sec: int,
        extra_instruction: Optional[str] = None,
        translation_only: bool = False,
    ) -> str:
        # Check if guide and clean are identical (Monitor Mode / optimized client)
        is_single_image = (guide_png == clean_png)

        prompt_text = self._build_grounding_prompt(
            return_roi_fallback,
            is_single_image,
            extra_instruction=extra_instruction,
            translation_only=translation_only,
        )
        clean_b64 = base64.b64encode(clean_png).decode()

        content_list = [{"type": "text", "text": prompt_text}]

        if is_single_image:
            content_list.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{clean_b64}"}}
            )
        else:
            guide_b64 = base64.b64encode(guide_png).decode()
            content_list.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{guide_b64}"}}
            )
            content_list.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{clean_b64}"}}
            )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful OCR and translation engine. You must follow the output schema exactly.",
                },
                {
                    "role": "user",
                    "content": content_list,
                },
            ],
            "max_tokens": 4096,
            "temperature": 0.0,
            "stop": None,
            "stream": False,
            "n": 1,
            "presence_penalty": 0.0,
            "frequency_penalty": 0,
            "logit_bias": {},
            "top_p": 0.8,
            "min_p": 0.05,
            "repetition_penalty": 1.0,
            "top_k": 20,
        }

        url = f"{self.api_base}/v1/chat/completions"
        resp = await self._client.post(url, json=payload, timeout=timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise RuntimeError(f"Unexpected response: {data}") from exc


    async def get_status(self) -> str:
        try:
            # Check llama-server health
            health_url = f"{self.api_base}/health"
            resp = await self._client.get(health_url)
            if resp.status_code == 200:
                s = resp.json().get("status")
                if s == "ok" or s == "loading model":
                     return "起動中（API応答あり）"
                return f"異常 ({s})"
            if resp.status_code == 503:
                return "モデル読込中 (HTTP 503)"
            return f"HTTP {resp.status_code}"
        except httpx.ConnectError:
             return "接続不能 (llama-serverダウン？)"
        except Exception as e:
             return f"エラー: {e}"


    @staticmethod
    def _build_grounding_prompt(
        return_roi_fallback: bool,
        is_single_image: bool = False,
        extra_instruction: Optional[str] = None,
        translation_only: bool = False,
    ) -> str:
        if is_single_image:
            base = (
                "You will receive an image.\n"
                "This image represents a screen area selected by the user for OCR and translation.\n\n"
                "Core rules (must follow):\n"
                "1) Extract ALL text in the image with correct ordering and line breaks.\n"
                "2) Scan the entire image area from top-left to bottom-right. Do not miss any independent text blocks.\n"
                "3) Include all text columns, headers, and footers. Do not focus only on the main body.\n"
                "4) Preserve code blocks and inline code verbatim; do NOT translate code.\n"
                "5) Do NOT summarize or omit any content. Translate every natural-language line faithfully.\n"
                "6) Do not merge, reorder, or duplicate lines or sections.\n"
                "7) If a line mixes natural language with code-like tokens, translate only the natural-language parts and keep the code-like parts unchanged.\n"
                "8) If unsure whether text should be translated or preserved verbatim, prefer preserving the original text.\n"
                "9) If a character is unreadable, use [UNK].\n\n"
                "Tasks:\n"
                "1) Treat the WHOLE image as the target.\n"
                "2) Return ONE bounding box that covers ALL text in the image. Do not create a partial box.\n"
                "3) OCR all text inside the image EXACTLY as visible.\n"
                "4) すべての内容を日本語に正確に翻訳してください。なお、コードはそのまま出力してください。\n"
                "5) Do not duplicate lines, paragraphs, or sections.\n"
                "6) If the image is ambiguous or text is unreadable, still return best-effort bbox and mark uncertainty in notes.\n"
                "Output STRICTLY as JSON (no markdown, no extra text).\n"
            )
        else:
            base = (
                "You will receive two images:\n"
                "- Image A (guide_image): A screen crop with a thin gesture stroke indicating what the user points at.\n"
                "- Image B (clean_image): The same crop without the stroke. Use this image for OCR and translation.\n\n"
                "Core rules (must follow):\n"
                "1) Extract ALL text in the target box with correct ordering and line breaks.\n"
                "2) Scan the entire image area from top-left to bottom-right. Do not miss any independent text blocks.\n"
                "3) Include all text columns, headers, and footers. Do not focus only on the main body.\n"
                "4) Preserve code blocks and inline code verbatim; do NOT translate code.\n"
                "5) Do NOT summarize or omit any content. Translate every natural-language line faithfully.\n"
                "6) Do not merge, reorder, or duplicate lines or sections.\n"
                "7) If a line mixes natural language with code-like tokens, translate only the natural-language parts and keep the code-like parts unchanged.\n"
                "8) If unsure whether text should be translated or preserved verbatim, prefer preserving the original text.\n"
                "9) If a character is unreadable, use [UNK].\n\n"
                "Tasks:\n"
                "1) The guide stroke (Image A) indicates that the USER SELECTED THE ENTIRE IMAGE AREA. Treat the whole image as the target.\n"
                "2) Return ONE bounding box that covers ALL text in the image. Do not create a partial box.\n"
                "3) OCR all text inside the image EXACTLY as visible.\n"
                "4) すべての内容を日本語に正確に翻訳してください。なお、コードはそのまま出力してください。\n"
                "5) Do not duplicate lines, paragraphs, or sections.\n"
                "6) If the box is ambiguous or text is unreadable, still return best-effort bbox and mark uncertainty in notes.\n"
                "Output STRICTLY as JSON (no markdown, no extra text).\n"
            )

        if extra_instruction:
            base += (
                "\nAdditional user instruction:\n"
                f"{extra_instruction.strip()}\n"
                "Follow this instruction unless it conflicts with the required output format.\n"
            )

        if translation_only:
            schema = (
                '{\n'
                '  "ja_translation": "<string>",\n'
                '  "notes": "<string>"\n'
                '}'
            )
        else:
            schema = (
                '{\n'
                '  "target_bbox": {"x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>},\n'
                '  "detected_language": "<string>",\n'
                '  "ocr_text": "<string>",\n'
                '  "ja_translation": "<string>",\n'
                '  "confidence": <number between 0 and 1>,\n'
                '  "notes": "<string>"\n'
                '}'
            )
            if return_roi_fallback:
                schema = schema[:-2] + ',\n  "roi_fallback": {"ocr_text": "<string>", "ja_translation": "<string>"}\n}'
        
        # Schema is same for both
        
        if translation_only:
            base += (
                "\nFor this request, you only need to return the final Japanese translation. "
                "Do not include OCR text, bounding boxes, detected language, or any extra fields. "
                "Put the full translated text in ja_translation. "
                "Preserve the original line breaks and keep any code-like lines verbatim.\n"
            )
        elif return_roi_fallback:
             # Roi fallback text also needs slight adjustment or is it generic?
             # "If roi_fallback is requested, it must contain the FULL OCR... of the entire ROI (Image B)"
             # In single image mode, there is only "The Image".
             # But "Image B" reference is problematic.
             if is_single_image:
                 base += (
                    "\nIf roi_fallback is requested, it must contain the FULL OCR and translation of the entire image. "
                    "Do not shorten or omit any lines. If there are multiple text blocks or lines, include ALL "
                    "of them in reading order. Never return only a partial block. "
                    "Always include the roi_fallback field when requested. "
                    "すべてのの内容を日本語に正確に翻訳してください。なお、コードはそのまま出力してください。"
                 )
             else:
                 base += (
                    "\nIf roi_fallback is requested, it must contain the FULL OCR and translation of the entire ROI "
                    "(Image B). Do not shorten or omit any lines. If there are multiple text blocks or lines, include ALL "
                    "of them in reading order (top-to-bottom, left-to-right). Never return only a partial block. "
                    "Always include the roi_fallback field when requested. "
                    "すべてのの内容を日本語に正確に翻訳してください。なお、コードはそのまま出力してください。"
                 )
        
        return f"{base}\nOutput JSON schema:\n{schema}\n"

    async def aclose(self) -> None:
        await self._client.aclose()
