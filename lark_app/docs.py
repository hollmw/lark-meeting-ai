"""
Lark Docs Integration
──────────────────────
Inserts formatted meeting notes into a Lark Doc using the Docs API (docx/v1).
Supports both manual doc URL entry and automatic context when running as a
Docs Add-on inside Lark.
"""

import re
import httpx
from datetime import datetime
from lark_app.auth import get_auth_headers

LARK_API_BASE = "https://open.larksuite.com/open-apis"


# ── Public API ────────────────────────────────────────────────────────────────

def extract_doc_token(url_or_token: str) -> str:
    """
    Extracts the document token from a Lark Doc URL, or returns the raw token.
    Handles URLs like:
      https://xxx.larksuite.com/docx/TOKEN
      https://xxx.feishu.cn/docx/TOKEN
      TOKEN (passed directly)
    """
    match = re.search(r'/docx/([A-Za-z0-9_-]+)', url_or_token)
    if match:
        return match.group(1)
    return url_or_token.strip()


async def insert_meeting_notes(document_id: str, notes: dict) -> dict:
    """
    Inserts formatted meeting notes at the end of a Lark Doc.

    Args:
        document_id: Lark Doc token (from URL or Docs Add-on context)
        notes:       Notes dict from the pipeline (summary, action_items, etc.)

    Returns:
        Lark API response dict
    """
    headers = await get_auth_headers()
    blocks = _build_notes_blocks(notes)

    print(f"[Docs] Inserting {len(blocks)} blocks into doc: {document_id}")

    async with httpx.AsyncClient() as client:
        # The document root block has the same ID as the document itself
        response = await client.post(
            f"{LARK_API_BASE}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            headers=headers,
            json={"children": blocks},
            timeout=30,
        )
        if not response.is_success:
            print(f"[Docs] API error {response.status_code}: {response.text}")
        response.raise_for_status()
        print("[Docs] ✅ Notes inserted into doc")
        return response.json()


# ── Block builders ────────────────────────────────────────────────────────────

def _build_notes_blocks(notes: dict) -> list:
    """
    Builds a list of Lark Doc blocks representing formatted meeting notes.

    Layout:
      📋 Meeting Notes — Date
      Duration | Language | Speakers
      ────────────────────────────
      📝 Summary
      [text]
      ✅ Action Items
      • item
      🔑 Key Decisions
      • decision
      📝 Additional Notes   ← empty space for manual typing
      ────────────────────────────
      📄 Full Transcript
      [text lines]
    """
    blocks = []
    date_str = datetime.now().strftime("%B %d, %Y  %H:%M")

    # ── Header ────────────────────────────────────────────
    blocks.append(_heading1(f"📋 Meeting Notes — {date_str}"))

    meta_parts = []
    if notes.get("duration"):
        meta_parts.append(f"⏱ {notes['duration']}")
    if notes.get("language"):
        meta_parts.append(f"🌐 {notes['language'].upper()}")
    if notes.get("speakers"):
        meta_parts.append(f"🎤 {', '.join(notes['speakers'])}")
    if meta_parts:
        blocks.append(_text("  ·  ".join(meta_parts)))

    blocks.append(_divider())

    # ── Summary ───────────────────────────────────────────
    blocks.append(_heading2("📝 Summary"))
    blocks.append(_text(notes.get("summary") or "—"))

    # ── Action Items ──────────────────────────────────────
    blocks.append(_heading2("✅ Action Items"))
    items = notes.get("action_items") or []
    if items:
        for item in items:
            blocks.append(_bullet(str(item)))
    else:
        blocks.append(_text("None identified"))

    # ── Key Decisions ─────────────────────────────────────
    blocks.append(_heading2("🔑 Key Decisions"))
    decisions = notes.get("decisions") or []
    if decisions:
        for d in decisions:
            blocks.append(_bullet(str(d)))
    else:
        blocks.append(_text("None identified"))

    # ── Additional Notes (manual space) ───────────────────
    blocks.append(_heading2("📝 Additional Notes"))
    blocks.append(_text(""))   # blank lines for manual input
    blocks.append(_text(""))
    blocks.append(_text(""))

    blocks.append(_divider())

    # ── Transcript ────────────────────────────────────────
    blocks.append(_heading2("📄 Full Transcript"))
    transcript = (notes.get("transcript") or "").strip()
    if transcript:
        for line in transcript.split("\n"):
            if line.strip():
                blocks.append(_text(line))
    else:
        blocks.append(_text("(no transcript available)"))

    return blocks


# ── Block helpers ─────────────────────────────────────────────────────────────

def _text(content: str) -> dict:
    return {
        "block_type": 2,
        "text": {
            "elements": [{"text_run": {"content": content}}],
            "style": {}
        }
    }

def _heading1(content: str) -> dict:
    return {
        "block_type": 3,
        "heading1": {
            "elements": [{"text_run": {"content": content}}],
            "style": {}
        }
    }

def _heading2(content: str) -> dict:
    return {
        "block_type": 4,
        "heading2": {
            "elements": [{"text_run": {"content": content}}],
            "style": {}
        }
    }

def _bullet(content: str) -> dict:
    return {
        "block_type": 12,
        "bullet": {
            "elements": [{"text_run": {"content": content}}],
            "style": {}
        }
    }

def _divider() -> dict:
    return {"block_type": 22}
