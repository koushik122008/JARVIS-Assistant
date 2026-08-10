"""
wake_word.py — MARK XLIX Wake Word Detection

Listens for the configured wake word (default: "Jarvis") in a background
thread using Picovoice Porcupine. When detected, signals the main loop
to start listening via a threading.Event.

Optional dependency: pvporcupine  (pip install pvporcupine)
Fallback: simple energy-based voice activity detection (less accurate, no
          wake word discrimination — just detects loud sounds).
"""

import json
import logging
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
FRAME_LENGTH = 512          # Porcupine expects 512 samples per frame
SILENCE_TIMEOUT = 8.0       # seconds of user silence before re-engaging wake word
ENERGY_THRESHOLD = 500      # RMS threshold for fallback VAD mode

# ── Vosk model management ─────────────────────────────────────────────────────

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"


def _model_cache_dir() -> Path:
    """User-level cache for the offline Vosk model (kept out of the repo)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(local) / "JARVIS"
    return Path.home() / ".local" / "share" / "jarvis"


def _vosk_model_dir() -> Path:
    return _model_cache_dir() / VOSK_MODEL_NAME


def _ensure_vosk_model() -> Optional[Path]:
    """Return the Vosk model directory, downloading it on first use (~40 MB)."""
    target = _vosk_model_dir()
    if target.is_dir() and any(target.iterdir()):
        return target
    import urllib.request
    import zipfile

    cache = _model_cache_dir()
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    zpath = cache / f"{VOSK_MODEL_NAME}.zip"
    try:
        logger.info(f"[WakeWord] Downloading Vosk model ({VOSK_MODEL_NAME}) — ~40 MB, one time…")
        urllib.request.urlretrieve(VOSK_MODEL_URL, zpath)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(cache)
        zpath.unlink(missing_ok=True)
        return target if target.is_dir() else None
    except Exception as e:
        logger.error(f"[WakeWord] Vosk model download failed: {e}")
        try:
            zpath.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _text_contains_keyword(text: str, keyword: str) -> bool:
    """True if recogniser text contains the wake keyword (case/space-insensitive)."""
    if not keyword:
        return False
    norm = " ".join(text.lower().split())
    kw = " ".join(keyword.lower().split())
    return bool(kw and kw in norm)

# Built-in Porcupine keywords available in the free tier
# Full list: "alexa", "americano", "blueberry", "bumblebee", "computer",
#            "grapefruit", "grasshopper", "hey google", "hey siri", "jarvis",
#            "ok google", "picovoice", "porcupine", "terminator"
BUILTIN_KEYWORDS = {
    "alexa": 0, "americano": 1, "blueberry": 2, "bumblebee": 3,
    "computer": 4, "grapefruit": 5, "grasshopper": 6, "hey google": 7,
    "hey siri": 8, "jarvis": 9, "ok google": 10, "picovoice": 11,
    "porcupine": 12, "terminator": 13,
}


class WakeWordDetector:
    """
    Background thread that listens for a wake word.

    Two detection modes:
      1. Porcupine (preferred) — accurate offline wake word engine
      2. Energy-based VAD (fallback) — detects any loud sound, no discrimination

    Usage:
        detector = WakeWordDetector()
        detector.on_detected = lambda: print("Wake word detected!")
        detector.start()
        ...
        detector.stop()
    """

    def __init__(self, keyword: str = "jarvis", sensitivity: float = 0.5):
        self.keyword = keyword.lower().strip()
        self.sensitivity = max(0.0, min(1.0, sensitivity))
        self.on_detected: Optional[Callable] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._porcupine = None
        self._porcupine_ok = False
        self._vosk = None
        self._vosk_ok = False
        self._vosk_recognizer = None
        self._audio_stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the wake word detection thread."""
        if self._running:
            return
        self._running = True
        self._try_init_porcupine()
        self._try_init_vosk()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="WakeWordThread",
        )
        self._thread.start()
        if self._porcupine_ok:
            mode = "Porcupine"
        elif self._vosk_ok:
            mode = "Vosk"
        else:
            mode = "Energy VAD (fallback)"
        logger.info(f"[WakeWord] Started — mode: {mode}, keyword: '{self.keyword}'")

    def stop(self):
        """Stop the wake word detection thread."""
        self._running = False
        if self._audio_stream:
            try:
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None
        logger.info("[WakeWord] Stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def using_porcupine(self) -> bool:
        return self._porcupine_ok

    def pause(self):
        """Pause detection without stopping the thread. Used to avoid echo triggers."""
        self._running = False
        logger.info("[WakeWord] Paused.")

    def resume(self):
        """Resume detection after pause. Restarts the audio stream."""
        if self._running:
            return
        self._running = True
        logger.info("[WakeWord] Resumed.")

    # ── Porcupine initialisation ──────────────────────────────────────────────

    def _try_init_porcupine(self):
        """Attempt to initialise Porcupine. Sets _porcupine_ok on success."""
        try:
            import pvporcupine
            self._porcupine = pvporcupine.create(
                access_key=None,           # free built-in keywords need no key
                keywords=[self.keyword],
                sensitivities=[self.sensitivity],
            )
            self._porcupine_ok = True
            logger.info(
                f"[WakeWord] Porcupine loaded — keyword='{self.keyword}', "
                f"sensitivity={self.sensitivity}"
            )
        except ImportError:
            logger.warning(
                "[WakeWord] pvporcupine not installed — falling back to energy VAD. "
                "Run: pip install pvporcupine"
            )
            self._porcupine_ok = False
        except Exception as e:
            logger.warning(f"[WakeWord] Porcupine init failed ({e}) — using VAD fallback.")
            self._porcupine_ok = False

    # ── Vosk initialisation ────────────────────────────────────────────────────

    def _try_init_vosk(self):
        """Attempt to initialise Vosk (offline, no API key). Sets _vosk_ok on success."""
        try:
            import vosk
            model_path = _ensure_vosk_model()
            if model_path is None:
                logger.warning("[WakeWord] Vosk model unavailable — skipping Vosk mode.")
                self._vosk_ok = False
                return
            self._vosk = vosk.Model(str(model_path))
            self._vosk_recognizer = vosk.KaldiRecognizer(self._vosk, SAMPLE_RATE)
            self._vosk_ok = True
            logger.info(f"[WakeWord] Vosk loaded — model: {model_path.name}")
        except ImportError:
            logger.warning("[WakeWord] vosk not installed — pip install vosk")
            self._vosk_ok = False
        except Exception as e:
            logger.warning(f"[WakeWord] Vosk init failed ({e}).")
            self._vosk_ok = False

    def _run_vosk(self):
        """Vosk-based detection loop — accurate offline keyword spotting."""
        recognizer = self._vosk_recognizer
        frame_bytes = bytearray()

        def callback(indata, frames, time_info, status):
            nonlocal frame_bytes
            if not self._running:
                return
            frame_bytes.extend(bytes(indata))

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=SAMPLE_RATE // 2,
            callback=callback,
        ):
            while self._running:
                if frame_bytes:
                    chunk = bytes(frame_bytes)
                    frame_bytes.clear()
                    try:
                        recognizer.AcceptWaveform(chunk)
                        partial = recognizer.PartialResult()
                        text = json.loads(partial).get("partial", "")
                        if _text_contains_keyword(text, self.keyword):
                            logger.info(f"[WakeWord] 🔔 Vosk detected: '{self.keyword}'")
                            recognizer.Reset()
                            if self.on_detected:
                                self.on_detected()
                            time.sleep(2.0)
                    except Exception as e:
                        logger.error(f"[WakeWord] Vosk error: {e}")
                else:
                    time.sleep(0.05)

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _run(self):
        """Continuously read microphone audio and check for wake word."""
        try:
            if self._porcupine_ok:
                self._run_porcupine()
            elif self._vosk_ok:
                self._run_vosk()
            else:
                self._run_vad()
        except Exception as e:
            logger.error(f"[WakeWord] Error: {e}")
        finally:
            if self._audio_stream:
                try:
                    self._audio_stream.close()
                except Exception:
                    pass

    def _run_porcupine(self):
        """Porcupine-based detection loop."""
        porcupine = self._porcupine
        frame_bytes = bytearray()
        pcm_frame = []

        def callback(indata, frames, time_info, status):
            nonlocal frame_bytes, pcm_frame
            if not self._running:
                return
            # RawInputStream yields CFFI buffers — bytes() is the portable cast.
            frame_bytes.extend(bytes(indata))
            # Process 512-sample frames
            while len(frame_bytes) >= FRAME_LENGTH * 2:
                chunk = frame_bytes[:FRAME_LENGTH * 2]
                frame_bytes = frame_bytes[FRAME_LENGTH * 2:]
                # Convert bytes to list of 16-bit integers
                pcm = struct.unpack_from("<" + "h" * FRAME_LENGTH, chunk)
                pcm_frame.append(pcm)

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_LENGTH,
            callback=callback,
        ):
            while self._running:
                while pcm_frame and self._running:
                    pcm = pcm_frame.pop(0)
                    kw_index = porcupine.process(pcm)
                    if kw_index >= 0:
                        logger.info(f"[WakeWord] 🔔 Detected: '{self.keyword}'")
                        if self.on_detected:
                            self.on_detected()
                time.sleep(0.01)

    def _run_vad(self):
        """Energy-based VAD fallback — triggers on any loud audio."""
        def callback(indata, frames, time_info, status):
            if not self._running:
                return
            # Compute RMS energy
            samples = struct.unpack_from(
                "<" + "h" * (len(indata) // 2), indata
            )
            energy = sum(s * s for s in samples) / len(samples)
            rms = (energy ** 0.5) if energy > 0 else 0
            if rms > ENERGY_THRESHOLD:
                logger.info(f"[WakeWord] 🔔 VAD trigger (energy={rms:.0f})")
                if self.on_detected:
                    self.on_detected()
                # Debounce — wait before next trigger
                time.sleep(2.0)

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=SAMPLE_RATE,  # 1-second chunks for stable energy calc
            callback=callback,
        ):
            while self._running:
                time.sleep(0.1)


# ── Convenience for testing ─────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import sys
    keyword = sys.argv[1] if len(sys.argv) > 1 else "jarvis"

    def _wake():
        print(f"\n🎤 WAKE WORD DETECTED: '{keyword}'")

    detector = WakeWordDetector(keyword=keyword)
    detector.on_detected = _wake
    detector.start()

    try:
        print(f"Listening for wake word '{keyword}'... Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        detector.stop()
