import datetime as dt
import os

# Use a shared in-memory database for tests. Must be set before the app is imported.
os.environ["DATABASE_URL"] = "sqlite://"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Doctor  # noqa: E402
from app.seed import seed_doctors  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_doctors(db)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def doctor(db) -> Doctor:
    return db.query(Doctor).filter_by(name="Dr. Ali").one()


@pytest.fixture
def other_doctor(db) -> Doctor:
    return db.query(Doctor).filter_by(name="Dr. Sarah").one()


@pytest.fixture
def future_day() -> dt.date:
    return dt.date.today() + dt.timedelta(days=7)
