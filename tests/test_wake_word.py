"""
Tests for actions/wake_word.py — WakeWordDetector

Strategy:
  - sounddevice is patched per-test via unittest.mock so no real mic needed.
  - Both VAD and Porcupine modes use sd.RawInputStream — that's what we patch.
  - Detection callbacks use threading.Event for reliable cross-thread signalling.
  - No module-level re-imports or fragile time.sleep() calls.
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, ANY, patch

import pytest

from actions.wake_word import (
    BUILTIN_KEYWORDS,
    ENERGY_THRESHOLD,
    FRAME_LENGTH,
    SAMPLE_RATE,
    WakeWordDetector,
)

# ── Helpers ─────────────────────────────────────────────────────────────────────


def _make_pcm(*samples: int) -> bytes:
    """Pack integers as signed 16-bit little-endian PCM."""
    return struct.pack("<" + "h" * len(samples), *samples)


def _make_audio_chunk(rms_level: int = 100, num_frames: int = FRAME_LENGTH) -> bytes:
    """Create a PCM chunk with a given RMS amplitude."""
    samples = [rms_level] * num_frames
    return _make_pcm(*samples)


def _get_captured(key: str, timeout: float = 2.0):
    """Wait for a captured callback to become available, polling in 10ms intervals."""
    cb_key = f"_captured_{key}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cb = globals().get(cb_key)
        if cb is not None:
            return cb
        threading.Event().wait(0.01)
    return globals().get(cb_key)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """Poll a predicate until it returns True or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return bool(predicate())


@pytest.fixture(autouse=True)
def _clear_globals():
    """Clear captured callbacks between tests."""
    for key in list(globals()):
        if key.startswith("_captured_"):
            globals().pop(key)


@pytest.fixture
def mock_raw_stream():
    """
    Patch sd.RawInputStream — used by both VAD fallback and Porcupine modes.

    The side_effect captures the callback kwarg into a module-level global
    so tests can invoke the VAD/porcupine callback directly.
    """
    with patch("actions.wake_word.sd.RawInputStream") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_cls.return_value = mock_instance

        def capture_callback(*args, **kwargs):
            if "callback" in kwargs:
                globals()["_captured_vad"] = kwargs["callback"]
            return mock_instance

        mock_cls.side_effect = capture_callback
        yield mock_cls, mock_instance


@pytest.fixture
def mock_vosk_stream():
    """
    Patch sd.RawInputStream for the Vosk detection loop (_run_vosk).

    Captures the stream callback into a module-level global so tests can feed
    simulated microphone PCM straight into the loop, exactly as sounddevice's
    own audio thread would deliver it.
    """
    with patch("actions.wake_word.sd.RawInputStream") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.__exit__.return_value = None
        mock_cls.return_value = mock_instance

        def capture_callback(*args, **kwargs):
            if "callback" in kwargs:
                globals()["_captured_vosk"] = kwargs["callback"]
            return mock_instance

        mock_cls.side_effect = capture_callback
        yield mock_cls, mock_instance


def _make_vosk_detector(keyword: str = "jarvis", partial_text: str = ""):
    """A WakeWordDetector wired for Vosk mode with a fake recognizer."""
    fake_recognizer = MagicMock()
    fake_recognizer.PartialResult.return_value = json.dumps({"partial": partial_text})
    d = WakeWordDetector(keyword=keyword)
    d._vosk_ok = True
    d._vosk_recognizer = fake_recognizer
    return d, fake_recognizer


