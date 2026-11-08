"""The agent loop, driven by a scripted (mocked) LLM."""

import datetime as dt
import json

import pytest
from assistant_fakes import ScriptedProvider, call, say

from app import services
from app.assistant import agent as agent_module
from app.assistant.agent import BookingAssistant
from app.assistant.providers.base import ModelResponseError, ModelTurn, ProviderNotConfigured, ProviderUnavailable
from app.assistant.sessions import ChatSession
from app.clock import today

PHONE = "0791234567"


@pytest.fixture
def slots(db):
    day = today() + dt.timedelta(days=1)
    services.generate_slots(db, 3, day, dt.time(9), dt.time(11), 30)  # Dr. Rasool, Dermatology
    return services.list_slots(db, 3, day)


@pytest.fixture
def session() -> ChatSession:
    return ChatSession(id="test")


def search() -> ModelTurn:
    return call("find_available_slots", doctor="Dermatology", date="tomorrow", time_of_day="morning")


def book(slot_id: int) -> ModelTurn:
    return call("book_slot", slot_id=slot_id, patient_name="Lina Haddad", phone_number=PHONE)


def is_booked(db, slot) -> bool:
    db.refresh(slot)
    return slot.is_booked


def test_full_booking_conversation(db, session, slots):
    slot = slots[2]  # 10:00
    provider = ScriptedProvider(
        search(), say("Dr. Rasool is free at 09:00, 09:30, 10:00 and 10:30."),
        book(slot.id), say("Please confirm: Dr. Rasool tomorrow at 10:00 for Lina Haddad."),
        book(slot.id), say("Done! You're booked."),
    )
    assistant = BookingAssistant(provider)

    first = assistant.reply(session, db, "I want a dermatologist tomorrow morning")
    assert first.text.startswith("Dr. Rasool is free")
    assert first.pending is None and not first.error

    second = assistant.reply(session, db, "10:00 please. Lina Haddad, 0791234567")
    assert second.pending["slot_id"] == slot.id
    assert second.pending["start"] == "10:00"  # card details come from the database
    assert not is_booked(db, slot)

    third = assistant.reply(session, db, "Yes")
    assert third.text == "Done! You're booked."
    assert third.pending is None
    assert is_booked(db, slot)
    assert len(provider.conversations) == 1  # one conversation for the whole chat
    assert len(session.transcript) == 6


def test_model_cannot_book_and_confirm_in_the_same_turn(db, session, slots):
    provider = ScriptedProvider(search(), book(slots[0].id), book(slots[0].id), say("All booked!"))

    reply = BookingAssistant(provider).reply(session, db, "Book me tomorrow 9am, Lina Haddad 0791234567")

    assert not is_booked(db, slots[0])
    assert reply.pending is not None  # the patient still has to confirm
    results = [json.loads(r.content) for r in provider.conversation.tool_results]
    assert [r.get("status") for r in results[1:]] == ["confirmation_required", "confirmation_required"]


def test_made_up_slot_id_is_rejected(db, session, slots):
    provider = ScriptedProvider(book(slots[0].id), say("Sorry, let me search first."))

    BookingAssistant(provider).reply(session, db, "Book slot 1 for Lina Haddad 0791234567")

    assert provider.conversation.tool_results[0].error_code == "slot_not_offered"
    assert not is_booked(db, slots[0])


def test_tool_call_written_as_text_is_executed(db, session):
    text_call = 'Let me check. {"name": "list_doctors", "arguments": {}}'
    provider = ScriptedProvider(say(text_call), say("We have four doctors."))

    reply = BookingAssistant(provider).reply(session, db, "Which doctors do you have?")

    assert reply.text == "We have four doctors."
    result = provider.conversation.tool_results[0]
    assert result.call.from_text and result.call.name == "list_doctors"
    assert "Dr. Rasool" in result.content


def test_plain_text_reply_is_returned(db, session):
    provider = ScriptedProvider(say("Which doctor would you like to see, and on what day?"))
    reply = BookingAssistant(provider).reply(session, db, "I need an appointment")
    assert reply.text == "Which doctor would you like to see, and on what day?"


def test_empty_reply_gets_a_fallback_message(db, session):
    reply = BookingAssistant(ScriptedProvider(say("   "))).reply(session, db, "hello")
    assert "say it another way" in reply.text


def test_invalid_arguments_are_sent_back_so_the_model_can_retry(db, session, slots):
    provider = ScriptedProvider(
        call("find_available_slots", doctor="skin", date="next-ish"),
        search(),
        say("Found some times."),
    )
    reply = BookingAssistant(provider).reply(session, db, "skin doctor tomorrow")

    first, second = provider.conversation.tool_results
    assert first.error_code == "invalid_arguments"
    assert "date must be YYYY-MM-DD" in first.content
    assert second.error_code is None
    assert reply.text == "Found some times."


