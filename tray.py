"""
Meeting AI — System Tray
─────────────────────────
Keeps the backend server running silently in the background.
Lark is the frontend — open the gadget there to record and view settings.
Right-click the tray icon → Quit to exit.
"""

import os
import sys
import threading
import time
from pathlib import Path

# ── Frozen-exe path setup ─────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    _BASE_DIR    = Path(sys.executable).parent
    _MEIPASS_DIR = Path(sys._MEIPASS)
    os.chdir(_BASE_DIR)
else:
    _BASE_DIR    = Path(__file__).parent
    _MEIPASS_DIR = Path(__file__).parent

os.environ['MEETING_AI_DIR']     = str(_BASE_DIR)
os.environ['MEETING_AI_MEIPASS'] = str(_MEIPASS_DIR)

import pystray
from PIL import Image, ImageDraw
import uvicorn

# ── Icon ──────────────────────────────────────────────────────────────────────

def _make_icon() -> Image.Image:
    size = 64
    img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=(79, 110, 247, 255))
    d.rounded_rectangle([22, 10, 42, 36], radius=10, fill=(255, 255, 255, 255))
    d.arc([14, 26, 50, 50], start=0, end=180, fill=(255, 255, 255, 255), width=3)
    d.line([32, 50, 32, 57], fill=(255, 255, 255, 255), width=3)
    d.line([24, 57, 40, 57], fill=(255, 255, 255, 255), width=3)
    return img


# ── Menu actions ──────────────────────────────────────────────────────────────

def on_quit(icon, *_):
    icon.stop()
    os._exit(0)


# ── Server ────────────────────────────────────────────────────────────────────

def _start_server():
    import traceback
    log_path = _BASE_DIR / 'meeting_ai.log'
    try:
        from app import app as fastapi_app
        uvicorn.run(fastapi_app, host='127.0.0.1', port=8000,
                    reload=False, log_level='warning')
    except Exception as e:
        with open(log_path, 'a') as f:
            f.write(f'\n--- SERVER CRASH ---\n')
            traceback.print_exc(file=f)
            f.write(str(e) + '\n')


# ── Main ──────────────────────────────────────────────────────────────────────

def _start_ngrok():
    """Starts ngrok tunnel on port 8000 silently in the background."""
    import subprocess
    try:
        subprocess.Popen(
            ['ngrok', 'http', '8000', '--domain=average-chain-bribe.ngrok-free.dev', '--log=false'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except FileNotFoundError:
        pass  # ngrok not installed — skip silently


def main():
    log_path = _BASE_DIR / 'meeting_ai.log'
    sys.stdout = open(log_path, 'a', buffering=1)
    sys.stderr = sys.stdout
    threading.Thread(target=_start_server, daemon=True).start()
    time.sleep(2)
    _start_ngrok()

    icon = pystray.Icon(
        name='MeetingAI',
        icon=_make_icon(),
        title='Meeting AI — running',
        menu=pystray.Menu(
            pystray.MenuItem('Quit Meeting AI', on_quit),
        ),
    )
    icon.run()


if __name__ == '__main__':
    main()
