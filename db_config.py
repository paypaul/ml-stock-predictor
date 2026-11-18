import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Explicitly load .env from the project root directory
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "stock_analytics"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}


def get_connection():
    """Returns a new PostgreSQL database connection using DB_CONFIG."""
    return psycopg2.connect(**DB_CONFIG)