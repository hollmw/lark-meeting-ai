"""
Speaker Diarization Module
───────────────────────────
Identifies who spoke when using pyannote.audio.

Takes the Whisper transcript segments and labels each one
with a speaker (SPEAKER_00, SPEAKER_01, etc.)

Output is a merged transcript where every segment has:
- text
- start/end timestamps
- speaker label
- language
"""

import warnings
import yaml
from pathlib import Path

# Suppress noisy torchcodec/pyannote warnings — these are non-fatal
warnings.filterwarnings("ignore", message="torchcodec is not installed correctly")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")


# ── Load config ───────────────────────────────────────────────────────────────

def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
PYANNOTE_CONFIG = CONFIG.get("models", {}).get("pyannote", {})
BACKEND_MODE = CONFIG.get("backend", {}).get("mode", "local")


# ── Pipeline loader (cached) ───────────────────────────────────────────────────

_pipeline = None

def get_pipeline():
    """Loads and caches the Pyannote diarization pipeline."""
    global _pipeline
    if _pipeline is None:
        from pyannote.audio import Pipeline
        hf_token = PYANNOTE_CONFIG.get("hf_token", "")

        if not hf_token:
            raise ValueError(
                "HuggingFace token required for Pyannote. "
                "Add it to config.yaml under models.pyannote.hf_token. "
                "Get one free at huggingface.co/settings/tokens"
            )

        print("[Pyannote] Loading diarization model...")
        print("[Pyannote] First load downloads model weights — may take a few minutes")
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token,
        )
        if _pipeline is None:
            raise RuntimeError(
                "Pipeline.from_pretrained returned None — "
                "check that your HuggingFace token is valid and you have accepted "
                "terms at hf.co/pyannote/speaker-diarization-3.1 and hf.co/pyannote/segmentation-3.0"
            )
        print("[Pyannote] Model loaded ✅")
    return _pipeline


# ── Diarization ───────────────────────────────────────────────────────────────

def diarize(audio_path: str) -> list:
    """
    Runs speaker diarization on an audio file.

    Returns a list of speaker turns:
    [
        {"start": 0.0, "end": 5.2, "speaker": "SPEAKER_00"},
        {"start": 5.2, "end": 12.1, "speaker": "SPEAKER_01"},
        ...
    ]
    """
    if BACKEND_MODE == "hosted":
        return _diarize_hosted(audio_path)
    return _diarize_local(audio_path)


def _diarize_local(audio_path: str) -> list:
    """Runs Pyannote diarization locally."""
    pipeline = get_pipeline()
    num_speakers = PYANNOTE_CONFIG.get("num_speakers", "auto")

    print(f"[Pyannote] Diarizing: {audio_path}")

    kwargs = {}
    if num_speakers != "auto":
        kwargs["num_speakers"] = int(num_speakers)

    diarization = pipeline(audio_path, **kwargs)

    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append({
            "start": round(turn.start, 2),
            "end": round(turn.end, 2),
            "speaker": speaker,
        })

    print(f"[Pyannote] Found {len(set(t['speaker'] for t in turns))} speakers, {len(turns)} turns")
    return turns


def _diarize_hosted(audio_path: str) -> list:
    """Sends audio to hosted backend for diarization."""
    import httpx
    hosted_url = CONFIG["backend"]["hosted_url"]
    api_key = CONFIG["backend"].get("hosted_api_key", "")

    with open(audio_path, "rb") as f:
        response = httpx.post(
            f"{hosted_url}/diarize",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"audio": f},
            timeout=300,
        )
        response.raise_for_status()
        return response.json()


# ── Merge transcript + speaker labels ─────────────────────────────────────────

def merge_transcript_with_speakers(segments: list, turns: list) -> list:
    """
    Merges Whisper transcript segments with Pyannote speaker turns.

    For each transcript segment, finds which speaker was talking
    at that time based on timestamp overlap.

    Returns:
    [
        {
            "start": 0.0,
            "end": 5.2,
            "speaker": "SPEAKER_00",
            "text": "Hello everyone, let's get started.",
            "language": "en"
        },
        ...
    ]
    """
    merged = []

    for seg in segments:
        seg_mid = (seg["start"] + seg["end"]) / 2

        # Find which speaker turn overlaps with the midpoint of this segment
        speaker = "SPEAKER_00"  # default fallback
        best_overlap = 0

        for turn in turns:
            # Calculate overlap between segment and speaker turn
            overlap_start = max(seg["start"], turn["start"])
            overlap_end = min(seg["end"], turn["end"])
            overlap = max(0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                speaker = turn["speaker"]

        merged.append({
            "start": seg["start"],
            "end": seg["end"],
            "speaker": speaker,
            "text": seg["text"],
            "language": seg.get("language", "unknown"),
        })

    return merged


def format_transcript(merged_segments: list, speaker_names: dict = None) -> str:
    """
    Formats merged segments into a readable transcript string.

    speaker_names: optional dict to map speaker IDs to real names
                   e.g. {"SPEAKER_00": "Max", "SPEAKER_01": "John"}
    """
    lines = []
    current_speaker = None

    for seg in merged_segments:
        speaker_id = seg["speaker"]
        name = speaker_names.get(speaker_id, speaker_id) if speaker_names else speaker_id

        # Only print speaker label when speaker changes
        if speaker_id != current_speaker:
            timestamp = f"[{_format_time(seg['start'])}]"
            lines.append(f"\n{name} {timestamp}")
            current_speaker = speaker_id

        lines.append(seg["text"])

    return "\n".join(lines).strip()


def _format_time(seconds: float) -> str:
    """Converts seconds to MM:SS format."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"
