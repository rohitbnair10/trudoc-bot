"""
Terminal test harness — simulates a WhatsApp conversation without any API keys
except ANTHROPIC_API_KEY.

Usage:
    python scripts/test_cli.py [phone_number]

Example:
    python scripts/test_cli.py whatsapp:+447911123456

Type your messages and press Enter. Type 'quit' to exit.
"""
import sys
from pathlib import Path

# Make sure imports resolve from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from bot import get_response  # noqa: E402

PHONE = sys.argv[1] if len(sys.argv) > 1 else "whatsapp:+10000000000"

print(f"\n{'─'*60}")
print(f"  WhatsApp Bot — CLI Test")
print(f"  Patient phone: {PHONE}")
print(f"  Type 'quit' to exit")
print(f"{'─'*60}\n")

while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        break

    if user_input.lower() in ("quit", "exit", "q"):
        print("Bye!")
        break

    if not user_input:
        continue

    print("Bot: ", end="", flush=True)
    reply = get_response(PHONE, user_input)
    print(reply)
    print()
