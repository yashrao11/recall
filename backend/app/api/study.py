from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.study.service import complete_session, generate_study_set, list_due_cards, respond_to_card

router = APIRouter(prefix="/study", tags=["study"])


class StudyGenerateRequest(BaseModel):
    source_id: int | None = Field(default=None, ge=1)
    object_id: int | None = Field(default=None, ge=1)


class StudyResponseRequest(BaseModel):
    card_id: int = Field(ge=1)
    quality: int = Field(ge=0, le=5)
    session_id: int | None = Field(default=None, ge=1)


class StudySessionRequest(BaseModel):
    source_id: int | None = Field(default=None, ge=1)
    object_id: int | None = Field(default=None, ge=1)
    started_at: str | None = None
    completed_at: str | None = None
    questions_attempted: int = Field(default=0, ge=0)
    answers_correct: int = Field(default=0, ge=0)


@router.post("/generate")
def generate(payload: StudyGenerateRequest, request: Request) -> dict:
    try:
        return generate_study_set(request.app.state.settings, source_id=payload.source_id, object_id=payload.object_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 404, detail=str(exc)) from exc


@router.get("/due")
def due(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    return list_due_cards(request.app.state.settings, limit)


@router.post("/respond")
def respond(payload: StudyResponseRequest, request: Request) -> dict:
    try:
        return respond_to_card(request.app.state.settings, payload.card_id, payload.quality, payload.session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions")
def sessions(payload: StudySessionRequest, request: Request) -> dict:
    try:
        return complete_session(request.app.state.settings, source_id=payload.source_id, object_id=payload.object_id,
                                started_at=payload.started_at, completed_at=payload.completed_at,
                                questions_attempted=payload.questions_attempted, answers_correct=payload.answers_correct)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 404, detail=str(exc)) from exc
