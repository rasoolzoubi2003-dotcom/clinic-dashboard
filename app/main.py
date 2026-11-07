from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, SessionLocal, engine
from app.routers import appointments
from app.seed import seed_doctors


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_doctors(db)
    yield


app = FastAPI(title="Clinic Appointment Booking", lifespan=lifespan)
app.include_router(appointments.router)
