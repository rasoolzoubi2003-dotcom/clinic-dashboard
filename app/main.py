from fastapi import FastAPI

from app.database import Base, engine
from app.routers import appointments

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Clinic Appointment Booking")
app.include_router(appointments.router)
