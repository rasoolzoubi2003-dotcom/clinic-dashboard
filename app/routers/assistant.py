"""Chat page and JSON API for the AI booking assistant."""

from functools import lru_cache

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.assistant.agent import MAX_MESSAGE_CHARS, AgentReply, BookingAssistant
from app.assistant.providers.factory import build_providers
from app.assistant.sessions import ChatSession, SessionStore
from app.database import get_db
from app.templating import templates

router = APIRouter()

SESSION_COOKIE = "assistant_session"


@lru_cache
def get_assistant() -> BookingAssistant:
    primary, fallback, setup_error = build_providers(config.LLM_PROVIDER, config.LLM_FALLBACK_PROVIDER)
    return BookingAssistant(primary, fallback, setup_error)


@lru_cache
def get_session_store() -> SessionStore:
    return SessionStore()


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    accept: bool


def _response(session: ChatSession, reply: AgentReply, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(
        {"reply": reply.text, "pending": reply.pending, "error": reply.error, "provider": reply.provider},
        status_code=status_code,
    )
    response.set_cookie(SESSION_COOKIE, session.id, httponly=True, samesite="lax", max_age=2 * 60 * 60)
    return response


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request, assistant: BookingAssistant = Depends(get_assistant)):
    provider = assistant.primary
    return templates.TemplateResponse(
        request=request,
        name="assistant.html",
        context={
            "provider_label": provider.label if provider else None,
            "is_local": provider is not None and provider.name == "ollama",
            "max_chars": MAX_MESSAGE_CHARS,
        },
    )


@router.get("/api/assistant/status")
def assistant_status(assistant: BookingAssistant = Depends(get_assistant)):
    return assistant.status()


# Sync endpoints: FastAPI runs them in a thread pool, so slow model calls don't block other requests.
@router.post("/api/assistant/chat")
def chat(
    body: ChatRequest,
    db: Session = Depends(get_db),
    assistant: BookingAssistant = Depends(get_assistant),
    store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    message = body.message.strip()
    if not message:
        return _error("Please type a message.", 400)
    if len(message) > MAX_MESSAGE_CHARS:
        return _error(f"Messages can be at most {MAX_MESSAGE_CHARS} characters.", 400)

    session = store.get_or_create(session_id)
    if not session.lock.acquire(blocking=False):
        return _error("Still working on your previous message. Please wait.", 429)
    try:
        reply = assistant.reply(session, db, message)
    finally:
        session.lock.release()
    return _response(session, reply)


@router.post("/api/assistant/confirm")
def confirm(
    body: ConfirmRequest,
    db: Session = Depends(get_db),
    assistant: BookingAssistant = Depends(get_assistant),
    store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    session = store.get_or_create(session_id)
    if not session.lock.acquire(blocking=False):
        return _error("Still working on your previous message. Please wait.", 429)
    try:
        reply = assistant.confirm(session, db, body.accept)
    finally:
        session.lock.release()
    return _response(session, reply)


@router.post("/api/assistant/reset")
def reset(
    store: SessionStore = Depends(get_session_store),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    store.delete(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response
