"""
Seed a demo patient so you can test the bot immediately.
Run: python scripts/seed_demo.py [phone_number]

Example:
  python scripts/seed_demo.py whatsapp:+447911123456
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

DATA_FILE = Path("data/patients.json")


def seed(phone: str):
    DATA_FILE.parent.mkdir(exist_ok=True)
    data = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}

    today = datetime.now().date()

    data[phone] = {
        "phone": phone,
        "name": "Alex Demo",
        "medications": [
            {
                "name": "Metformin",
                "dosage": "500mg",
                "frequency": "twice daily",
                "last_refill_date": (today - timedelta(days=25)).strftime("%Y-%m-%d"),
                "days_supply": 30,
                # Due in 5 days — should trigger a reminder
                "next_refill_date": (today + timedelta(days=5)).strftime("%Y-%m-%d"),
            },
            {
                "name": "Lisinopril",
                "dosage": "10mg",
                "frequency": "once daily",
                "last_refill_date": (today - timedelta(days=35)).strftime("%Y-%m-%d"),
                "days_supply": 30,
                # Overdue by 5 days — should trigger urgent reminder
                "next_refill_date": (today - timedelta(days=5)).strftime("%Y-%m-%d"),
            },
            {
                "name": "Atorvastatin",
                "dosage": "20mg",
                "frequency": "once daily at night",
                "last_refill_date": today.strftime("%Y-%m-%d"),
                "days_supply": 90,
                # Plenty of supply — no reminder needed
                "next_refill_date": (today + timedelta(days=83)).strftime("%Y-%m-%d"),
            },
        ],
        "callbacks": [],
        "conversation": [],
    }

    DATA_FILE.write_text(json.dumps(data, indent=2))
    print(f"✓ Seeded demo patient for {phone}")
    print(f"  Medications: Metformin (due in 5d), Lisinopril (OVERDUE 5d), Atorvastatin (ok)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/seed_demo.py <phone>")
        print("Example: python scripts/seed_demo.py whatsapp:+447911123456")
        sys.exit(1)
    seed(sys.argv[1])
