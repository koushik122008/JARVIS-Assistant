"""Tests for the camera sensing feature (actions/camera_sense.py) and its
wiring: config helpers, the HUD badge SENSE segment, and the proactive
person-arrival fast-track.

All engine tests use synthetic frames — no camera hardware or cv2 capture
required (the engine's frame provider is bypassed via inject_frame / detect)."""

from __future__ import annotations

import sys
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, ".")

from actions import camera_sense as cs  # noqa: E402


# ── Helpers: synthetic frames ─────────────────────────────────────────────────

def _black(w: int = 80, h: int = 60) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _bright(w: int = 80, h: int = 60, mean: int = 160) -> np.ndarray:
    return np.full((h, w, 3), mean, dtype=np.uint8)


def _skin_region(w: int = 80, h: int = 60) -> np.ndarray:
    """Frame whose centre is a large block of skin-tone HSV (~(10,120,200))."""
    frame = _black(w, h)
    # build via HSV→BGR so the skin detector's own colour space sees it
    rh, rw = h // 2, w // 2
    hsv = np.zeros((rh, rw, 3), dtype=np.uint8)
    hsv[..., 0] = 10
    hsv[..., 1] = 120
    hsv[..., 2] = 200
    frame[h // 4:h // 4 + rh, w // 4:w // 4 + rw] = cv2.cvtColor(
        hsv, cv2.COLOR_HSV2BGR)
    return frame


def _skin_bgr(w: int = 80, h: int = 60) -> np.ndarray:
    """Frame that is fully skin-toned BGR (direct BGR values ≈ HSV(10,120,200))."""
    return np.full((h, w, 3), (90, 130, 190), dtype=np.uint8)  # BGR


# cv2 is only needed by _skin_region; import lazily so test collection survives
try:
    import cv2  # noqa: F401
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


# ── Detection primitives ──────────────────────────────────────────────────────


def test_motion_ratio_zero_for_identical_frames():
    a = _black()
    assert cs._motion_ratio(a, a.copy()) == 0.0


def test_motion_ratio_high_for_different_frames():
    a = _black()
    b = _bright(mean=255)
    assert cs._motion_ratio(a, b) > 0.9


def test_motion_ratio_ignores_shape_mismatch():
    a = _black(80, 60)
    b = _black(60, 80)
    assert cs._motion_ratio(a, b) == 0.0


def test_ambient_labels():
    assert cs._ambient_label(10) == "dark"
    assert cs._ambient_label(50) == "dim"
    assert cs._ambient_label(120) == "normal"
    assert cs._ambient_label(230) == "bright"


def test_sense_interval_tiers():
    assert cs.sense_interval("active") == 1.0
    assert cs.sense_interval("background") == 2.5
    assert cs.sense_interval("hidden") == 5.0


def test_skin_ratio_high_for_skin_tone():
    if cv2 is None:
        pytest.skip("opencv not installed")
    frame = _skin_region()
    ratio = cs._skin_ratio(frame)
    assert ratio > cs._PERSON_RATIO       # real engine default


def test_skin_ratio_zero_for_black():
    assert cs._skin_ratio(_black()) == 0.0


# ── SensorSnapshot ────────────────────────────────────────────────────────────


def test_snapshot_label_and_event():
    snap = cs.SensorSnapshot(motion=True, person=False, ambient="normal")
    assert snap.label() == "MOTION"
    assert snap.event() == "SENS: Motion detected at camera"

    snap2 = cs.SensorSnapshot(motion=True, person=True, ambient="dark")
    assert snap2.label() == "PERSON"
    assert snap2.event() == "SENS: Person present at camera"

    snap3 = cs.SensorSnapshot(motion=False, person=False, ambient="dark")
    assert snap3.label() == "DARK"

    idle = cs.SensorSnapshot(motion=False, person=False, ambient="normal")
    assert idle.label() == ""
    assert idle.event() is None


# ── Engine state machine (synthetic frames) ──────────────────────────────────


def _engine(**kw) -> cs.CameraSenseEngine:
    return cs.CameraSenseEngine(
        motion_threshold=0.05, person_ratio=0.2, **kw)


def test_engine_detects_person_arrival_and_absence():
    eng = _engine()
    snaps: list[cs.SensorSnapshot] = []
    eng._on_state = snaps.append
    eng._person_since = 0.0
    eng._absent_s = 0.0  # absent immediately when skin disappears

    # No one there → no person
    eng.detect(_black())
    assert eng.snapshot.person is False

    # Skin tone appears → person present + arrival event
    snap = eng.detect(_skin_bgr())
    assert snap.person is True
    assert snap.label() == "PERSON"

    # Skin disappears → absent (absent_s=0)
    eng.detect(_black())
    assert eng.snapshot.person is False


def test_engine_motion_then_still():
    eng = _engine()
    eng.detect(_black())
    eng.detect(_bright(mean=255))      # big change → motion
    assert eng.snapshot.motion is True
    eng.detect(_bright(mean=255))      # same frame → no motion
    assert eng.snapshot.motion is False


