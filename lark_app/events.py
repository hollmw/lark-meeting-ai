"""
Lark Event Handlers
────────────────────
Handles all incoming Lark events routed from the webhook.
Each function corresponds to a specific Lark event type.
"""

from lark_app import bot


# ── Recording Events ──────────────────────────────────────────────────────────

async def on_recording_ready(event: dict):
    """
    Fired when Lark has finished processing a meeting recording.
    This is the main trigger for the AI pipeline.
    """
    meeting_id = event.get("meeting_id")
    recording_file = event.get("url")          # URL to the recording file
    duration = event.get("duration")           # meeting duration in seconds
    host_user_id = event.get("host_user_id")

    print(f"[Event] Recording ready — Meeting: {meeting_id}, Duration: {duration}s")

    # Trigger AI pipeline (Phase 4 — will be wired up later)
    # await pipeline.process(meeting_id=meeting_id, recording_url=recording_file)

    # Notify host that processing has started
    await bot.send_message(
        user_id=host_user_id,
        text="✅ Recording received. Processing your meeting notes now — I'll send them here when ready!"
    )


async def on_recording_started(event: dict):
    """Fired when a meeting recording begins."""
    meeting_id = event.get("meeting_id")
    print(f"[Event] Recording started — Meeting: {meeting_id}")


async def on_recording_ended(event: dict):
    """Fired when a meeting recording stops."""
    meeting_id = event.get("meeting_id")
    print(f"[Event] Recording ended — Meeting: {meeting_id}")


# ── Meeting Events ────────────────────────────────────────────────────────────

async def on_meeting_started(event: dict):
    """Fired when a Lark meeting begins."""
    meeting_id = event.get("meeting_id")
    host_user_id = event.get("host_user_id")
    print(f"[Event] Meeting started — Meeting: {meeting_id}")


async def on_meeting_ended(event: dict):
    """Fired when a Lark meeting ends."""
    meeting_id = event.get("meeting_id")
    print(f"[Event] Meeting ended — Meeting: {meeting_id}")


# ── Message Events (Bot) ──────────────────────────────────────────────────────

async def on_message_received(event: dict):
    """
    Fired when a user sends a message to the bot.
    Handles commands and consent confirmations.
    """
    message = event.get("message", {})
    sender = event.get("sender", {})

    user_id = sender.get("sender_id", {}).get("user_id")
    chat_id = message.get("chat_id")
    content = message.get("content", "{}")

    # Parse message text
    try:
        import json
        text = json.loads(content).get("text", "").strip().lower()
    except Exception:
        text = ""

    print(f"[Event] Message received from {user_id}: {text}")

    # Handle basic commands
    if text in ["start", "begin", "record"]:
        await bot.send_consent_prompt(chat_id=chat_id, user_id=user_id)
    elif text in ["help", "hi", "hello"]:
        await bot.send_help_message(chat_id=chat_id)
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="Type *start* to begin recording your meeting, or *help* for more info."
        )
