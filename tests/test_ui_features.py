"""
Unit tests for the UI/UX features added to ``ui.py``:

  1. ``_lerp_hex``        — hex colour interpolation used by the HUD state morph.
  2. ``_HistoryLineEdit`` — Up/Down command-history navigation in the input row.
  3. ``HudCanvas``        — mic VU meter (peak-hold + decay) and state-colour morph.
  4. ``HelpOverlay``      — the "What can I do?" quick-reference panel.

Strategy:
  - Runs fully headless: ``QT_QPA_PLATFORM=offscreen`` and a shared ``QApplication``.
  - Most widgets are exercised directly (no event loop, no timers firing) so
    assertions are deterministic; ``_step()`` is called by hand where the animation
    is tested.
  - Exception: ``TestHudCanvasLiveTimer`` deliberately pumps a real event loop
    (``QTest.qWait``) so the HUD's 16 ms animation timer fires live.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QTextEdit

import ui

# One shared QApplication for the whole module — PyQt6 allows only one per process.
_app = QApplication.instance() or QApplication([])


def _key(k: Qt.Key) -> QKeyEvent:
    """Build a bare key-press event for directly invoking keyPressEvent()."""
    return QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier)


# ═══════════════════════════════════════════════════════════════════════════════
# ui._lerp_hex — HUD state-colour interpolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestLerpHex:
    def test_endpoints(self):
        assert ui._lerp_hex("#000000", "#ffffff", 0.0) == "#000000"
        assert ui._lerp_hex("#000000", "#ffffff", 1.0) == "#ffffff"

    def test_midpoint_uses_bankers_rounding(self):
        # Python's round() is banker's rounding: 127.5 -> 128
        assert ui._lerp_hex("#000000", "#ffffff", 0.5) == "#808080"

    def test_mixed_channels(self):
        assert ui._lerp_hex("#ff0000", "#00ff00", 0.5) == "#808000"

    def test_same_colour_is_identity(self):
        assert ui._lerp_hex("#1a7fb5", "#1a7fb5", 0.37) == "#1a7fb5"

    def test_accepts_hex_without_hash(self):
        assert ui._lerp_hex("ffffff", "#000000", 0.5) == "#808080"


# ═══════════════════════════════════════════════════════════════════════════════
# ui._HistoryLineEdit — Up/Down command-history navigation
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoryLineEdit:
    @pytest.fixture
    def field(self):
        return ui._HistoryLineEdit()

    def test_push_appends_and_resets_position(self, field):
        field.push_history("open spotify")
        field.push_history("weather")
        assert field.history == ["open spotify", "weather"]
        assert field._hist_pos == 0

    def test_push_dedupes_consecutive_commands(self, field):
        field.push_history("weather")
        field.push_history("weather")          # repeated send -> ignored
        field.push_history("open spotify")
        field.push_history("weather")          # different from last -> kept
        assert field.history == ["weather", "open spotify", "weather"]

    def test_push_ignores_empty(self, field):
        field.push_history("")
        field.push_history("   ")
        assert field.history == []

    def test_history_capped_at_50(self, field):
        for i in range(60):
            field.push_history(f"cmd{i}")
        assert len(field.history) == 50
        assert field.history[0] == "cmd10"
        assert field.history[-1] == "cmd59"

    def test_up_shows_most_recent_first(self, field):
        for cmd in ("a", "b", "c"):
            field.push_history(cmd)
        field.setText("typing...")
        field.keyPressEvent(_key(Qt.Key.Key_Up))
        assert field.text() == "c"
        field.keyPressEvent(_key(Qt.Key.Key_Up))
        assert field.text() == "b"
        field.keyPressEvent(_key(Qt.Key.Key_Up))
        assert field.text() == "a"

    def test_up_stays_at_oldest_past_the_top(self, field):
        for cmd in ("a", "b"):
            field.push_history(cmd)
        field.keyPressEvent(_key(Qt.Key.Key_Up))      # b
        field.keyPressEvent(_key(Qt.Key.Key_Up))      # a
        field.keyPressEvent(_key(Qt.Key.Key_Up))      # still a (at the top)
        assert field.text() == "a"

    def test_down_restores_draft_after_navigating(self, field):
        for cmd in ("a", "b", "c"):
            field.push_history(cmd)
        field.setText("fresh draft")
        field.keyPressEvent(_key(Qt.Key.Key_Up))      # c
        field.keyPressEvent(_key(Qt.Key.Key_Up))      # b
        field.keyPressEvent(_key(Qt.Key.Key_Down))    # c
        assert field.text() == "c"
        field.keyPressEvent(_key(Qt.Key.Key_Down))    # back to the draft
        assert field.text() == "fresh draft"

    def test_down_is_a_noop_without_navigation(self, field):
        for cmd in ("a",):
            field.push_history(cmd)
        field.setText("untouched")
        field.keyPressEvent(_key(Qt.Key.Key_Down))
        assert field.text() == "untouched"
        assert field._hist_pos == 0

    def test_other_keys_fall_through(self, field):
        for cmd in ("a",):
            field.push_history(cmd)
        field.setText("untouched")
        field.keyPressEvent(_key(Qt.Key.Key_A))       # normal key -> default handling
        assert field.text() == "untouched"
        assert field._hist_pos == 0

    def test_up_is_a_noop_with_empty_history(self, field):
        field.setText("untouched")
        field.keyPressEvent(_key(Qt.Key.Key_Up))
        assert field.text() == "untouched"
        assert field._hist_pos == 0


# ═══════════════════════════════════════════════════════════════════════════════
# ui.HudCanvas — VU meter (peak-hold + decay) and state-colour morph
# ═══════════════════════════════════════════════════════════════════════════════


class TestHudCanvasVu:
    @staticmethod
    def _make_hud() -> ui.HudCanvas:
        """Build a HUD with its 16 ms animation timer stopped, so the tests are
        deterministic even if a future test runs an event loop (QTest.qWait etc.)."""
        hud = ui.HudCanvas("")
        hud._tmr.stop()
        return hud

    @pytest.fixture
    def hud(self):
        return self._make_hud()

    def test_set_vu_peak_holds(self, hud):
        hud.set_vu(0.8)
        assert hud._vu == 0.8
        hud.set_vu(0.2)                       # a quieter block must not drop the peak
        assert hud._vu == 0.8

    def test_set_vu_rises_on_louder_level(self, hud):
        hud.set_vu(0.4)
        hud.set_vu(0.9)
        assert hud._vu == 0.9

    def test_set_vu_clamps_to_unit_interval(self, hud):
        hud.set_vu(3.0)
        assert hud._vu == 1.0
        fresh = self._make_hud()
        fresh.set_vu(-0.5)
        assert fresh._vu == 0.0

    def test_step_decays_gradually(self, hud):
        hud.set_vu(0.8)
        hud._step()
        assert 0.0 < hud._vu < 0.8

    def test_step_eventually_floors_at_zero(self, hud):
        hud.set_vu(0.8)
        for _ in range(300):
            hud._step()
        assert hud._vu == 0.0

    def test_state_colour_morphs_toward_target(self, hud):
        assert hud._cur_hc == ui.C.PRI
        hud.state = "LISTENING"
        hud._step()
        assert hud._tgt_hc == ui.C.GREEN
        assert hud._cur_hc != ui.C.PRI          # moved toward the target
        assert hud._cur_hc.startswith("#") and len(hud._cur_hc) == 7

    def test_thinking_uses_accent2(self, hud):
        hud.state = "THINKING"
        hud._step()
        assert hud._tgt_hc == ui.C.ACC2

    def test_muted_overrides_other_states(self, hud):
        hud.state = "LISTENING"
        hud.muted = True
        hud._step()
        assert hud._tgt_hc == ui.C.MUTED_C


# ═══════════════════════════════════════════════════════════════════════════════
# HudCanvas with its 16 ms animation timer LIVE (real Qt event loop)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHudCanvasLiveTimer:
    """
    Verify the HUD animation loop holds up when its 16 ms QTimer actually fires.

    Uses ``QTest.qWait`` (no pytest-qt dependency) to pump the event loop so the
    timer's ``_step()`` runs live — including real paint events when shown. The
    ``live_hud`` fixture stops the timer in teardown so no live timer leaks into
    other tests (Qt timers are app-global, and any ``qWait`` would fire them).
    """

    @pytest.fixture
    def live_hud(self):
        """A HUD with its 16 ms animation timer RUNNING (stopped in teardown)."""
        hud = ui.HudCanvas("")
        yield hud
        hud._tmr.stop()

    def test_timer_fires_step_repeatedly(self, live_hud):
        t0 = live_hud._tick
        QTest.qWait(120)              # ~7 timer ticks at 16 ms
        assert live_hud._tick > t0

    def test_vu_decays_live_but_stays_in_range(self, live_hud):
        live_hud.set_vu(0.8)
        QTest.qWait(80)               # several ticks -> decayed but not floored
        assert 0.0 < live_hud._vu <= 0.8
        # Peak-hold still holds between ticks: a quieter block never drops it
        before = live_hud._vu
        live_hud.set_vu(0.2)
        live_hud.set_vu(0.2)
        assert live_hud._vu == before

    def test_state_morph_live(self, live_hud):
        start = live_hud._cur_hc
        live_hud.state = "LISTENING"
        QTest.qWait(80)
        assert live_hud._tgt_hc == ui.C.GREEN
        assert live_hud._cur_hc != start
        assert live_hud._cur_hc.startswith("#") and len(live_hud._cur_hc) == 7

    def test_paints_while_animating(self, live_hud):
        """Shown + resized so the live timer drives real paintEvent calls."""
        live_hud.resize(400, 400)
        live_hud.show()
        live_hud.state = "SPEAKING"
        live_hud.set_vu(0.9)
        QTest.qWait(120)              # live timer + real paint events
        assert live_hud._tick > 0
        assert live_hud.isVisible()

    def test_animation_no_crash_over_prolonged_state_changes(self, live_hud):
        live_hud.set_vu(1.0)
        for state in ("THINKING", "PROCESSING", "LISTENING"):
            live_hud.state = state
            QTest.qWait(60)
        live_hud.speaking = True
        QTest.qWait(60)
        live_hud.muted = True
        QTest.qWait(60)
        live_hud.muted = False
        live_hud.speaking = False
        assert live_hud._tick > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ui.HelpOverlay — the "What can I do?" quick-reference panel
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelpOverlay:
    @pytest.fixture
    def overlay(self):
        return ui.HelpOverlay("JARVIS")

    def test_contains_help_text(self, overlay):
        body = overlay.findChild(QTextEdit)
        assert body is not None
        text = body.toPlainText()
        assert "JARVIS is your local AI companion" in text
        assert "Open Spotify" in text
        assert "Ctrl+Alt+J" in text

    def test_ok_button_emits_done_and_hides(self, overlay):
        fired: list[bool] = []
        overlay.done.connect(lambda: fired.append(True))
        overlay.findChild(QPushButton).click()
        assert fired == [True]
        assert overlay.isHidden()

    @pytest.mark.parametrize("key", [Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter])
    def test_shortcut_keys_emit_done(self, overlay, key):
        fired: list[bool] = []
        overlay.done.connect(lambda: fired.append(True))
        overlay.keyPressEvent(_key(key))
        assert fired == [True]
        assert overlay.isHidden()

    def test_other_keys_do_not_emit_done(self, overlay):
        fired: list[bool] = []
        overlay.done.connect(lambda: fired.append(True))
        overlay.keyPressEvent(_key(Qt.Key.Key_A))
        assert fired == []
