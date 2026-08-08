"""
wake_word.py — MARK XLIX Wake Word Detection

Listens for the configured wake word (default: "Jarvis") in a background
thread using Picovoice Porcupine. When detected, signals the main loop
to start listening via a threading.Event.

Optional dependency: pvporcupine  (pip install pvporcupine)
Fallback: simple energy-based voice activity detection (less accurate, no
          wake word discrimination — just detects loud sounds).
"""

import logging
import struct
import threading
import time
from typing import Callable, Optional

import sounddevice as sd

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
FRAME_LENGTH = 512          # Porcupine expects 512 samples per frame
SILENCE_TIMEOUT = 8.0       # seconds of user silence before re-engaging wake word
ENERGY_THRESHOLD = 500      # RMS threshold for fallback VAD mode

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
        self._audio_stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the wake word detection thread."""
        if self._running:
            return
        self._running = True
        self._try_init_porcupine()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="WakeWordThread",
        )
        self._thread.start()
        mode = "Porcupine" if self._porcupine_ok else "Energy VAD (fallback)"
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

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _run(self):
        """Continuously read microphone audio and check for wake word."""
        try:
            if self._porcupine_ok:
                self._run_porcupine()
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
            frame_bytes.extend(indata.tobytes())
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
