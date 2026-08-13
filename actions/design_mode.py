"""
MARK XLIX — Design Mode: hand-gesture air-drawing on the live webcam feed.

When the user says "design mode", JARVIS opens the webcam and tracks the hand
with MediaPipe. The index fingertip draws ink lines onto a persistent canvas
that is composited over the live feed — a virtual whiteboard. Gesture controls
(draw / hover / erase / pause / clear / save) mean the user never needs a
keyboard or mouse.

Gestures (mapping may be tuned, the action set is fixed):
  • index finger only            → DRAW   — line follows landmark #8
  • index + middle               → HOVER  — no drawing; select palette swatches
  • open palm (3–4 fingers up)   → ERASE  — big circle follows the palm centre
  • fist (no fingers up)         → PAUSE  — reposition without drawing
  • thumb + index pinch, held    → CLEAR  — wipes the canvas after a 1 s hold
  • thumb + pinky pinch, held    → SAVE   — writes a timestamped PNG

The loop runs in its own daemon thread with a shared exit Event, so the main
assistant keeps listening: "exit design mode" (a tool call) sets the flag, and
pressing Q inside the OpenCV window is the manual fallback. cv2 resources and
the window are always released on every exit path.

Pure helpers (classify_gesture / pinch_distance / swatch_at) operate on plain
(x, y) landmark tuples so they can be unit-tested without MediaPipe or a camera.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

try:
    import cv2
    _CV2 = True
except Exception:  # pragma: no cover - env-specific
    cv2 = None  # type: ignore[assignment]
    _CV2 = False

try:
    from utils import BASE_DIR
except Exception:  # pragma: no cover - import guards
    BASE_DIR = Path(".")


# ── Tuning ─────────────────────────────────────────────────────────────────────

_WINDOW        = "JARVIS Design Mode"
_OUTPUT_DIR    = BASE_DIR / "design_mode_output"
_PINCH_THRESH  = 0.06     # normalized tip distance → "pinching"
_PINCH_HOLD_S  = 1.0      # sustained pinch time before clear/save fires
_DRAW_THICK    = 6
_ERASER_R      = 28       # palm-eraser radius (px)
_TIP_ERASER_R  = 16       # index-tip eraser radius when Eraser tool selected
_CURSOR_R      = 6
_FLASH_MS      = 1500.0   # how long transient feedback text stays on screen

# Palette strip: (name, BGR colour)
_PALETTE = [
    ("Red",    (0, 0, 255)),
    ("Green",  (0, 255, 0)),
    ("Blue",   (255, 0, 0)),
    ("Yellow", (0, 255, 255)),
    ("White",  (255, 255, 255)),
    ("Eraser", (60, 60, 60)),
]
_SWATCH_LEFT, _SWATCH_Y = 10, 10
_SWATCH_W, _SWATCH_H, _SWATCH_GAP = 92, 42, 8

# MediaPipe Hands landmark indices (21 points)
_TIP = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
_PIP = {"index": 6, "middle": 10, "ring": 14, "pinky": 18}


# ── Pure detection helpers (unit-testable, no MediaPipe required) ─────────────


def _euclid(a: tuple, b: tuple) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)


def _finger_up(lm: list, tip: int, pip: int, margin: float = 0.02) -> bool:
    """True when a fingertip sits above its lower joint (extended).

    MediaPipe y grows downward, so "up" means tip.y < pip.y. A small margin
    rejects jitter around the transition point.
    """
    return lm[tip][1] < lm[pip][1] - margin


def _fingers_up(lm: list) -> tuple[bool, bool, bool, bool]:
    """(index, middle, ring, pinky) extension booleans for one hand."""
    return (
        _finger_up(lm, _TIP["index"],  _PIP["index"]),
        _finger_up(lm, _TIP["middle"], _PIP["middle"]),
        _finger_up(lm, _TIP["ring"],   _PIP["ring"]),
        _finger_up(lm, _TIP["pinky"],  _PIP["pinky"]),
    )


def classify_gesture(lm) -> str:
    """Map one hand's landmarks to a mode: draw | hover | erase | pause."""
    if not lm:
        return "pause"
    idx, mid, ring, pinky = _fingers_up(lm)
    n = sum((idx, mid, ring, pinky))
    if n >= 3:
        return "erase"          # open palm (3–4 fingers) — tolerant of a bent pinky
    if n == 1 and idx:
        return "draw"           # only index up
    if n == 2 and idx and mid:
        return "hover"          # peace sign — select palette, no drawing
    return "pause"


