"""
LARK Meeting AI — Main Server
──────────────────────────────
FastAPI server that handles:
  - Lark webhook events
  - Recording start/stop from the Gadget
  - AI pipeline processing
  - Status polling
"""

import os
import sys
import asyncio
from pathlib import Path
from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from lark_app.webhook import handle_webhook
from lark_app.docs import insert_meeting_notes, extract_doc_token
from pipeline.audio_capture import recorder
from pipeline.run import run as pipeline_run

app = FastAPI(title="LARK Meeting AI", version="1.0.0")

# ── CORS (allows the Gadget to call the local backend) ────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # localhost-only server — safe to allow all origins
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

# ── Serve Gadget UI ────────────────────────────────────────────────────────────
# When frozen by PyInstaller, static assets live in MEIPASS; in dev, use relative path.
_MEIPASS = os.environ.get('MEETING_AI_MEIPASS', str(Path(__file__).parent))
app.mount("/gadget", StaticFiles(directory=str(Path(_MEIPASS) / "gadget"), html=True), name="gadget")


# ── Pipeline log capture ───────────────────────────────────────────────────────
# Intercepts stdout from the pipeline and stores lines for the gadget console.
# Only lines starting with recognised prefixes are captured (no HTTP noise).

_PIPELINE_LOG_PREFIXES = ('[Pipeline]', '[Whisper]', '[Pyannote]', '[LLM]',
                          '[Docs]', '[Bot]', '[Server]')
_pipeline_logs: list[str] = []


class _LogCapture:
    """Tees stdout: still prints to the real console AND captures pipeline lines."""
    def __init__(self, real):
        self._real = real

    def write(self, text: str):
        self._real.write(text)
        stripped = text.strip()
        if stripped and any(stripped.startswith(p) for p in _PIPELINE_LOG_PREFIXES):
            _pipeline_logs.append(stripped)

    def flush(self):
        self._real.flush()


@app.get("/pipeline/logs")
async def get_pipeline_logs():
    """Returns captured pipeline log lines for the gadget console."""
    return {"logs": _pipeline_logs}


# ── Lark Webhook ───────────────────────────────────────────────────────────────

@app.get("/webhook/lark")
async def lark_webhook_ping():
    """Lark connectivity check — must return 200."""
    return {"status": "ok"}

@app.post("/webhook/lark")
async def lark_webhook(request: Request):
    """Receives all Lark platform events."""
    return await handle_webhook(request)


# ── Recording Control (called by Gadget) ──────────────────────────────────────

recording_state = {
    "status": "idle",       # idle | recording | processing | done | error
    "audio_path": None,     # absolute path to saved audio file
    "open_id": None,        # Lark user open_id to DM notes to
    "notes": None,          # generated notes dict (surfaced to gadget)
    "error": None,          # error message if pipeline failed
}

# Reference to the running pipeline task so it can be cancelled
_pipeline_task: asyncio.Task = None

@app.get("/audio/devices")
async def list_audio_devices():
    """Returns all available WASAPI loopback devices for the audio source picker."""
    devices = await asyncio.to_thread(recorder.list_loopback_devices)
    return {"devices": devices}


@app.get("/audio/levels")
async def get_audio_levels():
    """Returns live RMS levels for the gadget VU meter (polled ~8x/sec during recording)."""
    return recorder.get_levels()


@app.get("/audio/mic-volume")
async def get_mic_volume():
    """Returns the Windows system microphone volume (0.0–1.0)."""
    from pipeline.audio_capture import get_mic_system_volume
    level = await asyncio.to_thread(get_mic_system_volume)
    return {"level": level, "available": level >= 0}


class MicVolumeRequest(BaseModel):
    level: float

@app.post("/audio/mic-volume")
async def set_mic_volume(req: MicVolumeRequest):
    """Sets the Windows system microphone volume (0.0–1.0)."""
    from pipeline.audio_capture import set_mic_system_volume
    ok = await asyncio.to_thread(set_mic_system_volume, req.level)
    return {"status": "ok" if ok else "unavailable", "level": req.level}