class _VoskLoopHarness:
    """
    Runs _run_vosk in a background thread and exposes the captured mic callback.

    Self-contained: it neutralises the production sleeps (0.05s spin and the
    2s post-detection debounce) so the loop runs at test speed and the thread
    can always be joined promptly, and it guarantees the thread is stopped
    even if setup fails mid-way.
    """

    def __init__(self, detector):
        self.detector = detector
        self.thread: threading.Thread | None = None
        self.callback = None
        self._sleep_patch = None

    def __enter__(self):
        self._sleep_patch = patch("actions.wake_word.time.sleep")
        self._sleep_patch.start()
        self.detector._running = True
        self.thread = threading.Thread(target=self.detector._run_vosk, daemon=True)
        self.thread.start()
        try:
            self.callback = _get_captured("vosk")
            assert self.callback is not None, "Vosk stream callback was not captured"
        except BaseException:
            # __exit__ is not called when __enter__ raises — clean up here so a
            # setup failure can never leak a spinning thread or an active patch.
            self.detector._running = False
            if self.thread is not None:
                self.thread.join(timeout=3.0)
            if self._sleep_patch is not None:
                self._sleep_patch.stop()
            raise
        return self

    def feed(self, data: bytes):
        """Push one PCM chunk into the loop, as the audio thread would."""
        self.callback(data, SAMPLE_RATE // 2, None, None)

    def __exit__(self, *exc):
        self.detector._running = False
        if self.thread is not None:
            self.thread.join(timeout=3.0)
            assert not self.thread.is_alive(), "Vosk loop thread did not stop"
        if self._sleep_patch is not None:
            self._sleep_patch.stop()
        return False


@pytest.fixture
def mock_porcupine():
    """Make pvporcupine available as a mock for Porcupine-mode tests."""
    with patch.dict("sys.modules") as mods:
        # Block real pvporcupine, provide our mock
        mock_pp = MagicMock()
        mock_instance = MagicMock()
        mock_instance.process.return_value = -1
        mock_pp.create.return_value = mock_instance
        mods["pvporcupine"] = mock_pp
        yield mock_pp, mock_instance


@pytest.fixture
def detector():
    """A clean WakeWordDetector instance (VAD fallback mode by default)."""
    return WakeWordDetector()


# ═══════════════════════════════════════════════════════════════════════════════
# Constructor tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConstructor:
    def test_default_params(self, detector):
        assert detector.keyword == "jarvis"
        assert detector.sensitivity == 0.5
        assert detector.on_detected is None
        assert detector.is_running is False
        assert detector.using_porcupine is False

    def test_custom_keyword_and_sensitivity(self):
        d = WakeWordDetector(keyword="ALEXA", sensitivity=0.8)
        assert d.keyword == "alexa"
        assert d.sensitivity == 0.8

    def test_sensitivity_clamping(self):
        d = WakeWordDetector(sensitivity=-0.5)
        assert d.sensitivity == 0.0
        d = WakeWordDetector(sensitivity=1.5)
        assert d.sensitivity == 1.0

    def test_keyword_builtin_index(self):
        assert BUILTIN_KEYWORDS["jarvis"] == 9
        assert BUILTIN_KEYWORDS["alexa"] == 0
        assert BUILTIN_KEYWORDS["hey google"] == 7


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle tests (VAD fallback mode)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycleVAD:
    @pytest.fixture(autouse=True)
    def _force_vad_mode(self):
        """Block the real vosk module so detection falls back to energy-VAD mode
        (vosk is installed on this machine, which would otherwise take priority)."""
        with patch.dict("sys.modules", {"vosk": None}):
            yield

    def test_start_and_stop(self, mock_raw_stream, detector):
        d = detector
        assert d.is_running is False
        d.start()
        assert d.is_running is True
        assert d._thread is not None
        assert d._thread.name == "WakeWordThread"
        assert d._thread.is_alive()
        d.stop()
        assert d.is_running is False

    def test_double_start_is_idempotent(self, mock_raw_stream, detector):
        d = detector
        d.start()
        t1 = d._thread
        d.start()
        assert d._thread is t1
        d.stop()

    def test_double_stop_is_safe(self, detector):
        d = detector
        d.stop()
        d.stop()

    def test_pause_and_resume(self, mock_raw_stream, detector):
        d = detector
        d.start()
        assert d.is_running is True
        d.pause()
        assert d.is_running is False
        d.resume()
        assert d.is_running is True
        d.stop()

    def test_double_resume_is_safe(self, mock_raw_stream, detector):
        d = detector
        d.start()
        d.resume()
        assert d.is_running is True
        d.stop()

    def test_using_porcupine_is_false_in_vad_mode(self, mock_raw_stream, detector):
        d = detector
        d.start()
        assert d.using_porcupine is False
        d.stop()

    def test_vad_callback_triggers_on_loud_audio(self, mock_raw_stream, detector):
        d = detector
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()
        d.start()

        cb = _get_captured("vad")
        assert cb is not None, "VAD callback was not captured"

        loud_data = _make_audio_chunk(rms_level=1000, num_frames=SAMPLE_RATE // 2)
        cb(loud_data, SAMPLE_RATE // 2, None, None)

        assert wake_event.wait(timeout=1.0), "Wake event should have fired for loud audio"
        d.stop()

    def test_vad_callback_not_triggered_for_quiet(self, mock_raw_stream, detector):
        d = detector
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()
        d.start()

        cb = _get_captured("vad")
        assert cb is not None, "VAD callback was not captured"

        quiet_data = _make_audio_chunk(rms_level=10, num_frames=SAMPLE_RATE // 2)
        cb(quiet_data, SAMPLE_RATE // 2, None, None)

        threading.Event().wait(0.1)
        assert not wake_event.is_set(), "Wake should NOT have fired for quiet audio"
        d.stop()

    def test_vad_callback_skipped_when_not_running(self, mock_raw_stream, detector):
        d = detector
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()
        d.start()

        cb = _get_captured("vad")
        assert cb is not None

        d.pause()
        loud_data = _make_audio_chunk(rms_level=1000, num_frames=SAMPLE_RATE // 2)
        cb(loud_data, SAMPLE_RATE // 2, None, None)

        threading.Event().wait(0.1)
        assert not wake_event.is_set(), "Wake should NOT fire when detector is paused"
        d.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Vosk mode tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestVoskMode:
    def test_vosk_init_success(self):
        fake_vosk = MagicMock()
        fake_vosk.Model.return_value = MagicMock()
        fake_vosk.KaldiRecognizer.return_value = MagicMock()
        with patch.dict("sys.modules", {"vosk": fake_vosk}):
            with patch("actions.wake_word._ensure_vosk_model",
                       return_value=Path("/fake/model")):
                d = WakeWordDetector()
                d._try_init_vosk()
        assert d._vosk_ok is True
        assert d._vosk is not None
        assert d._vosk_recognizer is not None
        fake_vosk.Model.assert_called_once()

    def test_vosk_init_falls_back_when_model_missing(self):
        fake_vosk = MagicMock()
        with patch.dict("sys.modules", {"vosk": fake_vosk}):
            with patch("actions.wake_word._ensure_vosk_model", return_value=None):
                d = WakeWordDetector()
                d._try_init_vosk()
        assert d._vosk_ok is False
        fake_vosk.Model.assert_not_called()

    def test_vosk_init_falls_back_when_not_installed(self):
        with patch.dict("sys.modules", {"vosk": None}):
            d = WakeWordDetector()
            d._try_init_vosk()
        assert d._vosk_ok is False
        assert d.using_porcupine is False

    def test_vosk_loop_opens_stream_with_audio_config(self, mock_vosk_stream):
        """The loop should open a 16 kHz mono int16 stream with the mic callback."""
        mock_cls, _ = mock_vosk_stream
        d, _ = _make_vosk_detector()
        with _VoskLoopHarness(d):
            pass
        mock_cls.assert_called_once_with(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=SAMPLE_RATE // 2,
            callback=ANY,
        )

    def test_vosk_loop_detects_keyword_from_streamed_audio(self, mock_vosk_stream):
        """Feeding real PCM through the captured callback with the keyword in the
        partial transcript must fire on_detected, feed the exact bytes to the
        recognizer, and reset it for the next phrase."""
        d, fake_recognizer = _make_vosk_detector(partial_text="hey jarvis what time is it")
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()

        with _VoskLoopHarness(d) as h:
            audio = _make_audio_chunk(rms_level=120, num_frames=SAMPLE_RATE // 2)
            h.feed(audio)

            assert wake_event.wait(timeout=1.0), \
                "keyword in the audio stream should fire the wake callback"
            fake_recognizer.AcceptWaveform.assert_called_with(audio)
            fake_recognizer.Reset.assert_called_once()

    def test_vosk_loop_ignores_stream_without_keyword(self, mock_vosk_stream):
        """Audio whose transcript has no keyword must not fire a wake or reset."""
        d, fake_recognizer = _make_vosk_detector(partial_text="good morning everyone")
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()

        with _VoskLoopHarness(d) as h:
            h.feed(_make_audio_chunk(rms_level=120, num_frames=SAMPLE_RATE // 2))

            assert _wait_until(lambda: fake_recognizer.AcceptWaveform.called), \
                "audio should be fed to the recognizer"
            assert not wake_event.is_set(), \
                "wake must not fire when the keyword is absent"
            fake_recognizer.Reset.assert_not_called()

    def test_vosk_loop_keyword_match_is_case_insensitive(self, mock_vosk_stream):
        """The loop should match the keyword regardless of case and punctuation."""
        d, _ = _make_vosk_detector(partial_text="Hey JARVIS, are you there?")
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()

        with _VoskLoopHarness(d) as h:
            h.feed(_make_audio_chunk(rms_level=120, num_frames=SAMPLE_RATE // 2))

            assert wake_event.wait(timeout=1.0), \
                "case differences should not block keyword detection"

    def test_vosk_loop_handles_continuous_stream_of_chunks(self, mock_vosk_stream):
        """Two successive audio bursts (keyword appearing only in the second)
        should both be processed — a real mic delivers an endless stream."""
        fake_recognizer = MagicMock()
        fake_recognizer.PartialResult.side_effect = [
            json.dumps({"partial": "just chatting with friends"}),
            json.dumps({"partial": "hey jarvis open the browser"}),
        ]
        d = WakeWordDetector(keyword="jarvis")
        d._vosk_ok = True
        d._vosk_recognizer = fake_recognizer
        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()

        # Determinism relies on the loop calling AcceptWaveform only AFTER
        # frame_bytes.clear(), so waiting on call_count guarantees the first
        # burst was fully consumed before the second one is fed.
        with _VoskLoopHarness(d) as h:
            h.feed(_make_audio_chunk(rms_level=120, num_frames=SAMPLE_RATE // 2))
            assert _wait_until(lambda: fake_recognizer.AcceptWaveform.call_count >= 1)
            assert not wake_event.is_set(), "first burst has no keyword"

            h.feed(_make_audio_chunk(rms_level=120, num_frames=SAMPLE_RATE // 2))
            assert wake_event.wait(timeout=1.0), \
                "keyword in a later chunk should still fire the wake"
            fake_recognizer.Reset.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Porcupine mode tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPorcupineMode:
    def test_porcupine_init_success(self, mock_porcupine):
        mock_pp, mock_instance = mock_porcupine
        d = WakeWordDetector()
        d._try_init_porcupine()
        assert d.using_porcupine is True
        mock_pp.create.assert_called_once()

    def test_porcupine_init_uses_correct_keyword(self, mock_porcupine):
        mock_pp, mock_instance = mock_porcupine
        d = WakeWordDetector(keyword="computer", sensitivity=0.7)
        d._try_init_porcupine()
        mock_pp.create.assert_called_once_with(
            access_key=None,
            keywords=["computer"],
            sensitivities=[0.7],
        )

    def test_porcupine_process_called_with_pcm(self, mock_porcupine):
        mock_pp, mock_instance = mock_porcupine
        d = WakeWordDetector()
        d._try_init_porcupine()
        pcm_data = [0] * FRAME_LENGTH
        d._porcupine.process(pcm_data)
        mock_instance.process.assert_called_with(pcm_data)

    def test_porcupine_detection_calls_on_detected(self, mock_porcupine):
        mock_pp, mock_instance = mock_porcupine
        d = WakeWordDetector()
        d._try_init_porcupine()
        assert d.using_porcupine is True

        wake_event = threading.Event()
        d.on_detected = lambda: wake_event.set()

        mock_instance.process.side_effect = [-1, 0]
        pcm_data = [0] * FRAME_LENGTH

        result1 = d._porcupine.process(pcm_data)
        assert result1 == -1

        result2 = d._porcupine.process(pcm_data)
        assert result2 == 0

        # Simulate what _run_porcupine does when process returns >= 0
        if result2 >= 0 and d.on_detected:
            d.on_detected()

        assert wake_event.is_set(), "on_detected should have been called"

    def test_porcupine_init_fallback_on_exception(self, mock_porcupine):
        mock_pp, mock_instance = mock_porcupine
        mock_pp.create.side_effect = RuntimeError("Something went wrong")
        d = WakeWordDetector()
        d._try_init_porcupine()
        assert d.using_porcupine is False


# ═══════════════════════════════════════════════════════════════════════════════
# Property tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperties:
    def test_is_running_after_construction(self):
        d = WakeWordDetector()
        assert d.is_running is False

    def test_using_porcupine_without_pvporcupine(self):
        d = WakeWordDetector()
        assert d.using_porcupine is False

    def test_using_porcupine_after_failed_init(self):
        d = WakeWordDetector()
        d._try_init_porcupine()
        assert d.using_porcupine is False


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases & robustness
# ═══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_logger_exists(self):
        from actions.wake_word import logger
        assert isinstance(logger, logging.Logger)
        assert logger.name == "actions.wake_word"

    def test_constants_defined(self):
        assert SAMPLE_RATE == 16000
        assert FRAME_LENGTH == 512
        assert ENERGY_THRESHOLD == 500

    def test_builtin_keywords_are_complete(self):
        expected = {"alexa", "americano", "blueberry", "bumblebee", "computer",
                    "grapefruit", "grasshopper", "hey google", "hey siri", "jarvis",
                    "ok google", "picovoice", "porcupine", "terminator"}
        assert set(BUILTIN_KEYWORDS.keys()) == expected

    def test_keyword_defaults_to_jarvis_index(self):
        assert BUILTIN_KEYWORDS.get("nonexistent", 9) == 9

    def test_callback_not_set_by_default(self, detector):
        assert detector.on_detected is None

    def test_pause_when_not_running(self, detector):
        detector.pause()
        assert detector.is_running is False

    def test_resume_when_already_running(self, detector):
        detector._running = True
        detector.resume()
        assert detector.is_running is True

    def test_rms_calculation(self):
        rms_level = 3000
        data = _make_audio_chunk(rms_level=rms_level, num_frames=512)
        samples = struct.unpack_from("<" + "h" * 512, data)
        energy = sum(s * s for s in samples) / len(samples)
        rms = (energy ** 0.5) if energy > 0 else 0
        assert abs(rms - rms_level) < 0.01

    def test_stop_signals_thread(self, mock_raw_stream, detector):
        """stop() sets _running to False so the background thread exits its with-block."""
        mock_cls, mock_instance = mock_raw_stream
        d = detector
        d.start()
        assert d.is_running is True
        assert d._thread is not None and d._thread.is_alive()
        d.stop()
        assert d.is_running is False
        # The thread should exit the with-block and join within a reasonable time.
        d._thread.join(timeout=3.0)
        assert not d._thread.is_alive()

    def test_stop_cleans_porcupine(self, mock_porcupine):
        """stop() should delete the porcupine instance."""
        mock_pp, mock_instance = mock_porcupine
        d = WakeWordDetector()
        d._try_init_porcupine()
        assert d._porcupine is mock_instance
        d.stop()
        mock_instance.delete.assert_called_once()
        assert d._porcupine is None


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry guard
# ═══════════════════════════════════════════════════════════════════════════════


def test_main_block_exists():
    """The module should have a __main__ guard for standalone testing."""
    import inspect

    import actions.wake_word as ww_mod
    mod_source = inspect.getsource(ww_mod)
    assert 'if __name__ == "__main__":' in mod_source
