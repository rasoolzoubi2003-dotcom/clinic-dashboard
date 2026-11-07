from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import Base, SessionLocal, engine
from app.migrations import add_missing_columns
from app.routers import appointments
from app.seed import seed_doctors

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
    with SessionLocal() as db:
        seed_doctors(db)
    yield


app = FastAPI(title="Clinic Appointment Booking", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(appointments.router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}
