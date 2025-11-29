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

    async def translate_image(self, image_bytes: bytes, prompt: Optional[str] = None) -> str:
        settings = get_settings()
        prompt_text = prompt or (
            "この画像全体を正確にOCRし、コードはそのまま出力し、"
            "英語の文章は日本語に正確に翻訳してください。要約は禁止。"
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
            "max_tokens": 1600,
            "temperature": 0.1,
            "stop": None,
            "stream": False,
            "n": 1,
            "presence_penalty": 0,
            "frequency_penalty": 0,
            "logit_bias": {},
            "extra_body": {
                "top_p": 0.6,
                "min_p": 0.05,
                "repetition_penalty": 1.05,
                "n_ctx": settings.ctx_size,
            },
        }

        url = f"{self.api_base}/v1/chat/completions"
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:  # pragma: no cover
            raise RuntimeError(f"Unexpected response: {data}") from exc

    async def get_status(self) -> str:
        """ステータス推定: /slots 優先、なければ /v1/models でAPI疎通のみ確認。"""
        url_slots = f"{self.api_base}/slots"
        try:
            resp = await self._client.get(url_slots)
            resp.raise_for_status()
            data = resp.json()
            states = {slot.get("state", "?") for slot in data.get("slots", [])} if isinstance(data, dict) else set()
            if states:
                if "loading" in states:
                    return "モデル読み込み中"
                if "active" in states:
                    return "実行中"
                if states == {"idle"}:
                    return "準備完了"
                return f"状態: {', '.join(sorted(states))}"
        except Exception:
            pass

        try:
            r_models = await self._client.get(f"{self.api_base}/v1/models")
            if r_models.status_code == 200:
                return "起動中（API応答あり・モデル読み込み未確認）"
        except Exception:
            return "起動中（状態確認待ち）"

        return "起動中（API応答あり・モデル読み込み未確認）"

    async def aclose(self) -> None:
        await self._client.aclose()
