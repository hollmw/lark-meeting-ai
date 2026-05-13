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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Serve Gadget UI ────────────────────────────────────────────────────────────
# When frozen by PyInstaller, static assets live in MEIPASS; in dev, use relative path.
_MEIPASS = os.environ.get('MEETING_AI_MEIPASS', str(Path(__file__).parent))
app.mount("/gadget", StaticFiles(directory=str(Path(_MEIPASS) / "gadget"), html=True), name="gadget")


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
    "audio_path": None,     # path to saved audio file after stop
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


@app.post("/recording/start")
async def start_recording(open_id: str = None, device_index: int = None):
    """Gadget calls this when user confirms consent and starts recording.

    Args:
        open_id:      Optional Lark user open_id — if provided, notes will be
                      DMed to that user when processing finishes.
        device_index: Optional loopback device index to record from.
                      Defaults to the first available loopback device.
    """
    audio_path = await asyncio.to_thread(recorder.start, device_index)
    recording_state["status"] = "recording"
    recording_state["audio_path"] = audio_path
    recording_state["open_id"] = open_id
    recording_state["notes"] = None
    recording_state["error"] = None
    print(f"[Server] Recording started → {audio_path}  (open_id={open_id})")
    return {"status": "recording", "audio_path": audio_path}


@app.post("/recording/stop")
async def stop_recording():
    """Gadget calls this when user stops the meeting."""
    recording_state["status"] = "processing"
    audio_path = await asyncio.to_thread(recorder.stop)
    recording_state["audio_path"] = audio_path
    print(f"[Server] Recording stopped → {audio_path}")

    # Wrap pipeline so we can update state when it finishes
    async def run_pipeline():
        try:
            notes = await pipeline_run(
                audio_path=audio_path,
                open_id=recording_state.get("open_id"),  # DM the host
            )
            recording_state["notes"] = notes
            recording_state["status"] = "done"
            print("[Server] Pipeline complete — status → done")
        except Exception as e:
            recording_state["error"] = str(e)
            recording_state["status"] = "error"
            print(f"[Server] Pipeline error: {e}")

    global _pipeline_task
    _pipeline_task = asyncio.create_task(run_pipeline())
    return {"status": "processing", "audio_path": audio_path}


@app.post("/recording/cancel")
async def cancel_pipeline():
    """
    Cancels an in-progress pipeline and resets state to idle.
    Safe to call even if no pipeline is running.
    """
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
    print("[Server] Pipeline cancelled — state reset to idle")
    return {"status": "idle"}


@app.post("/recording/mute")
async def toggle_mute():
    """Toggles microphone mute. Safe to call whether or not recording is active."""
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
    # Include notes when done so gadget can show preview
    if recording_state["status"] == "done" and recording_state["notes"]:
        notes = recording_state["notes"]
        resp["notes"] = {
            "summary": notes.get("summary", ""),
            "action_items": notes.get("action_items", []),
            "decisions": notes.get("decisions", []),
            "duration": notes.get("duration", ""),
            "language": notes.get("language", ""),
            "transcript": notes.get("transcript", ""),  # full raw transcript
            "speakers": notes.get("speakers", []),       # for speaker-rename UI
        }
    if recording_state["status"] == "error":
        resp["error"] = recording_state.get("error", "Unknown error")
    return resp


# ── Insert notes into Lark Doc ────────────────────────────────────────────────

class DocInsertRequest(BaseModel):
    doc_url: str
    speaker_names: dict = {}   # e.g. {"SPEAKER_00": "Max", "SPEAKER_01": "Joyce"}

@app.post("/docs/insert")
async def insert_to_doc(req: DocInsertRequest):
    """
    Inserts the last generated meeting notes into a Lark Doc.
    Accepts a Lark Doc URL (https://xxx.larksuite.com/docx/TOKEN) or raw token.
    """
    if recording_state["status"] != "done" or not recording_state["notes"]:
        return JSONResponse(
            status_code=400,
            content={"error": "No notes available — run a recording first"}
        )

    doc_token, is_wiki = extract_doc_token(req.doc_url)
    if not doc_token:
        return JSONResponse(status_code=400, content={"error": "Invalid doc URL or token"})

    try:
        # Wiki pages need their token resolved to the underlying doc token first
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
