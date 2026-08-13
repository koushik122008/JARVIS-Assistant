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
# Adaptive FPS — HUD animation rate throttled while idle/sleeping (low-spec)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdaptiveFps:
    def test_hud_speeds_up_when_active_and_slows_when_sleeping(self):
        hud = ui.HudCanvas("")
        hud._tmr.stop()
        hud.state = "SLEEPING"
        hud._step()
        assert hud._tmr.interval() == 250     # nearly frozen while sleeping
        hud.state = "SPEAKING"
        hud._step()
        assert hud._tmr.interval() == 16      # full 60 fps while animating
        hud.state = "LISTENING"
        hud._step()
        assert hud._tmr.interval() == 33      # moderate rate at rest

    def test_radar_throttles_when_sleeping(self):
        radar = ui.RadarWidget()
        radar._tmr.stop()
        radar.set_state("SLEEPING")
        radar._step()
        assert radar._tmr.interval() == 250
        radar.set_state("THINKING")
        radar._step()
        assert radar._tmr.interval() == 50

    def test_scan_line_throttles_when_sleeping(self):
        scan = ui.ScanLineOverlay()
        scan._tmr.stop()
        scan.set_state("SLEEPING")
        scan._step()
        assert scan._tmr.interval() == 250
        scan.set_state("PROCESSING")
        scan._step()
        assert scan._tmr.interval() == 30


# ═══════════════════════════════════════════════════════════════════════════════
# ui._camera_tier — camera stream adaptive capture rate (low-spec)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCameraTier:
    def test_active_is_full_rate_full_quality(self):
        interval, quality = ui._camera_tier("active")
        assert interval == 0.033
        assert quality == 65

    def test_background_throttles_rate_and_quality(self):
        interval, quality = ui._camera_tier("background")
        assert interval == 0.1          # 10 fps instead of 30
        assert quality == 55

    def test_hidden_disables_capture_entirely(self):
        interval, quality = ui._camera_tier("hidden")
        assert quality < 0              # grab-only warm keep, no decode/encode
        assert interval > 0.1

    def test_unknown_state_defaults_to_full_rate(self):
        interval, quality = ui._camera_tier("")
        assert interval == 0.033
        assert quality == 65


# ═══════════════════════════════════════════════════════════════════════════════
# ui._perf_badge_text — HUD adaptive-FPS indicator (camera tier + dashboard link)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerfBadgeText:
    def test_camera_off_dashboard_na_is_dim(self):
        label, col = ui._perf_badge_text("active", False, None, 1.5)
        assert label == "◈ CAM OFF · DASH N/A · POLL 1.5S"
        assert col == ui.C.TEXT_DIM

    def test_camera_off_dashboard_idle_is_amber(self):
        label, col = ui._perf_badge_text("active", False, False, 1.5)
        assert label == "◈ CAM OFF · DASH IDLE · POLL 1.5S"
        assert col == ui.C.ACC2

    def test_full_speed_is_green(self):
        label, col = ui._perf_badge_text("active", True, True, 1.5)
        assert label == "◈ CAM 30FPS · DASH LIVE · POLL 1.5S"
        assert col == ui.C.GREEN

    def test_camera_throttled_is_amber(self):
        label, col = ui._perf_badge_text("background", True, True, 5.0)
        assert label == "◈ CAM 10FPS · DASH LIVE · POLL 5S"
        assert col == ui.C.ACC2

    def test_camera_warm_when_hidden(self):
        label, col = ui._perf_badge_text("hidden", True, False, 15.0)
        assert label == "◈ CAM WARM · DASH IDLE · POLL 15S"
        assert col == ui.C.ACC2

    def test_unknown_camera_tier_falls_back(self):
        label, _ = ui._perf_badge_text("weird", True, True, 1.5)
        assert label == "◈ CAM -- · DASH LIVE · POLL 1.5S"

    def test_throttled_poll_turns_full_speed_amber(self):
        # Camera + dashboard at full speed, but the SYS MONITOR poll is slowed
        # (collapsed panel) — throttling is engaged, so amber not green.
        label, col = ui._perf_badge_text("active", True, True, 15.0)
        assert label == "◈ CAM 30FPS · DASH LIVE · POLL 15S"
        assert col == ui.C.ACC2


