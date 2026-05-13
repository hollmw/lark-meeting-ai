"""
Meeting AI — System Tray App
─────────────────────────────
Runs the backend server silently in the background.
Click the tray icon to open the Meeting AI gadget window.
"""

import os
import sys
import time
import threading
import subprocess
import webbrowser
from pathlib import Path

import re
import tkinter as tk
import pystray
from PIL import Image, ImageDraw

PROJECT_DIR = Path(__file__).parent
GADGET_URL  = "http://localhost:8000/gadget"
PYTHON      = sys.executable

_server_process = None


# ── Tray icon (microphone on blue circle) ─────────────────────────────────────

def create_icon():
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    # Blue background circle
    d.ellipse([2, 2, size - 2, size - 2], fill=(79, 110, 247, 255))

    # White mic body (rounded rect)
    d.rounded_rectangle([22, 10, 42, 36], radius=10, fill=(255, 255, 255, 255))

    # White mic stand arc
    d.arc([14, 26, 50, 50], start=0, end=180,
          fill=(255, 255, 255, 255), width=3)

    # Stand stem + base
    d.line([32, 50, 32, 57], fill=(255, 255, 255, 255), width=3)
    d.line([24, 57, 40, 57], fill=(255, 255, 255, 255), width=3)

    return img


# ── Read Lark doc token from clipboard ───────────────────────────────────────

def get_doc_token_from_clipboard():
    """
    Checks the clipboard for a Lark doc URL and extracts the token.
    User just does Ctrl+L, Ctrl+C in the browser before clicking the tray icon.
    """
    try:
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        match = re.search(r'(?:docx|wiki)/([A-Za-z0-9]+)', text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


# ── Open gadget in a clean app window (no browser chrome) ────────────────────

def open_gadget():
    """
    Opens the gadget as a standalone app window using Chrome or Edge app mode.
    Falls back to the default browser if neither is installed.
    """
    # Check clipboard for a Lark doc URL
    token = get_doc_token_from_clipboard()
    url   = f"{GADGET_URL}?docToken={token}" if token else GADGET_URL

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in candidates:
        if os.path.exists(exe):
            subprocess.Popen([
                exe,
                f"--app={url}",
                "--window-size=400,720",
                "--window-position=80,80",
            ])
            return

    # Fallback — opens in default browser
    webbrowser.open(url)


# ── Server management ─────────────────────────────────────────────────────────

def start_server():
    global _server_process
    _server_process = subprocess.Popen(
        [PYTHON, str(PROJECT_DIR / "app.py")],
        cwd=str(PROJECT_DIR),
        # Hide the console window on Windows
        # creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    print("[Tray] Server started")


def stop_server():
    if _server_process:
        _server_process.terminate()
        print("[Tray] Server stopped")


# ── Tray menu actions ─────────────────────────────────────────────────────────

def on_open(icon, item):
    open_gadget()


def on_quit(icon, item):
    stop_server()
    icon.stop()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Start backend silently
    threading.Thread(target=start_server, daemon=True).start()
    time.sleep(2)  # wait for server to be ready

    # Auto-open gadget on first launch
    open_gadget()

    # System tray icon
    icon = pystray.Icon(
        name="MeetingAI",
        icon=create_icon(),
        title="Meeting AI",
        menu=pystray.Menu(
            pystray.MenuItem("Open Meeting AI", on_open, default=True),
            pystray.MenuItem("─────────────", pystray.Menu()),
            pystray.MenuItem("Quit", on_quit),
        ),
    )

    print("[Tray] Running — right-click the tray icon to open or quit")
    icon.run()


if __name__ == "__main__":
    main()
