from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.db.database import get_connection
from app.rag.conversations import get_conversation, list_conversations, list_messages
from app.rag.pipeline import ask_question


router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = Field(default=None, ge=1)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/ask")
def ask(payload: AskRequest, request: Request) -> dict:
    try:
        return ask_question(request.app.state.settings, payload.question, payload.conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/conversations")
def conversations(request: Request, limit: int = 50, offset: int = 0) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        items = list_conversations(connection, limit, offset)
    return {
        "conversations": items,
        "limit": min(max(limit, 1), 100),
        "offset": max(offset, 0),
        "has_more": len(items) == min(max(limit, 1), 100),
    }


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: int, request: Request) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        conversation = get_conversation(connection, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        conversation["messages"] = list_messages(connection, conversation_id, 100, 0)
    return conversation


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(
    conversation_id: int, request: Request, limit: int = 50, offset: int = 0
) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        if get_conversation(connection, conversation_id) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        items = list_messages(connection, conversation_id, limit, offset)
    safe_limit = min(max(limit, 1), 100)
    return {
        "conversation_id": conversation_id,
        "messages": items,
        "limit": safe_limit,
        "offset": max(offset, 0),
        "has_more": len(items) == safe_limit,
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, request: Request) -> Response:
    with get_connection(request.app.state.settings.database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="conversation not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages")
def add_message(conversation_id: int, payload: MessageRequest, request: Request) -> dict:
    try:
        return ask_question(request.app.state.settings, payload.content, conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