def test_engine_ambient_update():
    eng = _engine()
    eng.detect(_black())               # mean 0 → dark
    assert eng.snapshot.ambient == "dark"
    eng.detect(_bright(mean=120))
    assert eng.snapshot.ambient == "normal"


def test_engine_event_debounce():
    """Person-arrival event must not re-fire while still present."""
    eng = _engine()
    events: list[str] = []
    eng._on_event = events.append
    eng.detect(_skin_bgr())
    assert any("Person present" in e for e in events)
    events.clear()
    for _ in range(5):
        eng.detect(_skin_bgr())
    assert events == []  # no duplicate arrival spam


def test_engine_person_absent_requires_stillness_window():
    """Absence only registers after the stillness window elapses."""
    eng = _engine()
    eng._absent_s = 100.0
    eng.detect(_skin_bgr())
    assert eng.snapshot.person is True
    eng.detect(_black())               # still within absence window
    assert eng.snapshot.person is True
    eng._person_since = 0.0            # simulate long stillness
    eng.detect(_black())
    assert eng.snapshot.person is False


def test_engine_interval_clamp():
    eng = _engine()
    eng.set_interval(0.05)
    assert eng._interval == 0.5        # clamped — can never busy-loop
    eng.set_interval(4.0)
    assert eng._interval == 4.0


def test_privacy_mode_disables_scene_analysis():
    """Privacy mode must never invoke the AI scene hook."""
    analyzer = mock.MagicMock(return_value="a room")
    eng = cs.CameraSenseEngine(
        on_event=lambda t: None,
        scene_analyzer=analyzer,
        privacy=True,
        person_ratio=0.2,
    )
    snap = eng.detect(_skin_bgr())
    assert snap.person is True
    assert snap.privacy is True
    assert snap.label() == "PRIVATE"
    # force a scene check — must short-circuit
    eng._last_scene_ts = 0.0
    eng._maybe_scene_analysis(snap)
    analyzer.assert_not_called()
    assert snap.scene == ""


def test_privacy_snapshot_label_and_event():
    snap = cs.SensorSnapshot(person=True, privacy=True)
    assert snap.label() == "PRIVATE"
    assert "privacy mode" in (snap.event() or "")


def test_engine_start_stop_with_injected_frame():
    """Full lifecycle: start() polls inject_frame() without a real camera."""
    eng = _engine()
    eng.inject_frame(_black())
    eng.start()
    try:
        import time
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and eng.snapshot.ambient == "normal":
            time.sleep(0.02)
        assert eng.snapshot.ambient == "dark"
    finally:
        eng.stop()
    assert not eng.running


def test_engine_gives_up_quietly_without_frames():
    eng = _engine()
    events: list[str] = []
    eng._on_event = events.append
    # no injected frame + cv2 missing/closed → 3 failures then silent exit
    with mock.patch.object(eng, "_read_frame", return_value=None):
        eng._loop()
    assert any("paused" in e for e in events)


# ── Config helpers ────────────────────────────────────────────────────────────


def test_config_roundtrip(tmp_path, monkeypatch):
    import utils
    from memory import config_manager as cm

    cfg = tmp_path / "api_keys.json"
    monkeypatch.setattr(utils, "API_CONFIG_PATH", cfg)
    monkeypatch.setattr(cm, "CONFIG_FILE", cfg)      # imported as alias
    monkeypatch.setattr(utils, "CONFIG_DIR", tmp_path)

    assert cm.get_camera_sensing_enabled() is False
    assert cm.get_camera_sensing_scene_ai() is False
    assert cm.get_camera_sensing_privacy() is False

    cm.save_camera_sensing_enabled(True)
    cm.save_camera_sensing_scene_ai(True)
    cm.save_camera_sensing_privacy(True)
    assert cm.get_camera_sensing_enabled() is True
    assert cm.get_camera_sensing_scene_ai() is True
    assert cm.get_camera_sensing_privacy() is True


# ── HUD badge SENSE segment ──────────────────────────────────────────────────


def test_sense_badge_segment():
    import ui

    lbl, col = ui._sense_badge_segment(cs.SensorSnapshot())
    assert lbl == ""                       # idle → no segment

    lbl, col = ui._sense_badge_segment(cs.SensorSnapshot(motion=True))
    assert "MOTION" in lbl
    assert col == ui.C.ACC2

    lbl, col = ui._sense_badge_segment(cs.SensorSnapshot(person=True))
    assert "PERSON" in lbl
    assert col == ui.C.GREEN

    assert ui._sense_badge_segment(None) == ("", ui.C.TEXT_DIM)


def test_perf_badge_text_includes_sense():
    import ui

    label, _ = ui._perf_badge_text("active", False, None, 1.5,
                                   sense_label="SENSE PERSON", sense_col=ui.C.GREEN)
    assert "SENSE PERSON" in label
    label2, col2 = ui._perf_badge_text("active", False, None, 1.5,
                                       sense_label="SENSE MOTION", sense_col=ui.C.ACC2)
    assert col2 == ui.C.ACC2               # sensing state dominates colour


