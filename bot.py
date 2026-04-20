"""
Claude-powered conversation engine.
Maintains per-patient message history and runs the tool-use agentic loop.
Supports optional media (image / PDF) for lab report uploads.
"""
import base64
import os
from datetime import datetime

import anthropic

from storage import get_patient, save_patient
from tools import TOOL_DEFINITIONS, run_tool

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

HISTORY_WINDOW = 20  # text turns kept in memory

SYSTEM_PROMPT = """\
You are a warm, compassionate healthcare assistant supporting patients with chronic conditions.

━━ WORKFLOW A — OUTBOUND REFILL ALERT (bot messaged first) ━━
Detected when the conversation history starts with an assistant message about a refill.
Follow this exact flow for each medication mentioned in that alert:

  Step 1 — CALLBACK OFFER
    "Would you like to schedule a callback with your doctor to arrange your refill?"
    • YES → book_callback (reason: "Medication refill — [med name]"), confirm details, done.
    • NO  → go to Step 2.

  Step 2 — UNDERSTAND THEIR SITUATION
    Ask both questions in one message:
    "No problem. Do you have an existing repeatable prescription you can use?
     And roughly how many days of medication do you have left?"

  Step 3 — RECORD & WRAP UP
    Call record_refill_status with what they told you.
    Respond warmly:
    - If ≥14 days remaining: "Great, sounds like you have some time. We'll check in again soon."
    - If <14 days remaining: "You're running fairly low — if anything changes or you need \
help sooner, just message us."
    - If no prescription: "If you need a new prescription at any point, just reach out and \
we can arrange a callback with your doctor."

━━ WORKFLOW B — INBOUND (patient messaged first) ━━

1. GREET & NAME
   - Ask for name on first contact if unknown.

2. CHECK REFILLS
   - Call check_refills_due at the start.
   - If any medication is overdue or due within 7 days, proceed to step 3.

3. CHECK LAB TESTS (when a refill is due/overdue)
   - Call get_lab_tests.
   - If no lab test exists in the last 90 days:
       Ask: "Have you had a recent lab test? An up-to-date report helps your doctor \
fine-tune your medication."
       • If YES → Ask them to share it via WhatsApp (photo or PDF).
       • If NO  → "Would you like me to book a lab test first so the results are ready \
when you see the doctor?"
   - If a recent lab test exists, acknowledge it and proceed normally.

4. ANALYSE A SHARED LAB REPORT
   - Analyse ALL visible values from the image or PDF.
   - Call record_lab_test with the key findings.
   - Set flag_for_doctor=true if ANY value is outside the reference range.
   - Give a plain-English summary and suggest a doctor callback if needed.

5. BOOKINGS
   - book_lab_test  → lab appointments
   - book_callback  → doctor callbacks
   Always confirm date, time, and reason clearly.

━━ TONE ━━
Warm, concise, no jargon. Never alarm the patient unnecessarily.

Today's date: {today}
{patient_context}\
"""


def _build_system(patient: dict) -> str:
    ctx = f"Patient name: {patient['name']}\n" if patient.get("name") else ""
    return SYSTEM_PROMPT.format(
        today=datetime.now().strftime("%Y-%m-%d"),
        patient_context=ctx,
    )


def _build_user_content(text: str, media: dict | None) -> str | list:
    """
    Return a plain string when there is no media, or a content-block list
    when the patient has attached an image or PDF.

    `media` format: {"data": "<base64>", "content_type": "image/jpeg"}
    """
    if not media or not media.get("data"):
        return text or "(no message)"

    blocks: list = []
    ct = media.get("content_type", "image/jpeg")

    if "pdf" in ct:
        blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": media["data"],
                },
            }
        )
    else:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": ct,
                    "data": media["data"],
                },
            }
        )

    # Append the patient's caption (or a default prompt if they sent media silently)
    blocks.append(
        {"type": "text", "text": text or "I've shared my lab report."}
    )
    return blocks


def get_response(phone: str, user_message: str, media: dict | None = None) -> str:
    """
    Process one incoming message and return the bot's reply.

    Args:
        phone:        Normalised phone key, e.g. "whatsapp:+447911123456"
        user_message: Text content of the WhatsApp message (may be empty if media-only)
        media:        Optional dict {"data": base64_str, "content_type": "image/jpeg"}
    """
    patient = get_patient(phone)

    # Store a human-readable version of the user turn (no binary data in history)
    stored_text = user_message or ("[Lab report shared]" if media else "")
    patient["conversation"].append({"role": "user", "content": stored_text})

    # Build the sliding-window message list for the API call
    # Earlier turns are plain text; the current (last) turn may include media blocks
    history = patient["conversation"][-HISTORY_WINDOW:]
    messages: list = []
    for i, m in enumerate(history):
        is_last = i == len(history) - 1
        if is_last and m["role"] == "user":
            content = _build_user_content(user_message, media)
        else:
            content = m["content"]
        messages.append({"role": m["role"], "content": content})

    system = _build_system(patient)

    # ── Agentic loop ──────────────────────────────────────────────────────────
    while True:
        response = _client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=system,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            reply_text = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            patient["conversation"].append({"role": "assistant", "content": reply_text})
            save_patient(phone, patient)
            return reply_text

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json = run_tool(phone, block.name, dict(block.input))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_json,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "I'm sorry, something went wrong. Please try again in a moment."
