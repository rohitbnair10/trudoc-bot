"""
Flask webhook server.
Supports both Twilio and Meta Cloud API — set WHATSAPP_PROVIDER in .env.
Handles text messages AND media (images / PDFs) for lab report uploads.
"""
import base64
import os

import requests
from dotenv import load_dotenv
from flask import Flask, Response, request

load_dotenv()

from bot import get_response  # noqa: E402 — load_dotenv must run first

app = Flask(__name__)
PROVIDER = os.getenv("WHATSAPP_PROVIDER", "twilio").lower()


# ─── Shared media helper ──────────────────────────────────────────────────────

def _to_base64(raw: bytes) -> str:
    return base64.standard_b64encode(raw).decode("utf-8")


# ─── Twilio helpers ───────────────────────────────────────────────────────────

def _download_twilio_media(media_url: str) -> tuple[bytes, str]:
    """Download a Twilio media attachment (requires HTTP Basic Auth)."""
    resp = requests.get(
        media_url,
        auth=(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "image/jpeg")


def _send_twilio(to: str, body: str) -> None:
    from twilio.rest import Client
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_NUMBER"],
        to=to,
        body=body,
    )


def _handle_twilio() -> Response:
    from twilio.twiml.messaging_response import MessagingResponse

    def _safe_reply(text: str) -> Response:
        resp = MessagingResponse()
        resp.message(text)
        return Response(str(resp), mimetype="text/xml")

    def _empty() -> Response:
        return Response(str(MessagingResponse()), mimetype="text/xml")

    try:
        from_number = request.form.get("From", "").strip()
        body = request.form.get("Body", "").strip()
        num_media = int(request.form.get("NumMedia", "0"))

        media = None
        if num_media > 0:
            media_url = request.form.get("MediaUrl0", "")
            content_type = request.form.get("MediaContentType0", "image/jpeg")
            try:
                raw, ct = _download_twilio_media(media_url)
                media = {"data": _to_base64(raw), "content_type": ct}
            except Exception as exc:
                app.logger.error("Failed to download Twilio media: %s", exc)
                # Tell the patient something went wrong with their image
                return _safe_reply(
                    "Sorry, I wasn't able to open that image. "
                    "Could you try sending it again, or send a clearer photo?"
                )

        # Require either text or media
        if not body and media is None:
            return _empty()

        reply = get_response(from_number, body, media=media)

        # Guard against empty reply crashing Twilio
        if not reply or not reply.strip():
            app.logger.warning("Empty reply from get_response for %s", from_number)
            return _safe_reply(
                "Sorry, something went wrong on our end. Please try again in a moment."
            )

        return _safe_reply(reply)

    except Exception as exc:
        app.logger.error("Unhandled error in _handle_twilio: %s", exc, exc_info=True)
        try:
            resp = MessagingResponse()
            resp.message("Sorry, something went wrong. Please try again in a moment.")
            return Response(str(resp), mimetype="text/xml")
        except Exception:
            return Response("<?xml version='1.0'?><Response/>", mimetype="text/xml")


# ─── Meta Cloud API helpers ───────────────────────────────────────────────────

def _download_meta_media(media_id: str) -> tuple[bytes, str]:
    """Resolve a Meta media ID to bytes using the Graph API."""
    token = os.environ["META_ACCESS_TOKEN"]

    # Step 1: get the temporary download URL
    meta_resp = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    meta_resp.raise_for_status()
    media_url = meta_resp.json()["url"]

    # Step 2: download the actual bytes
    dl_resp = requests.get(
        media_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    dl_resp.raise_for_status()
    return dl_resp.content, dl_resp.headers.get("Content-Type", "image/jpeg")


def _send_meta(to: str, body: str) -> None:
    phone_number_id = os.environ["META_PHONE_NUMBER_ID"]
    token = os.environ["META_ACCESS_TOKEN"]
    requests.post(
        f"https://graph.facebook.com/v19.0/{phone_number_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        },
        timeout=10,
    )


def _handle_meta_verify() -> Response:
    verify_token = os.getenv("META_VERIFY_TOKEN", "")
    if (
        request.args.get("hub.mode") == "subscribe"
        and request.args.get("hub.verify_token") == verify_token
    ):
        return Response(request.args.get("hub.challenge", ""), status=200)
    return Response("Forbidden", status=403)


def _handle_meta() -> Response:
    data = request.json or {}
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                phone = msg["from"]
                normalised = f"whatsapp:+{phone}"
                msg_type = msg.get("type", "")

                body = ""
                media = None

                if msg_type == "text":
                    body = msg["text"]["body"].strip()

                elif msg_type in ("image", "document"):
                    # Patient sent a photo or PDF of their lab report
                    media_obj = msg.get(msg_type, {})
                    media_id = media_obj.get("id", "")
                    body = media_obj.get("caption", "").strip()
                    try:
                        raw, ct = _download_meta_media(media_id)
                        media = {"data": _to_base64(raw), "content_type": ct}
                    except Exception as exc:
                        app.logger.error("Failed to download Meta media: %s", exc)

                else:
                    # Unsupported message type (voice, sticker, etc.) — skip
                    continue

                if body or media:
                    reply = get_response(normalised, body, media=media)
                    _send_meta(phone, reply)

    return Response("OK", status=200)


# ─── Single webhook endpoint ──────────────────────────────────────────────────

@app.route("/webhook", methods=["GET", "POST"])
def webhook() -> Response:
    if PROVIDER == "twilio":
        return _handle_twilio()
    elif PROVIDER == "meta":
        if request.method == "GET":
            return _handle_meta_verify()
        return _handle_meta()
    return Response(f"Unknown WHATSAPP_PROVIDER: {PROVIDER}", status=400)


@app.route("/health")
def health() -> Response:
    return Response("OK", status=200)


@app.route("/run-outreach", methods=["POST"])
def run_outreach() -> Response:
    """
    Called daily by Railway's cron service (or any scheduler).
    Protected by a shared secret set in OUTREACH_SECRET env var.
    """
    secret = os.getenv("OUTREACH_SECRET", "")
    if not secret or request.headers.get("X-Outreach-Secret") != secret:
        return Response("Unauthorized", status=401)

    from outreach import run_refill_outreach
    try:
        run_refill_outreach()
        return Response("Outreach complete", status=200)
    except Exception as exc:
        app.logger.error("Outreach failed: %s", exc)
        return Response(f"Error: {exc}", status=500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
