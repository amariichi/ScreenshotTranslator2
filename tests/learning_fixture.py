"""Synthetic local fixture for actual GTK/GNOME smoke tests. No model or video access."""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI
import uvicorn
from app.learning import LearningService
from app.learning_api import router, resolve_service
from app.learning_store import LearningStore


class FixtureModel:
    async def translate(self, data, context, user_context):
        await asyncio.sleep(0.03)
        return {"blocks": [
            {"kind": "subtitle", "speaker": None, "source": "Let's hear it for Alex!",
             "translation": "アレックスに拍手を！", "uncertain": False, "note": ""},
            {"kind": "sms", "speaker": "Mia", "source": "Maybe it wasn't meant to be.",
             "translation": "たぶん、縁がなかったのかもね。", "uncertain": False, "note": ""}],
            "order_uncertain": False, "ocr_note": ""}

    async def explain(self, entries, known_words, user_context):
        await asyncio.sleep(0.15)
        entry = entries[0]
        return {"items": [{"entry_id": entry["entry_id"], "quote": "hear it for Alex",
                           "expression": "let's hear it for", "meaning_ja": "〜に拍手を送ろう。",
                           "context_ja": "この台詞ではアレックスへの拍手を促しています。",
                           "register_ja": "人を紹介・称賛する場面", "uncertain": False}]}

    async def refine_quote(self, item, entry, user_context):
        return {key: item[key] for key in ("meaning_ja", "context_ja", "register_ja", "uncertain")}


class FixtureService(LearningService):
    active_waiters = 0
    total_waits = 0

    async def changes(self, *args, **kwargs):
        self.active_waiters += 1
        self.total_waits += 1
        try:
            return await super().changes(*args, **kwargs)
        finally:
            self.active_waiters -= 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    service = FixtureService(LearningStore(args.data_dir), FixtureModel())
    app = FastAPI()
    app.include_router(router)

    async def fixture_service():
        return service

    app.dependency_overrides[resolve_service] = fixture_service
    @app.get("/api/learning/fixture-ready")
    async def fixture_ready():
        return {"data_dir": args.data_dir, "active_waiters": service.active_waiters,
                "total_waits": service.total_waits}
    app.add_event_handler("shutdown", service.close)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
