import os
from functools import lru_cache


class Settings:
    api_base: str
    ctx_size: int
    system_prompt: str
    model_name: str

    def __init__(self) -> None:
        self.api_base = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8009")
        self.ctx_size = int(os.getenv("LLAMA_CTX", "8192"))
        self.model_name = os.getenv("LLAMA_MODEL_NAME", "qwen3-vl")
        self.system_prompt = (
            "You are a precise OCR + translation assistant."
            " 1) Extract *all* text from the image with exact spacing and line breaks."
            " 2) Preserve code blocks and inline code verbatim; do not translate code."
            " 3) For natural-language text, translate to Japanese with faithful meaning, no summary."
            " 4) Keep ordering; do not drop bullet points or lines."
            " 5) Output must be Markdown."
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
