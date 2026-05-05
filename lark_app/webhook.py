"""
Lark Webhook Handler
─────────────────────
Receives events from Lark Open Platform and routes them
to the correct handler (recording ready, messages, etc.)
"""

import json
import hashlib
import hmac
from fastapi import Request, HTTPException
from lark_app.auth import verify_lark_request
from lark_app import events


# ── Main webhook entry point ──────────────────────────────────────────────────

async def handle_webhook(request: Request) -> dict:
    """
    Entry point for all incoming Lark webhook events.
    Verifies the request, parses the event type, and routes to handler.
    """
    body = await request.json()

    # ── Step 1: Handle Lark URL verification challenge ────────────────────────
    # Lark sends a challenge when you first register your webhook URL.
    # Must echo it back immediately.
    if body.get("type") == "url_verification":
        challenge = body.get("challenge")
        token = body.get("token", "")
        if not verify_lark_request(token):
            raise HTTPException(status_code=401, detail="Invalid verification token")
        return {"challenge": challenge}

    # ── Step 2: Verify event token ────────────────────────────────────────────
    header = body.get("header", {})
    token = header.get("token", "")
    if not verify_lark_request(token):
        raise HTTPException(status_code=401, detail="Invalid token")

    # ── Step 3: Route event to correct handler ────────────────────────────────
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
