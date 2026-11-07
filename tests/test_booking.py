import datetime as dt

import pytest

from app import services


@pytest.fixture
def slots(db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, dt.time(9), dt.time(11), 30)
    return services.list_slots(db, doctor.id, future_day)


def test_book_slot(db, slots):
    booked = services.book_slot(db, slots[0].id, "Lina Haddad")

    assert booked.is_booked
    assert booked.patient_name == "Lina Haddad"


def test_book_missing_slot(db):
    with pytest.raises(services.SlotNotFound):
        services.book_slot(db, 12345, "Lina")


def test_cannot_book_an_already_booked_slot(db, slots):
    services.book_slot(db, slots[0].id, "Lina")

    with pytest.raises(services.SlotAlreadyBooked):
        services.book_slot(db, slots[0].id, "Omar")
    db.refresh(slots[0])
    assert slots[0].patient_name == "Lina"


def test_patient_cannot_book_same_doctor_twice_on_one_day(db, slots):
    services.book_slot(db, slots[0].id, "Lina")

    with pytest.raises(services.PatientAlreadyBooked):
        services.book_slot(db, slots[2].id, "LINA")  # names are compared case-insensitively


def test_patient_cannot_hold_overlapping_bookings_with_different_doctors(db, slots, other_doctor, future_day):
    services.generate_slots(db, other_doctor.id, future_day, dt.time(9), dt.time(10), 30)
    other_slots = services.list_slots(db, other_doctor.id, future_day)
    services.book_slot(db, slots[0].id, "Lina")  # 09:00-09:30

    with pytest.raises(services.PatientAlreadyBooked):
        services.book_slot(db, other_slots[0].id, "Lina")  # also 09:00-09:30
    # A non-overlapping time with the other doctor is fine.
    assert services.book_slot(db, other_slots[1].id, "Lina").is_booked


def test_patient_can_book_same_doctor_on_another_day(db, slots, doctor, future_day):
    next_day = future_day + dt.timedelta(days=1)
    services.generate_slots(db, doctor.id, next_day, dt.time(9), dt.time(10), 30)
    services.book_slot(db, slots[0].id, "Lina")

    other = services.list_slots(db, doctor.id, next_day)[0]
    assert services.book_slot(db, other.id, "Lina").is_booked


def test_different_patients_can_book_different_slots(db, slots):
    services.book_slot(db, slots[0].id, "Lina")
    assert services.book_slot(db, slots[1].id, "Omar").is_booked


def test_name_with_sql_wildcards_is_compared_literally(db, slots):
    services.book_slot(db, slots[0].id, "Lina")
    # "%" used to match every name through ILIKE.
    assert services.book_slot(db, slots[1].id, "%").is_booked


def test_cannot_book_a_slot_in_the_past(db, slots, monkeypatch):
    later = dt.datetime.combine(slots[0].date, dt.time(12))
    monkeypatch.setattr(services, "now", lambda: later)

    with pytest.raises(services.SlotInPast):
        services.book_slot(db, slots[0].id, "Lina")


def test_cancel_booking_frees_the_slot(db, slots):
    services.book_slot(db, slots[0].id, "Lina")
    cancelled = services.cancel_booking(db, slots[0].id)

    assert not cancelled.is_booked
    assert cancelled.patient_name is None
    assert services.book_slot(db, slots[0].id, "Omar").patient_name == "Omar"


def test_cancel_unbooked_slot(db, slots):
    with pytest.raises(services.SlotNotBooked):
        services.cancel_booking(db, slots[0].id)


def test_cancel_missing_slot(db):
    with pytest.raises(services.SlotNotFound):
        services.cancel_booking(db, 12345)


# --- Routes ---------------------------------------------------------------


def test_book_and_cancel_through_routes(client, slots):
    booked = client.post(f"/book-slot/{slots[0].id}", data={"patient_name": "  Lina   Haddad "})
    assert "Appointment booked." in booked.text
    assert "Lina Haddad" in booked.text

    cancelled = client.post(f"/cancel-slot/{slots[0].id}")
    assert "Booking cancelled." in cancelled.text
    assert "Lina Haddad" not in cancelled.text


def test_double_booking_route_shows_error_and_keeps_data(client, slots):
    client.post(f"/book-slot/{slots[0].id}", data={"patient_name": "Lina"})
    response = client.post(f"/book-slot/{slots[0].id}", data={"patient_name": "Omar"})

    assert "already been booked" in response.text
    assert response.text.count("Booked</span>") == 1
    assert response.text.count("Available</span>") == len(slots) - 1


def test_route_errors_for_missing_slots(client):
    assert "no longer exists" in client.post("/book-slot/999", data={"patient_name": "Lina"}).text
    assert "no longer exists" in client.post("/cancel-slot/999").text


def test_failed_actions_do_not_wipe_appointments(client, db, slots, doctor, future_day):
    client.post("/book-slot/999", data={"patient_name": "Lina"})
    client.post("/cancel-slot/999")

    assert len(services.list_slots(db, doctor.id, future_day)) == len(slots)
