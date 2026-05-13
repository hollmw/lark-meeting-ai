"""
Pipeline Orchestrator
──────────────────────
Runs the full AI pipeline end-to-end:

  Audio file
    → Whisper (transcription + language detection)
    → Pyannote (speaker diarization)
    → Merge (labelled transcript)
    → LLM (structured meeting notes)
    → Lark Bot (post notes to chat)

Called from app.py when recording stops.
"""

import asyncio
import yaml
from pathlib import Path
from pipeline.transcribe import transcribe
from pipeline.diarize import diarize, merge_transcript_with_speakers, format_transcript
from pipeline.summarise import summarise
from lark_app import bot

def _load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

_CONFIG = _load_config()
_DELETE_AFTER = _CONFIG.get("audio", {}).get("delete_after_processing", True)


async def run(audio_path: str, chat_id: str = None, user_id: str = None, open_id: str = None) -> dict:
    """
    Runs the full pipeline on a recorded audio file.

    Args:
        audio_path: Path to the WAV recording
        chat_id:    Lark group chat ID to post notes to (receive_id_type=chat_id)
        open_id:    Lark user open_id to DM notes to  (receive_id_type=open_id)
        user_id:    Legacy alias for open_id

    Returns:
        Full notes dict with transcript and summary
    """
    # Normalise: user_id is treated as open_id for backwards compatibility
    if user_id and not open_id:
        open_id = user_id
    print(f"\n[Pipeline] Starting — {audio_path}")
    print("[Pipeline] ─────────────────────────────────")

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    print("[Pipeline] Step 1/4 — Transcribing audio...")
    transcript_result = await asyncio.to_thread(transcribe, audio_path)
    language = transcript_result["language"]
    segments = transcript_result["segments"]
    full_text = transcript_result["text"]
    print(f"[Pipeline] ✅ Transcribed — language: {language}, segments: {len(segments)}")

    # ── Step 2: Diarize ───────────────────────────────────────────────────────
    print("[Pipeline] Step 2/4 — Identifying speakers...")
    try:
        turns = await asyncio.to_thread(diarize, audio_path)
        merged = merge_transcript_with_speakers(segments, turns)
        print(f"[Pipeline] ✅ Diarized — {len(set(t['speaker'] for t in turns))} speakers found")
    except Exception as e:
        print(f"[Pipeline] ⚠️  Diarization skipped: {e}")
        # Fall back to transcript without speaker labels
        merged = [
            {**seg, "speaker": "SPEAKER_00"}
            for seg in segments
        ]

    # ── Step 3: Format transcript ─────────────────────────────────────────────
    print("[Pipeline] Step 3/4 — Formatting transcript...")
    formatted_transcript = format_transcript(merged)

    # ── Step 4: Summarise ─────────────────────────────────────────────────────
    print("[Pipeline] Step 4/4 — Generating meeting notes...")
    notes = await asyncio.to_thread(summarise, formatted_transcript, language)

    # Add transcript and metadata to notes
    notes["transcript"] = formatted_transcript
    notes["language"] = language
    notes["audio_file"] = Path(audio_path).name
    notes["duration"] = _format_duration(
        merged[-1]["end"] if merged else 0
    )

    print("[Pipeline] ✅ Notes generated")
    print("[Pipeline] ─────────────────────────────────")
    print("[Pipeline] TRANSCRIPT:")
    print(formatted_transcript if formatted_transcript.strip() else "(empty — no speech detected)")
    print("[Pipeline] ─────────────────────────────────\n")

    # ── Cleanup audio file ────────────────────────────────────────────────────
    if _DELETE_AFTER and audio_path:
        try:
            Path(audio_path).unlink(missing_ok=True)
            print(f"[Pipeline] 🗑️  Deleted audio file: {audio_path}")
        except Exception as e:
            print(f"[Pipeline] ⚠️  Could not delete audio file: {e}")

    # ── Post to Lark ──────────────────────────────────────────────────────────
    if chat_id or open_id:
        try:
            print("[Pipeline] Posting notes to Lark...")
            await bot.post_meeting_notes(
                notes=notes,
                chat_id=chat_id,
                open_id=open_id,
            )
            print("[Pipeline] ✅ Notes posted to Lark")
        except Exception as e:
            print(f"[Pipeline] ⚠️  Could not post to Lark: {e}")

    return notes


def _format_duration(seconds: float) -> str:
    """Converts seconds to a human-readable duration."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes == 0:
        return f"{secs}s"
    return f"{minutes}m {secs}s"
