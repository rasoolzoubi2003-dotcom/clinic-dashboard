"""Assistant tools: argument validation, slot-ID checks, confirmation guard and privacy."""

import datetime as dt
import json

import pytest

from app import services
from app.assistant.dates import resolve_date
from app.assistant.providers.base import ToolCall
from app.assistant.tools import (
    MAX_SLOTS_SHOWN,
    TOOL_SPECS,
    AssistantState,
    BookSlotArgs,
    CancelBookingArgs,
    FindAvailableSlotsArgs,
    FindBookingsArgs,
    ToolExecutor,
)
from app.clock import today
from app.models import Doctor

PHONE = "0791234567"


@pytest.fixture
def tomorrow() -> dt.date:
    return today() + dt.timedelta(days=1)


@pytest.fixture
def state() -> AssistantState:
    return AssistantState(user_turn=1)


@pytest.fixture
def run(db, state):
    executor = ToolExecutor(db, state)

    def _run(name: str, arguments=None):
        result = executor.execute(ToolCall(id="1", name=name, arguments=arguments if arguments is not None else {}))
        return json.loads(result.content), result.error_code

    return _run


@pytest.fixture
def derm_slots(db, tomorrow):
    derm = db.query(Doctor).filter_by(specialty="Dermatology").one()
    services.generate_slots(db, derm.id, tomorrow, dt.time(9), dt.time(11), 30)
    services.generate_slots(db, derm.id, tomorrow, dt.time(15), dt.time(16), 30)
    return services.list_slots(db, derm.id, tomorrow)


def find(run, tomorrow, **overrides):
    args = {"doctor": "Dermatology", "date": tomorrow.isoformat(), "time_of_day": "any", **overrides}
    return run("find_available_slots", args)


def book_args(slot_id: int, **overrides) -> dict:
    return {"slot_id": slot_id, "patient_name": "Lina Haddad", "phone_number": PHONE, **overrides}


# --- Tool definitions -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("find_available_slots", FindAvailableSlotsArgs),
        ("book_slot", BookSlotArgs),
        ("find_bookings", FindBookingsArgs),
        ("cancel_booking", CancelBookingArgs),
    ],
)
def test_tool_schemas_match_argument_models(name, model):
    spec = next(s for s in TOOL_SPECS if s.name == name)
    assert set(spec.parameters["properties"]) == set(model.model_fields)
    assert set(spec.parameters["required"]) <= set(model.model_fields)


# --- list_doctors / find_available_slots ------------------------------------


def test_list_doctors(run):
    data, error = run("list_doctors")
    assert error is None
    assert {d["specialty"] for d in data["doctors"]} == {"Cardiology", "Dermatology", "Pediatrics", "General Medicine"}


@pytest.mark.parametrize("doctor", ["Dermatology", "dermatologist", "skin doctor", "Dr. Rasool", "rasool", "dr rasool"])
def test_find_slots_matches_doctor_by_name_or_specialty(run, derm_slots, tomorrow, doctor):
    data, error = find(run, tomorrow, doctor=doctor)
    assert error is None
    assert data["doctor"]["name"] == "Dr. Rasool"
    assert len(data["available_slots"]) == len(derm_slots)


def test_find_slots_records_offered_ids(run, state, derm_slots, tomorrow):
    data, _ = find(run, tomorrow)
    assert state.offered_slot_ids == {s["slot_id"] for s in data["available_slots"]}


@pytest.mark.parametrize(("time_of_day", "hours"), [("morning", [9, 9, 10, 10]), ("afternoon", [15, 15]), ("evening", [])])
def test_find_slots_time_of_day(run, derm_slots, tomorrow, time_of_day, hours):
    data, _ = find(run, tomorrow, time_of_day=time_of_day)
    assert [int(s["start"][:2]) for s in data["available_slots"]] == hours


def test_unknown_time_of_day_means_any(run, derm_slots, tomorrow):
    data, error = find(run, tomorrow, time_of_day="whenever works")
    assert error is None
    assert data["time_of_day"] == "any"
    assert len(data["available_slots"]) == len(derm_slots)


def test_find_slots_accepts_day_words(run, derm_slots):
    data, error = run("find_available_slots", {"doctor": "skin", "date": "tomorrow"})
    assert error is None
    assert data["date"] == (today() + dt.timedelta(days=1)).isoformat()


def test_find_slots_caps_results(run, db, tomorrow):
    services.generate_slots(db, 1, tomorrow, dt.time(8), dt.time(18), 15)
    data, _ = run("find_available_slots", {"doctor": "Cardiology", "date": tomorrow.isoformat()})
    assert len(data["available_slots"]) == MAX_SLOTS_SHOWN
    assert data["more_available"] > 0


