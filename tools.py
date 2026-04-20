"""
Tool implementations called by the Claude agentic loop.
Each function takes `phone` as its first argument (injected by the bot).
"""
import json
from datetime import datetime, timedelta

from storage import get_patient, save_patient


# ─── Tool implementations ─────────────────────────────────────────────────────

def get_medications(phone: str) -> dict:
    """Return the patient's full medication list."""
    patient = get_patient(phone)
    if not patient["medications"]:
        return {"medications": [], "note": "No medications on file yet."}
    return {"medications": patient["medications"]}


def check_refills_due(phone: str, days_ahead: int = 7) -> dict:
    """Return medications that are overdue or due within `days_ahead` days."""
    patient = get_patient(phone)
    today = datetime.now().date()
    overdue, due_soon, ok = [], [], []

    for med in patient["medications"]:
        refill_date_str = med.get("next_refill_date")
        if not refill_date_str:
            continue
        refill_date = datetime.strptime(refill_date_str, "%Y-%m-%d").date()
        delta = (refill_date - today).days

        entry = {**med, "days_delta": delta}
        if delta < 0:
            overdue.append({**entry, "days_overdue": abs(delta)})
        elif delta <= days_ahead:
            due_soon.append({**entry, "days_until_refill": delta})
        else:
            ok.append(entry)

    return {"today": str(today), "overdue": overdue, "due_soon": due_soon, "ok": ok}


def book_callback(phone: str, preferred_date: str, preferred_time: str, reason: str) -> dict:
    """Schedule a doctor callback and persist it."""
    patient = get_patient(phone)

    callback = {
        "id": len(patient["callbacks"]) + 1,
        "date": preferred_date,
        "time": preferred_time,
        "reason": reason,
        "status": "scheduled",
        "booked_at": datetime.now().isoformat(),
    }
    patient["callbacks"].append(callback)
    save_patient(phone, patient)

    return {
        "success": True,
        "callback": callback,
        "confirmation": f"Callback booked for {preferred_date} at {preferred_time}.",
    }


def list_callbacks(phone: str) -> dict:
    """List all upcoming (not past) callbacks."""
    patient = get_patient(phone)
    today = datetime.now().date()
    upcoming = [
        cb for cb in patient["callbacks"]
        if cb.get("status") == "scheduled"
        and datetime.strptime(cb["date"], "%Y-%m-%d").date() >= today
    ]
    return {"upcoming_callbacks": upcoming}


def update_refill_date(phone: str, medication_name: str, refill_date: str, days_supply: int) -> dict:
    """Record that the patient refilled a medication and compute the next refill date."""
    patient = get_patient(phone)

    for med in patient["medications"]:
        if med["name"].lower() == medication_name.lower():
            # Remind patient 7 days before they run out
            next_refill = datetime.strptime(refill_date, "%Y-%m-%d") + timedelta(days=days_supply - 7)
            med["last_refill_date"] = refill_date
            med["days_supply"] = days_supply
            med["next_refill_date"] = next_refill.strftime("%Y-%m-%d")
            save_patient(phone, patient)
            return {"success": True, "medication": med}

    return {"success": False, "error": f"Medication '{medication_name}' not found on file."}


def set_patient_name(phone: str, name: str) -> dict:
    """Store or update the patient's name."""
    patient = get_patient(phone)
    patient["name"] = name
    save_patient(phone, patient)
    return {"success": True, "name": name}


def get_lab_tests(phone: str) -> dict:
    """Return the patient's lab test history and upcoming lab appointments."""
    patient = get_patient(phone)
    lab_tests = patient.get("lab_tests", [])
    upcoming = [
        a for a in patient.get("lab_appointments", [])
        if a.get("status") == "scheduled"
    ]
    return {
        "lab_tests": lab_tests[-5:] if lab_tests else [],  # last 5 only
        "upcoming_lab_appointments": upcoming,
    }


def record_lab_test(
    phone: str,
    test_date: str,
    test_type: str,
    key_findings: str,
    flag_for_doctor: bool = False,
) -> dict:
    """Persist an analysed lab report with extracted key findings."""
    patient = get_patient(phone)
    lab_test = {
        "id": len(patient.get("lab_tests", [])) + 1,
        "date": test_date,
        "test_type": test_type,
        "key_findings": key_findings,
        "flag_for_doctor": flag_for_doctor,
        "recorded_at": datetime.now().isoformat(),
    }
    patient.setdefault("lab_tests", []).append(lab_test)
    save_patient(phone, patient)
    return {"success": True, "lab_test": lab_test}


def record_refill_status(
    phone: str,
    medication_name: str,
    has_prescription: bool,
    days_remaining: int | None = None,
    notes: str = "",
) -> dict:
    """
    Record the patient's refill situation when they decline a callback.
    Captures whether they have an existing prescription and how many days
    of medication they have left — useful urgency context for the doctor.
    """
    patient = get_patient(phone)
    entry = {
        "medication": medication_name,
        "has_prescription": has_prescription,
        "days_remaining": days_remaining,
        "notes": notes,
        "recorded_at": datetime.now().isoformat(),
    }
    patient.setdefault("refill_status", []).append(entry)
    save_patient(phone, patient)
    return {"success": True, "status": entry}


def book_lab_test(
    phone: str,
    preferred_date: str,
    preferred_time: str,
    test_type: str,
    lab_name: str = "",
) -> dict:
    """Book a lab test appointment for the patient."""
    patient = get_patient(phone)
    appointment = {
        "id": len(patient.get("lab_appointments", [])) + 1,
        "date": preferred_date,
        "time": preferred_time,
        "test_type": test_type,
        "lab_name": lab_name,
        "status": "scheduled",
        "booked_at": datetime.now().isoformat(),
    }
    patient.setdefault("lab_appointments", []).append(appointment)
    save_patient(phone, patient)
    return {
        "success": True,
        "appointment": appointment,
        "confirmation": f"Lab test booked for {preferred_date} at {preferred_time}.",
    }


