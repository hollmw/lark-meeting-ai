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
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 1024
OUTPUT_DIR = Path(AUDIO_CONFIG.get("output_dir", "./recordings"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# dB floor for VU meter — maps [-60dB, 0dB] → [0.0, 1.0]
_DB_FLOOR = -60.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_mono(raw_bytes: bytes, channels: int) -> np.ndarray:
    audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def _resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate:
        return audio
    target_len = int(len(audio) * target_rate / orig_rate)
    return np.interp(
        np.linspace(0, len(audio) - 1, target_len),
        np.arange(len(audio)),
        audio,
    )


def _rms_level(raw_bytes: bytes, channels: int) -> float:
    """
    Returns a 0.0–1.0 display level using a logarithmic (dB) scale.
    Normal conversational speech typically reads 0.45–0.75.
    """
    arr = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)
    if len(arr) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(arr ** 2))) / 32768.0
    if rms < 1e-10:
        return 0.0
    db = 20.0 * np.log10(rms)
    level = (db - _DB_FLOOR) / (-_DB_FLOOR)
    return float(np.clip(level, 0.0, 1.0))


# ── Windows mic volume (pycaw) ────────────────────────────────────────────────

def _get_pycaw_mic_volume():
    """Returns the pycaw IAudioEndpointVolume for the default mic, or None."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return None
        iface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return iface.QueryInterface(IAudioEndpointVolume)
    except Exception:
        return None


def get_mic_system_volume() -> float:
    """Returns Windows mic volume as 0.0–1.0, or -1 if pycaw unavailable."""
    vol = _get_pycaw_mic_volume()
    if vol is None:
        return -1.0
    try:
        return float(vol.GetMasterVolumeLevelScalar())
    except Exception:
        return -1.0


def set_mic_system_volume(level: float) -> bool:
    """Sets Windows mic volume (0.0–1.0). Returns True on success."""
    vol = _get_pycaw_mic_volume()
    if vol is None:
        return False
    try:
        vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)
        return True
    except Exception:
        return False


# ── Recorder class ────────────────────────────────────────────────────────────

class AudioRecorder:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.output_path = None

        self._stop_event = threading.Event()
        self._stop_event.set()

        self._system_frames = []
        self._mic_frames    = []

        self._system_channels = 1
        self._system_rate     = SAMPLE_RATE
        self._mic_channels    = 1
        self._mic_rate        = SAMPLE_RATE

        self._muted    = False
        self._deafened = False

        # Live level meters (0.0–1.0, updated by recording/monitoring threads)
        self._mic_level    = 0.0
        self._system_level = 0.0

        # Mic monitoring (hear yourself)
        self._monitor_stop   = threading.Event()
        self._monitor_stop.set()
        self._monitor_thread = None
        self._is_monitoring  = False

        # Threads
        self._system_thread  = None
        self._mic_thread     = None
        self._silence_thread = None

    @property
    def is_recording(self):
        return not self._stop_event.is_set()

    # ── Mute / Deafen ─────────────────────────────────────────────────────────

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        if self._muted:
            self._deafened = False
        print(f"[Audio] Mute → {'ON' if self._muted else 'OFF'}")
        return self._muted

    def toggle_deafen(self) -> bool:
        self._deafened = not self._deafened
        if self._deafened:
            self._muted = False
        print(f"[Audio] Deafen → {'ON' if self._deafened else 'OFF'}")
        return self._deafened

    @property
    def mute_state(self) -> dict:
        return {"muted": self._muted, "deafened": self._deafened}

    # ── Levels ────────────────────────────────────────────────────────────────

    def get_levels(self) -> dict:
        return {
            "mic":    round(self._mic_level,    3),
            "system": round(self._system_level, 3),
        }

    # ── Mic monitoring ────────────────────────────────────────────────────────

    def toggle_monitoring(self) -> bool:
        """Toggles mic monitoring (hear yourself through speakers). Returns new state."""
        if self._is_monitoring:
            self._stop_monitoring()
        else:
            self._start_monitoring()
        return self._is_monitoring

    def _start_monitoring(self):
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_mic, daemon=True
        )
        self._monitor_thread.start()
        self._is_monitoring = True
        print("[Audio] Mic monitoring started")

    def _stop_monitoring(self):
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
        self._monitor_thread = None
        self._is_monitoring = False
        self._mic_level = 0.0
        print("[Audio] Mic monitoring stopped")

    def _monitor_mic(self):
        """
        Streams microphone to the default output in real-time so the user
        can hear their own voice and check levels. Also drives the mic VU
        meter even when not recording.
        """
        try:
            mic_info = self.pa.get_default_input_device_info()
            mic_idx  = mic_info["index"]
            mic_rate = int(mic_info["defaultSampleRate"])
            mic_ch   = max(1, int(mic_info.get("maxInputChannels", 1)))

            out_info = self.pa.get_default_output_device_info()
            out_idx  = out_info["index"]
            out_rate = int(out_info["defaultSampleRate"])
            out_ch   = max(1, int(out_info.get("maxOutputChannels", 2)))

            in_stream = self.pa.open(
                format=FORMAT, channels=mic_ch, rate=mic_rate,
                input=True, input_device_index=mic_idx,
                frames_per_buffer=CHUNK,
            )
            out_stream = self.pa.open(
                format=FORMAT, channels=out_ch, rate=out_rate,
                output=True, output_device_index=out_idx,
                frames_per_buffer=CHUNK,
            )

            while not self._monitor_stop.is_set():
                data = in_stream.read(CHUNK, exception_on_overflow=False)
                # Update mic level meter even when not recording
                self._mic_level = _rms_level(data, mic_ch)
                # Adapt channels if output is stereo but mic is mono
                out_data = data
                if out_ch > mic_ch:
                    arr = np.frombuffer(data, dtype=np.int16)
                    out_data = np.column_stack([arr] * out_ch).flatten().tobytes()
                out_stream.write(out_data)

            in_stream.stop_stream()
            in_stream.close()
            out_stream.stop_stream()
            out_stream.close()
        except Exception as e:
            print(f"[Audio] Monitor error: {e}")
        finally:
            self._mic_level = 0.0
            self._is_monitoring = False

    # ── Devices ───────────────────────────────────────────────────────────────

    def list_loopback_devices(self) -> list:
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
        for i in range(self.pa.get_device_count()):
            device = self.pa.get_device_info_by_index(i)
            if device.get("isLoopbackDevice", False):
                if preferred_index is not None and i != preferred_index:
                    continue
                channels = max(1, int(device.get("maxInputChannels", 2)))
                rate = int(device["defaultSampleRate"])
                print(f"[Audio] System audio device: {device['name']} ({channels}ch @ {rate}Hz)")
                return i, rate, channels
        raise RuntimeError("No WASAPI loopback device found.")

    def _get_microphone_device(self):
        info = self.pa.get_default_input_device_info()
        channels = max(1, int(info.get("maxInputChannels", 1)))
        rate = int(info["defaultSampleRate"])
        print(f"[Audio] Microphone device: {info['name']} ({channels}ch @ {rate}Hz)")
        return info["index"], rate, channels

    def _play_silence(self, device_rate: int, device_channels: int):
        silent_chunk = b'\x00' * CHUNK * device_channels * 2
        stream = self.pa.open(
            format=FORMAT, channels=device_channels,
            rate=device_rate, output=True, frames_per_buffer=CHUNK,
        )
        while not self._stop_event.is_set():
            stream.write(silent_chunk)
        stream.stop_stream()
        stream.close()

    # ── Recording threads ─────────────────────────────────────────────────────

    def _record_system_audio(self, device_index, device_rate, device_channels):
        stream = self.pa.open(
            format=FORMAT, channels=device_channels, rate=device_rate,
            input=True, input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        silent_chunk = b'\x00' * CHUNK * device_channels * 2
        print("[Audio] System audio recording started")
        while not self._stop_event.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                if self._deafened:
                    self._system_level = 0.0
                    self._system_frames.append(silent_chunk)
                else:
                    self._system_level = _rms_level(data, device_channels)
                    self._system_frames.append(data)
            except Exception as e:
                print(f"[Audio] System stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        self._system_level = 0.0
        print("[Audio] System audio recording stopped")

    def _record_microphone(self, device_index, device_rate, device_channels):
        stream = self.pa.open(
            format=FORMAT, channels=device_channels, rate=device_rate,
            input=True, input_device_index=device_index,
            frames_per_buffer=CHUNK,
        )
        silent_chunk = b'\x00' * CHUNK * device_channels * 2
        print("[Audio] Microphone recording started")
        while not self._stop_event.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                silenced = self._muted or self._deafened
                if silenced:
                    self._mic_level = 0.0
                    self._mic_frames.append(silent_chunk)
                else:
                    self._mic_level = _rms_level(data, device_channels)
                    self._mic_frames.append(data)
            except Exception as e:
                print(f"[Audio] Mic stream error: {e}")
                break
        stream.stop_stream()
        stream.close()
        self._mic_level = 0.0
        print("[Audio] Microphone recording stopped")

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def start(self, device_index: int = None) -> str:
        if not self._stop_event.is_set():
            print("[Audio] Already recording")
            return self.output_path

        # Stop monitoring if active — recording thread takes over the mic meter
        if self._is_monitoring:
            self._stop_monitoring()

        self._stop_event.clear()
        self._system_frames = []
        self._mic_frames    = []
        self._muted    = False
        self._deafened = False
        self._mic_level    = 0.0
        self._system_level = 0.0

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path = str(OUTPUT_DIR / f"meeting_{timestamp}.wav")

        try:
            sys_index, sys_rate, sys_channels = self._get_loopback_device(preferred_index=device_index)
            self._system_channels = sys_channels
            self._system_rate     = sys_rate
        except RuntimeError as e:
            print(f"[Audio] Warning: {e} — recording microphone only")
            sys_index, sys_rate, sys_channels = None, SAMPLE_RATE, 1
            self._system_channels = 1
            self._system_rate     = SAMPLE_RATE

        mic_index, mic_rate, mic_channels = self._get_microphone_device()
        self._mic_channels = mic_channels
        self._mic_rate     = mic_rate

        if sys_index is not None:
            self._silence_thread = threading.Thread(
                target=self._play_silence, args=(sys_rate, sys_channels), daemon=True,
            )
            self._silence_thread.start()
            self._system_thread = threading.Thread(
                target=self._record_system_audio,
                args=(sys_index, sys_rate, sys_channels), daemon=True,
            )
            self._system_thread.start()

        self._mic_thread = threading.Thread(
            target=self._record_microphone,
            args=(mic_index, mic_rate, mic_channels), daemon=True,
        )
        self._mic_thread.start()

        print(f"[Audio] Recording started → {self.output_path}")
        return self.output_path

    def stop(self) -> str:
        if self._stop_event.is_set():
            print("[Audio] Not currently recording")
            return None

        self._stop_event.set()

        if self._silence_thread: self._silence_thread.join(timeout=10)
        if self._system_thread:  self._system_thread.join(timeout=10)
        if self._mic_thread:     self._mic_thread.join(timeout=10)

        output_path = self._mix_and_save()
        print(f"[Audio] Recording saved → {output_path}")
        return output_path

    # ── Mix & Save ────────────────────────────────────────────────────────────

    def _mix_and_save(self) -> str:
        has_system = len(self._system_frames) > 0
        has_mic    = len(self._mic_frames) > 0

        if not has_system and not has_mic:
            print("[Audio] No audio captured")
            return None

        if has_system and has_mic:
            sys_audio = _resample(
                _to_mono(b"".join(self._system_frames), self._system_channels),
                self._system_rate, SAMPLE_RATE,
            )
            mic_audio = _resample(
                _to_mono(b"".join(self._mic_frames), self._mic_channels),
                self._mic_rate, SAMPLE_RATE,
            )
            max_len = max(len(sys_audio), len(mic_audio))
            if len(sys_audio) < max_len:
                sys_audio = np.pad(sys_audio, (0, max_len - len(sys_audio)))
            if len(mic_audio) < max_len:
                mic_audio = np.pad(mic_audio, (0, max_len - len(mic_audio)))
            # Equal mix, 0.8x each to leave headroom and avoid clipping
            mixed = (sys_audio * 0.8 + mic_audio * 0.8).clip(-32768, 32767).astype(np.int16)
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

        with wave.open(self.output_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data)

        return self.output_path

    def cleanup(self):
        if self._is_monitoring:
            self._stop_monitoring()
        self.pa.terminate()


# ── Global recorder instance ──────────────────────────────────────────────────

recorder = AudioRecorder()
