"""Business logic for schedules and bookings.

Routes (and the tests) call these functions; they never touch the request or response.
Failures raise a ServiceError subclass whose `code` the UI maps to a message.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

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


def list_doctors(db: Session) -> list[Doctor]:
    return list(db.scalars(select(Doctor).order_by(Doctor.name)))


def get_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise DoctorNotFound()
    return doctor


def generate_slots(
    db: Session, doctor_id: int, day: date, start: time, end: time, duration_minutes: int
) -> int:
    """Create back-to-back slots between start and end. Returns how many were created."""
    get_doctor(db, doctor_id)
    step = timedelta(minutes=duration_minutes)
    current = datetime.combine(day, start)
    end_dt = datetime.combine(day, end)

    created = 0
    while current + step <= end_dt:
        db.add(
            Appointment(
                doctor_id=doctor_id, date=day, start_time=current.time(), end_time=(current + step).time()
            )
        )
        created += 1
        current += step
    db.commit()
    return created


def list_slots(db: Session, doctor_id: int, day: date) -> list[Appointment]:
    query = (
        select(Appointment)
        .options(joinedload(Appointment.doctor))
        .where(Appointment.doctor_id == doctor_id, Appointment.date == day)
        .order_by(Appointment.start_time)
    )
    return list(db.scalars(query))


def get_slot(db: Session, slot_id: int) -> Appointment:
    slot = db.get(Appointment, slot_id)
    if slot is None:
        raise SlotNotFound()
    return slot


def book_slot(db: Session, slot_id: int, patient_name: str) -> Appointment:
    """Book a slot for a patient.

    A patient can't book the same doctor twice on one day, or hold two bookings
    at overlapping times on the same day.
    """
    slot = get_slot(db, slot_id)
    if slot.is_booked:
        raise SlotAlreadyBooked()

    conflict = db.scalars(
        select(Appointment).where(
            Appointment.is_booked.is_(True),
            func.lower(Appointment.patient_name) == patient_name.lower(),
            Appointment.date == slot.date,
            (Appointment.doctor_id == slot.doctor_id)
            | ((Appointment.start_time < slot.end_time) & (Appointment.end_time > slot.start_time)),
        )
    ).first()
    if conflict:
        raise PatientAlreadyBooked()

    # Conditional update so two simultaneous requests can't both book the same slot.
    result = db.execute(
        update(Appointment)
        .where(Appointment.id == slot_id, Appointment.is_booked.is_(False))
        .values(is_booked=True, patient_name=patient_name)
    )
    if result.rowcount == 0:
        db.rollback()
        raise SlotAlreadyBooked()
    db.commit()
    db.refresh(slot)
    return slot


def cancel_booking(db: Session, slot_id: int) -> Appointment:
    slot = get_slot(db, slot_id)
    if not slot.is_booked:
        raise SlotNotBooked()
    slot.is_booked = False
    slot.patient_name = None
    db.commit()
    return slot