def add_medication(
    phone: str,
    name: str,
    dosage: str,
    frequency: str,
    next_refill_date: str,
    days_supply: int = 30,
) -> dict:
    """Add a new medication to the patient's profile."""
    patient = get_patient(phone)

    for med in patient["medications"]:
        if med["name"].lower() == name.lower():
            return {"success": False, "error": f"{name} is already on file."}

    medication = {
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "next_refill_date": next_refill_date,
        "days_supply": days_supply,
        "last_refill_date": None,
    }
    patient["medications"].append(medication)
    save_patient(phone, patient)
    return {"success": True, "medication": medication}


# ─── Tool schema definitions (passed to Claude) ──────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_medications",
        "description": "Get the patient's full list of medications and their refill schedule.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_refills_due",
        "description": (
            "Check which of the patient's medications are overdue for refill "
            "or coming due soon. Call this proactively at the start of each conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look for upcoming refills. Default 7.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "book_callback",
        "description": "Book a doctor callback for the patient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "preferred_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format",
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Time in HH:MM 24-hour format, e.g. 14:00",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the callback (e.g. medication refill, symptoms)",
                },
            },
            "required": ["preferred_date", "preferred_time", "reason"],
        },
    },
    {
        "name": "list_callbacks",
        "description": "List all upcoming doctor callbacks for the patient.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_refill_date",
        "description": "Record that the patient has refilled a medication and update the next refill date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "medication_name": {"type": "string", "description": "Name of the medication"},
                "refill_date": {
                    "type": "string",
                    "description": "Date it was refilled, YYYY-MM-DD",
                },
                "days_supply": {
                    "type": "integer",
                    "description": "Number of days this supply will last",
                },
            },
            "required": ["medication_name", "refill_date", "days_supply"],
        },
    },
    {
        "name": "set_patient_name",
        "description": "Set or update the patient's name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Patient's full name"}
            },
            "required": ["name"],
        },
    },
    {
        "name": "record_refill_status",
        "description": (
            "Record the patient's current refill situation when they decline a callback. "
            "Call this after the patient says NO to a refill callback and you have asked "
            "about their prescription and days of medication remaining."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "medication_name": {
                    "type": "string",
                    "description": "Name of the medication",
                },
                "has_prescription": {
                    "type": "boolean",
                    "description": "True if the patient has an existing repeatable prescription",
                },
                "days_remaining": {
                    "type": "integer",
                    "description": "Approximate number of days of medication the patient has left",
                },
                "notes": {
                    "type": "string",
                    "description": "Any other relevant detail the patient mentioned",
                },
            },
            "required": ["medication_name", "has_prescription"],
        },
    },
    {
        "name": "get_lab_tests",
        "description": (
            "Get the patient's lab test history and upcoming lab appointments. "
            "Call this whenever a medication refill is due or overdue to check "
            "if a recent test exists (within the last 90 days)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "record_lab_test",
        "description": (
            "Save the key findings from a lab report the patient has shared. "
            "Call this after analysing an uploaded lab report image or PDF."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "test_date": {
                    "type": "string",
                    "description": "Date the test was taken, YYYY-MM-DD. Extract from the report; use today if not found.",
                },
                "test_type": {
                    "type": "string",
                    "description": "Type of test, e.g. HbA1c, Lipid Panel, CBC, Kidney Function",
                },
                "key_findings": {
                    "type": "string",
                    "description": "A concise plain-English summary of the key values and any abnormal results",
                },
                "flag_for_doctor": {
                    "type": "boolean",
                    "description": "True if any values are outside the normal range and the doctor should review urgently",
                },
            },
            "required": ["test_date", "test_type", "key_findings"],
        },
    },
    {
        "name": "book_lab_test",
        "description": "Book a lab test appointment for the patient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "preferred_date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format",
                },
                "preferred_time": {
                    "type": "string",
                    "description": "Time in HH:MM 24-hour format",
                },
                "test_type": {
                    "type": "string",
                    "description": "Type of lab test to book, e.g. HbA1c, Lipid Panel",
                },
                "lab_name": {
                    "type": "string",
                    "description": "Name of the lab or clinic (optional)",
                },
            },
            "required": ["preferred_date", "preferred_time", "test_type"],
        },
    },
    {
        "name": "add_medication",
        "description": "Add a new medication to the patient's profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Medication name"},
                "dosage": {"type": "string", "description": "e.g. 500mg"},
                "frequency": {"type": "string", "description": "e.g. twice daily"},
                "next_refill_date": {
                    "type": "string",
                    "description": "Next refill due date, YYYY-MM-DD",
                },
                "days_supply": {
                    "type": "integer",
                    "description": "Days per supply (default 30)",
                },
            },
            "required": ["name", "dosage", "frequency", "next_refill_date"],
        },
    },
]

# ─── Dispatcher ───────────────────────────────────────────────────────────────

_TOOL_MAP = {
    "get_medications": get_medications,
    "check_refills_due": check_refills_due,
    "book_callback": book_callback,
    "list_callbacks": list_callbacks,
    "update_refill_date": update_refill_date,
    "set_patient_name": set_patient_name,
    "add_medication": add_medication,
    "get_lab_tests": get_lab_tests,
    "record_lab_test": record_lab_test,
    "book_lab_test": book_lab_test,
    "record_refill_status": record_refill_status,
}


def run_tool(phone: str, tool_name: str, tool_input: dict) -> str:
    fn = _TOOL_MAP.get(tool_name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    try:
        result = fn(phone=phone, **tool_input)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