@app.post("/audio/monitor")
async def toggle_monitor():
    """Toggles mic monitoring — lets the user hear themselves through speakers."""
    monitoring = await asyncio.to_thread(recorder.toggle_monitoring)
    return {"monitoring": monitoring}


@app.post("/recording/start")
async def start_recording(open_id: str = None, device_index: int = None):
    """Gadget calls this when user confirms consent and starts recording."""
    _pipeline_logs.clear()

    audio_path = await asyncio.to_thread(recorder.start, device_index)
    recording_state["status"] = "recording"
    recording_state["audio_path"] = str(Path(audio_path).resolve())
    recording_state["open_id"] = open_id
    recording_state["notes"] = None
    recording_state["error"] = None
    print(f"[Server] Recording started → {audio_path}  (open_id={open_id})")
    return {"status": "recording", "audio_path": recording_state["audio_path"]}


@app.post("/recording/stop")
async def stop_recording():
    """Gadget calls this when user stops the meeting."""
    recording_state["status"] = "processing"
    audio_path = await asyncio.to_thread(recorder.stop)
    recording_state["audio_path"] = str(Path(audio_path).resolve())
    print(f"[Server] Recording stopped → {audio_path}")

    # Wrap pipeline so we can update state when it finishes
    async def run_pipeline():
        _real_stdout = sys.stdout
        sys.stdout = _LogCapture(_real_stdout)
        try:
            notes = await pipeline_run(
                audio_path=audio_path,
                open_id=recording_state.get("open_id"),
            )
            recording_state["notes"] = notes
            recording_state["status"] = "done"
            print("[Server] Pipeline complete — status → done")
        except Exception as e:
            recording_state["error"] = str(e)
            recording_state["status"] = "error"
            print(f"[Server] Pipeline error: {e}")
        finally:
            sys.stdout = _real_stdout

    global _pipeline_task
    _pipeline_task = asyncio.create_task(run_pipeline())
    return {"status": "processing", "audio_path": recording_state["audio_path"]}


@app.post("/recording/cancel")
async def cancel_pipeline():
    """Cancels an in-progress pipeline and resets state to idle."""
    global _pipeline_task
    if _pipeline_task and not _pipeline_task.done():
        _pipeline_task.cancel()
        try:
            await _pipeline_task
        except asyncio.CancelledError:
            pass
    _pipeline_task = None
    recording_state["status"] = "idle"
    recording_state["audio_path"] = None
    recording_state["notes"] = None
    recording_state["error"] = None
    _pipeline_logs.clear()
    print("[Server] Pipeline cancelled — state reset to idle")
    return {"status": "idle"}


@app.post("/recording/mute")
async def toggle_mute():
    """Toggles microphone mute."""
    muted = await asyncio.to_thread(recorder.toggle_mute)
    return {"muted": muted, "deafened": recorder.mute_state["deafened"]}


@app.post("/recording/deafen")
async def toggle_deafen():
    """Toggles deafen (mic + system audio silenced)."""
    deafened = await asyncio.to_thread(recorder.toggle_deafen)
    return {"muted": recorder.mute_state["muted"], "deafened": deafened}


@app.get("/recording/status")
async def get_status():
    """Gadget polls this to know when notes are ready."""
    resp = {
        "status": recording_state["status"],
        "audio_path": recording_state["audio_path"],
    }
    if recording_state["status"] == "done" and recording_state["notes"]:
        notes = recording_state["notes"]
        resp["notes"] = {
            "summary":      notes.get("summary", ""),
            "action_items": notes.get("action_items", []),
            "decisions":    notes.get("decisions", []),
            "duration":     notes.get("duration", ""),
            "language":     notes.get("language", ""),
            "transcript":   notes.get("transcript", ""),
            "speakers":     notes.get("speakers", []),
        }
    if recording_state["status"] == "error":
        resp["error"] = recording_state.get("error", "Unknown error")
    return resp


