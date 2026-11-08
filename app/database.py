from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DATABASE_URL


def make_engine(url: str):
    if not url.startswith("sqlite"):
        return create_engine(url)
    kwargs = {"connect_args": {"check_same_thread": False}}
    if url in ("sqlite://", "sqlite:///:memory:"):
        # Share one in-memory database across threads (used by the tests).
        kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


class Base(DeclarativeBase):
    pass


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
