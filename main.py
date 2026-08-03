from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime, timedelta

DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, index=True)
    time_slot = Column(String)
    patient_name = Column(String, default="")
    is_booked = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DOCTORS_LIST = [
    "Dr. Khalil (Cardiology)",
    "Dr. Sarah (Pediatrics)",
    "Dr. Ahmad (Dermatology)",
    "Dr. Ali (General Medicine)"
]

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def view_appointments(
    request: Request, 
    selected_doctor: str = None, 
    error: str = None, 
    db: Session = Depends(get_db)
):
    if not selected_doctor:
        db.query(Appointment).delete()
        db.commit()
        appointments = []
    else:
        appointments = db.query(Appointment).filter(Appointment.service_name == selected_doctor).all()

    return templates.TemplateResponse(
        request=request, 
        name="home.html", 
        context={
            "appointments": appointments, 
            "doctors": DOCTORS_LIST, 
            "selected_doctor": selected_doctor,
            "error": error
        }
    )

@app.post("/generate-schedule")
async def generate_schedule(
    service_name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    slot_duration: int = Form(...),
    db: Session = Depends(get_db)
):
    fmt = "%H:%M"
    current = datetime.strptime(start_time, fmt)
    end = datetime.strptime(end_time, fmt)

    while current + timedelta(minutes=slot_duration) <= end:
        next_time = current + timedelta(minutes=slot_duration)
        slot_str = f"{current.strftime('%I:%M %p')} - {next_time.strftime('%I:%M %p')}"
        
        new_slot = Appointment(
            service_name=service_name,
            time_slot=slot_str,
            patient_name="",
            is_booked=False
        )
        db.add(new_slot)
        current = next_time

    db.commit()
    return RedirectResponse(url=f"/?selected_doctor={service_name}", status_code=303)

@app.post("/book-slot/{slot_id}")
async def book_slot(
    slot_id: int, 
    patient_name: str = Form(...), 
    db: Session = Depends(get_db)
):
    clean_name = patient_name.strip()
    
    slot = db.query(Appointment).filter(Appointment.id == slot_id).first()
    
    if not slot or slot.is_booked:
        return RedirectResponse(url="/", status_code=303)

    same_doctor_booking = db.query(Appointment).filter(
        Appointment.patient_name.ilike(clean_name),
        Appointment.service_name == slot.service_name,
        Appointment.is_booked == True
    ).first()

    same_time_booking = db.query(Appointment).filter(
        Appointment.patient_name.ilike(clean_name),
        Appointment.time_slot == slot.time_slot,
        Appointment.is_booked == True
    ).first()

    if same_doctor_booking or same_time_booking:
        doc_param = f"&selected_doctor={slot.service_name}"
        return RedirectResponse(url=f"/?error=already_booked{doc_param}", status_code=303)

    slot.is_booked = True
    slot.patient_name = clean_name
    db.commit()

    return RedirectResponse(url=f"/?selected_doctor={slot.service_name}", status_code=303)

@app.post("/cancel-slot/{slot_id}")
async def cancel_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(Appointment).filter(Appointment.id == slot_id).first()
    doc_name = slot.service_name if slot else None
    
    if slot:
        slot.is_booked = False
        slot.patient_name = ""
        db.commit()

    doc_param = f"?selected_doctor={doc_name}" if doc_name else ""
    return RedirectResponse(url=f"/{doc_param}", status_code=303)