def test_no_slots_suggests_next_dates(run, db, tomorrow):
    later = tomorrow + dt.timedelta(days=2)
    services.generate_slots(db, 1, later, dt.time(9), dt.time(10), 30)

    data, error = run("find_available_slots", {"doctor": "heart", "date": tomorrow.isoformat()})
    assert error is None
    assert data["available_slots"] == []
    assert data["next_available_dates"] == [later.isoformat()]


def test_ambiguous_doctor_returns_candidates(run, db, tomorrow):
    db.add(Doctor(name="Dr. Huda", specialty="Cardiology"))
    db.commit()
    data, error = run("find_available_slots", {"doctor": "cardiology", "date": tomorrow.isoformat()})
    assert error is None
    assert data["status"] == "ambiguous_doctor"
    assert {c["name"] for c in data["candidates"]} == {"Dr. Zoubi", "Dr. Huda"}


def test_unknown_doctor(run, tomorrow):
    data, error = run("find_available_slots", {"doctor": "Dr. Nobody", "date": tomorrow.isoformat()})
    assert error == "doctor_not_found"
    assert "list_doctors" in data["message"]


def test_past_date_is_reported(run):
    data, error = run("find_available_slots", {"doctor": "skin", "date": "2020-01-01"})
    assert error is None
    assert data["status"] == "past_date"


# --- Argument validation -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "problem"),
    [
        ("find_available_slots", {"doctor": "skin", "date": "someday"}, "date must be YYYY-MM-DD"),
        ("find_available_slots", {"date": "tomorrow"}, "doctor is required"),
        ("book_slot", {"slot_id": -1, "patient_name": "Lina", "phone_number": PHONE}, "slot_id must be a positive"),
        ("book_slot", {"slot_id": "abc", "patient_name": "Lina", "phone_number": PHONE}, "slot_id must be a positive"),
        ("book_slot", {"slot_id": 1, "patient_name": "Lina"}, "phone_number is required"),
        ("book_slot", {"slot_id": 1, "patient_name": "Lina", "phone_number": "12"}, "7-15 digits"),
        ("find_bookings", {"patient_name": " ", "phone_number": PHONE}, "patient_name must be"),
    ],
)
def test_invalid_arguments_return_clear_errors(run, name, arguments, problem):
    data, error = run(name, arguments)
    assert error == "invalid_arguments"
    assert any(problem in p for p in data["problems"]), data["problems"]


def test_arguments_as_json_string_are_accepted(run):
    data, error = run("list_doctors", "{}")
    assert error is None and data["doctors"]


@pytest.mark.parametrize("arguments", ["not json", [1, 2]])
def test_non_object_arguments(run, arguments):
    _, error = run("list_doctors", arguments)
    assert error == "invalid_arguments"


def test_unknown_tool(run):
    data, error = run("delete_everything")
    assert error == "unknown_tool"
    assert "book_slot" in data["message"]


def test_extra_arguments_are_ignored(run):
    _, error = run("list_doctors", {"unexpected": True})
    assert error is None


def test_resolve_date_words():
    saturday = dt.date(2026, 11, 7)
    assert resolve_date("Sunday", saturday) == dt.date(2026, 11, 8)
    assert resolve_date("on Monday", saturday) == dt.date(2026, 11, 9)
    assert resolve_date("saturday", saturday) == saturday
    assert resolve_date("next Saturday", saturday) == dt.date(2026, 11, 14)
    assert resolve_date("the day after tomorrow", saturday) == dt.date(2026, 11, 9)
    assert resolve_date("2026-11-20 (Friday)", saturday) == "2026-11-20"
    assert resolve_date("someday", saturday) == "someday"


# --- book_slot: slot IDs are never trusted ----------------------------------


def test_cannot_book_a_slot_that_was_not_offered(run, db, derm_slots):
    # The ID exists, but this chat never saw it (e.g. the model made it up).
    data, error = run("book_slot", book_args(derm_slots[0].id))
    assert error == "slot_not_offered"
    assert "find_available_slots" in data["message"]
    db.refresh(derm_slots[0])
    assert not derm_slots[0].is_booked


def test_cannot_book_a_slot_that_does_not_exist(run, state):
    state.offered_slot_ids.add(9999)
    _, error = run("book_slot", book_args(9999))
    assert error == "slot_not_found"


def test_slot_booked_by_someone_else(run, db, state, derm_slots, tomorrow):
    find(run, tomorrow)
    services.book_slot(db, derm_slots[0].id, "Omar Khalil", "0787654321")
    data, error = run("book_slot", book_args(derm_slots[0].id))
    assert error == "slot_already_booked"
    assert "Omar" not in json.dumps(data)


# --- Confirmation guard ------------------------------------------------------


