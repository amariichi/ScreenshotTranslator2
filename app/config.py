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
        self.model_name = os.getenv("LLAMA_MODEL_NAME", "Gemma-4-E4B-It")
        self.system_prompt = (
            "You are a precise OCR + translation engine."
            " Output the FULL text exactly as seen."
            " Do NOT omit any part such as file names, headings, or section labels."
            " Do NOT summarize. Translate every line."
            " Preserve code blocks and inline code verbatim; do NOT translate code."
            " Keep ordering and line breaks exactly."
            " If a character is unreadable, use [UNK]."
            " Output only the translated/recognized content. Do NOT output instructions or meta commentary."
            " Output plain text (no markdown formatting)."
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
