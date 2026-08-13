"""Tests for Design Mode (actions/design_mode.py) — hand-gesture air-drawing.

Pure helpers (classify_gesture / pinch_distance / swatch_at) are tested with
synthetic landmark tuples — no camera or MediaPipe required. The
DesignModeSession.update() path is exercised with synthetic frames + landmark
poses to verify draw / hover / palette-select / erase / clear / save behaviour
on the canvas (needs cv2; those tests skip when OpenCV is absent).
"""

from __future__ import annotations

import sys
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, ".")

from actions import design_mode as dm  # noqa: E402

try:
    import cv2  # noqa: F401
    HAS_CV2 = True
except Exception:  # pragma: no cover - env-specific
    HAS_CV2 = False


# ── Synthetic hand poses ──────────────────────────────────────────────────────
# MediaPipe y grows downward, so "up" = tip above its PIP joint (smaller y).


def _pose(index: bool = True, middle: bool = True,
          ring: bool = True, pinky: bool = True) -> list:
    """21-landmark (x, y) list; the four fingers are raised per the flags."""
    lm = [(0.5, 0.95)] * 21                        # wrist
    lm[1] = (0.44, 0.80); lm[2] = (0.38, 0.72)     # thumb (irrelevant here)
    lm[3] = (0.33, 0.66); lm[4] = (0.27, 0.60)
    lm[5], lm[6] = (0.42, 0.62), (0.41, 0.47)      # index mcp / pip
    lm[8] = (0.40, 0.30 if index else 0.60)        # index tip
    lm[9], lm[10] = (0.50, 0.61), (0.50, 0.44)     # middle mcp / pip
    lm[12] = (0.50, 0.26 if middle else 0.58)      # middle tip
    lm[13], lm[14] = (0.58, 0.62), (0.59, 0.47)    # ring mcp / pip
    lm[16] = (0.60, 0.30 if ring else 0.60)        # ring tip
    lm[17], lm[18] = (0.66, 0.64), (0.67, 0.50)    # pinky mcp / pip
    lm[20] = (0.68, 0.36 if pinky else 0.63)       # pinky tip
    return lm


def _pinch_pose(index: bool = False, pinky: bool = False) -> list:
    """Fist with the thumb pinching the index (clear) or pinky (save) tip.

    The thumb reaches to the pinched finger, so the other pinch pair stays far
    apart — exactly like a real hand.
    """
    lm = _pose(False, False, False, False)
    if index:
        lm[4] = (0.43, 0.55)
        lm[8] = (0.44, 0.55)
    if pinky:
        lm[4] = (0.62, 0.55)
        lm[20] = (0.63, 0.55)
    return lm


# ── Gesture classification ────────────────────────────────────────────────────


def test_open_palm_is_erase():
    assert dm.classify_gesture(_pose(True, True, True, True)) == "erase"


def test_three_fingers_tolerated_as_erase():
    assert dm.classify_gesture(_pose(True, True, True, False)) == "erase"


def test_only_index_is_draw():
    assert dm.classify_gesture(_pose(True, False, False, False)) == "draw"


def test_index_middle_is_hover():
    assert dm.classify_gesture(_pose(True, True, False, False)) == "hover"


def test_fist_is_pause():
    assert dm.classify_gesture(_pose(False, False, False, False)) == "pause"


def test_mixed_two_fingers_is_pause():
    # index + ring (no middle) → ambiguous → pause, never draw/hover
    assert dm.classify_gesture(_pose(True, False, True, False)) == "pause"


def test_no_hand_is_pause():
    assert dm.classify_gesture(None) == "pause"


# ── Pinch detection ───────────────────────────────────────────────────────────


def test_thumb_index_pinch_is_close():
    assert dm.pinch_distance(_pinch_pose(index=True), 4, 8) < dm._PINCH_THRESH


def test_thumb_pinky_pinch_is_close():
    assert dm.pinch_distance(_pinch_pose(pinky=True), 4, 20) < dm._PINCH_THRESH


def test_open_hand_is_not_pinching():
    lm = _pose()
    assert dm.pinch_distance(lm, 4, 8) > dm._PINCH_THRESH
    assert dm.pinch_distance(lm, 4, 20) > dm._PINCH_THRESH


