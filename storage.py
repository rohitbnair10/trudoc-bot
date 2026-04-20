import json
from pathlib import Path

DATA_FILE = Path("data/patients.json")

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


def _load() -> dict:
    DATA_FILE.parent.mkdir(exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}")
    return json.loads(DATA_FILE.read_text())


def _save(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2))


def get_patient(phone: str) -> dict:
    data = _load()
    if phone not in data:
        return {**_EMPTY_PATIENT, "phone": phone}
    return data[phone]


def save_patient(phone: str, patient: dict) -> None:
    data = _load()
    data[phone] = patient
    _save(data)
