"""Learning-only server, useful when no legacy TTS backend is needed."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .learning_api import router, close_service
from .version import __version__


@asynccontextmanager
async def lifespan(app):
    yield
    await close_service()


app = FastAPI(title="Screenshot Translator · Learning", version=__version__, lifespan=lifespan)
app.include_router(router)
