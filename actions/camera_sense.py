"""Camera sensing engine — continuous, low-cost awareness of what the webcam sees.

The engine runs its own background thread so it never blocks the assistant.
Detection is deliberately lightweight (OpenCV + numpy only) so it stays
low-spec friendly:

  • motion      — inter-frame differencing (cheap, no model)
  • person      — HSV skin-tone presence heuristic (no bundled Haar model in
                  OpenCV 5 wheels, so we avoid cv2.data.haarcascades entirely)
  • ambient     — frame brightness → dark / dim / normal / bright
  • AI scene    — OPTIONAL: a caller-provided hook (e.g. Gemini vision) that
                  describes the room. Disabled by default; throttled hard.

The engine is camera-optional for testing: `inject_frame()` lets tests feed
synthetic frames without ever touching a real camera.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

try:
    import cv2
    _CV2 = True
except Exception:  # pragma: no cover - env-specific
    cv2 = None  # type: ignore[assignment]
    _CV2 = False

# ── Tuning (sane low-spec defaults; overridable via constructor) ─────────────

_MOTION_THRESHOLD  = 0.004   # fraction of pixels changed → motion
_PERSON_RATIO      = 0.015   # skin-tone pixel fraction in centre region → person
_PERSON_ABSENT_S   = 45.0    # sustained stillness before person is marked absent
_PERSON_DEBOUNCE_S = 60.0    # min gap between two "person arrived" events
_DARK_LUX          = 30.0    # mean gray < this → "dark"
_DIM_LUX           = 70.0    # mean gray < this → "dim"
_BRIGHT_LUX        = 200.0   # mean gray > this → "bright"
_SKIN_LOWER        = (0, 40, 50)     # HSV lower bound (H may wrap)
_SKIN_UPPER        = (25, 160, 255)  # HSV upper bound (H may wrap)
_SKIN_HIGH_H       = (165, 40, 50)   # wrap-around hue band upper
_SKIN_HIGH_V       = (180, 160, 255)


# ── Public types ──────────────────────────────────────────────────────────────


@dataclass
class SensorSnapshot:
    """What the camera sensed on the latest poll."""

    motion: bool = False
    person: bool = False
    ambient: str = "normal"          # dark | dim | normal | bright
    scene: str = ""                  # last AI scene description (if enabled)
    scene_ts: float = 0.0            # monotonic time of last scene analysis
    privacy: bool = False            # privacy mode — local detection only
    changed: bool = False            # True only on state transitions

    def label(self) -> str:
        """Short HUD label for this snapshot (empty when nothing to report).

        Privacy mode reports a single PRIVATE marker — detection events still
        fire, but no scene/AI flag appears because nothing is sent off-device.
        """
        if self.privacy:
            return "PRIVATE"
        parts: list[str] = []
        if self.person:
            parts.append("PERSON")
        if self.motion and not self.person:
            parts.append("MOTION")
        # ambient is secondary — only shown when nothing more interesting
        if not self.person and not self.motion and self.ambient in ("dark", "dim"):
            parts.append(self.ambient.upper())
        if self.scene:
            parts.append("SCENE")
        return "·".join(parts)

    def event(self) -> str | None:
        """Human event string for the activity log, or None for no-op polls."""
        if self.person:
            return "SENS: Person present at camera (privacy mode)" if self.privacy \
                else "SENS: Person present at camera"
        if self.motion:
            return "SENS: Motion detected at camera"
        if self.ambient == "dark":
            return "SENS: Room dark"
        return None


# ── Pure detection helpers (unit-testable, no camera required) ────────────────


def _gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _skin_ratio(frame: np.ndarray) -> float:
    """Fraction of centre-region pixels that look like human skin (HSV)."""
    if frame.ndim != 3 or frame.shape[2] < 3:
        return 0.0
    h, w = frame.shape[:2]
    cy, cx = h // 2, w // 2
    rh, rw = max(1, h // 2), max(1, w // 2)
    region = frame[cy - rh:cy + rh, cx - rw:cx + rw]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _SKIN_LOWER, _SKIN_UPPER)
    mask |= cv2.inRange(hsv, _SKIN_HIGH_H, _SKIN_HIGH_V)
    return float(np.count_nonzero(mask)) / max(1, mask.size)


def _motion_ratio(prev: np.ndarray, cur: np.ndarray) -> float:
    """Fraction of pixels that changed meaningfully between two grayscale frames."""
    if prev is None or prev.shape != cur.shape:
        return 0.0
    diff = cv2.absdiff(prev, cur)
    _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    return float(np.count_nonzero(mask)) / max(1, mask.size)


def _ambient_label(mean: float) -> str:
    if mean < _DARK_LUX:
        return "dark"
    if mean < _DIM_LUX:
        return "dim"
    if mean > _BRIGHT_LUX:
        return "bright"
    return "normal"


def sense_interval(cam_tier: str) -> float:
    """Adaptive sensing cadence, mirroring the camera stream tiers (seconds)."""
    if cam_tier == "hidden":
        return 5.0
    if cam_tier == "background":
        return 2.5
    return 1.0


# ── Engine ────────────────────────────────────────────────────────────────────


class CameraSenseEngine:
    """Runs a background loop that senses the webcam at an adaptive cadence.

    Callbacks (all invoked from the engine thread — keep them cheap or forward
    through a thread-safe queue/signal):
      on_state(snapshot)  — every meaningful state transition
      on_event(text)      — human-readable activity-log lines
    """

    def __init__(
        self,
        on_state: Optional[Callable[[SensorSnapshot], None]] = None,
        on_event: Optional[Callable[[str], None]] = None,
        scene_analyzer: Optional[Callable[[], str]] = None,
        scene_cooldown_s: float = 300.0,     # 5 min between AI analyses
        scene_min_interval_s: float = 90.0,  # earliest re-check after first
        privacy: bool = False,               # local detection only, no AI/network
        cam_index: int | None = None,
        motion_threshold: float = _MOTION_THRESHOLD,
        person_ratio: float = _PERSON_RATIO,
        person_absent_s: float = _PERSON_ABSENT_S,
        person_debounce_s: float = _PERSON_DEBOUNCE_S,
        **_: object,                         # tolerate future tuning kwargs
    ) -> None:
        self._on_state = on_state
        self._on_event = on_event
        self._analyzer = scene_analyzer
        self._privacy = bool(privacy)
        self._scene_cd = float(scene_cooldown_s)
        self._scene_min = float(scene_min_interval_s)
        self._motion_thr = float(motion_threshold)
        self._person_ratio = float(person_ratio)
        self._absent_s = float(person_absent_s)
        self._debounce_s = float(person_debounce_s)

        self._cam_index: int | None = cam_index
        self._interval = 1.0
        self._tier = "active"
        self._stop = threading.Event()
        self._wake = threading.Event()

        # state
        self._prev_gray: Optional[np.ndarray] = None
        self._last_motion_ts = 0.0
        self._person_present = False
        self._person_since = 0.0
        self._last_person_event = 0.0
        self._last_scene_ts = 0.0
        self._snap = SensorSnapshot(privacy=self._privacy)
        self._injected: Optional[np.ndarray] = None
        self._inject_lock = threading.Lock()

        self._cap: object | None = None
        self._thread: Optional[threading.Thread] = None
        self._last_frame: Optional[np.ndarray] = None   # latest decoded frame (AI hook)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def snapshot(self) -> SensorSnapshot:
        return self._snap

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="camera-sense")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._close_camera()

    def set_tier(self, cam_tier: str) -> None:
        """Update the adaptive cadence from the window state tier."""
        self._tier = cam_tier
        self.set_interval(sense_interval(cam_tier))

    def set_interval(self, seconds: float) -> None:
        self._interval = max(0.5, float(seconds))
        self._wake.set()

    def inject_frame(self, frame: Optional[np.ndarray]) -> None:
        """Feed a synthetic frame (tests / debug). None resumes real camera."""
        with self._inject_lock:
            self._injected = frame

    # ── core detection ────────────────────────────────────────────────────────

    def detect(self, frame: np.ndarray) -> SensorSnapshot:
        """Run all detectors on one frame; returns a (possibly unchanged) snapshot."""
        snap = self._snap
        changed = False

        g = _gray(frame)
        motion = False
        if self._prev_gray is not None:
            motion = _motion_ratio(self._prev_gray, g) >= self._motion_thr
        self._prev_gray = g.copy()
        now = time.monotonic()
        if motion:
            self._last_motion_ts = now

        person = _skin_ratio(frame) >= self._person_ratio
        if person and not self._person_present:
            self._person_present = True
            self._person_since = now
        elif not person and self._person_present:
            if now - self._person_since >= self._absent_s:
                self._person_present = False

        ambient = _ambient_label(float(g.mean()))
        if motion != snap.motion or person != snap.person or ambient != snap.ambient:
            changed = True
        snap.motion = motion
        snap.person = self._person_present
        snap.ambient = ambient

        # debounced "person arrived" event
        if self._person_present and not self._snap.person and \
                now - self._last_person_event >= self._debounce_s:
            self._last_person_event = now
            changed = True
        elif not self._person_present:
            self._last_person_event = 0.0

        snap.changed = changed
        if changed:
            ev = snap.event()
            if ev and self._on_event:
                self._on_event(ev)
        return snap

    def _maybe_scene_analysis(self, snap: SensorSnapshot) -> None:
        """Run the optional AI scene hook, heavily throttled.

        Privacy mode short-circuits here: no scene hook is ever invoked, so no
        frame leaves the device — only local detection events are reported.
        """
        if self._privacy or self._analyzer is None or not snap.person:
            return
        now = time.monotonic()
        if now - self._last_scene_ts < (self._scene_min if self._last_scene_ts else self._scene_cd):
            return
        self._last_scene_ts = now
        try:
            desc = self._analyzer(self._last_frame)
            if desc and str(desc).strip():
                snap.scene = str(desc).strip()[:240]
                snap.changed = True
                if self._on_event:
                    self._on_event(f"SENS: Scene — {snap.scene}")
        except Exception as e:
            if self._on_event:
                self._on_event(f"SENS: Scene analysis failed: {e}")

    # ── camera plumbing ───────────────────────────────────────────────────────

    def _open_camera(self) -> bool:
        if not _CV2:
            return False
        if self._cap is not None:
            return True
        idx = self._cam_index
        if idx is None:
            idx = 0
            try:
                from utils import load_config as _load_cfg
                idx = int(_load_cfg().get("camera_index", 0))
            except Exception:
                pass
        try:
            backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        except Exception:
            backend = 0
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return False
        for _ in range(5):
            cap.read()          # warm-up
        self._cap = cap
        return True

    def _close_camera(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()  # type: ignore[union-attr]
            except Exception:
                pass
            self._cap = None

    def _read_frame(self) -> Optional[np.ndarray]:
        with self._inject_lock:
            if self._injected is not None:
                return self._injected
        if not _CV2 or not self._open_camera():
            return None
        ret, frame = self._cap.read()  # type: ignore[union-attr]
        if ret and frame is not None:
            self._last_frame = frame.copy()
            return frame
        return None

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                frame = self._read_frame()
                if frame is None:
                    failures += 1
                    if failures >= 3:       # no camera — give up quietly
                        if self._on_event:
                            self._on_event("SENS: Camera unavailable — sensing paused.")
                        return
                else:
                    failures = 0
                    snap = self.detect(frame)
                    if snap.changed:
                        self._snap = snap
                        if self._on_state:
                            self._on_state(snap)
                    self._maybe_scene_analysis(snap)
            except Exception:
                # e.g. device unplugged mid-read — treat as a failed frame
                failures += 1
                if failures >= 3:
                    if self._on_event:
                        self._on_event("SENS: Camera error — sensing paused.")
                    return
            self._wake.wait(self._interval)
            self._wake.clear()
        self._close_camera()
