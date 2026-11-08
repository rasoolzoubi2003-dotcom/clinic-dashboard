"""Application settings loaded from environment variables (and an optional .env file)."""

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinic.db")
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Amman")