def test_retry_limit_for_invalid_tool_calls(db, session):
    bad = call("find_available_slots", doctor="skin", date="whenever")
    provider = ScriptedProvider(bad, bad, bad, say("should not be reached"))

    reply = BookingAssistant(provider).reply(session, db, "skin doctor")

    assert "trouble with that request" in reply.text
    assert len(provider.turns) == 1  # stopped after the third invalid call
    assert session.conversation is None  # dangling tool calls: start fresh next turn


def test_loop_is_capped(db, session, monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_TOOL_ROUNDS", 3)
    provider = ScriptedProvider(*[call("list_doctors") for _ in range(10)])

    reply = BookingAssistant(provider).reply(session, db, "hi")

    assert "too many steps" in reply.text
    assert len(provider.conversation.tool_results) == 3


def test_provider_down_gives_a_friendly_error(db, session):
    provider = ScriptedProvider(ProviderUnavailable("Can't reach Ollama at http://localhost:11434."))

    reply = BookingAssistant(provider).reply(session, db, "hi")

    assert reply.error
    assert "Can't reach Ollama" in reply.text
    assert session.transcript == []


def test_conversation_is_rebuilt_from_history_after_an_error(db, session):
    provider = ScriptedProvider(say("Hello! How can I help?"), ProviderUnavailable("down"), say("Back again."))
    assistant = BookingAssistant(provider)

    assistant.reply(session, db, "hi")
    assert assistant.reply(session, db, "are you there?").error
    assert assistant.reply(session, db, "hello?").text == "Back again."

    assert len(provider.conversations) == 2
    assert [m.content for m in provider.conversation.history] == ["hi", "Hello! How can I help?"]


def test_fallback_provider_is_used_when_primary_fails(db, session):
    primary = ScriptedProvider(ProviderUnavailable("Ollama is down"), name="ollama")
    fallback = ScriptedProvider(say("Hi from the fallback."), name="anthropic")

    reply = BookingAssistant(primary, fallback).reply(session, db, "hi")

    assert reply.text == "Hi from the fallback."
    assert reply.provider == "anthropic · scripted"
    assert not reply.error


def test_no_fallback_for_unusable_model_answers(db, session):
    primary = ScriptedProvider(ModelResponseError("Sorry, I can't help with that."))
    fallback = ScriptedProvider(say("should not be used"))

    reply = BookingAssistant(primary, fallback).reply(session, db, "something odd")

    assert reply.error and reply.text == "Sorry, I can't help with that."
    assert fallback.conversations == []


def test_not_configured_assistant(db, session):
    assistant = BookingAssistant(None, setup_error="Set ANTHROPIC_API_KEY in .env.")
    reply = assistant.reply(session, db, "hi")
    assert reply.error and "ANTHROPIC_API_KEY" in reply.text
    assert assistant.status() == {"ready": False, "provider": None, "message": "Set ANTHROPIC_API_KEY in .env."}


def test_status_reports_missing_model(db):
    provider = ScriptedProvider()
    provider.ready_error = ProviderNotConfigured("Run: ollama pull qwen2.5:7b")
    status = BookingAssistant(provider).status()
    assert status["ready"] is False and "ollama pull" in status["message"]


# --- Confirm / Not now buttons (no model involved) ----------------------------


def propose(db, session, slot) -> BookingAssistant:
    provider = ScriptedProvider(search(), book(slot.id), say("Please confirm."))
    assistant = BookingAssistant(provider)
    assistant.reply(session, db, "dermatologist tomorrow 9am, Lina Haddad 0791234567")
    assert session.state.pending is not None
    return assistant


def test_confirm_button_books_without_the_model(db, session, slots):
    assistant = propose(db, session, slots[0])

    reply = assistant.confirm(session, db, accept=True)

    assert is_booked(db, slots[0])
    assert reply.text.startswith("Your appointment is booked: Dr. Rasool (Dermatology)")
    assert reply.pending is None
    assert session.conversation.exchanges[-1][0] == "Yes, confirm."  # the model sees what happened


def test_not_now_button_discards_the_pending_booking(db, session, slots):
    assistant = propose(db, session, slots[0])

    reply = assistant.confirm(session, db, accept=False)

    assert not is_booked(db, slots[0])
    assert reply.pending is None and "haven't changed anything" in reply.text


def test_confirm_button_when_slot_was_taken_meanwhile(db, session, slots):
    assistant = propose(db, session, slots[0])
    services.book_slot(db, slots[0].id, "Omar Khalil", "0787654321")

    reply = assistant.confirm(session, db, accept=True)

    assert "just booked by someone else" in reply.text
    db.refresh(slots[0])
    assert slots[0].patient_name == "Omar Khalil"


def test_confirm_with_nothing_pending(db, session):
    reply = BookingAssistant(ScriptedProvider()).confirm(session, db, accept=True)
    assert "nothing waiting" in reply.text
