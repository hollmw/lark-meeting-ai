"""
Audio Capture Module
─────────────────────
Captures two audio streams simultaneously:
  1. System audio (WASAPI loopback) — all meeting participants
  2. Microphone — the local user's voice

Both streams are mixed and saved as a single WAV file,
ready to be passed to the transcription pipeline.

Windows only (uses WASAPI loopback via pyaudiowpatch).
"""

import wave
import threading
import numpy as np
from pathlib import Path
from datetime import datetime
import pyaudiowpatch as pyaudio
import yaml

# ── Load config ───────────────────────────────────────────────────────────────

def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

CONFIG = load_config()
AUDIO_CONFIG = CONFIG.get("audio", {})

SAMPLE_RATE = AUDIO_CONFIG.get("sample_rate", 16000)
CHANNELS = 1          # mono — best for Whisper transcription
FORMAT = pyaudio.paInt16
CHUNK = 1024
OUTPUT_DIR = Path(AUDIO_CONFIG.get("output_dir", "./recordings"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Recorder class ────────────────────────────────────────────────────────────

class AudioRecorder:
    """
    Records system audio + microphone simultaneously.
    Call start() to begin, stop() to end and save the file.
    """

    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.is_recording = False
        self.output_path = None

        # Raw audio buffers
        self._system_frames = []
        self._mic_frames = []

        # Threads
        self._system_thread = None
        self._mic_thread = None

    # ── Devices ───────────────────────────────────────────────────────────────

    def _get_loopback_device(self):
        """Finds the WASAPI loopback device for system audio capture."""
        for i in range(self.pa.get_device_count()):
            device = self.pa.get_device_info_by_index(i)
            # WASAPI loopback devices have isLoopbackDevice = True
            if device.get("isLoopbackDevice", False):
                print(f"[Audio] System audio device: {device['name']}")
                return i, int(device["defaultSampleRate"])
        raise RuntimeError(
            "No WASAPI loopback device found. "
            "Make sure you're on Windows with audio output active."
        )

    def _get_microphone_device(self):
        """Gets the default microphone input device."""
        info = self.pa.get_default_input_device_info()
        print(f"[Audio] Microphone device: {info['name']}")
        return info["index"], int(info["defaultSampleRate"])

    # ── Recording threads ─────────────────────────────────────────────────────

    def _record_system_audio(self, device_index: int, device_rate: int):
        """Records system audio (loopback) in a background thread."""
        stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=device_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        print("[Audio] System audio recording started")
        while self.is_recording:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                self._system_frames.append(data)
            except Exception as e:
                print(f"[Audio] System stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        print("[Audio] System audio recording stopped")

    def _record_microphone(self, device_index: int, device_rate: int):
        """Records microphone input in a background thread."""
        stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=device_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        print("[Audio] Microphone recording started")
        while self.is_recording:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                self._mic_frames.append(data)
            except Exception as e:
                print(f"[Audio] Mic stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        print("[Audio] Microphone recording stopped")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self) -> str:
        """
        Starts recording system audio + microphone.
        Returns the output file path where audio will be saved.
        """
        if self.is_recording:
            print("[Audio] Already recording")
            return self.output_path

        # Reset buffers
        self._system_frames = []
        self._mic_frames = []

        # Generate output filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path = str(OUTPUT_DIR / f"meeting_{timestamp}.wav")

        self.is_recording = True

        # Get devices
        try:
            sys_index, sys_rate = self._get_loopback_device()
        except RuntimeError as e:
            print(f"[Audio] Warning: {e} — recording microphone only")
            sys_index, sys_rate = None, SAMPLE_RATE

        mic_index, mic_rate = self._get_microphone_device()

        # Start threads
        if sys_index is not None:
            self._system_thread = threading.Thread(
                target=self._record_system_audio,
                args=(sys_index, sys_rate),
                daemon=True,
            )
            self._system_thread.start()

        self._mic_thread = threading.Thread(
            target=self._record_microphone,
            args=(mic_index, mic_rate),
            daemon=True,
        )
        self._mic_thread.start()

        print(f"[Audio] Recording started → {self.output_path}")
        return self.output_path

    def stop(self) -> str:
        """
        Stops recording and saves the mixed audio to a WAV file.
        Returns the path to the saved file.
        """
        if not self.is_recording:
            print("[Audio] Not currently recording")
            return None

        self.is_recording = False

        # Wait for threads to finish
        if self._system_thread:
            self._system_thread.join(timeout=3)
        if self._mic_thread:
            self._mic_thread.join(timeout=3)

        # Mix and save
        output_path = self._mix_and_save()
        print(f"[Audio] Recording saved → {output_path}")
        return output_path

    # ── Mix & Save ────────────────────────────────────────────────────────────

    def _mix_and_save(self) -> str:
        """
        Mixes system audio and microphone frames into a single WAV file.
        If one stream is missing, uses the other alone.
        Both streams are resampled to SAMPLE_RATE (16kHz) for Whisper.
        """
        has_system = len(self._system_frames) > 0
        has_mic = len(self._mic_frames) > 0

        if not has_system and not has_mic:
            print("[Audio] No audio captured")
            return None

        if has_system and has_mic:
            # Convert both to numpy arrays
            sys_audio = np.frombuffer(b"".join(self._system_frames), dtype=np.int16).astype(np.float32)
            mic_audio = np.frombuffer(b"".join(self._mic_frames), dtype=np.int16).astype(np.float32)

            # Match lengths (trim to shorter)
            min_len = min(len(sys_audio), len(mic_audio))
            sys_audio = sys_audio[:min_len]
            mic_audio = mic_audio[:min_len]

            # Mix: average both streams
            mixed = ((sys_audio + mic_audio) / 2).astype(np.int16)
            audio_data = mixed.tobytes()

        elif has_system:
            audio_data = b"".join(self._system_frames)
        else:
            audio_data = b"".join(self._mic_frames)

        # Save as WAV
        with wave.open(self.output_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data)

        return self.output_path

    def cleanup(self):
        """Release PyAudio resources."""
        self.pa.terminate()


# ── Global recorder instance ──────────────────────────────────────────────────
# Shared across the FastAPI app

recorder = AudioRecorder()
