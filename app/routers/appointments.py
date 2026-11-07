import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import services
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def view_appointments(
    request: Request,
    doctor_id: int | None = None,
    date: dt.date | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    selected_date = date or dt.date.today()
    doctors = services.list_doctors(db)
    selected_doctor = next((d for d in doctors if d.id == doctor_id), None)
    appointments = services.list_slots(db, selected_doctor.id, selected_date) if selected_doctor else []

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "appointments": appointments,
            "doctors": doctors,
            "selected_doctor": selected_doctor,
            "selected_date": selected_date,
            "error": error,
        },
    )


@router.post("/generate-schedule")
async def generate_schedule(
    doctor_id: int = Form(...),
    date: dt.date = Form(...),
    start_time: dt.time = Form(...),
    end_time: dt.time = Form(...),
    slot_duration: int = Form(...),
    db: Session = Depends(get_db),
):
    services.generate_slots(db, doctor_id, date, start_time, end_time, slot_duration)
    return RedirectResponse(url=f"/?doctor_id={doctor_id}&date={date}", status_code=303)


@router.post("/book-slot/{slot_id}")
async def book_slot(slot_id: int, patient_name: str = Form(...), db: Session = Depends(get_db)):
    try:
        slot = services.get_slot(db, slot_id)
        services.book_slot(db, slot_id, patient_name.strip())
    except services.ServiceError as exc:
        return RedirectResponse(url=f"/?error={exc.code}", status_code=303)
    return RedirectResponse(url=f"/?doctor_id={slot.doctor_id}&date={slot.date}", status_code=303)


@router.post("/cancel-slot/{slot_id}")
async def cancel_slot(slot_id: int, db: Session = Depends(get_db)):
    try:
        slot = services.cancel_booking(db, slot_id)
    except services.ServiceError as exc:
        return RedirectResponse(url=f"/?error={exc.code}", status_code=303)
    return RedirectResponse(url=f"/?doctor_id={slot.doctor_id}&date={slot.date}", status_code=303)
