"""PyAudio helpers for the Pi 5 JARVIS unit.

Handles:
  - listing ALSA/PipeWire devices (find the ReSpeaker mic / USB speaker)
  - recording 16 kHz mono PCM with end-of-speech silence detection
  - playing int16 mono PCM to the speaker
"""
from __future__ import annotations

import numpy as np
import pyaudio

RATE = 16000
FORMAT = pyaudio.paInt16
CHANNELS = 1


def list_devices() -> None:
    """Print every audio device so the ReSpeaker mic / speaker can be found."""
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            print(
                f"[{i}] {info['name']}  "
                f"in={info['maxInputChannels']} out={info['maxOutputChannels']} "
                f"rate={int(info['defaultSampleRate'])}"
            )
    finally:
        pa.terminate()


class Mic:
    """Blocking microphone wrapper around a PyAudio input stream."""

    def __init__(self, rate: int = RATE, chunk: int = 2048, device_index: int | None = None):
        self.rate = rate
        self.chunk = chunk
        self.chunk_seconds = chunk / rate
        self.pa = pyaudio.PyAudio()
        try:
            self.stream = self.pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=rate,
                input=True,
                frames_per_buffer=chunk,
                input_device_index=device_index,
            )
        except Exception:
            self.pa.terminate()
            raise

    def read(self, seconds: float) -> bytes:
        """Read `seconds` of audio and return int16 PCM bytes."""
        n = max(1, int(round(seconds / self.chunk_seconds)))
        return b"".join(
            self.stream.read(self.chunk, exception_on_overflow=False) for _ in range(n)
        )

    def close(self) -> None:
        try:
            self.stream.stop_stream()
            self.stream.close()
        finally:
            self.pa.terminate()


def rms_level(pcm_bytes: bytes) -> float:
    """Root-mean-square level of int16 PCM, used for silence detection."""
    data = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0


def record_until_silence(
    mic: Mic,
    max_seconds: float = 12.0,
    silence_seconds: float = 0.9,
    threshold: int = 500,
) -> bytes:
    """Record until the mic stays quiet, or `max_seconds` elapses.

    Returns concatenated int16 PCM bytes at the mic's sample rate.
    """
    frames = []
    silent_chunks = 0
    needed = max(1, int(silence_seconds / mic.chunk_seconds))
    elapsed = 0.0

    while elapsed < max_seconds:
        chunk = mic.read(mic.chunk_seconds)
        frames.append(chunk)
        elapsed += mic.chunk_seconds

        if rms_level(chunk) < threshold:
            silent_chunks += 1
            if silent_chunks >= needed:
                break
        else:
            silent_chunks = 0

    return b"".join(frames)


def play_pcm(pcm, rate: int, device_index: int | None = None) -> None:
    """Play int16 mono PCM (bytes or numpy array) at `rate` Hz."""
    if isinstance(pcm, np.ndarray):
        pcm = np.asarray(pcm, dtype=np.int16).tobytes()
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=rate,
            output=True,
            output_device_index=device_index,
        )
        try:
            stream.write(pcm)
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        pa.terminate()
