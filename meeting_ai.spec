# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Meeting AI
# Build with:  pyinstaller meeting_ai.spec
#
# Output: dist/MeetingAI/MeetingAI.exe  (onedir — share the whole folder)
# Model weights (Whisper, pyannote) are NOT bundled — downloaded on first run.

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files
import sys

block_cipher = None

# ── Collect data + binaries for complex packages ──────────────────────────────
whisper_d,   whisper_b,   whisper_h   = collect_all('whisper')
pyannote_d,  pyannote_b,  pyannote_h  = collect_all('pyannote')
torch_d,     torch_b,     torch_h     = collect_all('torch')
torchaudio_d,torchaudio_b,torchaudio_h= collect_all('torchaudio')

# ── ffmpeg binary (required by Whisper) ───────────────────────────────────────
import shutil, os
_ffmpeg = shutil.which('ffmpeg') or ''
_ffmpeg_binaries = [(_ffmpeg, '.')] if _ffmpeg else []

a = Analysis(
    ['tray.py'],
    pathex=['.'],
    binaries=[
        *whisper_b,
        *pyannote_b,
        *torch_b,
        *torchaudio_b,
        *_ffmpeg_binaries,
    ],
    datas=[
        # Bundled UI — served by FastAPI as static files
        ('gadget', 'gadget'),
        # Package data
        *whisper_d,
        *pyannote_d,
        *torch_d,
        *torchaudio_d,
        # lark_app templates / assets (if any)
        ('lark_app', 'lark_app'),
        ('pipeline', 'pipeline'),
        # NOTE: config.yaml is NOT bundled — users provide their own next to the exe
    ],
    hiddenimports=[
        # ── uvicorn ──────────────────────────────────────────────────────────
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # ── fastapi / starlette ───────────────────────────────────────────────
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.staticfiles',
        'starlette.responses',
        # ── pydantic ─────────────────────────────────────────────────────────
        'pydantic',
        'pydantic.deprecated.class_validators',
        # ── audio ────────────────────────────────────────────────────────────
        'pyaudiowpatch',
        # ── ML ───────────────────────────────────────────────────────────────
        *whisper_h,
        *pyannote_h,
        *torch_h,
        *torchaudio_h,
        'speechbrain',
        'asteroid_filterbanks',
        # ── http / async ─────────────────────────────────────────────────────
        'httpx',
        'httpcore',
        'anyio',
        'anyio._backends._asyncio',
        'h11',
        # ── misc ─────────────────────────────────────────────────────────────
        'yaml',
        'numpy',
        'scipy',
        'sklearn',
        'sklearn.utils._cython_blas',
        'sklearn.neighbors.typedefs',
        'sklearn.neighbors._partition_nodes',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'pystray',
        'pystray._win32',
        'anthropic',
        'ollama',
        'openai',
        'pycaw',
        'comtypes',
        *collect_submodules('pyannote'),
        *collect_submodules('asteroid_filterbanks'),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Save space — not needed at runtime
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'pytest',
        'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeetingAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # no terminal window — tray app only
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # TODO: add an .ico file here if you want a custom exe icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MeetingAI',
)