def pinch_distance(lm, a: int, b: int) -> float:
    """Normalized distance between two landmark tips (pinch test)."""
    if not lm or a >= len(lm) or b >= len(lm):
        return 1.0
    return _euclid(lm[a], lm[b])


def swatch_at(x: int, y: int) -> Optional[str]:
    """Palette swatch name at pixel coords, or None when outside the strip."""
    for i, (name, _) in enumerate(_PALETTE):
        x0 = _SWATCH_LEFT + i * (_SWATCH_W + _SWATCH_GAP)
        if x0 <= x <= x0 + _SWATCH_W and _SWATCH_Y <= y <= _SWATCH_Y + _SWATCH_H:
            return name
    return None


# ── Camera plumbing ────────────────────────────────────────────────────────────


def _open_camera():
    """Open + warm the webcam. Returns an open cv2.VideoCapture or None."""
    if not _CV2:
        return None
    try:
        import platform
        if platform.system() == "Windows":
            backend = cv2.CAP_DSHOW
        elif platform.system() == "Darwin":
            backend = cv2.CAP_AVFOUNDATION
        else:
            backend = cv2.CAP_ANY
        from utils import load_config
        index = int(load_config().get("camera_index", 0))
    except Exception:
        backend, index = 0, 0
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        for _ in range(5):
            cap.read()          # warm-up — first frames are often black
        return cap
    except Exception:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None


# ── Session ────────────────────────────────────────────────────────────────────


