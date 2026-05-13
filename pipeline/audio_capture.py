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
from pipeline._config import load_config

# ── Load config ───────────────────────────────────────────────────────────────

CONFIG = load_config()
AUDIO_CONFIG = CONFIG.get("audio", {})

SAMPLE_RATE = AUDIO_CONFIG.get("sample_rate", 16000)
CHANNELS = 1          # mono — best for Whisper transcription
FORMAT = pyaudio.paInt16
CHUNK = 1024
OUTPUT_DIR = Path(AUDIO_CONFIG.get("output_dir", "./recordings"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_mono(raw_bytes: bytes, channels: int) -> np.ndarray:
    """
    Converts raw int16 PCM bytes to a mono float32 numpy array.
    If channels > 1 (stereo / multi-channel), averages all channels.
    Returns float32 array ready for resampling / mixing / saving.
    """
    audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def _resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """
    Resamples audio from orig_rate to target_rate using linear interpolation.
    Fast enough for speech; no external dependencies beyond numpy.
    """
    if orig_rate == target_rate:
        return audio
    target_len = int(len(audio) * target_rate / orig_rate)
    return np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    )


# ── Recorder class ────────────────────────────────────────────────────────────

class AudioRecorder:
    """
    Records system audio + microphone simultaneously.
    Call start() to begin, stop() to end and save the file.
    """

    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.output_path = None

        # Stop signal — SET means "not recording", CLEAR means "recording active"
        # Starts set (not recording) so is_recording property returns False initially
        self._stop_event = threading.Event()
        self._stop_event.set()

        # Raw audio buffers
        self._system_frames = []
        self._mic_frames = []

        # Device info (needed for downmix + resample in _mix_and_save)
        self._system_channels = 1
        self._system_rate = SAMPLE_RATE
        self._mic_channels = 1
        self._mic_rate = SAMPLE_RATE

        # Mute / deafen flags (thread-safe: Python bool assignment is atomic)
        self._muted    = False   # mic silenced
        self._deafened = False   # mic + system audio silenced

        # Threads
        self._system_thread = None
        self._mic_thread = None
        self._silence_thread = None

    @property
    def is_recording(self):
        return not self._stop_event.is_set()

    # ── Mute / Deafen ─────────────────────────────────────────────────────────

    def toggle_mute(self) -> bool:
        """Toggles microphone mute. Returns new muted state."""
        self._muted = not self._muted
        if self._muted:
            self._deafened = False   # mute supersedes deafen — clear deafen
        print(f"[Audio] Mute → {'ON' if self._muted else 'OFF'}")
        return self._muted

    def toggle_deafen(self) -> bool:
        """
        Toggles deafen (mic + system audio both silenced).
        Deafening also mutes; un-deafening restores prior mute state.
        Returns new deafened state.
        """
        self._deafened = not self._deafened
        if self._deafened:
            self._muted = False   # deafen owns both channels; clear independent mute
        print(f"[Audio] Deafen → {'ON' if self._deafened else 'OFF'}")
        return self._deafened

    @property
    def mute_state(self) -> dict:
        return {"muted": self._muted, "deafened": self._deafened}

    # ── Devices ───────────────────────────────────────────────────────────────

    def list_loopback_devices(self) -> list:
        """Returns all available WASAPI loopback devices."""
        devices = []
        for i in range(self.pa.get_device_count()):
            device = self.pa.get_device_info_by_index(i)
            if device.get("isLoopbackDevice", False):
                devices.append({
                    "index": i,
                    "name": device["name"],
                    "channels": max(1, int(device.get("maxInputChannels", 2))),
                    "rate": int(device["defaultSampleRate"]),
                })
        return devices

    def _get_loopback_device(self, preferred_index: int = None):
        """
        Finds the WASAPI loopback device for system audio capture.
        Returns (device_index, sample_rate, channel_count).
        If preferred_index is set, uses that device directly.
        """
        for i in range(self.pa.get_device_count()):
            device = self.pa.get_device_info_by_index(i)
            if device.get("isLoopbackDevice", False):
                if preferred_index is not None and i != preferred_index:
                    continue
                channels = max(1, int(device.get("maxInputChannels", 2)))
                rate = int(device["defaultSampleRate"])
                print(f"[Audio] System audio device: {device['name']} "
                      f"({channels}ch @ {rate}Hz)")
                return i, rate, channels
        raise RuntimeError(
            "No WASAPI loopback device found. "
            "Make sure you're on Windows with audio output active."
        )

    def _get_microphone_device(self):
        """Gets the default microphone input device."""
        info = self.pa.get_default_input_device_info()
        channels = max(1, int(info.get("maxInputChannels", 1)))
        rate = int(info["defaultSampleRate"])
        print(f"[Audio] Microphone device: {info['name']} ({channels}ch @ {rate}Hz)")
        return info["index"], rate, channels

    def _play_silence(self, device_rate: int, device_channels: int):
        """
        Plays silent audio on the output device to keep WASAPI from going idle.
        Without this, loopback capture returns silence when no audio is playing.
        """
        silent_chunk = b'\x00' * CHUNK * device_channels * 2  # int16 = 2 bytes
        stream = self.pa.open(
            format=FORMAT,
            channels=device_channels,
            rate=device_rate,
            output=True,
            frames_per_buffer=CHUNK,
        )
        while not self._stop_event.is_set():
            stream.write(silent_chunk)
        stream.stop_stream()
        stream.close()

    # ── Recording threads ─────────────────────────────────────────────────────

    def _record_system_audio(self, device_index: int, device_rate: int, device_channels: int):
        """Records system audio (loopback) in a background thread.
        Must use the device's native channel count — WASAPI rejects anything else.
        Uses _stop_event so the thread exits cleanly without blocking on stream.read().
        """
        stream = self.pa.open(
            format=FORMAT,
            channels=device_channels,   # native (usually stereo = 2)
            rate=device_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        silent_chunk = b'\x00' * CHUNK * device_channels * 2  # int16 = 2 bytes per sample
        print("[Audio] System audio recording started")
        while not self._stop_event.is_set():
            try:
                # Blocking read — returns in ~21ms (1024 samples @ 48kHz)
                # Stop event is checked after every chunk, so latency is ~21ms max
                data = stream.read(CHUNK, exception_on_overflow=False)
                # Deafen silences both channels — write zeros to keep timing aligned
                self._system_frames.append(silent_chunk if self._deafened else data)
            except Exception as e:
                print(f"[Audio] System stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        print("[Audio] System audio recording stopped")

    def _record_microphone(self, device_index: int, device_rate: int, device_channels: int):
        """Records microphone input in a background thread."""
        stream = self.pa.open(
            format=FORMAT,
            channels=device_channels,
            rate=device_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        silent_chunk = b'\x00' * CHUNK * device_channels * 2  # int16 = 2 bytes per sample
        print("[Audio] Microphone recording started")
        while not self._stop_event.is_set():
            try:
                # Blocking read — returns in ~23ms (1024 samples @ 44100Hz)
                data = stream.read(CHUNK, exception_on_overflow=False)
                # Mute silences mic only; deafen silences mic + system
                silenced = self._muted or self._deafened
                self._mic_frames.append(silent_chunk if silenced else data)
            except Exception as e:
                print(f"[Audio] Mic stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        print("[Audio] Microphone recording stopped")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self, device_index: int = None) -> str:
        """
        Starts recording system audio + microphone.
        Args:
            device_index: Optional loopback device index to use.
                          If None, picks the first available loopback device.
        Returns the output file path where audio will be saved.
        """
        if not self._stop_event.is_set():
            print("[Audio] Already recording")
            return self.output_path

        # Reset state
        self._stop_event.clear()
        self._system_frames = []
        self._mic_frames = []
        self._muted    = False
        self._deafened = False

        # Generate output filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path = str(OUTPUT_DIR / f"meeting_{timestamp}.wav")

        # _stop_event already cleared above — recording is now active

        # Get devices
        try:
            sys_index, sys_rate, sys_channels = self._get_loopback_device(preferred_index=device_index)
            self._system_channels = sys_channels
            self._system_rate = sys_rate
        except RuntimeError as e:
            print(f"[Audio] Warning: {e} — recording microphone only")
            sys_index, sys_rate, sys_channels = None, SAMPLE_RATE, 1
            self._system_channels = 1
            self._system_rate = SAMPLE_RATE

        mic_index, mic_rate, mic_channels = self._get_microphone_device()
        self._mic_channels = mic_channels
        self._mic_rate = mic_rate

        # Start threads
        if sys_index is not None:
            # Keep audio device awake so loopback captures even during silence
            self._silence_thread = threading.Thread(
                target=self._play_silence,
                args=(sys_rate, sys_channels),
                daemon=True,
            )
            self._silence_thread.start()

            self._system_thread = threading.Thread(
                target=self._record_system_audio,
                args=(sys_index, sys_rate, sys_channels),
                daemon=True,
            )
            self._system_thread.start()

        self._mic_thread = threading.Thread(
            target=self._record_microphone,
            args=(mic_index, mic_rate, mic_channels),
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
        if self._stop_event.is_set():
            print("[Audio] Not currently recording")
            return None

        # Signal threads to stop — they check this event each loop iteration
        self._stop_event.set()

        # Wait for threads to exit cleanly (up to 10s each)
        if self._silence_thread:
            self._silence_thread.join(timeout=10)
        if self._system_thread:
            self._system_thread.join(timeout=10)
        if self._mic_thread:
            self._mic_thread.join(timeout=10)

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
            # Downmix to mono then resample both to 16kHz
            sys_audio = _resample(
                _to_mono(b"".join(self._system_frames), self._system_channels),
                self._system_rate, SAMPLE_RATE,
            )
            mic_audio = _resample(
                _to_mono(b"".join(self._mic_frames), self._mic_channels),
                self._mic_rate, SAMPLE_RATE,
            )

            # Match lengths (pad shorter stream with silence)
            max_len = max(len(sys_audio), len(mic_audio))
            if len(sys_audio) < max_len:
                sys_audio = np.pad(sys_audio, (0, max_len - len(sys_audio)))
            if len(mic_audio) < max_len:
                mic_audio = np.pad(mic_audio, (0, max_len - len(mic_audio)))

            # Mix: average both streams (boost mic slightly so voice isn't drowned out)
            mixed = (sys_audio * 1.5 + mic_audio * 0.8).clip(-32768, 32767).astype(np.int16)
            audio_data = mixed.tobytes()

        elif has_system:
            audio = _resample(
                _to_mono(b"".join(self._system_frames), self._system_channels),
                self._system_rate, SAMPLE_RATE,
            )
            audio_data = audio.clip(-32768, 32767).astype(np.int16).tobytes()
        else:
            audio = _resample(
                _to_mono(b"".join(self._mic_frames), self._mic_channels),
                self._mic_rate, SAMPLE_RATE,
            )
            audio_data = audio.clip(-32768, 32767).astype(np.int16).tobytes()

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
