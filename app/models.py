from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, index=True)
    time_slot = Column(String)
    patient_name = Column(String, default="")
    is_booked = Column(Boolean, default=False)
