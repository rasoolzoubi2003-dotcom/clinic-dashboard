"""Input validation for form submissions.

Custom checks raise PydanticCustomError with a short code (e.g. "past_date"),
so routes can turn any ValidationError into a message code with `error_code()`.
"""

import datetime as dt

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.clock import today

MIN_SLOT_MINUTES = 1
MAX_SLOT_MINUTES = 240
MAX_NAME_LENGTH = 100


class ScheduleCreate(BaseModel):
    doctor_id: int = Field(gt=0)
    date: dt.date
    start_time: dt.time
    end_time: dt.time
    slot_duration: int = Field(ge=MIN_SLOT_MINUTES, le=MAX_SLOT_MINUTES)

    @field_validator("date")
    @classmethod
    def date_not_in_past(cls, value: dt.date) -> dt.date:
        if value < today():
            raise PydanticCustomError("past_date", "Date can't be in the past")
        return value

    @model_validator(mode="after")
    def check_time_range(self) -> "ScheduleCreate":
        if self.end_time <= self.start_time:
            raise PydanticCustomError("invalid_time_range", "End time must be after start time")
        start = dt.datetime.combine(self.date, self.start_time)
        end = dt.datetime.combine(self.date, self.end_time)
        if end - start < dt.timedelta(minutes=self.slot_duration):
            raise PydanticCustomError("duration_too_long", "Slot duration is longer than the time range")
        return self


class BookingCreate(BaseModel):
    patient_name: str

    @field_validator("patient_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or len(value) > MAX_NAME_LENGTH:
            raise PydanticCustomError("invalid_name", "Name must be 1-100 characters")
        return value


_CUSTOM_CODES = {"past_date", "invalid_time_range", "duration_too_long", "invalid_name"}
_FIELD_CODES = {
    "doctor_id": "doctor_not_found",
    "date": "invalid_date",
    "start_time": "invalid_time",
    "end_time": "invalid_time",
    "slot_duration": "invalid_duration",
    "patient_name": "invalid_name",
}


def error_code(exc: ValidationError) -> str:
    """Map the first validation error to a message code."""
    err = exc.errors()[0]
    if err["type"] in _CUSTOM_CODES:
        return err["type"]
    field = err["loc"][0] if err["loc"] else None
    return _FIELD_CODES.get(field, "invalid_input")
