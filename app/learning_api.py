"""Opt-in learning routes; importing does not initialize models, TTS or storage."""
from functools import lru_cache
import asyncio
import math
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .learning import LearningError, LearningService

async def native_only(request: Request):
    if request.headers.get("origin"):
        raise HTTPException(403, "学習APIはローカル専用パネルから使用してください。")


router = APIRouter(prefix="/api/learning", tags=["learning"], dependencies=[Depends(native_only)])


@lru_cache()
def get_service():
    return LearningService()


async def resolve_service():
    # Keep sqlite creation and every mutation on the event-loop thread.
    return get_service()


async def close_service():
    if get_service.cache_info().currsize:
        await get_service().close()
        get_service.cache_clear()


class CreateSession(BaseModel):
    title: str = Field(default="視聴記録", max_length=200)
    source_url: str = Field(default="", max_length=2000)


class EditSession(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    user_context: Optional[str] = Field(default=None, max_length=3000)
    known_words: Optional[list[str]] = Field(default=None, max_length=500)


class Control(BaseModel):
    active: bool
    controller_id: str = Field(min_length=1, max_length=100)
    control_seq: int = Field(ge=0)


class Heartbeat(BaseModel):
    generation: int
    controller_id: str = Field(min_length=1, max_length=100)


class EditNote(BaseModel):
    meaning_ja: Optional[str] = Field(default=None, min_length=1, max_length=6000)
    context_ja: Optional[str] = Field(default=None, max_length=6000)
    register_ja: Optional[str] = Field(default=None, max_length=2000)
    uncertain: Optional[bool] = None


class AnalysisRequest(BaseModel):
    retry_failed: bool = False


class TrashRequest(BaseModel):
    confirm_title: str = Field(max_length=200)


class EmptyTrashRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_permanent: Literal[True]
    token: str = Field(min_length=32, max_length=32)


def call(operation, *args):
    try:
        return operation(*args)
    except LearningError as error:
        raise HTTPException(error.status, error.message)


@router.get("/sessions")
async def sessions(service=Depends(resolve_service)):
    return service.sessions()


@router.get("/trash")
async def trash_preview(service=Depends(resolve_service)):
    return call(service.preview_trash)


@router.post("/trash/empty")
async def trash_empty(body: EmptyTrashRequest, service=Depends(resolve_service)):
    return call(service.empty_trash, body.confirm_permanent, body.token)


@router.post("/sessions", status_code=201)
async def create(body: CreateSession, service=Depends(resolve_service)):
    return call(service.create, body.title, body.source_url)


@router.get("/sessions/{sid}")
async def snapshot(sid: str, service=Depends(resolve_service)):
    return call(service.snapshot, sid)


@router.patch("/sessions/{sid}")
async def update(sid: str, body: EditSession, service=Depends(resolve_service)):
    changes = body.model_dump(exclude_none=True)
    if "known_words" in changes and any(len(w) > 200 for w in changes["known_words"]):
        raise HTTPException(422, "既知語は1件200文字以内にしてください。")
    return call(service.update, sid, changes)


@router.post("/sessions/{sid}/control")
async def control(sid: str, body: Control, service=Depends(resolve_service)):
    return call(service.control, sid, body.active, body.controller_id, body.control_seq)


@router.get("/sessions/{sid}/changes")
async def changes(sid: str, request: Request, after: int = Query(ge=0),
                  timeout: float = Query(default=20, ge=0, le=25), service=Depends(resolve_service)):
    async def disconnected():
        while (await request.receive())["type"] != "http.disconnect":
            pass

    waiting = asyncio.create_task(service.changes(sid, after, timeout))
    connection = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((waiting, connection), return_when=asyncio.FIRST_COMPLETED)
        if waiting in done:
            return await waiting
        return Response(status_code=204)
    except LearningError as error:
        raise HTTPException(error.status, error.message)
    finally:
        waiting.cancel()
        connection.cancel()
        await asyncio.gather(waiting, connection, return_exceptions=True)


@router.post("/sessions/{sid}/heartbeat")
async def heartbeat(sid: str, body: Heartbeat, service=Depends(resolve_service)):
    return call(service.heartbeat, sid, body.generation, body.controller_id)


@router.post("/sessions/{sid}/frames")
async def frame(sid: str, image: UploadFile = File(...), generation: int = Form(...),
                sequence: int = Form(...), captured_at: float = Form(...), service=Depends(resolve_service)):
    if not math.isfinite(captured_at) or sequence < 0:
        raise HTTPException(422, "不正な取得時刻または連番です。")
    data = await image.read(8_000_001)
    if len(data) > 8_000_000:
        raise HTTPException(413, "画像は8MB以内にしてください。")
    try:
        return await service.frame(sid, data, generation, sequence, captured_at)
    except LearningError as error:
        raise HTTPException(error.status, error.message)


@router.post("/sessions/{sid}/analysis", status_code=202)
async def analyse(sid: str, body: AnalysisRequest = AnalysisRequest(), service=Depends(resolve_service)):
    return call(service.start_analysis, sid, body.retry_failed)


@router.post("/sessions/{sid}/trash")
async def trash(sid: str, body: TrashRequest, service=Depends(resolve_service)):
    return call(service.trash, sid, body.confirm_title)


@router.post("/sessions/{sid}/analysis/stop")
async def stop(sid: str, service=Depends(resolve_service)):
    return call(service.stop_analysis, sid)


@router.post("/sessions/{sid}/analysis/on-stop")
async def on_stop(sid: str, service=Depends(resolve_service)):
    return call(service.arm_analysis_on_stop, sid)


@router.patch("/sessions/{sid}/notes/{note_id}")
async def edit_note(sid: str, note_id: str, body: EditNote, service=Depends(resolve_service)):
    return call(service.edit_note, sid, note_id, body.model_dump(exclude_none=True))


@router.post("/sessions/{sid}/export")
async def export(sid: str, service=Depends(resolve_service)):
    doc = call(service.document, sid)
    service.persist(doc)
    return service.snapshot(sid)
