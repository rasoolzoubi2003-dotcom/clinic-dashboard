import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import services
from app.clock import today
from app.database import get_db
from app.schemas import BookingCreate, ScheduleCreate, error_code

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def home_url(**params) -> str:
    """Build a URL for the dashboard with properly encoded query parameters."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"/?{query}" if query else "/"


def redirect(**params) -> RedirectResponse:
    return RedirectResponse(url=home_url(**params), status_code=303)


@router.get("/", response_class=HTMLResponse)
async def view_appointments(
    request: Request,
    doctor_id: int | None = None,
    date: dt.date | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    selected_date = date or today()
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
            "min_date": today(),
            "error": error,
        },
    )


# Form fields are read as plain strings and validated by the Pydantic schemas, so bad input
# produces a friendly message instead of FastAPI's default 422 JSON response.
@router.post("/generate-schedule")
async def generate_schedule(
    doctor_id: str = Form(""),
    date: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    slot_duration: str = Form(""),
    db: Session = Depends(get_db),
):
    back = {"doctor_id": doctor_id, "date": date}
    try:
        data = ScheduleCreate(
            doctor_id=doctor_id, date=date, start_time=start_time, end_time=end_time, slot_duration=slot_duration
        )
        services.generate_slots(db, data.doctor_id, data.date, data.start_time, data.end_time, data.slot_duration)
    except ValidationError as exc:
        return redirect(**back, error=error_code(exc))
    except services.ServiceError as exc:
        return redirect(**back, error=exc.code)
    return redirect(**back)


@router.post("/book-slot/{slot_id}")
async def book_slot(slot_id: int, patient_name: str = Form(""), db: Session = Depends(get_db)):
    try:
        slot = services.get_slot(db, slot_id)
    except services.ServiceError as exc:
        return redirect(error=exc.code)

    back = {"doctor_id": slot.doctor_id, "date": slot.date}
    try:
        data = BookingCreate(patient_name=patient_name)
        services.book_slot(db, slot_id, data.patient_name)
    except ValidationError as exc:
        return redirect(**back, error=error_code(exc))
    except services.ServiceError as exc:
        return redirect(**back, error=exc.code)
    return redirect(**back)


@router.post("/cancel-slot/{slot_id}")
async def cancel_slot(slot_id: int, db: Session = Depends(get_db)):
    try:
        slot = services.cancel_booking(db, slot_id)
    except services.ServiceError as exc:
        return redirect(error=exc.code)
    return redirect(doctor_id=slot.doctor_id, date=slot.date)
