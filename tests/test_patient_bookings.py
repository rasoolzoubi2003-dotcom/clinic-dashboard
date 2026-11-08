"""Patient self-service lookups and cancellations must match name AND phone number."""

import datetime as dt

import pytest

from app import services

LINA_PHONE = "0791234567"
OMAR_PHONE = "0787654321"


@pytest.fixture
def booked(db, doctor, other_doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, dt.time(9), dt.time(10), 30)
    services.generate_slots(db, other_doctor.id, future_day, dt.time(11), dt.time(12), 30)
    a, b = services.list_slots(db, doctor.id, future_day)
    c, _ = services.list_slots(db, other_doctor.id, future_day)
    return {
        "lina": services.book_slot(db, a.id, "Lina Haddad", LINA_PHONE),
        "omar": services.book_slot(db, b.id, "Omar Khalil", OMAR_PHONE),
        "lina_2": services.book_slot(db, c.id, "Lina Haddad", LINA_PHONE),
    }


def test_find_bookings_returns_only_matching_patient(db, booked):
    found = services.find_patient_bookings(db, "lina haddad", LINA_PHONE)

    assert [s.id for s in found] == [booked["lina"].id, booked["lina_2"].id]
    assert all(s.patient_name == "Lina Haddad" for s in found)


@pytest.mark.parametrize(
    ("name", "phone"),
    [
        ("Lina Haddad", OMAR_PHONE),  # right name, someone else's phone
        ("Omar Khalil", LINA_PHONE),  # someone else's name, right phone
        ("Lina", LINA_PHONE),  # partial name
        ("Lina Haddad", "0790000000"),
    ],
)
def test_find_bookings_requires_name_and_phone_to_match(db, booked, name, phone):
    assert services.find_patient_bookings(db, name, phone) == []


def test_find_bookings_skips_past_bookings(db, booked, monkeypatch):
    after_all = dt.datetime.combine(booked["lina_2"].date, dt.time(23))
    monkeypatch.setattr(services, "now", lambda: after_all)

    assert services.find_patient_bookings(db, "Lina Haddad", LINA_PHONE) == []


def test_cancel_own_booking(db, booked):
    services.cancel_patient_booking(db, booked["lina"].id, "LINA HADDAD", LINA_PHONE)

    db.refresh(booked["lina"])
    assert not booked["lina"].is_booked
    assert booked["lina"].patient_name is None and booked["lina"].phone_number is None


@pytest.mark.parametrize(
    ("name", "phone"),
    [
        ("Lina Haddad", LINA_PHONE),  # Lina trying to cancel Omar's booking
        ("Omar Khalil", LINA_PHONE),  # right name, wrong phone
        ("Lina Haddad", OMAR_PHONE),  # right phone, wrong name
    ],
)
def test_cannot_cancel_someone_elses_booking(db, booked, name, phone):
    with pytest.raises(services.BookingNotFound):
        services.cancel_patient_booking(db, booked["omar"].id, name, phone)

    db.refresh(booked["omar"])
    assert booked["omar"].is_booked
    assert booked["omar"].patient_name == "Omar Khalil"


@pytest.mark.parametrize("slot_id", [999, None])
def test_missing_or_free_slot_gives_the_same_error(db, booked, doctor, future_day, slot_id):
    if slot_id is None:
        services.generate_slots(db, doctor.id, future_day, dt.time(14), dt.time(15), 30)
        slot_id = services.list_slots(db, doctor.id, future_day)[-1].id  # exists but not booked

    with pytest.raises(services.BookingNotFound):
        services.cancel_patient_booking(db, slot_id, "Lina Haddad", LINA_PHONE)


def test_available_slots_exclude_booked_and_past(db, doctor, future_day, monkeypatch):
    services.generate_slots(db, doctor.id, future_day, dt.time(9), dt.time(11), 30)
    first = services.list_slots(db, doctor.id, future_day)[0]
    services.book_slot(db, first.id, "Lina", LINA_PHONE)
    assert len(services.list_available_slots(db, doctor.id, future_day)) == 3

    monkeypatch.setattr(services, "now", lambda: dt.datetime.combine(future_day, dt.time(10)))
    remaining = services.list_available_slots(db, doctor.id, future_day)
    assert [s.start_time for s in remaining] == [dt.time(10, 30)]


def test_available_slots_time_window(db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, dt.time(9), dt.time(15), 60)

    afternoon = services.list_available_slots(db, doctor.id, future_day, dt.time(12), dt.time(17))
    assert [s.start_time.hour for s in afternoon] == [12, 13, 14]


def test_next_available_dates(db, doctor, future_day):
    for offset in (1, 3, 4, 5):
        day = future_day + dt.timedelta(days=offset)
        services.generate_slots(db, doctor.id, day, dt.time(9), dt.time(10), 60)

    dates = services.next_available_dates(db, doctor.id, future_day, limit=3)
    assert dates == [future_day + dt.timedelta(days=d) for d in (1, 3, 4)]


def test_dashboard_booking_requires_phone(client, db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, dt.time(9), dt.time(10), 30)
    slot = services.list_slots(db, doctor.id, future_day)[0]

    response = client.post(f"/book-slot/{slot.id}", data={"patient_name": "Lina", "phone_number": ""})
    assert "valid phone number" in response.text
    response = client.post(f"/book-slot/{slot.id}", data={"patient_name": "Lina", "phone_number": "079-123-4567"})
    assert "Appointment booked." in response.text
    assert "0791234567" in response.text


def test_leading_zero_dropped_by_the_model_still_matches(db, booked):
    found = services.find_patient_bookings(db, "Lina Haddad", "791234567")
    assert [s.id for s in found] == [booked["lina"].id, booked["lina_2"].id]

    services.cancel_patient_booking(db, booked["lina"].id, "Lina Haddad", "791234567")
    db.refresh(booked["lina"])
    assert not booked["lina"].is_booked


@pytest.mark.parametrize("phone", ["1791234567", "91234567", "0791234568"])
def test_similar_but_different_phone_numbers_do_not_match(db, booked, phone):
    assert services.find_patient_bookings(db, "Lina Haddad", phone) == []
    with pytest.raises(services.BookingNotFound):
        services.cancel_patient_booking(db, booked["lina"].id, "Lina Haddad", phone)