@app.post("/recording/delete-audio")
async def delete_audio():
    """Deletes the saved audio file on user request from the gadget."""
    path = recording_state.get("audio_path")
    if not path:
        return JSONResponse(status_code=404, content={"error": "No audio file recorded"})
    p = Path(path)
    if not p.exists():
        return JSONResponse(status_code=404, content={"error": "File already deleted or not found"})
    try:
        p.unlink()
        recording_state["audio_path"] = None
        print(f"[Server] Audio deleted by user: {path}")
        return {"status": "deleted"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Insert notes into Lark Doc ────────────────────────────────────────────────

class DocInsertRequest(BaseModel):
    doc_url: str
    speaker_names: dict = {}

@app.post("/docs/insert")
async def insert_to_doc(req: DocInsertRequest):
    """Inserts the last generated meeting notes into a Lark Doc."""
    if recording_state["status"] != "done" or not recording_state["notes"]:
        return JSONResponse(
            status_code=400,
            content={"error": "No notes available — run a recording first"}
        )

    doc_token, is_wiki = extract_doc_token(req.doc_url)
    if not doc_token:
        return JSONResponse(status_code=400, content={"error": "Invalid doc URL or token"})

    try:
        if is_wiki:
            from lark_app.docs import resolve_wiki_to_doc_token
            doc_token = await resolve_wiki_to_doc_token(doc_token)

        result = await insert_meeting_notes(doc_token, recording_state["notes"], req.speaker_names)
        return {"status": "ok", "doc_token": doc_token, "result": result}
    except PermissionError as e:
        print(f"[Docs] Insert failed (permissions): {e}")
        return JSONResponse(status_code=403, content={"error": str(e)})
    except Exception as e:
        print(f"[Docs] Insert failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Settings ──────────────────────────────────────────────────────────────────

def _load_yaml():
    from pipeline._config import get_config_path
    import yaml
    with open(get_config_path()) as f:
        return yaml.safe_load(f)

def _save_yaml(cfg):
    from pipeline._config import get_config_path
    import yaml
    with open(get_config_path(), 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

@app.get("/settings")
async def get_settings():
    cfg = _load_yaml()
    llm      = cfg.get("models", {}).get("llm", {})
    whisper  = cfg.get("models", {}).get("whisper", {})
    pyannote = cfg.get("models", {}).get("pyannote", {})
    output   = cfg.get("output", {})
    from pipeline.summarise import DEFAULT_PROMPT_TEMPLATE
    return {
        "llm": {
            "provider":      llm.get("provider", "nim"),
            "model":         llm.get("model", ""),
            "api_key":       llm.get("api_key", ""),
            "custom_prompt": llm.get("custom_prompt", ""),
        },
        "whisper": {
            "size":     whisper.get("size", "medium"),
            "language": whisper.get("language", "auto"),
            "device":   whisper.get("device", "cuda"),
        },
        "pyannote": {
            "num_speakers": str(pyannote.get("num_speakers", "auto")),
        },
        "output": {
            "language": output.get("language", "en"),
        },
        "default_prompt": DEFAULT_PROMPT_TEMPLATE,
    }

class SettingsRequest(BaseModel):
    llm: dict = {}
    whisper: dict = {}
    pyannote: dict = {}
    output: dict = {}

@app.post("/settings")
async def save_settings(req: SettingsRequest):
    cfg = _load_yaml()
    cfg.setdefault("models", {})

    if req.output:
        cfg.setdefault("output", {}).update(req.output)

    if req.llm:
        cfg["models"].setdefault("llm", {}).update({
            k: v for k, v in req.llm.items() if v is not None
        })

    if req.whisper:
        cfg["models"].setdefault("whisper", {}).update(req.whisper)

    if req.pyannote:
        ns = req.pyannote.get("num_speakers", "auto")
        cfg["models"].setdefault("pyannote", {})["num_speakers"] = (
            ns if ns == "auto" else int(ns)
        )

    _save_yaml(cfg)

    from pipeline.summarise import reload_config
    reload_config()

    return {"status": "ok"}


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "LARK Meeting AI"}


# ── Cleanup on shutdown ────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown():
    recorder.cleanup()


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
