"""
Centralised config path resolution.
Works both in development (config.yaml next to project root)
and when running as a PyInstaller frozen exe (config.yaml next to the .exe).
"""
import os
from pathlib import Path
import yaml


def get_config_path() -> Path:
    # Frozen exe: tray.py sets MEETING_AI_DIR to the folder containing the .exe
    if 'MEETING_AI_DIR' in os.environ:
        return Path(os.environ['MEETING_AI_DIR']) / 'config.yaml'
    # Dev: config.yaml is two levels up from this file (project root)
    return Path(__file__).parent.parent / 'config.yaml'


def load_config() -> dict:
    with open(get_config_path()) as f:
        return yaml.safe_load(f)
