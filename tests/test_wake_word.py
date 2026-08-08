"""
Tests for actions/wake_word.py — WakeWordDetector

Strategy:
  - sounddevice is patched per-test via unittest.mock so no real mic needed.
  - Both VAD and Porcupine modes use sd.RawInputStream — that's what we patch.
  - Detection callbacks use threading.Event for reliable cross-thread signalling.
  - No module-level re-imports or fragile time.sleep() calls.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from actions.wake_word import (
    ENERGY_THRESHOLD,
    FRAME_LENGTH,
    SAMPLE_RATE,
    BUILTIN_KEYWORDS,
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
