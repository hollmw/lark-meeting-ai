"""
LLM Summarisation Module
─────────────────────────
Takes a labelled transcript and generates structured meeting notes:
  - Summary
  - Action items
  - Key decisions
  - Full transcript

Supports local (Ollama) and hosted (Claude/OpenAI) modes via config.
"""

import yaml
import json
from pathlib import Path


# ── Load config ───────────────────────────────────────────────────────────────

def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
LLM_CONFIG = CONFIG.get("models", {}).get("llm", {})
BACKEND_MODE = CONFIG.get("backend", {}).get("mode", "local")


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a professional meeting notes assistant.
You support multilingual meetings including English and Cantonese.
Your job is to produce clear, structured meeting notes from a transcript.
Always respond in valid JSON only — no extra text, no markdown, no // comments, no trailing commas."""

def build_prompt(transcript: str, language: str = "auto") -> str:
    """
    Builds the summarisation prompt.
    Instructs the LLM to output notes in the same language(s) as the meeting.
    """
    lang_instruction = ""
    if language in ("zh", "yue", "zh-HK"):
        lang_instruction = "The meeting was primarily in Cantonese/Chinese. Write the notes in Traditional Chinese (繁體中文)."
    elif language == "en":
        lang_instruction = "Write the notes in English."
    else:
        lang_instruction = "Write the notes in the same language(s) used in the meeting. If mixed Cantonese and English, write in English with Cantonese terms preserved where appropriate."

    return f"""Please analyse the following meeting transcript and produce structured meeting notes.

{lang_instruction}

Return ONLY a JSON object in this exact format.
IMPORTANT: action_items and decisions must be plain strings — never return objects or dicts.
Extract ALL action items mentioned, even if there are 4 or 5.
{{
    "summary": "2-4 sentence overview of what was discussed",
    "action_items": [
        "First action item with owner name in brackets if mentioned",
        "Second action item",
        "Third action item — include every task that was agreed upon"
    ],
    "decisions": [
        "First decision made",
        "Second decision made"
    ],
    "key_topics": ["topic1", "topic2"],
    "speakers": ["SPEAKER_00", "SPEAKER_01"],
    "sentiment": "positive | neutral | negative"
}}

TRANSCRIPT:
{transcript}"""


# ── Summarisation ─────────────────────────────────────────────────────────────

def summarise(transcript: str, language: str = "auto") -> dict:
    """
    Generates structured meeting notes from a transcript.

    Returns:
    {
        "summary": "...",
        "action_items": ["...", "..."],
        "decisions": ["...", "..."],
        "key_topics": ["...", "..."],
        "speakers": ["SPEAKER_00", "SPEAKER_01"],
        "sentiment": "positive"
    }
    """
    if BACKEND_MODE == "hosted":
        return _summarise_hosted(transcript, language)
    return _summarise_local(transcript, language)


def _summarise_local(transcript: str, language: str) -> dict:
    """Runs summarisation using local Ollama LLM."""
    import ollama

    model = LLM_CONFIG.get("model", "mistral")
    prompt = build_prompt(transcript, language)

    print(f"[LLM] Summarising with local model: {model}")

    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.3},  # lower temp = more consistent output
    )

    raw = response["message"]["content"].strip()
    return _parse_json_response(raw)


def _summarise_hosted(transcript: str, language: str) -> dict:
    """Sends transcript to hosted backend for summarisation."""
    import httpx
    hosted_url = CONFIG["backend"]["hosted_url"]
    api_key = CONFIG["backend"].get("hosted_api_key", "")

    print(f"[LLM] Summarising via hosted backend: {hosted_url}")

    response = httpx.post(
        f"{hosted_url}/summarise",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"transcript": transcript, "language": language},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _parse_json_response(raw: str) -> dict:
    """
    Parses JSON from LLM response.
    Handles cases where LLM wraps JSON in markdown code blocks.
    """
    # Strip markdown code blocks if present
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    # Strip // line comments (phi3 sometimes adds these — invalid JSON)
    import re
    raw = re.sub(r'//[^\n"]*', '', raw)
    # Strip trailing commas before } or ] (another phi3 quirk)
    raw = re.sub(r',\s*([\]}])', r'\1', raw)

    try:
        result = json.loads(raw)
        # Flatten any action_items / decisions that came back as dicts
        result["action_items"] = _flatten_list(result.get("action_items", []))
        result["decisions"]    = _flatten_list(result.get("decisions", []))
        return result
    except json.JSONDecodeError as e:
        print(f"[LLM] JSON parse error: {e}")
        print(f"[LLM] Raw response: {raw[:200]}")
        return {
            "summary": raw[:500] if raw else "Could not generate summary",
            "action_items": [],
            "decisions": [],
            "key_topics": [],
            "speakers": [],
            "sentiment": "neutral",
        }


def _flatten_list(items: list) -> list:
    """
    Ensures every item in a list is a plain string.
    Handles cases where the LLM returns dicts like
    {'Action item': 'foo', 'assignee': 'bar'} instead of 'foo (bar)'.
    """
    result = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # Try common keys, fall back to joining all values
            text = (
                item.get("Action item")
                or item.get("action_item")
                or item.get("action")
                or item.get("text")
                or item.get("description")
                or ", ".join(str(v) for v in item.values() if v)
            )
            assignee = item.get("assignee") or item.get("owner") or item.get("assigned_to")
            if assignee:
                text = f"{text} ({assignee})"
            result.append(str(text))
        else:
            result.append(str(item))
    return result
