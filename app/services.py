"""Business logic for schedules and bookings.

Both the dashboard routes and the AI assistant's tools call these functions, so every
booking rule lives in one place. Failures raise a ServiceError subclass whose `code`
the UI maps to a message.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.clock import now
from app.models import Appointment, Doctor


class ServiceError(Exception):
    code = "error"


class DoctorNotFound(ServiceError):
    code = "doctor_not_found"


class SlotNotFound(ServiceError):
    code = "slot_not_found"


class SlotAlreadyBooked(ServiceError):
    code = "slot_already_booked"


class PatientAlreadyBooked(ServiceError):
    code = "patient_already_booked"


class SlotNotBooked(ServiceError):
    code = "slot_not_booked"


class SlotInPast(ServiceError):
    code = "slot_in_past"


class InvalidSchedule(ServiceError):
    code = "invalid_input"


class BookingNotFound(ServiceError):
    """No booking matches this slot, name and phone number.

    Deliberately the same error whether the slot is missing, free, or booked by
    someone else, so callers can't learn anything about other patients.
    """

    code = "booking_not_found"


# --- Doctors ---------------------------------------------------------------


def list_doctors(db: Session) -> list[Doctor]:
    return list(db.scalars(select(Doctor).order_by(Doctor.name)))


def get_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise DoctorNotFound()
    return doctor


# --- Schedules -------------------------------------------------------------


@dataclass
class GenerationResult:
    created: int
    skipped: int


def generate_slots(
    db: Session, doctor_id: int, day: date, start: time, end: time, duration_minutes: int
) -> GenerationResult:
    """Create back-to-back slots between start and end.

    Slots that would overlap an existing slot for the same doctor and day are skipped,
    so generating the same schedule twice never creates duplicates.
    """
    # Routes validate input with schemas.ScheduleCreate; this guard keeps direct callers
    # from ever starting an endless loop.
    if duration_minutes < 1 or end <= start:
        raise InvalidSchedule()
    get_doctor(db, doctor_id)

    existing = [(s.start_time, s.end_time) for s in list_slots(db, doctor_id, day)]
    step = timedelta(minutes=duration_minutes)
    current = datetime.combine(day, start)
    end_dt = datetime.combine(day, end)

    created = skipped = 0
    while current + step <= end_dt:
        slot_start, slot_end = current.time(), (current + step).time()
        if any(s < slot_end and e > slot_start for s, e in existing):
            skipped += 1
        else:
            db.add(Appointment(doctor_id=doctor_id, date=day, start_time=slot_start, end_time=slot_end))
            created += 1
        current += step
    db.commit()
    return GenerationResult(created=created, skipped=skipped)


def list_slots(db: Session, doctor_id: int, day: date) -> list[Appointment]:
    query = (
        select(Appointment)
        .options(joinedload(Appointment.doctor))
        .where(Appointment.doctor_id == doctor_id, Appointment.date == day)
        .order_by(Appointment.start_time)
    )
    return list(db.scalars(query))


def list_available_slots(
    db: Session,
    doctor_id: int,
    day: date,
    start_from: time | None = None,
    start_before: time | None = None,
) -> list[Appointment]:
    """Free slots for a doctor on a day that haven't started yet, optionally within a time window."""
    current = now()
    return [
        slot
        for slot in list_slots(db, doctor_id, day)
        if not slot.is_booked
        and datetime.combine(slot.date, slot.start_time) > current
        and (start_from is None or slot.start_time >= start_from)
        and (start_before is None or slot.start_time < start_before)
    ]


def next_available_dates(db: Session, doctor_id: int, after: date, limit: int = 3) -> list[date]:
    """The next few dates after `after` on which the doctor has a free slot."""
    current = now()
    query = (
        select(Appointment.date, Appointment.start_time)
        .where(Appointment.doctor_id == doctor_id, Appointment.date > after, Appointment.is_booked.is_(False))
        .order_by(Appointment.date, Appointment.start_time)
    )
    dates: list[date] = []
    for day, start in db.execute(query):
        if datetime.combine(day, start) > current and day not in dates:
            dates.append(day)
            if len(dates) == limit:
                break
    return dates


