import datetime as dt

import pytest
from pydantic import ValidationError

from app.schemas import BookingCreate, ScheduleCreate, error_code


def schedule(**overrides) -> dict:
    data = {
        "doctor_id": "1",
        "date": (dt.date.today() + dt.timedelta(days=3)).isoformat(),
        "start_time": "09:00",
        "end_time": "12:00",
        "slot_duration": "30",
    }
    return {**data, **overrides}


def code_for(model, **data) -> str:
    with pytest.raises(ValidationError) as exc_info:
        model(**data)
    return error_code(exc_info.value)


def test_valid_schedule_is_parsed():
    data = ScheduleCreate(**schedule())
    assert data.slot_duration == 30
    assert data.start_time == dt.time(9)


@pytest.mark.parametrize("duration", ["1", "240"])
def test_duration_limits_are_inclusive(duration):
    data = ScheduleCreate(**schedule(start_time="08:00", end_time="13:00", slot_duration=duration))
    assert data.slot_duration == int(duration)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"slot_duration": "0"}, "invalid_duration"),
        ({"slot_duration": "-10"}, "invalid_duration"),
        ({"slot_duration": "241"}, "invalid_duration"),
        ({"slot_duration": "abc"}, "invalid_duration"),
        ({"slot_duration": "12.5"}, "invalid_duration"),
        ({"end_time": "09:00"}, "invalid_time_range"),
        ({"end_time": "08:00"}, "invalid_time_range"),
        ({"end_time": "09:20"}, "duration_too_long"),
        ({"start_time": "25:00"}, "invalid_time"),
        ({"date": "2020-01-01"}, "past_date"),
        ({"date": "not-a-date"}, "invalid_date"),
        ({"date": ""}, "invalid_date"),
        ({"doctor_id": ""}, "doctor_not_found"),
        ({"doctor_id": "0"}, "doctor_not_found"),
    ],
)
def test_invalid_schedule_input(overrides, expected):
    assert code_for(ScheduleCreate, **schedule(**overrides)) == expected


def test_booking_name_is_normalised():
    assert BookingCreate(patient_name="  Lina    Haddad ").patient_name == "Lina Haddad"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_patient_name(name):
    assert code_for(BookingCreate, patient_name=name) == "invalid_name"


# --- Routes ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"slot_duration": "0"}, "between 1 and 240 minutes"),
        ({"end_time": "08:00"}, "End time must be after start time"),
        ({"date": "2020-01-01"}, "date in the past"),
        ({"doctor_id": "999"}, "Please choose a doctor"),
    ],
)
def test_generate_route_shows_validation_errors(client, overrides, message):
    response = client.post("/generate-schedule", data=schedule(**overrides))

    assert response.status_code == 200
    assert message in response.text


def test_generate_route_redirect_is_url_encoded(client):
    response = client.post(
        "/generate-schedule", data=schedule(doctor_id="1&error=x y"), follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/?doctor_id=1%26error%3Dx+y&")


def test_empty_patient_name_is_rejected(client, db, doctor):
    from app import services

    day = dt.date.today() + dt.timedelta(days=3)
    services.generate_slots(db, doctor.id, day, dt.time(9), dt.time(10), 30)
    slot = services.list_slots(db, doctor.id, day)[0]

    response = client.post(f"/book-slot/{slot.id}", data={"patient_name": "   "})
    assert "Please enter the patient's name" in response.text.replace("&#39;", "'")
    db.refresh(slot)
    assert not slot.is_booked


def test_bad_query_params_fall_back_to_defaults(client):
    assert client.get("/", params={"doctor_id": "abc", "date": "nope"}).status_code == 200


def test_unknown_error_code_shows_generic_text(client):
    response = client.get("/", params={"error": "<script>alert(1)</script>"})
    assert "Something went wrong" in response.text
    assert "<script>alert(1)</script>" not in response.text
