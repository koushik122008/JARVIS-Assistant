"""Tests for 'camera feed only opens when the user says open camera' (main.py).

Covers:
  • an explicit `open_camera` tool is declared (and `screen_process` no longer
    promises a persistent live view in its description)
  • the `open_camera` tool handler starts the live stream
  • the `close_camera` tool handler stops the live stream
  • `screen_process` with angle="camera" captures a single still for vision
    analysis but does NOT open the live feed

The handlers are exercised directly on a bare instance (no __init__), with all
dependencies mocked.
"""

from __future__ import annotations

import asyncio
import sys
from unittest import mock

import pytest

sys.path.insert(0, ".")

import main  # noqa: E402


class _Fc:
    """Minimal stand-in for a function-call object passed to _execute_tool."""

    def __init__(self, name: str, args: dict | None = None, fid: str = "1"):
        self.name = name
        self.args = args or {}
        self.id = fid


def _make_ui():
    ui = mock.MagicMock()
    ui.muted = False
    return ui


def _make_obj(ui):
    obj = main.JarvisLive.__new__(main.JarvisLive)
    obj.ui = ui
    obj._vision_busy = False
    obj._vision_last_time = 0.0
    obj._pending_vision = None
    return obj


def _run_tool(obj, fc) -> str:
    fr = asyncio.run(obj._execute_tool(fc))
    return fr.response["result"]


# ── Tool declarations ─────────────────────────────────────────────────────────


def test_open_camera_tool_is_declared():
    names = {t["name"] for t in main.TOOL_DECLARATIONS}
    assert "open_camera" in names
    assert "close_camera" in names
    assert "screen_process" in names


def test_screen_process_description_no_longer_opens_feed():
    decl = next(t for t in main.TOOL_DECLARATIONS if t["name"] == "screen_process")
    desc = decl["description"]
    assert "single still" in desc
    assert "does NOT open the live feed" in desc
    assert "open_camera" in desc
    assert "live view stays open" not in desc


# ── open_camera / close_camera handlers ───────────────────────────────────────


def test_open_camera_handler_starts_live_stream():
    ui = _make_ui()
    obj = _make_obj(ui)
    result = _run_tool(obj, _Fc("open_camera"))
    ui.start_camera_stream.assert_called_once()
    ui.stop_camera_stream.assert_not_called()
    assert "Camera feed opened" in result


def test_close_camera_handler_stops_live_stream():
    ui = _make_ui()
    obj = _make_obj(ui)
    result = _run_tool(obj, _Fc("close_camera"))
    ui.stop_camera_stream.assert_called_once()
    ui.start_camera_stream.assert_not_called()
    assert "Camera closed" in result


# ── screen_process no longer opens the live feed ──────────────────────────────


def test_screen_process_camera_captures_still_without_feed():
    ui = _make_ui()
    obj = _make_obj(ui)
    with mock.patch(
        "actions.screen_processor._capture_camera",
        return_value=(b"fake-jpeg-bytes", "image/jpeg"),
    ):
        result = _run_tool(
            obj,
            _Fc("screen_process", {"angle": "camera", "text": "what do you see?"}),
        )
    # A vision still is produced for analysis…
    assert "[VISION_ACTIVE]" in result
    assert "Camera" in result
    # …but the persistent live feed must NOT open.
    ui.start_camera_stream.assert_not_called()
    ui.stop_camera_stream.assert_not_called()


# ── idempotent start (repeated open_camera must not spawn a second thread) ────


def _make_cam_win():
    import threading
    import ui as ui_mod
    win = ui_mod.MainWindow.__new__(ui_mod.MainWindow)
    win._cam_stop = threading.Event()
    return win, ui_mod


def test_start_camera_stream_is_idempotent():
    win, ui_mod = _make_cam_win()
    win._cam_stop.set()   # stopped state → first call may start
    with mock.patch.object(win, "_cam_loop"), \
         mock.patch.object(ui_mod.MainWindow, "_cam_stream_sig", mock.MagicMock()), \
         mock.patch.object(ui_mod.threading, "Thread") as thread_cls:
        win.start_camera_stream()
        win.start_camera_stream()   # second call while running
        win.start_camera_stream()   # third call while running
    assert thread_cls.call_count == 1                  # only ONE thread created
    assert thread_cls.return_value.start.call_count == 1


def test_start_camera_stream_restarts_after_stop():
    win, ui_mod = _make_cam_win()
    win._cam_stop.set()   # stopped state
    with mock.patch.object(win, "_cam_loop"), \
         mock.patch.object(ui_mod.MainWindow, "_cam_stream_sig", mock.MagicMock()), \
         mock.patch.object(ui_mod.threading, "Thread") as thread_cls:
        win.start_camera_stream()
    assert thread_cls.call_count == 1
    assert thread_cls.return_value.start.call_count == 1


def test_screen_process_screen_angle_still_works():
    ui = _make_ui()
    obj = _make_obj(ui)
    with mock.patch(
        "actions.screen_processor._capture_screen",
        return_value=(b"fake-screen-bytes", "image/png"),
    ):
        result = _run_tool(obj, _Fc("screen_process", {"angle": "screen"}))
    assert "[VISION_ACTIVE]" in result
    assert "Screen" in result
    ui.start_camera_stream.assert_not_called()
