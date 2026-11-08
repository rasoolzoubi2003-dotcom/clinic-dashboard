import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import messages, services
from app.clock import today
from app.database import get_db
from app.schemas import BookingCreate, ScheduleCreate, error_code
from app.templating import templates

router = APIRouter()


def home_url(**params) -> str:
    """Build a URL for the dashboard with properly encoded query parameters."""
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return f"/?{query}" if query else "/"


def redirect(**params) -> RedirectResponse:
    return RedirectResponse(url=home_url(**params), status_code=303)


def _parse_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _parse_date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
async def view_appointments(
    request: Request,
    doctor_id: str | None = None,
    date: str | None = None,
    error: str | None = None,
    success: str | None = None,
    created: str | None = None,
    skipped: str | None = None,
    db: Session = Depends(get_db),
):
    # Query params are parsed leniently: a bad value falls back to the default instead of a 422.
    selected_date = _parse_date(date) or today()
    selected_id = _parse_int(doctor_id)
    doctors = services.list_doctors(db)
    selected_doctor = next((d for d in doctors if d.id == selected_id), None)
    appointments = services.list_slots(db, selected_doctor.id, selected_date) if selected_doctor else []

    # Per-doctor totals for the selected day, shown on the doctor cards and the summary banner.
    day_stats = {}
    for doctor in doctors:
        slots = services.list_slots(db, doctor.id, selected_date)
        day_stats[doctor.id] = {"total": len(slots), "free": sum(not s.is_booked for s in slots)}

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "appointments": appointments,
            "doctors": doctors,
            "selected_doctor": selected_doctor,
            "selected_date": selected_date,
            "min_date": today(),
            "today": today(),
            "day_stats": day_stats,
            "day_total": sum(s["total"] for s in day_stats.values()),
            "day_free": sum(s["free"] for s in day_stats.values()),
            "prev_date": selected_date - dt.timedelta(days=1),
            "next_date": selected_date + dt.timedelta(days=1),
            "error_message": messages.error_message(error),
            "success_message": messages.success_message(
                success, _parse_int(created) or 0, _parse_int(skipped) or 0
            ),
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
        result = services.generate_slots(
            db, data.doctor_id, data.date, data.start_time, data.end_time, data.slot_duration
        )
    except ValidationError as exc:
        return redirect(**back, error=error_code(exc))
    except services.ServiceError as exc:
        return redirect(**back, error=exc.code)
    return redirect(**back, success="generated", created=result.created, skipped=result.skipped)


@router.post("/book-slot/{slot_id}")
async def book_slot(
    slot_id: int,
    patient_name: str = Form(""),
    phone_number: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        slot = services.get_slot(db, slot_id)
    except services.ServiceError as exc:
        return redirect(error=exc.code)

    back = {"doctor_id": slot.doctor_id, "date": slot.date}
    try:
        data = BookingCreate(patient_name=patient_name, phone_number=phone_number)
        services.book_slot(db, slot_id, data.patient_name, data.phone_number)
    except ValidationError as exc:
        return redirect(**back, error=error_code(exc))
    except services.ServiceError as exc:
        return redirect(**back, error=exc.code)
    return redirect(**back, success="booked")


@router.post("/cancel-slot/{slot_id}")
async def cancel_slot(slot_id: int, db: Session = Depends(get_db)):
    try:
        slot = services.cancel_booking(db, slot_id)
    except services.ServiceError as exc:
        return redirect(error=exc.code)
    return redirect(doctor_id=slot.doctor_id, date=slot.date, success="cancelled")
