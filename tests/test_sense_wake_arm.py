"""Tests for the person-arrival → wake-arming fast-track (main.py).

Covers the `_arm_wake_on_person_arrival` method:
  • in-app WakeWordDetector is resumed when paused, started when stopped
  • wake-from-closed listener is restarted only when enabled AND dead
  • user toggles are always respected (nothing is force-enabled)

The method is exercised directly on a bare instance (no __init__), with all
config + background-wake dependencies mocked.
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

sys.path.insert(0, ".")

import main  # noqa: E402


class _FakeDetector:
    """Minimal stand-in for actions.wake_word.WakeWordDetector."""

    def __init__(self, running: bool = True):
        self.is_running = running
        self.resumed = 0
        self.started = 0

    def resume(self) -> None:
        self.resumed += 1

    def start(self) -> None:
        self.started += 1
        self.is_running = True


def _make_ui(detector) -> object:
    """Bare JarvisLive with only the attributes the method touches."""
    obj = main.JarvisLive.__new__(main.JarvisLive)
    obj._wake_word_detector = detector
    obj.ui = mock.MagicMock()
    obj.ui.write_log = mock.MagicMock()
    return obj


def _patch_background_wake(**kwargs):
    """Fake the lazily-imported actions.background_wake module."""
    mod = mock.MagicMock()
    for k, v in kwargs.items():
        setattr(mod, k, v)
    return mock.patch.dict(sys.modules, {"actions.background_wake": mod})


# ── In-app detector fast-track ────────────────────────────────────────────────


def test_resumes_paused_detector():
    det = _FakeDetector(running=True)
    app = _make_ui(det)
    with (
        mock.patch("main.get_background_wake_enabled", return_value=False),
        mock.patch("main.get_wake_word_keyword", return_value="jarvis"),
    ):
        app._arm_wake_on_person_arrival()
    assert det.resumed == 1        # un-paused (was paused during speech)
    assert det.started == 0
    # already listening → no 'Listening' announcement (no state change)
    app.ui.write_log.assert_not_called()


def test_starts_stopped_detector():
    det = _FakeDetector(running=False)
    app = _make_ui(det)
    with (
        mock.patch("main.get_background_wake_enabled", return_value=False),
        mock.patch("main.get_wake_word_keyword", return_value="jarvis"),
    ):
        app._arm_wake_on_person_arrival()
    assert det.started == 1
    assert det.is_running is True
    logged = [c.args[0].lower() for c in app.ui.write_log.call_args_list]
    assert any("listening" in m and "jarvis" in m for m in logged)


def test_no_detector_is_quiet_noop():
    app = _make_ui(None)
    with (
        mock.patch("main.get_background_wake_enabled", return_value=False),
        mock.patch("main.get_wake_word_keyword", return_value="jarvis"),
    ):
        app._arm_wake_on_person_arrival()   # must not raise
    app.ui.write_log.assert_not_called()


# ── Wake-from-closed listener fast-track ──────────────────────────────────────


def test_restarts_listener_when_enabled_and_dead():
    det = _FakeDetector(running=False)
    app = _make_ui(det)
    bg = mock.MagicMock()
    bg._listener_pids.return_value = []     # no listener running
    bg.start_listener.return_value = 4321
    with (
        mock.patch("main.get_background_wake_enabled", return_value=True),
        _patch_background_wake(_listener_pids=bg._listener_pids,
                               start_listener=bg.start_listener),
    ):
        app._arm_wake_on_person_arrival()
    bg.start_listener.assert_called_once()
    app.ui.write_log.assert_any_call(
        "SYS: Wake-from-closed listener restarted (person arrived).")


def test_does_not_restart_listener_when_already_running():
    det = _FakeDetector(running=False)
    app = _make_ui(det)
    bg = mock.MagicMock()
    bg._listener_pids.return_value = [777]  # already alive
    with (
        mock.patch("main.get_background_wake_enabled", return_value=True),
        _patch_background_wake(_listener_pids=bg._listener_pids,
                               start_listener=bg.start_listener),
    ):
        app._arm_wake_on_person_arrival()
    bg.start_listener.assert_not_called()


def test_does_not_touch_listener_when_disabled():
    det = _FakeDetector(running=False)
    app = _make_ui(det)
    bg = mock.MagicMock()
    with (
        mock.patch("main.get_background_wake_enabled", return_value=False),
        _patch_background_wake(_listener_pids=bg._listener_pids,
                               start_listener=bg.start_listener),
    ):
        app._arm_wake_on_person_arrival()
    bg._listener_pids.assert_not_called()
    bg.start_listener.assert_not_called()


# ── Full person-arrival edge wiring ───────────────────────────────────────────


def test_on_sense_state_arms_wake_on_arrival_edge():
    """The arrival edge must call the arming helper exactly once."""
    det = _FakeDetector(running=True)
    app = _make_ui(det)
    app._sense_person_present = False
    app._proactive = mock.MagicMock()

    class _Snap:
        person = True

    with (
        mock.patch("main.get_background_wake_enabled", return_value=False),
        mock.patch("main.get_wake_word_keyword", return_value="jarvis"),
    ):
        app._on_sense_state(_Snap())          # arrival → armed + proactive
        assert det.resumed == 1
        app._proactive.note_person_arrival.assert_called_once()

        app._on_sense_state(_Snap())          # still present → no re-arm
        assert det.resumed == 1

        class _Gone:
            person = False
        app._on_sense_state(_Gone())          # leaves
        app._on_sense_state(_Snap())          # arrives again → re-arm
        assert det.resumed == 2