def test_booking_needs_confirmation_in_a_later_turn(run, db, state, derm_slots, tomorrow):
    find(run, tomorrow)
    slot_id = derm_slots[0].id

    data, error = run("book_slot", book_args(slot_id))
    assert error is None
    assert data["status"] == "confirmation_required"
    assert data["details"]["doctor"] == "Dr. Rasool"
    assert state.pending is not None

    # Same call in the same turn (the patient hasn't replied): still not booked.
    data, _ = run("book_slot", book_args(slot_id))
    assert data["status"] == "confirmation_required"
    db.refresh(derm_slots[0])
    assert not derm_slots[0].is_booked

    # The patient replied: now it books.
    state.user_turn += 1
    data, error = run("book_slot", book_args(slot_id))
    assert error is None
    assert data["status"] == "booked"
    assert state.pending is None
    db.refresh(derm_slots[0])
    assert derm_slots[0].patient_name == "Lina Haddad"
    assert derm_slots[0].phone_number == PHONE


def test_changed_arguments_need_a_new_confirmation(run, db, state, derm_slots, tomorrow):
    find(run, tomorrow)
    run("book_slot", book_args(derm_slots[0].id))
    state.user_turn += 1

    data, _ = run("book_slot", book_args(derm_slots[1].id))  # patient picked another time
    assert data["status"] == "confirmation_required"
    assert state.pending.slot_id == derm_slots[1].id
    data, _ = run("book_slot", book_args(derm_slots[1].id, phone_number="0790000000"))
    assert data["status"] == "confirmation_required"

    db.refresh(derm_slots[0])
    db.refresh(derm_slots[1])
    assert not derm_slots[0].is_booked and not derm_slots[1].is_booked


def test_confirmed_booking_still_checks_the_database(run, db, state, derm_slots, tomorrow):
    find(run, tomorrow)
    run("book_slot", book_args(derm_slots[0].id))
    services.book_slot(db, derm_slots[0].id, "Omar Khalil", "0787654321")  # someone else was faster
    state.user_turn += 1

    _, error = run("book_slot", book_args(derm_slots[0].id))
    assert error == "slot_already_booked"


# --- Privacy: find_bookings and cancel_booking need name AND phone ------------


@pytest.fixture
def bookings(db, derm_slots):
    lina = services.book_slot(db, derm_slots[0].id, "Lina Haddad", PHONE)
    omar = services.book_slot(db, derm_slots[1].id, "Omar Khalil", "0787654321")
    return lina, omar


def test_find_bookings_returns_only_the_patients_own(run, bookings):
    data, error = run("find_bookings", {"patient_name": "lina haddad", "phone_number": "079-123-4567"})
    assert error is None
    assert [b["slot_id"] for b in data["bookings"]] == [bookings[0].id]
    text = json.dumps(data)
    assert "Omar" not in text and "0787654321" not in text


@pytest.mark.parametrize(
    ("name", "phone"),
    [("Lina Haddad", "0787654321"), ("Omar Khalil", PHONE), ("Lina", PHONE)],
)
def test_find_bookings_with_wrong_details_reveals_nothing(run, bookings, name, phone):
    data, error = run("find_bookings", {"patient_name": name, "phone_number": phone})
    assert error is None
    assert data["bookings"] == []
    assert "Omar" not in json.dumps(data)


def test_cancel_own_booking_after_confirmation(run, db, state, bookings):
    lina, _ = bookings
    args = {"slot_id": lina.id, "patient_name": "Lina Haddad", "phone_number": PHONE}

    data, _ = run("cancel_booking", args)
    assert data["status"] == "confirmation_required"
    db.refresh(lina)
    assert lina.is_booked

    state.user_turn += 1
    data, error = run("cancel_booking", args)
    assert error is None and data["status"] == "cancelled"
    db.refresh(lina)
    assert not lina.is_booked


@pytest.mark.parametrize(
    ("name", "phone"),
    [("Lina Haddad", PHONE), ("Omar Khalil", PHONE), ("Lina Haddad", "0787654321")],
)
def test_cannot_cancel_someone_elses_booking(run, db, state, bookings, name, phone):
    _, omar = bookings
    data, error = run("cancel_booking", {"slot_id": omar.id, "patient_name": name, "phone_number": phone})
    assert error == "booking_not_found"
    assert state.pending is None
    assert "Omar" not in json.dumps(data)
    db.refresh(omar)
    assert omar.is_booked


def test_find_and_cancel_when_model_drops_the_leading_zero(run, db, state, bookings):
    lina, _ = bookings
    data, _ = run("find_bookings", {"patient_name": "Lina Haddad", "phone_number": 791234567})  # a number, no 0
    assert [b["slot_id"] for b in data["bookings"]] == [lina.id]

    args = {"slot_id": lina.id, "patient_name": "Lina Haddad", "phone_number": "791234567"}
    run("cancel_booking", args)
    state.user_turn += 1
    # The confirmation may come back with the zero restored: still the same action.
    data, error = run("cancel_booking", {**args, "phone_number": "0791234567"})
    assert error is None and data["status"] == "cancelled"
