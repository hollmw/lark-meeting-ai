"""
Lark Bot
─────────
Handles all outbound messages from the bot:
- Consent prompt before recording
- Processing status updates
- Posting finished meeting notes to chat / Lark Docs
"""

import httpx
from lark_app.auth import get_auth_headers

LARK_API_BASE = "https://open.larksuite.com/open-apis"


# ── Send plain message ────────────────────────────────────────────────────────

async def send_message(chat_id: str = None, user_id: str = None, text: str = "") -> dict:
    """
    Sends a plain text message to a chat or user.
    Provide either chat_id (group chat) or user_id (direct message).
    """
    headers = await get_auth_headers()

    # Build recipient
    if chat_id:
        receive_id_type = "chat_id"
        receive_id = chat_id
    elif user_id:
        receive_id_type = "user_id"
        receive_id = user_id
    else:
        raise ValueError("Must provide chat_id or user_id")

    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": f'{{"text": "{text}"}}',
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LARK_API_BASE}/im/v1/messages?receive_id_type={receive_id_type}",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ── Consent prompt ────────────────────────────────────────────────────────────

async def send_consent_prompt(chat_id: str, user_id: str) -> dict:
    """
    Sends a consent confirmation to the user activating the tool.
    User must confirm before recording starts.
    Uses an interactive card with a confirm button.
    """
    headers = await get_auth_headers()

    # Interactive card with confirm/cancel buttons
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🎙️ Start Meeting Recording"},
            "template": "blue"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "By continuing, you confirm that **all meeting participants have been informed and consent to being recorded and transcribed.**\n\nRecordings are processed locally and never stored on external servers."
                }
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅  I confirm — Start Recording"},
                        "type": "primary",
                        "value": {"action": "confirm_consent", "user_id": user_id}
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Cancel"},
                        "type": "default",
                        "value": {"action": "cancel_consent"}
                    }
                ]
            }
        ]
    }

    import json
    payload = {
        "receive_id": user_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LARK_API_BASE}/im/v1/messages?receive_id_type=user_id",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ── Post meeting notes ─────────────────────────────────────────────────────────

async def post_meeting_notes(chat_id: str, notes: dict) -> dict:
    """
    Posts the finished meeting notes as a rich card to the Lark chat.
    Notes dict expected format:
      {
        "summary": "...",
        "action_items": ["...", "..."],
        "decisions": ["...", "..."],
        "speakers": ["Speaker 1", "Speaker 2"],
        "duration": "45 mins"
      }
    """
    headers = await get_auth_headers()

    # Build action items list
    action_items_text = "\n".join(
        [f"- {item}" for item in notes.get("action_items", [])]
    ) or "_None identified_"

    decisions_text = "\n".join(
        [f"- {d}" for d in notes.get("decisions", [])]
    ) or "_None identified_"

    speakers_text = ", ".join(notes.get("speakers", [])) or "Unknown"

    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 Meeting Notes"},
            "template": "green"
        },
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Duration**\n{notes.get('duration', 'N/A')}"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**Speakers**\n{speakers_text}"}},
                ]
            },
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**📝 Summary**\n{notes.get('summary', 'N/A')}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**✅ Action Items**\n{action_items_text}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**🔑 Key Decisions**\n{decisions_text}"}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "View Full Transcript"},
                        "type": "default",
                        "value": {"action": "view_transcript"}
                    }
                ]
            }
        ]
    }

    import json
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LARK_API_BASE}/im/v1/messages?receive_id_type=chat_id",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


# ── Help message ──────────────────────────────────────────────────────────────

async def send_help_message(chat_id: str) -> dict:
    """Sends a help/intro message explaining the bot."""
    return await send_message(
        chat_id=chat_id,
        text=(
            "👋 Hi! I'm your Meeting AI assistant.\n\n"
            "• Type *start* — begin recording your meeting\n"
            "• After the meeting, I'll automatically generate:\n"
            "  📝 Summary  ✅ Action Items  🔑 Decisions  📄 Full Transcript\n\n"
            "Supports Cantonese, English, and mixed-language meetings."
        )
    )
