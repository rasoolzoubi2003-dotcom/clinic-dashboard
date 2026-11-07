"""Tiny additive migration step run on startup.

`create_all` creates missing tables but never alters existing ones. This adds any
new nullable columns to existing tables so older local databases keep working.
A production deployment should use Alembic instead.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import Base


def add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing and column.nullable:
                    col_type = column.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
