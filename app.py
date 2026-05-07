"""
LARK Meeting AI — Main Server
──────────────────────────────
FastAPI server that handles:
  - Lark webhook events
  - Recording start/stop from the Gadget
  - AI pipeline processing
  - Status polling
"""

import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from lark_app.webhook import handle_webhook
from pipeline.audio_capture import recorder
from pipeline.run import run as pipeline_run

app = FastAPI(title="LARK Meeting AI", version="1.0.0")

# ── CORS (allows the Gadget to call the local backend) ────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve Gadget UI ────────────────────────────────────────────────────────────
app.mount("/gadget", StaticFiles(directory="gadget", html=True), name="gadget")


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

@app.post("/recording/start")
async def start_recording(open_id: str = None):
    """Gadget calls this when user confirms consent and starts recording.

    Args:
        open_id: Optional Lark user open_id — if provided, notes will be
                 DMed to that user when processing finishes.
    """
    audio_path = await asyncio.to_thread(recorder.start)
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

    asyncio.create_task(run_pipeline())
    return {"status": "processing", "audio_path": audio_path}


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
        }
    if recording_state["status"] == "error":
        resp["error"] = recording_state.get("error", "Unknown error")
    return resp


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
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