class TestPerfBadgeWiring:
    """Badge overlay + dashboard-state signal: dedup and show/hide behaviour."""

    @pytest.fixture
    def win(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        w = ui.MainWindow("")
        yield w
        w._perf_badge.deleteLater()
        w.deleteLater()

    def test_set_dashboard_state_emits_only_on_change(self, win):
        received = []
        win._dash_sig.connect(received.append)  # same-thread: direct connection
        win.set_dashboard_state(True)
        win.set_dashboard_state(True)   # no change → no second emit
        win.set_dashboard_state(False)
        win.set_dashboard_state(False)  # no change → no second emit
        assert received == [True, False]
        # Disabling the server clears the tracked state (None)
        win.set_dashboard_state(None)
        assert win._dash_live is None

    def test_badge_stays_visible_as_sensing_toggle(self, win):
        win.show()
        win._cam_on = False
        win._dash_live = None
        win._refresh_perf_badge()
        # The chip doubles as the camera-sensing toggle — it stays visible with
        # a neutral SENSE OFF marker so sensing can always be clicked back on.
        assert win._perf_badge.isVisible()
        assert "SENSE OFF" in win._perf_badge.text()
        # Camera on → chip appears with the camera tier
        win._cam_on = True
        win._cam_win_state = "background"
        win._refresh_perf_badge()
        assert win._perf_badge.isVisible()
        # Camera off but dashboard server live → chip stays for link status
        win._cam_on = False
        win._dash_live = True
        win._refresh_perf_badge()
        assert win._perf_badge.isVisible()
        win.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Adaptive psutil throttling — _metrics_interval tiers + _SysMetrics.set_interval
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetricsInterval:
    def test_full_cadence_when_panel_visible_and_window_active(self):
        assert ui._metrics_interval("active", True) == 1.5

    def test_background_window_slows_to_five_seconds(self):
        assert ui._metrics_interval("background", True) == 5.0

    def test_hidden_window_slows_to_fifteen_seconds(self):
        assert ui._metrics_interval("hidden", True) == 15.0

    def test_collapsed_panel_slows_even_while_window_active(self):
        assert ui._metrics_interval("active", False) == 15.0


class TestSysMetricsSetInterval:
    def test_set_interval_clamps_and_wakes(self):
        import threading
        s = object.__new__(ui._SysMetrics)   # no worker thread or psutil needed
        s._interval = 1.5
        s._wake = threading.Event()
        s.set_interval(0.05)                 # clamped up — no busy-loop risk
        assert s._interval == 0.5
        assert s._wake.is_set()
        s._wake.clear()
        s.set_interval(10.0)
        assert s._interval == 10.0
        assert s._wake.is_set()


class TestMetricsToggleWiring:
    """Collapsing the SYS MONITOR column hides the gauges and slows the psutil
    poll loop + metrics UI timer to the slowest tier."""

    @pytest.fixture
    def saved_collapsed(self):
        from memory import config_manager as cm
        snap = cm.get_metrics_panel_collapsed()
        yield
        cm.save_metrics_panel_collapsed(snap)

    @pytest.fixture
    def win(self, saved_collapsed):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        w = ui.MainWindow("")
        w.show()
        w._cam_win_state = "active"      # deterministic tier for the assertions
        w._apply_metrics_interval()
        yield w
        w._perf_badge.deleteLater()
        w.deleteLater()

    def test_toggle_collapses_box_and_slows_polling(self, win):
        assert win._left_metrics_visible is True
        assert win._metric_tmr.interval() == 2000
        win._metrics_toggle.click()
        assert win._left_metrics_visible is False
        assert not win._left_metrics_box.isVisible()
        assert win._metric_tmr.interval() == 15000
        assert "POLL 15S" in win._perf_badge.text()   # chip follows the tier
        win._metrics_toggle.click()
        assert win._left_metrics_visible is True
        assert win._left_metrics_box.isVisible()
        assert win._metric_tmr.interval() == 2000
        assert "POLL 1.5S" in win._perf_badge.text()

    def test_toggle_persists_collapsed_state(self, win, saved_collapsed):
        from memory import config_manager as cm
        win._metrics_toggle.click()
        assert cm.get_metrics_panel_collapsed() is True
        win._metrics_toggle.click()
        assert cm.get_metrics_panel_collapsed() is False


class TestMetricsPanelPersistence:
    """The SYS MONITOR collapsed state survives restarts via the config manager."""

    @pytest.fixture
    def saved_collapsed(self):
        from memory import config_manager as cm
        snap = cm.get_metrics_panel_collapsed()
        yield
        cm.save_metrics_panel_collapsed(snap)

    def test_config_roundtrip(self, saved_collapsed):
        from memory import config_manager as cm
        cm.save_metrics_panel_collapsed(True)
        assert cm.get_metrics_panel_collapsed() is True
        cm.save_metrics_panel_collapsed(False)
        assert cm.get_metrics_panel_collapsed() is False

    def test_window_starts_collapsed_from_config(self, saved_collapsed):
        from memory import config_manager as cm
        from PyQt6.QtWidgets import QApplication
        cm.save_metrics_panel_collapsed(True)
        app = QApplication.instance() or QApplication([])
        w = ui.MainWindow("")
        try:
            w.show()
            assert w._left_metrics_visible is False
            assert not w._left_metrics_box.isVisible()   # meaningful: window shown
            assert w._metrics_toggle.text() == "◈ SYS MONITOR  ▸"
            assert w._metric_tmr.interval() == 15000   # slow tier from the start
        finally:
            w._perf_badge.deleteLater()
            w.deleteLater()


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOG pause/resume — LogWidget typewriter + collapse toggle
# ═══════════════════════════════════════════════════════════════════════════════


class TestLogWidgetPauseResume:
    @pytest.fixture
    def log(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        l = ui.LogWidget()
        yield l
        l.deleteLater()

    def test_pause_freezes_typewriter_and_queues_messages(self, log):
        log.pause()
        assert log._paused is True
        assert not log._tmr.isActive()
        # New messages queue while paused, but typing does not start.
        log.append_log("hello")
        assert log._queue == ["hello"]
        assert log._typing is False

    def test_resume_restarts_typing_from_queue(self, log):
        log.pause()
        log.append_log("hello")
        log.resume()
        assert log._paused is False
        assert log._typing is True           # _next() popped the queue, armed the timer
        assert log._queue == []

    def test_step_is_a_noop_while_paused(self, log):
        log.append_log("hello")             # starts typing synchronously
        assert log._typing is True
        log.pause()
        pos = log._pos
        log._step()
        assert log._pos == pos               # frozen mid-message
        assert not log._tmr.isActive()
        log.resume()
        assert log._paused is False

    def test_resume_between_messages_leaves_gap_to_pending_singleshot(self, log):
        # Simulate the 20 ms gap: a message is fully typed, _typing is still
        # True, and the next message is queued. resume() must NOT restart the
        # typewriter here — the pending singleShot advances exactly once.
        log._text = "hi"
        log._pos = 2
        log._typing = True
        log._queue = ["next"]
        log._paused = True
        log.resume()
        assert log._paused is False
        assert log._typing is True           # untouched — singleShot advances
        assert not log._tmr.isActive()       # timer not restarted (no double \n)


class TestLogToggleWiring:
    """Collapsing the ACTIVITY LOG section hides the log, pauses its typewriter
    and stops the header clock; expanding restores all three."""

    @pytest.fixture
    def win(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        w = ui.MainWindow("")
        w.show()
        yield w
        w._perf_badge.deleteLater()
        w.deleteLater()

    def test_toggle_collapses_log_and_pauses_refreshes(self, win):
        assert win._log_panel_visible is True
        assert win._clock_tmr.isActive()
        win._log_toggle.click()
        assert win._log_panel_visible is False
        assert not win._log.isVisible()
        assert win._log._paused is True
        assert not win._log._tmr.isActive()
        assert not win._clock_tmr.isActive()
        assert win._log_toggle.text() == "▸ ACTIVITY LOG"
        win._log_toggle.click()
        assert win._log_panel_visible is True
        assert win._log.isVisible()
        assert win._log._paused is False
        assert win._clock_tmr.isActive()
        assert win._log_toggle.text() == "▾ ACTIVITY LOG"


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
