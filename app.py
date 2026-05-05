"""
LARK Meeting AI — Main Server
──────────────────────────────
FastAPI server that handles:
  - Lark webhook events
  - Recording start/stop from the Gadget
  - AI pipeline processing
  - Status polling
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from lark_app.webhook import handle_webhook

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

recording_state = {"status": "idle"}  # idle | recording | processing | done

@app.post("/recording/start")
async def start_recording():
    """Gadget calls this when user confirms consent and starts recording."""
    recording_state["status"] = "recording"
    # Phase 3 — audio capture will be wired up here
    print("[Server] Recording started")
    return {"status": "recording"}


@app.post("/recording/stop")
async def stop_recording():
    """Gadget calls this when user stops the meeting."""
    recording_state["status"] = "processing"
    print("[Server] Recording stopped — starting AI pipeline")
    # Phase 4 — AI pipeline will be triggered here
    # await pipeline.run()
    return {"status": "processing"}


@app.get("/recording/status")
async def get_status():
    """Gadget polls this to know when notes are ready."""
    return {"status": recording_state["status"]}


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "LARK Meeting AI"}


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
