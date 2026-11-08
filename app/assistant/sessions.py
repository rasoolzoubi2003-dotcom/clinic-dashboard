"""In-memory chat sessions, keyed by a random cookie value.

Sessions are lost when the server restarts and aren't shared between server processes.
That's fine for a single-instance demo; a production setup would store them in Redis or the database.
"""

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.assistant.providers.base import ChatMessage, Conversation, LLMProvider
from app.assistant.tools import AssistantState


@dataclass
class ChatSession:
    id: str
    state: AssistantState = field(default_factory=AssistantState)
    transcript: list[ChatMessage] = field(default_factory=list)  # completed exchanges only
    provider: LLMProvider | None = None
    conversation: Conversation | None = None  # rebuilt from the transcript when None
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_active: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(self, ttl_seconds: float = 2 * 60 * 60, max_sessions: int = 500):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None) -> ChatSession:
        with self._lock:
            self._evict_expired()
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                session = ChatSession(id=secrets.token_urlsafe(24))
                self._sessions[session.id] = session
                while len(self._sessions) > self.max_sessions:
                    self._sessions.popitem(last=False)  # drop the least recently used
            session.last_active = time.monotonic()
            self._sessions.move_to_end(session.id)
            return session

    def delete(self, session_id: str | None) -> None:
        with self._lock:
            self._sessions.pop(session_id or "", None)

    def __len__(self) -> int:
        return len(self._sessions)

    def _evict_expired(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        for key in [k for k, s in self._sessions.items() if s.last_active < cutoff]:
            del self._sessions[key]