# ── Palette hit-testing ───────────────────────────────────────────────────────


def test_swatch_at_hit_and_miss():
    x0, y0 = dm._SWATCH_LEFT, dm._SWATCH_Y
    assert dm.swatch_at(x0 + 10, y0 + 10) == "Red"
    gx = x0 + dm._SWATCH_W + dm._SWATCH_GAP + 10
    assert dm.swatch_at(gx, y0 + 10) == "Green"
    assert dm.swatch_at(5, 5) is None       # above/left of the strip
    assert dm.swatch_at(x0, y0 + 200) is None  # below the strip
    assert dm.swatch_at(5000, y0 + 10) is None  # past the last swatch


# ── Canvas behaviour via DesignModeSession.update (needs cv2) ─────────────────


def _session() -> dm.DesignModeSession:
    return dm.DesignModeSession(cap=mock.MagicMock())


def _frame(w: int = 320, h: int = 200) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_draw_follows_index_tip():
    sess = _session()
    frame = _frame()
    lm = _pose(True, False, False, False)
    lm[8] = (0.50, 0.40)                     # tip stays above its PIP (0.47)
    sess.update(frame, lm)
    lm[8] = (0.55, 0.40)                     # move the fingertip → a line
    out, mode = sess.update(frame, lm)
    assert mode == "DRAW"
    assert np.count_nonzero(sess._canvas) > 0
    # ink lands at the second tip position
    tip = (int(0.55 * 320), int(0.40 * 200))
    assert sess._canvas[tip[1], tip[0]].tolist() != [0, 0, 0]
    # composited frame is not blank either
    assert np.count_nonzero(out) > 0


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_palm_erases_strokes_under_it():
    sess = _session()
    frame = _frame()
    lm = _pose(True, False, False, False)
    lm[8] = (0.50, 0.40)
    sess.update(frame, lm)
    lm[8] = (0.60, 0.40)
    sess.update(frame, lm)
    assert np.count_nonzero(sess._canvas) > 0

    palm = _pose(True, True, True, True)
    palm[9] = (0.55, 0.40)                   # palm centre over the stroke
    out, mode = sess.update(frame, palm)
    assert mode == "ERASE"
    cx, cy = int(0.55 * 320), int(0.40 * 200)
    assert sess._canvas[cy, cx].tolist() == [0, 0, 0]


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_hover_selects_colour_swatch_without_drawing():
    sess = _session()
    frame = _frame()
    hover = _pose(True, True, False, False)
    gx = dm._SWATCH_LEFT + (dm._SWATCH_W + dm._SWATCH_GAP) + 30
    gy = dm._SWATCH_Y + 10
    hover[8] = (gx / 320, gy / 200)
    out, mode = sess.update(frame, hover)
    assert mode == "HOVER"
    assert sess._selected == "Green"
    assert sess._color == (0, 255, 0)
    assert np.count_nonzero(sess._canvas) == 0   # hover never draws


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_hover_over_eraser_swatch_makes_index_erase():
    sess = _session()
    frame = _frame()
    # draw a stroke first
    lm = _pose(True, False, False, False)
    lm[8] = (0.50, 0.40)
    sess.update(frame, lm)
    lm[8] = (0.60, 0.40)
    sess.update(frame, lm)
    assert np.count_nonzero(sess._canvas) > 0

    hover = _pose(True, True, False, False)
    ex = dm._SWATCH_LEFT + 5 * (dm._SWATCH_W + dm._SWATCH_GAP) + 30
    hover[8] = (ex / 320, (dm._SWATCH_Y + 10) / 200)
    sess.update(frame, hover)
    assert sess._selected == "Eraser"
    assert sess._tool == "erase"

    # a single raised index now erases instead of drawing
    lm[8] = (0.55, 0.40)
    out, mode = sess.update(frame, lm)
    assert mode == "ERASE"
    cx, cy = int(0.55 * 320), int(0.40 * 200)
    assert sess._canvas[cy, cx].tolist() == [0, 0, 0]


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_held_thumb_index_pinch_clears_canvas():
    sess = _session()
    frame = _frame()
    lm = _pose(True, False, False, False)
    lm[8] = (0.50, 0.40)
    sess.update(frame, lm)
    lm[8] = (0.60, 0.40)
    sess.update(frame, lm)
    assert np.count_nonzero(sess._canvas) > 0

    pinch = _pinch_pose(index=True)
    sess.update(frame, pinch, now=100.0)
    assert np.count_nonzero(sess._canvas) > 0     # hold not long enough yet
    sess.update(frame, pinch, now=101.5)
    assert np.count_nonzero(sess._canvas) == 0    # canvas wiped


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_held_thumb_pinky_pinch_saves_png(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "_OUTPUT_DIR", tmp_path)
    spoken: list[str] = []
    sess = dm.DesignModeSession(cap=mock.MagicMock(), speak=spoken.append)
    frame = _frame()
    lm = _pose(True, False, False, False)
    lm[8] = (0.50, 0.40)
    sess.update(frame, lm)
    lm[8] = (0.60, 0.40)
    sess.update(frame, lm)

    pinch = _pinch_pose(pinky=True)
    sess.update(frame, pinch, now=100.0)
    sess.update(frame, pinch, now=101.5)

    files = list(tmp_path.glob("drawing_*.png"))
    assert len(files) == 1
    assert spoken and spoken[0].startswith("Saved as drawing_")


