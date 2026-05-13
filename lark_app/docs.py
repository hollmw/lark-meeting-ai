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
    Extracts the token from a Lark Doc or Wiki URL.
    Returns a tuple: (token, is_wiki)
    Handles URLs like:
      https://xxx.larksuite.com/docx/TOKEN
      https://xxx.larksuite.com/wiki/TOKEN
      TOKEN (passed directly)
    """
    match = re.search(r'/docx/([A-Za-z0-9_-]+)', url_or_token)
    if match:
        return match.group(1), False

    match = re.search(r'/wiki/([A-Za-z0-9_-]+)', url_or_token)
    if match:
        return match.group(1), True

    return url_or_token.strip(), False


async def resolve_wiki_to_doc_token(wiki_token: str) -> str:
    """
    Resolves a Wiki node token to the underlying document token.
    Wiki pages are backed by a regular doc — we need that doc token to insert blocks.
    """
    headers = await get_auth_headers()
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{LARK_API_BASE}/wiki/v2/spaces/get_node",
            headers=headers,
            params={"token": wiki_token},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        obj_token = data.get("data", {}).get("node", {}).get("obj_token")
        if not obj_token:
            raise ValueError(f"Could not resolve wiki token to doc token: {data}")
        print(f"[Docs] Resolved wiki token {wiki_token} → doc token {obj_token}")
        return obj_token


def _apply_speaker_names(notes: dict, speaker_names: dict) -> dict:
    """Replaces SPEAKER_XX labels with real names throughout all notes fields."""
    if not speaker_names:
        return notes
    import copy, json
    notes = copy.deepcopy(notes)
    # Serialise → replace → deserialise (handles all nested strings at once)
    raw = json.dumps(notes)
    # Sort by length descending so SPEAKER_01 doesn't match inside SPEAKER_010
    for spk, name in sorted(speaker_names.items(), key=lambda x: len(x[0]), reverse=True):
        raw = raw.replace(spk, name)
    return json.loads(raw)


async def insert_meeting_notes(document_id: str, notes: dict, speaker_names: dict = None) -> dict:
    """
    Inserts formatted meeting notes at the end of a Lark Doc or Wiki page.

    Args:
        document_id: Lark Doc token or Wiki node token
        notes:       Notes dict from the pipeline (summary, action_items, etc.)

    Returns:
        Lark API response dict
    """
    if speaker_names:
        notes = _apply_speaker_names(notes, speaker_names)
        print(f"[Docs] Applied speaker names: {speaker_names}")

    headers = await get_auth_headers()
    blocks = _build_notes_blocks(notes)

    print(f"[Docs] Inserting {len(blocks)} blocks into doc: {document_id}")

    async with httpx.AsyncClient() as client:
        # Step 1: fetch doc to get the actual root block ID and revision
        doc_resp = await client.get(
            f"{LARK_API_BASE}/docx/v1/documents/{document_id}",
            headers=headers,
            timeout=15,
        )
        print(f"[Docs] Fetch doc response: {doc_resp.status_code}: {doc_resp.text[:300]}")
        doc_resp.raise_for_status()
        doc_data   = doc_resp.json().get("data", {}).get("document", {})
        root_block = doc_data.get("block_id", document_id)
        revision   = doc_data.get("revision_id", -1)
        print(f"[Docs] Root block: {root_block}, revision: {revision}")

        # Step 2: insert blocks in batches of 50 (Lark API limit)
        BATCH_SIZE = 50
        last_response = None
        for i in range(0, len(blocks), BATCH_SIZE):
            batch = blocks[i:i + BATCH_SIZE]
            response = await client.post(
                f"{LARK_API_BASE}/docx/v1/documents/{document_id}/blocks/{root_block}/children",
                headers=headers,
                params={"document_revision_id": -1},
                json={"children": batch},
                timeout=30,
            )
            print(f"[Docs] Batch {i//BATCH_SIZE + 1}: {response.status_code}")
            if response.status_code == 403:
                raise PermissionError(
                    f"Bot lacks Editor access to doc '{document_id}'. "
                    "Fix: open the Lark page → Share → add your bot as Editor."
                )
            response.raise_for_status()
            last_response = response

        print("[Docs] ✅ Notes inserted into doc")
        return last_response.json()


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
            "elements": [{"text_run": {"content": content or " "}}],
        }
    }

def _heading1(content: str) -> dict:
    return {
        "block_type": 3,
        "heading1": {
            "elements": [{"text_run": {"content": content}}],
        }
    }

def _heading2(content: str) -> dict:
    return {
        "block_type": 4,
        "heading2": {
            "elements": [{"text_run": {"content": content}}],
        }
    }

def _bullet(content: str) -> dict:
    return {
        "block_type": 12,
        "bullet": {
            "elements": [{"text_run": {"content": content}}],
        }
    }

def _divider() -> dict:
    return _text("─" * 40)
