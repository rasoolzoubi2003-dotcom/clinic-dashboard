import datetime as dt

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Time, UniqueConstraint
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
    """A bookable time slot. It becomes an appointment once a patient books it."""

    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("doctor_id", "date", "start_time", name="uq_slot_doctor_date_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)
    patient_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)

    doctor: Mapped[Doctor] = relationship(back_populates="appointments")

    @property
    def time_range(self) -> str:
        return f"{self.start_time:%I:%M %p} - {self.end_time:%I:%M %p}"
