"""
Claude-powered conversation engine.
Maintains per-patient message history and runs the tool-use agentic loop.
Supports optional media (image / PDF) for lab report uploads.
"""
import base64
import os
from datetime import datetime

import anthropic

from storage import get_patient, patient_exists, save_patient, get_unregistered, save_unregistered
from tools import TOOL_DEFINITIONS, run_tool

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

HISTORY_WINDOW = 20  # text turns kept in memory

SYSTEM_PROMPT = """\
You are a warm, compassionate healthcare assistant supporting patients with chronic conditions \
at TruDoc Healthcare.

━━ GLOBAL RULE — LAB TEST QUESTIONS ━━
If a patient asks about lab tests at ANY point in ANY workflow, respond:
"Your doctor can prescribe a lab test during your refill consultation as well — \
no need to book it separately. Let's get that consultation set up for you first."
Then continue with the relevant workflow step.

━━ WORKFLOW A — OUTBOUND REFILL ALERT (bot messaged first) ━━
Detected when the conversation history starts with an assistant message about a refill.
Follow this exact flow:

  Step 1 — CALLBACK OFFER
    Ask: "Would you like to schedule a refill consultation with your doctor?"
    • YES → go to Step 1a.
    • NO  → go to Step 2.

  Step 1a — COLLECT DATE & TIME (only after patient says YES)
    Ask: "Sure! Please share your preferred date and time — for example: '22nd April, 3pm'."

    STRICT RULE — DO NOT call book_callback until the patient's reply contains BOTH:
      • a specific date  (e.g. "22nd April", "tomorrow", "25 April")
      • a specific time  (e.g. "3pm", "15:00", "morning" is NOT specific enough)

    If EITHER is missing, respond warmly and ask again. Examples:
      - Date only  → "Got it — and what time works best for you?"
      - Time only  → "Perfect — and which date were you thinking?"
      - Neither    → "Please share both a date and a time so I can book this for you."

    MANDATORY: You MUST call the book_callback tool before sending any confirmation.
    Calling the tool is not optional — if you do not call it, the booking does not exist.
    Do NOT write "booked", "done", or "confirmed" without first executing book_callback.
    Call book_callback with:
      preferred_date = YYYY-MM-DD
      preferred_time = HH:MM (24-hour)
      reason = "Medication refill consultation"
    Only AFTER the tool call succeeds, send:
Your doctor will call you then."

  Step 2 — PATIENT SAID NO — CHECK PRESCRIPTION STATUS
    Ask: "No problem! Do you currently have an active refill prescription?"

    • YES → Ask in one message:
        "Great! Could you share a photo or PDF of your prescription here? \
Or if that's not handy, just let me know how many days of medication you have left."
        - If they share a document/image → acknowledge it, call record_lab_test to log it \
(test_type: "Prescription"), thank them, and close warmly.
        - If they give a number of days → call record_refill_status \
(has_prescription=true, days_remaining=<number>), then respond:
            · >=14 days: "Great, you have plenty of time. We'll check in again closer to \
your refill date."
            · <14 days: "You're running fairly low — don't leave it too long. \
Reach out any time and we can arrange a consultation quickly."

    • NO → Respond:
        "No worries! If you ever need a new refill prescription, you can reach TruDoc \
directly:\n\nCall: 800 800 088\nWhatsApp: Send 'Hi' to 800 800 088\n\nWe're here \
whenever you need us."
        Then call record_refill_status (has_prescription=false).

━━ WORKFLOW B — INBOUND REGISTERED (patient in DB messages first) ━━
Detected when the conversation history is empty or starts with a patient message,
AND the patient exists in the database.

  Step 1 — GREET
    Greet the patient warmly by name if known.

  Step 2 — COLLECT DATE & TIME
    Ask: "I'd be happy to help you book a refill consultation with your doctor. \
Please share your preferred date and time — for example: '22nd April, 3pm'."

    STRICT RULE — DO NOT call book_callback until the patient's reply contains BOTH:
      • a specific date  (e.g. "22nd April", "tomorrow", "25 April")
      • a specific time  (e.g. "3pm", "15:00", "morning" is NOT specific enough)

    If EITHER is missing, respond warmly and ask again:
      - Date only  → "Got it — and what time works best for you?"
      - Time only  → "Perfect — and which date were you thinking?"
      - Neither    → "Please share both a date and a time so I can book this for you."

  Step 3 — BOOK & CONFIRM
    MANDATORY: You MUST call the book_callback tool before sending any confirmation.
    Calling the tool is not optional — if you do not call it, the booking does not exist.
    Do NOT write "booked", "done", or "confirmed" without first executing book_callback.
    Call book_callback with:
      preferred_date = YYYY-MM-DD
      preferred_time = HH:MM (24-hour)
      reason = "Refill consultation"
    Only AFTER the tool call succeeds, send:
A TruDoc doctor will call you then."

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


def _log_unregistered_interest(phone: str, message: str) -> None:
    """
    Persist an unregistered contact in the Supabase unregistered table.
    Two-turn state machine:
      - Turn 1: first message logged; bot asks for name + condition.
      - Turn 2: reply stored as name_and_condition; request marked raised.
    Further messages are silently acknowledged.
    """
    entry = get_unregistered(phone)

    if entry is None:
        save_unregistered(phone, {
            "phone": phone,
            "first_message": message or "(media / no text)",
            "name_and_condition": None,
            "request_raised": False,
            "contacted_at": datetime.now().isoformat(),
        })
    elif entry.get("name_and_condition") is None:
        entry["name_and_condition"] = message or "(no details given)"
        entry["request_raised"] = True
        save_unregistered(phone, entry)


def _unregistered_state(phone: str) -> str:
    """Return 'new' | 'waiting_details' | 'done' for an unregistered number."""
    entry = get_unregistered(phone)
    if entry is None:
        return "new"
    if entry.get("request_raised"):
        return "done"
    return "waiting_details"


def get_response(phone: str, user_message: str, media: dict | None = None) -> str:
    """
    Process one incoming message and return the bot's reply.

    Args:
        phone:        Normalised phone key, e.g. "whatsapp:+447911123456"
        user_message: Text content of the WhatsApp message (may be empty if media-only)
        media:        Optional dict {"data": base64_str, "content_type": "image/jpeg"}
    """
    # ── Unregistered patient gate ─────────────────────────────────────────────
    # Unregistered numbers go through a simple 2-turn flow:
    #   Turn 1 — ask for name + chronic condition
    #   Turn 2 — store their answer, confirm request raised
    #   Turn 3+ — silent repeat of the confirmation
    if not patient_exists(phone):
        state = _unregistered_state(phone)
        _log_unregistered_interest(phone, user_message)

        if state == "new":
            return (
                "Hi! Thank you for reaching out to TruDoc. 😊 "
                "To help us get you connected with the right doctor, could you please share: "
                "your name and the chronic condition you are managing?"
            )
        elif state == "waiting_details":
            return (
                "Thank you! We've noted your details and raised your request. "
                "A TruDoc associate will reach out to you shortly with the next steps. "
                "If it's urgent, you can also call us at 800 800 088."
            )
        else:
            return (
                "Your request is already with us! "
                "A TruDoc associate will be in touch with you shortly. "
                "For urgent queries, call us at 800 800 088."
            )

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
        # If the last user message contains a time (e.g. "3pm", "15:00", "3 pm")
        # AND recent history shows we already have a date, force book_callback to fire.
        import re
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"
             and isinstance(m["content"], str)),
            ""
        )
        recent_text = " ".join(
            m["content"] for m in messages[-6:]
            if m["role"] in ("user", "assistant") and isinstance(m["content"], str)
        ).lower()

        time_given = bool(re.search(r'\b(\d{1,2}(:\d{2})?(\s?[ap]m)|\d{2}:\d{2})\b', last_user, re.I))
        date_in_history = bool(re.search(
            r'\b(\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|'
            r'tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}/\d{1,2})',
            recent_text, re.I
        ))
        force_booking = time_given and date_in_history

        api_kwargs = dict(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=system,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        if force_booking:
            api_kwargs["tool_choice"] = {"type": "tool", "name": "book_callback"}

        response = _client.messages.create(**api_kwargs)

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
