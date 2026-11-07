from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    specialty: Mapped[str] = mapped_column(String(100))

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="doctor")

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.specialty})"


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    time_slot: Mapped[str] = mapped_column(String)
    patient_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)

    doctor: Mapped[Doctor] = relationship(back_populates="appointments")
