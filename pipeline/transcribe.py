"""
Whisper Transcription Module
──────────────────────────────
Transcribes audio files using OpenAI Whisper.

Key features:
- Auto language detection (handles Cantonese + English + code-switching)
- Returns timestamped segments for merging with speaker labels
- Supports local (whisper package) and hosted (API) modes via config
"""

import whisper
import yaml
from pathlib import Path


# ── Load config ───────────────────────────────────────────────────────────────

def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
WHISPER_CONFIG = CONFIG.get("models", {}).get("whisper", {})
BACKEND_MODE = CONFIG.get("backend", {}).get("mode", "local")


# ── Model loader (cached after first load) ────────────────────────────────────

_model = None

def get_model():
    """Loads and caches the Whisper model. Only loads once on first call."""
    global _model
    if _model is None:
        size = WHISPER_CONFIG.get("size", "medium")
        device = WHISPER_CONFIG.get("device", "cpu")
        print(f"[Whisper] Loading model: {size} on {device}...")
        print(f"[Whisper] First load may take a minute — model will be cached after")
        _model = whisper.load_model(size, device=device)
        print(f"[Whisper] Model loaded ✅")
    return _model


# ── Transcription ─────────────────────────────────────────────────────────────

def transcribe(audio_path: str) -> dict:
    """
    Transcribes an audio file and returns structured segments.

    Returns:
    {
        "language": "zh" | "en" | "yue" ...,
        "text": "full transcript as string",
        "segments": [
            {
                "start": 0.0,       # seconds
                "end": 2.5,
                "text": "Hello everyone",
                "language": "en"
            },
            ...
        ]
    }
    """
    if BACKEND_MODE == "hosted":
        return _transcribe_hosted(audio_path)
    return _transcribe_local(audio_path)


def _transcribe_local(audio_path: str) -> dict:
    """Runs Whisper locally on this machine."""
    model = get_model()
    language = WHISPER_CONFIG.get("language", "auto")

    print(f"[Whisper] Transcribing: {audio_path}")

    # Whisper options
    options = {
        "task": "transcribe",
        "verbose": False,
        "word_timestamps": False,
    }

    # If language is set to auto, let Whisper detect it
    # For Cantonese specifically, pass "yue" or "zh" — Whisper handles both
    if language != "auto":
        options["language"] = language

    result = model.transcribe(audio_path, **options)

    detected_language = result.get("language", "unknown")
    print(f"[Whisper] Detected language: {detected_language}")
    print(f"[Whisper] Transcription complete — {len(result['segments'])} segments")

    # Normalise segments
    segments = []
    for seg in result["segments"]:
        segments.append({
            "start": round(seg["start"], 2),
            "end": round(seg["end"], 2),
            "text": seg["text"].strip(),
            "language": detected_language,
        })

    return {
        "language": detected_language,
        "text": result["text"].strip(),
        "segments": segments,
    }


def _transcribe_hosted(audio_path: str) -> dict:
    """
    Sends audio to hosted backend server for transcription.
    Swap in when switching from local to hosted mode.
    """
    import httpx
    hosted_url = CONFIG["backend"]["hosted_url"]
    api_key = CONFIG["backend"].get("hosted_api_key", "")

    print(f"[Whisper] Sending to hosted backend: {hosted_url}")

    with open(audio_path, "rb") as f:
        response = httpx.post(
            f"{hosted_url}/transcribe",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"audio": f},
            timeout=300,  # 5 min timeout for long meetings
        )
        response.raise_for_status()
        return response.json()