def test_sense_badge_privacy_segment():
    import ui

    lbl, col = ui._sense_badge_segment(
        cs.SensorSnapshot(person=True, privacy=True))
    assert lbl == "SENSE PRIVATE"
    assert col == ui.C.PURPLE              # distinct violet marker
    assert col != ui.C.TEXT_DIM            # must not be mistaken for idle

    # and it must survive the full badge pipeline (privacy overrides amber)
    lbl2, col2 = ui._perf_badge_text("active", False, None, 1.5,
                                     sense_label=lbl, sense_col=col)
    assert col2 == ui.C.PURPLE


# ── Proactive person-arrival fast-track ──────────────────────────────────────


def test_proactive_person_arrival_waives_silence():
    from actions.proactive import ProactiveEngine

    eng = ProactiveEngine(min_silence_secs=900, check_cooldown=600)
    eng.mark_triggered()                   # start the cooldown clock

    # Without arrival: silence too short → no trigger
    import time
    eng._last_triggered = time.monotonic() - 700   # gap < cooldown
    assert eng.should_trigger(time.monotonic()) is False

    # Person arrives: eligible as soon as cooldown passes
    eng.note_person_arrival()
    eng._last_triggered = time.monotonic() - 700
    assert eng.should_trigger(time.monotonic()) is True

    # mark_triggered consumes the pending arrival
    eng.mark_triggered()
    assert eng.should_trigger(time.monotonic()) is False


# ── HUD chip runtime toggle (click to enable/disable camera sensing) ──────────


def test_perf_badge_text_sense_off_is_neutral():
    import ui

    label, col = ui._perf_badge_text("active", False, None, 1.5,
                                     sense_label="SENSE OFF",
                                     sense_col=ui.C.TEXT_DIM)
    assert "SENSE OFF" in label
    assert col == ui.C.TEXT_DIM              # idle chip stays dim, not amber


class TestSenseToggleWiring:
    """Clicking the HUD chip flips camera sensing on/off + persists the choice."""

    @pytest.fixture
    def win(self):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        import ui
        app = QApplication.instance() or QApplication([])
        w = ui.MainWindow("")
        w.show()
        yield w
        # Fully tear the window down: stop every live timer (the HUD's 16 ms
        # animation timer especially), close, and process deferred deletes so no
        # lingering window/timer bleeds into later test modules and starves
        # their live-timer tests (QTest.qWait).
        for t in w.findChildren(QTimer):
            t.stop()
        w.close()
        w._perf_badge.deleteLater()
        w.deleteLater()
        app.processEvents()

    def test_badge_is_a_clickable_widget(self, win):
        from PyQt6.QtCore import Qt
        import ui
        assert isinstance(win._perf_badge, ui._PerfBadge)
        assert win._perf_badge.cursor().shape() == Qt.CursorShape.PointingHandCursor

    def test_badge_shows_sense_off_when_disabled(self, win):
        win._cam_on = False
        win._dash_live = None
        win._sense_engine = None
        win._sense_snap = None
        win._refresh_perf_badge()
        assert "SENSE OFF" in win._perf_badge.text()
        assert win._perf_badge.isVisible()   # toggle affordance always on screen

    def test_dead_engine_shows_off_not_idle(self, win):
        # Engine object present but its thread died (camera lost mid-run) → the
        # chip must not claim it is actively sensing.
        dead = mock.MagicMock()
        dead.running = False
        win._cam_on = False
        win._dash_live = None
        win._sense_engine = dead
        win._sense_snap = None
        win._refresh_perf_badge()
        assert "SENSE OFF" in win._perf_badge.text()

    def test_click_disables_running_engine(self, win):
        from memory import config_manager as cm
        eng = mock.MagicMock()
        win._sense_engine = eng
        win._sense_active = True
        with mock.patch.object(cm, "save_camera_sensing_enabled") as save:
            win._toggle_camera_sensing()
        eng.stop.assert_called_once()
        assert win._sense_engine is None
        assert win._sense_snap is None
        save.assert_called_once_with(False)
        assert "SENSE OFF" in win._perf_badge.text()

    def test_click_enables_sensing_and_starts_engine(self, win):
        from memory import config_manager as cm
        with (
            mock.patch.object(cm, "get_camera_sensing_enabled", return_value=False),
            mock.patch.object(cm, "save_camera_sensing_enabled") as save,
            mock.patch.object(cm, "get_camera_sensing_privacy", return_value=False),
            mock.patch.object(cm, "get_camera_sensing_scene_ai", return_value=False),
            mock.patch.object(cs, "CameraSenseEngine") as Eng,
        ):
            win._toggle_camera_sensing()
        save.assert_called_once_with(True)
        Eng.assert_called_once()
        Eng.return_value.start.assert_called_once()
        assert win._sense_engine is not None

    def test_badge_click_signal_toggles(self, win):
        eng = mock.MagicMock()
        win._sense_engine = eng
        win._sense_active = True
        win._perf_badge.clicked.emit()
        eng.stop.assert_called_once()
        assert win._sense_engine is None

    def test_stale_sense_signal_ignored_after_disable(self, win):
        # Queued engine events arriving after disable must not resurrect the
        # badge's sense state.
        win._sense_active = False
        win._on_sense_state("PERSON", "normal", "")
        assert getattr(win, "_sense_snap", None) is None
