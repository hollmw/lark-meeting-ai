"""
Lark Webhook Handler
─────────────────────
Receives events from Lark Open Platform and routes them
to the correct handler (recording ready, messages, etc.)

Supports both encrypted and unencrypted payloads.
"""

import json
import base64
import hashlib
from fastapi import Request, HTTPException
from Crypto.Cipher import AES
from lark_app.auth import verify_lark_request, load_config
from lark_app import events

CONFIG = load_config()
ENCRYPT_KEY = CONFIG["lark"].get("encrypt_key", "")


# ── Decryption ────────────────────────────────────────────────────────────────

def decrypt_payload(encrypted: str) -> dict:
    """
    Decrypts an AES-256-CBC encrypted Lark webhook payload.
    Lark encrypts when encrypt_key is configured in the developer console.
    """
    # Key = first 32 bytes of SHA256 hash of encrypt_key
    key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()

    # Decode base64 payload
    encrypted_bytes = base64.b64decode(encrypted)

    # First 16 bytes = IV, rest = ciphertext
    iv = encrypted_bytes[:16]
    ciphertext = encrypted_bytes[16:]

    # Decrypt
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(ciphertext)

    # Remove PKCS7 padding
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len]

    return json.loads(decrypted.decode("utf-8"))


# ── Main webhook entry point ──────────────────────────────────────────────────

async def handle_webhook(request: Request) -> dict:
    """
    Entry point for all incoming Lark webhook events.
    Handles encrypted payloads, verifies token, routes to correct handler.
    """
    raw_body = await request.json()

    # ── Step 1: Decrypt if payload is encrypted ───────────────────────────────
    if "encrypt" in raw_body:
        try:
            body = decrypt_payload(raw_body["encrypt"])
            print("[Webhook] Decrypted encrypted payload")
        except Exception as e:
            print(f"[Webhook] Decryption failed: {e}")
            raise HTTPException(status_code=400, detail="Decryption failed")
    else:
        body = raw_body

    # ── Step 2: Handle Lark URL verification challenge ────────────────────────
    # Lark sends this when you first register your webhook URL.
    # Must echo the challenge back immediately.
    if body.get("type") == "url_verification":
        challenge = body.get("challenge")
        token = body.get("token", "")
        if not verify_lark_request(token):
            raise HTTPException(status_code=401, detail="Invalid verification token")
        print("[Webhook] URL verification challenge passed ✅")
        return {"challenge": challenge}

    # ── Step 3: Verify event token ────────────────────────────────────────────
    header = body.get("header", {})
    token = header.get("token", "")
    if not verify_lark_request(token):
        print(f"[Webhook] Token mismatch — received: {token[:8]}...")
        raise HTTPException(status_code=401, detail="Invalid token")

    # ── Step 4: Route event to correct handler ────────────────────────────────
    event_type = header.get("event_type", "")
    event_data = body.get("event", {})

    print(f"[Webhook] Received event: {event_type}")

    handlers = {
        # Recording events
        "vc.meeting.recording_ready_v1":   events.on_recording_ready,
        "vc.meeting.recording_started_v1": events.on_recording_started,
        "vc.meeting.recording_ended_v1":   events.on_recording_ended,

        # Meeting events
        "vc.meeting.meeting_started_v1":   events.on_meeting_started,
        "vc.meeting.meeting_ended_v1":     events.on_meeting_ended,

        # Message events (bot interactions)
        "im.message.receive_v1":           events.on_message_received,
    }

    handler = handlers.get(event_type)
    if handler:
        await handler(event_data)
    else:
        print(f"[Webhook] No handler for event type: {event_type}")

    return {"code": 0}