@pytest.mark.skipif(not HAS_CV2, reason="opencv not installed")
def test_save_with_empty_canvas_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "_OUTPUT_DIR", tmp_path)
    sess = _session()
    assert "nothing drawn" in sess.save()
    assert list(tmp_path.glob("*.png")) == []


# ── Module-level entry points (mocked camera / session) ───────────────────────


def test_start_requires_opencv():
    with mock.patch.object(dm, "_CV2", False), mock.patch.object(dm, "_session", None):
        assert "OpenCV" in dm.start_design_mode()


def test_start_requires_mediapipe():
    with (
        mock.patch.object(dm, "_CV2", True),
        mock.patch.object(dm, "_mediapipe_available", return_value=False),
        mock.patch.object(dm, "_session", None),
    ):
        assert "MediaPipe" in dm.start_design_mode()


def test_start_camera_failure_returns_error_not_crash():
    with (
        mock.patch.object(dm, "_CV2", True),
        mock.patch.object(dm, "_mediapipe_available", return_value=True),
        mock.patch.object(dm, "_open_camera", return_value=None),
        mock.patch.object(dm, "_session", None),
    ):
        msg = dm.start_design_mode()
    assert "camera" in msg.lower()


def test_start_when_already_running():
    sess = mock.MagicMock()
    sess.running = True
    with mock.patch.object(dm, "_session", sess):
        assert "already" in dm.start_design_mode().lower()
    sess.start.assert_not_called()


def test_start_launches_background_session():
    sess = mock.MagicMock()
    sess.running = False
    sess.start.return_value = True
    cap = mock.MagicMock()
    with (
        mock.patch.object(dm, "_CV2", True),
        mock.patch.object(dm, "_mediapipe_available", return_value=True),
        mock.patch.object(dm, "_open_camera", return_value=cap),
        mock.patch.object(dm, "DesignModeSession", return_value=sess) as cls,
        mock.patch.object(dm, "_session", None),
    ):
        msg = dm.start_design_mode()
    assert "activated" in msg.lower()
    cls.assert_called_once()
    sess.start.assert_called_once()


def test_stop_when_not_active():
    with mock.patch.object(dm, "_session", None):
        assert "not active" in dm.stop_design_mode().lower()


def test_stop_active_session_sets_exit():
    sess = mock.MagicMock()
    sess.running = True
    with mock.patch.object(dm, "_session", sess):
        assert "exiting" in dm.stop_design_mode().lower()
    sess.stop.assert_called_once()


def test_save_drawing_requires_active_session():
    with mock.patch.object(dm, "_session", None):
        assert "no active" in dm.save_drawing().lower()


def test_save_drawing_delegates_to_session():
    sess = mock.MagicMock()
    sess.running = True
    sess.save.return_value = "Saved as drawing_x.png."
    with mock.patch.object(dm, "_session", sess):
        assert dm.save_drawing() == "Saved as drawing_x.png."
    sess.save.assert_called_once()
