"""Default data inserted on startup."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Doctor

DEFAULT_DOCTORS = [
    ("Dr. Zoubi", "Cardiology"),
    ("Dr. Sarah", "Pediatrics"),
    ("Dr. Rasool", "Dermatology"),
    ("Dr. Ali", "General Medicine"),
]


def seed_doctors(db: Session) -> None:
    """Insert any default doctors that are missing. Safe to run on every startup."""
    existing = set(db.scalars(select(Doctor.name)))
    for name, specialty in DEFAULT_DOCTORS:
        if name not in existing:
            db.add(Doctor(name=name, specialty=specialty))
    db.commit()
