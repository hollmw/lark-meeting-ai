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
from pipeline import run as pipeline

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

@app.post("/webhook/lark")
async def lark_webhook(request: Request):
    """Receives all Lark platform events."""
    return await handle_webhook(request)


# ── Recording Control (called by Gadget) ──────────────────────────────────────

recording_state = {
    "status": "idle",       # idle | recording | processing | done
    "audio_path": None,     # path to saved audio file after stop
}

@app.post("/recording/start")
async def start_recording():
    """Gadget calls this when user confirms consent and starts recording."""
    audio_path = await asyncio.to_thread(recorder.start)
    recording_state["status"] = "recording"
    recording_state["audio_path"] = audio_path
    print(f"[Server] Recording started → {audio_path}")
    return {"status": "recording", "audio_path": audio_path}


@app.post("/recording/stop")
async def stop_recording():
    """Gadget calls this when user stops the meeting."""
    recording_state["status"] = "processing"
    audio_path = await asyncio.to_thread(recorder.stop)
    recording_state["audio_path"] = audio_path
    print(f"[Server] Recording stopped → {audio_path}")

    # Trigger AI pipeline in background — doesn't block the response
    asyncio.create_task(pipeline.run(audio_path=audio_path))

    return {"status": "processing", "audio_path": audio_path}


@app.get("/recording/status")
async def get_status():
    """Gadget polls this to know when notes are ready."""
    return {
        "status": recording_state["status"],
        "audio_path": recording_state["audio_path"],
    }


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
