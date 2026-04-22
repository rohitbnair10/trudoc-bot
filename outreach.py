"""
Outbound refill alert system.

Scans all patients, finds those with refills due within `days_ahead` days,
and sends them a proactive WhatsApp message. The message is also written into
their conversation history so the bot follows Workflow A when they reply.

Run manually:
    python outreach.py

Run daily via cron (9 AM):
    0 9 * * * cd /path/to/project && python outreach.py >> logs/outreach.log 2>&1
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

from storage import get_patient, save_patient  # noqa: E402

DATA_FILE = Path("data/patients.json")
DAYS_AHEAD = int(os.getenv("OUTREACH_DAYS_AHEAD", "7"))
PROVIDER = os.getenv("WHATSAPP_PROVIDER", "twilio").lower()


# ─── WhatsApp senders ─────────────────────────────────────────────────────────

def _send_twilio(to: str, body: str) -> None:
    from twilio.rest import Client
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_NUMBER"],
        to=to,
        body=body,
    )


def _send_meta(to: str, body: str) -> None:
    """
    Note: Meta requires pre-approved message templates for outbound-initiated
    conversations. Replace `body` with an approved template call in production.
    """
    phone_number_id = os.environ["META_PHONE_NUMBER_ID"]
    token = os.environ["META_ACCESS_TOKEN"]
    requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to.lstrip("whatsapp:+"),
            "type": "text",
            "text": {"body": body},
        },
        timeout=10,
    )


def _send(phone: str, body: str) -> None:
    if PROVIDER == "twilio":
        _send_twilio(phone, body)
    elif PROVIDER == "meta":
        _send_meta(phone, body)
    else:
        raise ValueError(f"Unknown WHATSAPP_PROVIDER: {PROVIDER}")


# ─── Refill detection ─────────────────────────────────────────────────────────

def _due_medications(patient: dict, days_ahead: int) -> list[dict]:
    """Return medications that are overdue or due within days_ahead days."""
    today = datetime.now().date()
    due = []
    for med in patient.get("medications", []):
        refill_str = med.get("next_refill_date")
        if not refill_str:
            continue
        refill_date = datetime.strptime(refill_str, "%Y-%m-%d").date()
        if (refill_date - today).days <= days_ahead:
            due.append(med)
    return due


def _already_has_pending_callback(patient: dict) -> bool:
    """True if a refill callback is already scheduled in the next 14 days."""
    today = datetime.now().date()
    for cb in patient.get("callbacks", []):
        if cb.get("status") != "scheduled":
            continue
        cb_date = datetime.strptime(cb["date"], "%Y-%m-%d").date()
        if (cb_date - today).days <= 14 and "refill" in cb.get("reason", "").lower():
            return True
    return False


def _already_outreached_today(patient: dict) -> bool:
    """Avoid double-messaging if outreach already ran today."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    for turn in reversed(patient.get("conversation", [])):
        if turn.get("role") == "assistant" and today_str in turn.get("content", ""):
            if "refill" in turn["content"].lower():
                return True
        break  # only check the most recent assistant turn
    return False


# ─── Message builder ──────────────────────────────────────────────────────────

def _build_message(patient: dict, due_meds: list[dict]) -> str:
    name = patient.get("name") or "there"
    today = datetime.now().date()

    med_lines = []
    for med in due_meds:
        refill_date = datetime.strptime(med["next_refill_date"], "%Y-%m-%d").date()
        delta = (refill_date - today).days
        if delta < 0:
            timing = f"was due {abs(delta)} day{'s' if abs(delta) != 1 else ''} ago"
        elif delta == 0:
            timing = "is due today"
        else:
            timing = f"is due in {delta} day{'s' if delta != 1 else ''} (on {refill_date.strftime('%d %b')})"
        med_lines.append(f"- {med['name']} {med['dosage']}: {timing}")

    meds_block = "\n".join(med_lines)

    return (
        f"Hi {name}, this is a message from your healthcare team.\n\n"
        f"The following medication refill{'s are' if len(due_meds) > 1 else ' is'} coming up:\n"
        f"{meds_block}\n\n"
        f"Would you like to schedule a callback with your doctor to arrange your refill? "
        f"Reply YES to book or NO if you are sorted."
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_refill_outreach(days_ahead: int = DAYS_AHEAD) -> None:
    from storage import all_patients
    patients_map = all_patients()
    if not patients_map:
        print("No patient data found.")
        return

    sent, skipped = 0, 0

    for phone, _ in patients_map.items():
        patient = get_patient(phone)
        # Backfill missing keys for records seeded before full schema
        patient.setdefault("conversation", [])
        patient.setdefault("callbacks", [])
        patient.setdefault("medications", [])
        patient.setdefault("refill_status", [])
        due_meds = _due_medications(patient, days_ahead)

        if not due_meds:
            skipped += 1
            continue

        if _already_has_pending_callback(patient):
            print(f"  skip {phone} — callback already scheduled")
            skipped += 1
            continue

        if _already_outreached_today(patient):
            print(f"  skip {phone} — already messaged today")
            skipped += 1
            continue

        message = _build_message(patient, due_meds)

        try:
            _send(phone, message)
        except Exception as exc:
            print(f"  ERROR sending to {phone}: {exc}")
            continue

        # Write the outbound message into conversation history so the bot
        # recognises Workflow A when the patient replies.
        patient["conversation"].append({"role": "assistant", "content": message})
        save_patient(phone, patient)

        med_names = ", ".join(m["name"] for m in due_meds)
        print(f"  sent  {phone} — {med_names}")
        sent += 1

    print(f"\nOutreach complete: {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    print(f"Running refill outreach (looking {DAYS_AHEAD} days ahead)...\n")
    run_refill_outreach()
