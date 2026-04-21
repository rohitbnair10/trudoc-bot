"""
Postgres-backed patient storage via Supabase (or any Postgres).
Same interface as the original JSON storage — drop-in replacement.
 
Schema (auto-created on first run):
  patients (phone TEXT PRIMARY KEY, data JSONB NOT NULL)
  unregistered (phone TEXT PRIMARY KEY, data JSONB NOT NULL)
"""
import json
import os
 
import psycopg2
from psycopg2.extras import Json
 
DATABASE_URL = os.environ["DATABASE_URL"]
 
_EMPTY_PATIENT = {
    "phone": "",
    "name": None,
    "medications": [],
    "callbacks": [],
    "lab_tests": [],
    "lab_appointments": [],
    "refill_status": [],
    "conversation": [],
}
 
 
def _connect():
    return psycopg2.connect(DATABASE_URL, sslmode="require")
 
 
def _ensure_tables():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS patients (
                    phone TEXT PRIMARY KEY,
                    data  JSONB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS unregistered (
                    phone      TEXT PRIMARY KEY,
                    data       JSONB NOT NULL
                );
            """)
        conn.commit()
 
 
_ensure_tables()
 
 
# ─── Registered patients ──────────────────────────────────────────────────────
 
def patient_exists(phone: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM patients WHERE phone = %s", (phone,))
            return cur.fetchone() is not None
 
 
def get_patient(phone: str) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM patients WHERE phone = %s", (phone,))
            row = cur.fetchone()
    if row is None:
        return {**_EMPTY_PATIENT, "phone": phone}
    return row[0]
 
 
def save_patient(phone: str, patient: dict) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO patients (phone, data)
                VALUES (%s, %s)
                ON CONFLICT (phone) DO UPDATE SET data = EXCLUDED.data
            """, (phone, Json(patient)))
        conn.commit()
 
 
def all_patients() -> dict:
    """Return {phone: data} for all patients — used by outreach."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, data FROM patients")
            rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}
 
 
# ─── Unregistered contacts ────────────────────────────────────────────────────
 
def get_unregistered(phone: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM unregistered WHERE phone = %s", (phone,))
            row = cur.fetchone()
    return row[0] if row else None
 
 
def save_unregistered(phone: str, data: dict) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO unregistered (phone, data)
                VALUES (%s, %s)
                ON CONFLICT (phone) DO UPDATE SET data = EXCLUDED.data
            """, (phone, Json(data)))
        conn.commit()
 
