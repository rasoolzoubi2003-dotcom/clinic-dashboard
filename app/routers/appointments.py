from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, Doctor

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def view_appointments(
    request: Request,
    doctor_id: int | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    doctors = db.scalars(select(Doctor).order_by(Doctor.name)).all()
    selected_doctor = db.get(Doctor, doctor_id) if doctor_id else None
    appointments = []
    if selected_doctor:
        appointments = db.scalars(
            select(Appointment).where(Appointment.doctor_id == selected_doctor.id).order_by(Appointment.id)
        ).all()

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "appointments": appointments,
            "doctors": doctors,
            "selected_doctor": selected_doctor,
            "error": error,
        },
    )


@router.post("/generate-schedule")
async def generate_schedule(
    doctor_id: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    slot_duration: int = Form(...),
    db: Session = Depends(get_db),
):
    fmt = "%H:%M"
    current = datetime.strptime(start_time, fmt)
    end = datetime.strptime(end_time, fmt)

    while current + timedelta(minutes=slot_duration) <= end:
        next_time = current + timedelta(minutes=slot_duration)
        slot_str = f"{current.strftime('%I:%M %p')} - {next_time.strftime('%I:%M %p')}"
        db.add(Appointment(doctor_id=doctor_id, time_slot=slot_str))
        current = next_time

    db.commit()
    return RedirectResponse(url=f"/?doctor_id={doctor_id}", status_code=303)


@router.post("/book-slot/{slot_id}")
async def book_slot(slot_id: int, patient_name: str = Form(...), db: Session = Depends(get_db)):
    clean_name = patient_name.strip()
    slot = db.get(Appointment, slot_id)

    if not slot or slot.is_booked:
        return RedirectResponse(url="/", status_code=303)

    same_patient = func.lower(Appointment.patient_name) == clean_name.lower()
    conflict = db.scalars(
        select(Appointment).where(
            Appointment.is_booked.is_(True),
            same_patient,
            (Appointment.doctor_id == slot.doctor_id) | (Appointment.time_slot == slot.time_slot),
        )
    ).first()

    if conflict:
        return RedirectResponse(url=f"/?error=already_booked&doctor_id={slot.doctor_id}", status_code=303)

    slot.is_booked = True
    slot.patient_name = clean_name
    db.commit()
    return RedirectResponse(url=f"/?doctor_id={slot.doctor_id}", status_code=303)


@router.post("/cancel-slot/{slot_id}")
async def cancel_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.get(Appointment, slot_id)
    if not slot:
        return RedirectResponse(url="/", status_code=303)

    slot.is_booked = False
    slot.patient_name = None
    db.commit()
    return RedirectResponse(url=f"/?doctor_id={slot.doctor_id}", status_code=303)
