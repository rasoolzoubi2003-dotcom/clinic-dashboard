"""HTTP endpoints for the assistant, with a scripted LLM."""

import datetime as dt

import pytest
from assistant_fakes import ScriptedProvider, call, say

from app import services
from app.assistant.agent import BookingAssistant
from app.assistant.sessions import SessionStore
from app.clock import today
from app.main import app
from app.routers.assistant import SESSION_COOKIE, get_assistant, get_session_store


@pytest.fixture
def use_provider():
    store = SessionStore()
    app.dependency_overrides[get_session_store] = lambda: store

    def _use(*turns) -> ScriptedProvider:
        provider = ScriptedProvider(*turns)
        assistant = BookingAssistant(provider)
        app.dependency_overrides[get_assistant] = lambda: assistant
        return provider

    yield _use
    app.dependency_overrides.clear()


def test_page_renders_without_api_key_or_model(client):
    response = client.get("/assistant")
    assert response.status_code == 200
    assert 'id="chat-form"' in response.text
    assert "ollama · qwen2.5:7b" in response.text
    assert "patient data never leaves this machine" in response.text


def test_chat_keeps_one_conversation_per_session(client, use_provider):
    provider = use_provider(say("Hello! How can I help?"), say("Sure, which doctor?"))

    first = client.post("/api/assistant/chat", json={"message": "hi"})
    assert first.status_code == 200
    assert first.json() == {
        "reply": "Hello! How can I help?",
        "pending": None,
        "error": False,
        "provider": "fake · scripted",
    }
    assert SESSION_COOKIE in first.cookies

    second = client.post("/api/assistant/chat", json={"message": "I need an appointment"})
    assert second.json()["reply"] == "Sure, which doctor?"
    assert len(provider.conversations) == 1
    assert provider.conversation.user_messages == ["hi", "I need an appointment"]


@pytest.mark.parametrize(("message", "detail"), [("   ", "type a message"), ("x" * 1001, "at most 1000")])
def test_message_validation(client, use_provider, message, detail):
    use_provider()
    response = client.post("/api/assistant/chat", json={"message": message})
    assert response.status_code == 400
    assert detail in response.json()["detail"]


def test_confirm_button_endpoint_books(client, db, use_provider):
    day = today() + dt.timedelta(days=1)
    services.generate_slots(db, 3, day, dt.time(9), dt.time(10), 30)
    slot = services.list_slots(db, 3, day)[0]
    use_provider(
        call("find_available_slots", doctor="skin", date="tomorrow"),
        call("book_slot", slot_id=slot.id, patient_name="Lina Haddad", phone_number="0791234567"),
        say("Please confirm."),
    )

    proposed = client.post("/api/assistant/chat", json={"message": "skin doctor tomorrow 9am, Lina Haddad 0791234567"})
    assert proposed.json()["pending"]["doctor"] == "Dr. Rasool"

    confirmed = client.post("/api/assistant/confirm", json={"accept": True})
    assert confirmed.json()["pending"] is None
    assert "Your appointment is booked" in confirmed.json()["reply"]
    db.refresh(slot)
    assert slot.patient_name == "Lina Haddad"


def test_provider_error_is_returned_as_json(client, use_provider):
    from app.assistant.providers.base import ProviderUnavailable

    use_provider(ProviderUnavailable("Can't reach Ollama. Run: ollama pull qwen2.5:7b"))
    response = client.post("/api/assistant/chat", json={"message": "hi"})
    assert response.status_code == 200
    assert response.json()["error"] is True
    assert "ollama pull" in response.json()["reply"]


def test_reset_starts_a_new_session(client, use_provider):
    provider = use_provider(say("First."), say("Fresh start."))
    client.post("/api/assistant/chat", json={"message": "hi"})

    assert client.post("/api/assistant/reset").json() == {"ok": True}
    client.cookies.clear()
    client.post("/api/assistant/chat", json={"message": "hi again"})

    assert len(provider.conversations) == 2
    assert provider.conversation.history == []


def test_status_endpoint(client, use_provider):
    use_provider()
    assert client.get("/api/assistant/status").json() == {"ready": True, "provider": "fake · scripted", "message": None}


def test_session_store_expires_and_caps_sessions():
    store = SessionStore(ttl_seconds=60, max_sessions=2)
    a = store.get_or_create(None)
    store.get_or_create(None)
    store.get_or_create(None)
    assert len(store) == 2
    assert store.get_or_create(a.id).id != a.id  # the oldest was dropped

    expired = SessionStore(ttl_seconds=-1)
    old = expired.get_or_create(None)
    assert expired.get_or_create(old.id).id != old.id