class DesignModeSession:
    """Runs the design-mode OpenCV loop in its own daemon thread.

    The loop polls a shared exit Event every frame, so the main assistant keeps
    listening while design mode is active — a "stop design mode" tool call sets
    the flag and the loop tears down camera + window on its way out.
    """

    def __init__(self, cap, speak: Optional[Callable] = None, ui=None):
        self._cap = cap
        self._speak = speak
        self._ui = ui
        self._exit = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # drawing state (mutated only from the loop thread; save() locks)
        self._canvas: Optional[np.ndarray] = None
        self._prev_tip: Optional[tuple[int, int]] = None
        self._selected = "Red"              # highlighted swatch name
        self._color: tuple = _PALETTE[0][1]  # active draw colour (BGR)
        self._tool = "draw"                 # draw | erase (palette Eraser swatch)
        self._flash_msg = ""                # transient on-screen feedback
        self._flash_until = 0.0

        # pinch hold bookkeeping
        self._clear_since = 0.0
        self._save_since = 0.0
        self._was_clear = False
        self._was_save = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        self._exit.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="design-mode")
        try:
            self._thread.start()
            return True
        except Exception:
            self._thread = None
            return False

    def stop(self) -> None:
        self._exit.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _log(self, text: str) -> None:
        if self._ui is None:
            return
        try:
            self._ui.write_log(text)
        except Exception:
            pass

    def _say(self, text: str) -> None:
        if self._speak is None:
            return
        try:
            self._speak(text)
        except Exception:
            pass

    # ── save ──────────────────────────────────────────────────────────────────

    def save(self) -> str:
        """Write the current canvas (not the raw feed) to design_mode_output/.

        Returns a spoken-ready confirmation, or an explanation when there is
        nothing to save. Safe to call from any thread (the canvas is snapshotted
        under a lock).
        """
        with self._lock:
            canvas = self._canvas
        if canvas is None or int(np.count_nonzero(canvas)) == 0:
            return "There is nothing drawn yet to save."
        name = f"drawing_{datetime.now():%Y%m%d_%H%M%S}.png"
        try:
            _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(_OUTPUT_DIR / name), canvas)
        except Exception as e:
            return f"I couldn't save the drawing. {e}"
        self._log(f"[DesignMode] Saved {name}")
        return f"Saved as {name}."

    # ── per-frame update (unit-testable: synthetic frame + landmarks) ─────────

    def update(self, frame, lm, now: Optional[float] = None) -> tuple:
        """Advance one frame of drawing state; returns (composite, mode)."""
        h, w = frame.shape[:2]
        now = now if now is not None else time.monotonic()
        if self._canvas is None or self._canvas.shape[:2] != (h, w):
            self._canvas = np.zeros((h, w, 3), dtype=np.uint8)
            self._prev_tip = None

        gesture = classify_gesture(lm) if lm else "pause"
        mode = {"draw": "DRAW", "hover": "HOVER",
                "erase": "ERASE", "pause": "PAUSED"}[gesture]
        if gesture == "draw" and self._tool == "erase":
            mode = "ERASE"

        # ── pinch bookkeeping + held-gesture actions ─────────────────────────
        clear_pinch = bool(lm) and pinch_distance(lm, 4, 8) < _PINCH_THRESH
        save_pinch  = bool(lm) and pinch_distance(lm, 4, 20) < _PINCH_THRESH
        if clear_pinch and not self._was_clear:
            self._clear_since = now
        elif not clear_pinch:
            self._clear_since = 0.0
        if save_pinch and not self._was_save:
            self._save_since = now
        elif not save_pinch:
            self._save_since = 0.0
        self._was_clear, self._was_save = clear_pinch, save_pinch

        if clear_pinch and self._clear_since and now - self._clear_since >= _PINCH_HOLD_S:
            self._canvas[:] = 0
            self._prev_tip = None
            self._clear_since = 0.0
            self._flash_msg, self._flash_until = "Canvas cleared", now + _FLASH_MS / 1000.0
        if save_pinch and self._save_since and now - self._save_since >= _PINCH_HOLD_S:
            self._save_since = 0.0
            self._flash_msg, self._flash_until = "Saved", now + _FLASH_MS / 1000.0
            self._say(self.save())

        if not (clear_pinch or save_pinch):
            self._apply_gesture(gesture, lm, w, h)

        return self._render(frame, lm, gesture, mode, now), mode

    def _apply_gesture(self, gesture: str, lm, w: int, h: int) -> None:
        if gesture == "draw" and lm:
            tip = (int(lm[_TIP["index"]][0] * w), int(lm[_TIP["index"]][1] * h))
            if self._tool == "erase":
                cv2.circle(self._canvas, tip, _TIP_ERASER_R, (0, 0, 0), -1)
            elif self._prev_tip is not None:
                cv2.line(self._canvas, self._prev_tip, tip,
                         self._color, _DRAW_THICK, cv2.LINE_AA)
            self._prev_tip = tip
        elif gesture == "hover" and lm:
            # no drawing — select a palette swatch under the index fingertip
            tip = (int(lm[_TIP["index"]][0] * w), int(lm[_TIP["index"]][1] * h))
            name = swatch_at(*tip)
            if name:
                self._select_swatch(name)
            self._prev_tip = None
        elif gesture == "erase" and lm:
            # big eraser follows the palm centre (middle-finger MCP, #9)
            cx, cy = int(lm[9][0] * w), int(lm[9][1] * h)
            cv2.circle(self._canvas, (cx, cy), _ERASER_R, (0, 0, 0), -1)
            self._prev_tip = None
        else:
            self._prev_tip = None

    def _select_swatch(self, name: str) -> None:
        self._selected = name
        if name == "Eraser":
            self._tool = "erase"
            return
        self._tool = "draw"
        for n, col in _PALETTE:
            if n == name:
                self._color = col
                break

    def _render(self, frame, lm, gesture: str, mode: str, now: float) -> np.ndarray:
        h, w = frame.shape[:2]

        # composite the persistent canvas over the live feed (masked add, so
        # strokes never darken the camera picture)
        drawn = cv2.inRange(self._canvas, (1, 1, 1), (255, 255, 255))
        strokes = cv2.bitwise_and(self._canvas, self._canvas, mask=drawn)
        base = cv2.bitwise_and(frame, frame, mask=cv2.bitwise_not(drawn))
        out = cv2.add(base, strokes)

        # ── palette strip ─────────────────────────────────────────────────────
        for i, (name, col) in enumerate(_PALETTE):
            x0 = _SWATCH_LEFT + i * (_SWATCH_W + _SWATCH_GAP)
            cv2.rectangle(out, (x0, _SWATCH_Y),
                          (x0 + _SWATCH_W, _SWATCH_Y + _SWATCH_H), col, -1)
            text_col = (0, 0, 0) if name in ("Yellow", "White") else (255, 255, 255)
            cv2.putText(out, name, (x0 + 8, _SWATCH_Y + _SWATCH_H // 2 + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_col, 2, cv2.LINE_AA)
            if name == self._selected:
                cv2.rectangle(out, (x0, _SWATCH_Y),
                              (x0 + _SWATCH_W, _SWATCH_Y + _SWATCH_H),
                              (255, 255, 255), 3)

        # ── cursors ───────────────────────────────────────────────────────────
        if gesture == "erase" and lm:
            cx, cy = int(lm[9][0] * w), int(lm[9][1] * h)
            cv2.circle(out, (cx, cy), _ERASER_R, (0, 0, 255), 2)
        elif gesture == "hover" and lm:
            tip = (int(lm[_TIP["index"]][0] * w), int(lm[_TIP["index"]][1] * h))
            cv2.circle(out, tip, _CURSOR_R, (255, 255, 255), 2)
        elif gesture == "draw" and lm:
            tip = (int(lm[_TIP["index"]][0] * w), int(lm[_TIP["index"]][1] * h))
            cv2.circle(out, tip, _CURSOR_R, self._color, 2)

        # ── mode + feedback text ──────────────────────────────────────────────
        mcol = {"DRAW": (0, 255, 0), "HOVER": (255, 255, 0),
                "ERASE": (0, 0, 255), "PAUSED": (180, 180, 180)}[mode]
        cv2.putText(out, mode, (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, mcol, 2, cv2.LINE_AA)
        if self._flash_msg and now < self._flash_until:
            (tw, _), _ = cv2.getTextSize(self._flash_msg, cv2.FONT_HERSHEY_SIMPLEX,
                                         1.0, 3)
            cv2.putText(out, self._flash_msg, (max(10, w // 2 - tw // 2), h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3, cv2.LINE_AA)
        hint = ""
        if self._clear_since and now - self._clear_since < _PINCH_HOLD_S:
            hint = "HOLD TO CLEAR"
        elif self._save_since and now - self._save_since < _PINCH_HOLD_S:
            hint = "HOLD TO SAVE"
        if hint:
            cv2.putText(out, hint, (12, h - 48), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 255), 2, cv2.LINE_AA)
        return out

    # ── main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        hands = None
        try:
            import mediapipe as mp  # noqa: C0415 — lazy: ~1 s import, heavy
            hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        except Exception:
            self._say("I couldn't start hand tracking.")
            self._cleanup()
            return

        try:
            while not self._exit.is_set():
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    time.sleep(0.03)      # camera hiccup — keep the loop alive
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                results = hands.process(rgb)
                lm = None
                if results.multi_hand_landmarks:
                    lm = [(p.x, p.y) for p in results.multi_hand_landmarks[0].landmark]
                out, _ = self.update(frame, lm)
                cv2.imshow(_WINDOW, out)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):       # manual fallback exit
                    break
        except Exception as e:
            self._log(f"[DesignMode] Loop error: {e}")
            self._say("Design mode hit an error and stopped.")
        finally:
            self._cleanup()
            if hands is not None:
                try:
                    hands.close()
                except Exception:
                    pass

    def _cleanup(self) -> None:
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        with self._lock:
            self._canvas = None
            self._prev_tip = None


# ── Module-level entry points (called by the command router) ──────────────────

_session: Optional[DesignModeSession] = None
_session_lock = threading.Lock()


def _mediapipe_available() -> bool:
    try:
        import mediapipe  # noqa: F401, C0415
        return True
    except Exception:
        return False


def start_design_mode(speak: Optional[Callable] = None, ui=None) -> str:
    """Open the webcam and launch the design-mode loop in a background thread.

    Returns a spoken-ready confirmation, or an error explanation that hands
    control back to the main assistant without crashing.
    """
    global _session
    with _session_lock:
        if _session is not None and _session.running:
            return "Design mode is already active."
        if not _CV2:
            return "Design mode needs OpenCV. Run: pip install opencv-python"
        if not _mediapipe_available():
            return "Design mode needs MediaPipe. Run: pip install mediapipe"
        cap = _open_camera()
        if cap is None:
            return "I couldn't access the camera."
        sess = DesignModeSession(cap=cap, speak=speak, ui=ui)
        if not sess.start():
            try:
                cap.release()
            except Exception:
                pass
            return "I couldn't start design mode."
        _session = sess
    return "Design mode activated. Show me your hand."


def stop_design_mode() -> str:
    """Signal the design-mode loop to exit; releases camera + window."""
    with _session_lock:
        if _session is None or not _session.running:
            return "Design mode is not active."
        _session.stop()
    return "Exiting design mode."


def save_drawing() -> str:
    """Voice-command path: save the active canvas and return a confirmation."""
    with _session_lock:
        sess = _session
    if sess is None or not sess.running:
        return "There is no active design mode to save."
    return sess.save()
