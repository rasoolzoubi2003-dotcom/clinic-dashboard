import datetime as dt

import pytest

from app import services
from app.models import Doctor


def t(hour: int, minute: int = 0) -> dt.time:
    return dt.time(hour, minute)


def test_seed_creates_doctors_once(db):
    from app.seed import DEFAULT_DOCTORS, seed_doctors

    seed_doctors(db)  # already seeded by the fixture; running again must not duplicate
    assert db.query(Doctor).count() == len(DEFAULT_DOCTORS)


def test_generates_back_to_back_slots(db, doctor, future_day):
    result = services.generate_slots(db, doctor.id, future_day, t(9), t(11), 30)

    assert (result.created, result.skipped) == (4, 0)
    slots = services.list_slots(db, doctor.id, future_day)
    assert [(s.start_time, s.end_time) for s in slots] == [
        (t(9), t(9, 30)),
        (t(9, 30), t(10)),
        (t(10), t(10, 30)),
        (t(10, 30), t(11)),
    ]
    assert all(not s.is_booked and s.date == future_day for s in slots)


def test_last_partial_slot_is_not_created(db, doctor, future_day):
    result = services.generate_slots(db, doctor.id, future_day, t(9), t(10, 15), 30)

    assert result.created == 2
    assert services.list_slots(db, doctor.id, future_day)[-1].end_time == t(10)


def test_generating_twice_creates_no_duplicates(db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, t(9), t(11), 30)
    second = services.generate_slots(db, doctor.id, future_day, t(9), t(11), 30)

    assert (second.created, second.skipped) == (0, 4)
    assert len(services.list_slots(db, doctor.id, future_day)) == 4


def test_overlapping_schedule_only_adds_free_time(db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, t(9), t(10), 30)
    result = services.generate_slots(db, doctor.id, future_day, t(9, 15), t(11), 30)

    # 09:15-09:45 and 09:45-10:15 overlap existing slots; 10:15-10:45 is free.
    assert (result.created, result.skipped) == (1, 2)


def test_same_times_for_another_doctor_or_day_are_allowed(db, doctor, other_doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, t(9), t(10), 30)

    assert services.generate_slots(db, other_doctor.id, future_day, t(9), t(10), 30).created == 2
    next_day = future_day + dt.timedelta(days=1)
    assert services.generate_slots(db, doctor.id, next_day, t(9), t(10), 30).created == 2


def test_unknown_doctor_is_rejected(db, future_day):
    with pytest.raises(services.DoctorNotFound):
        services.generate_slots(db, 999, future_day, t(9), t(10), 30)


@pytest.mark.parametrize("duration", [0, -15])
def test_service_rejects_non_positive_duration(db, doctor, future_day, duration):
    # Previously this looped forever.
    with pytest.raises(services.InvalidSchedule):
        services.generate_slots(db, doctor.id, future_day, t(9), t(10), duration)


def test_generate_route_shows_result(client, doctor, future_day):
    form = {
        "doctor_id": doctor.id,
        "date": future_day.isoformat(),
        "start_time": "09:00",
        "end_time": "10:00",
        "slot_duration": "20",
    }
    first = client.post("/generate-schedule", data=form)
    second = client.post("/generate-schedule", data=form)

    assert "Created 3 new slots." in first.text
    assert "No new slots were created: all 3 already exist." in second.text
    assert second.text.count("Available</span>") == 3


def test_page_load_does_not_delete_data(client, db, doctor, future_day):
    services.generate_slots(db, doctor.id, future_day, t(9), t(10), 30)

    assert client.get("/").status_code == 200
    assert client.get("/", params={"doctor_id": doctor.id, "date": future_day}).status_code == 200
    assert len(services.list_slots(db, doctor.id, future_day)) == 2