# --- Bookings --------------------------------------------------------------


def get_slot(db: Session, slot_id: int) -> Appointment:
    slot = db.get(Appointment, slot_id)
    if slot is None:
        raise SlotNotFound()
    return slot


def _same_name(patient_name: str):
    return func.lower(Appointment.patient_name) == patient_name.lower()


def _names_match(stored: str | None, given: str) -> bool:
    """Unicode-aware, case-insensitive name comparison (SQLite's lower() is ASCII-only)."""
    return stored is not None and stored.casefold() == given.casefold()


def check_can_book(db: Session, slot: Appointment, patient_name: str) -> None:
    """Raise if this patient can't book this slot.

    A patient can't book the same doctor twice on one day, or hold two bookings
    at overlapping times on the same day.
    """
    if slot.is_booked:
        raise SlotAlreadyBooked()
    if datetime.combine(slot.date, slot.start_time) <= now():
        raise SlotInPast()

    conflict = db.scalars(
        select(Appointment).where(
            Appointment.is_booked.is_(True),
            _same_name(patient_name),
            Appointment.date == slot.date,
            (Appointment.doctor_id == slot.doctor_id)
            | ((Appointment.start_time < slot.end_time) & (Appointment.end_time > slot.start_time)),
        )
    ).first()
    if conflict:
        raise PatientAlreadyBooked()


def book_slot(db: Session, slot_id: int, patient_name: str, phone_number: str) -> Appointment:
    """Book a slot. Name and phone must already be validated (see schemas.BookingCreate)."""
    slot = get_slot(db, slot_id)
    check_can_book(db, slot, patient_name)

    # Conditional update so two simultaneous requests can't both book the same slot.
    result = db.execute(
        update(Appointment)
        .where(Appointment.id == slot_id, Appointment.is_booked.is_(False))
        .values(is_booked=True, patient_name=patient_name, phone_number=phone_number)
    )
    if result.rowcount == 0:
        db.rollback()
        raise SlotAlreadyBooked()
    db.commit()
    db.refresh(slot)
    return slot


def cancel_booking(db: Session, slot_id: int) -> Appointment:
    """Staff cancellation from the dashboard."""
    slot = get_slot(db, slot_id)
    if not slot.is_booked:
        raise SlotNotBooked()
    slot.is_booked = False
    slot.patient_name = None
    slot.phone_number = None
    db.commit()
    return slot


def find_patient_bookings(db: Session, patient_name: str, phone_number: str) -> list[Appointment]:
    """Upcoming bookings that match BOTH the name and the phone number."""
    current = now()
    query = (
        select(Appointment)
        .options(joinedload(Appointment.doctor))
        .where(
            Appointment.is_booked.is_(True),
            Appointment.phone_number == phone_number,
            Appointment.date >= current.date(),
        )
        .order_by(Appointment.date, Appointment.start_time)
    )
    return [
        s
        for s in db.scalars(query)
        if _names_match(s.patient_name, patient_name) and datetime.combine(s.date, s.start_time) > current
    ]


def get_patient_booking(db: Session, slot_id: int, patient_name: str, phone_number: str) -> Appointment:
    """Return the slot only if it is booked by this exact patient (name AND phone)."""
    slot = db.get(Appointment, slot_id)
    if (
        slot is None
        or not slot.is_booked
        or not _names_match(slot.patient_name, patient_name)
        or slot.phone_number != phone_number
    ):
        raise BookingNotFound()
    return slot


def cancel_patient_booking(db: Session, slot_id: int, patient_name: str, phone_number: str) -> Appointment:
    """Patient self-service cancellation: only works when name AND phone match the booking."""
    slot = get_patient_booking(db, slot_id, patient_name, phone_number)
    return cancel_booking(db, slot.id)
