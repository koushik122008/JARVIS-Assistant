"""
Regression tests for previously-broken code paths.

These cover the crash-level (F821 undefined-name) bugs found by the ruff pass:

  1. computer_control._focus_window  — used ``subprocess`` without ever importing it
     (every focus_window action crashed on all OSes).
  2. screen_processor._compress      — referenced ``_IMG_MAX_W`` / ``_IMG_MAX_H`` /
     ``_JPEG_Q``, which never existed (the module imports ``IMG_MAX_W``, ``IMG_MAX_H``,
     ``JPEG_Q``). The screen + webcam vision feature was completely broken.
  3. dev_agent._get_model / _plan_project — ``_get_model()`` was called but never
     defined, so the whole build-a-project feature crashed on first use.

Strategy:
  - No real OS interaction: ``_get_os()`` and ``subprocess`` are patched.
  - No API calls: ``new_gemini_client`` / ``_get_model`` are patched.
  - ``screen_processor._compress`` is exercised with real in-memory images.
"""

from __future__ import annotations

import contextlib
import io
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# Module-level guards — the exact F821 bugs
# ═══════════════════════════════════════════════════════════════════════════════


def test_computer_control_imports_subprocess():
    """F821 regression: 'subprocess' was used by _focus_window but never imported."""
    import actions.computer_control as cc
    assert hasattr(cc, "subprocess")


def test_screen_processor_imports_vision_constants():
    """F821 regression: code referenced _IMG_MAX_W etc. — only IMG_MAX_W exist."""
    import actions.screen_processor as sp
    assert hasattr(sp, "IMG_MAX_W")
    assert hasattr(sp, "IMG_MAX_H")
    assert hasattr(sp, "JPEG_Q")
    # The underscore-prefixed names must NOT be the ones used.
    assert not hasattr(sp, "_IMG_MAX_W")
    assert not hasattr(sp, "_JPEG_Q")


def test_screen_processor_cv2_constant_not_corrupted():
    """Regression: the '_JPEG_Q' rename accidentally corrupted cv2.IMWRITE_JPEG_QUALITY."""
    import inspect

    import actions.screen_processor as sp
    # The module no longer references the corrupted token anywhere.
    source = inspect.getsource(sp)
    assert "IMWRITEJPEG_QUALITY" not in source
    assert "IMWRITE_JPEG_QUALITY" in source


def test_dev_agent_defines_get_model():
    """F821 regression: _get_model() was called by _plan_project but never defined."""
    import actions.dev_agent as da
    assert hasattr(da, "_get_model")
    assert callable(da._get_model)


# ═══════════════════════════════════════════════════════════════════════════════
# computer_control._focus_window
# ═══════════════════════════════════════════════════════════════════════════════


@contextlib.contextmanager
def _focus_env(os_name: str):
    """Patch OS detection, subprocess and sleep so _focus_window runs offline."""
    import actions.computer_control as cc
    with (
        patch.object(cc, "_get_os", return_value=os_name),
        patch.object(cc, "subprocess") as mock_sub,
        patch.object(cc.time, "sleep"),
    ):
        yield cc, mock_sub


class TestFocusWindow:
    @pytest.mark.parametrize("os_name", ["windows", "mac", "linux"])
    def test_all_branches_use_argument_lists_not_shell(self, os_name):
        """The security fix: every subprocess call must use a list, never shell=True."""
        with _focus_env(os_name) as (cc, mock_sub):
            mock_sub.run.return_value = MagicMock(returncode=0)
            cc._focus_window("Some Window")
        assert mock_sub.run.called
        for call in mock_sub.run.call_args_list:
            args, kwargs = call
            assert isinstance(args[0], list), f"expected list argv, got {args!r}"
            assert "shell" not in kwargs, f"shell must not be set: {kwargs!r}"

    def test_windows_uses_powershell_appactivate(self):
        with _focus_env("windows") as (cc, mock_sub):
            mock_sub.run.return_value = MagicMock()
            result = cc._focus_window("Notepad")
        assert result == "Focused window: Notepad"
        (args, _) = mock_sub.run.call_args
        assert args[0][0] == "powershell"
        assert "AppActivate" in args[0][-1]

    def test_windows_failure_returns_message(self):
        with _focus_env("windows") as (cc, mock_sub):
            mock_sub.run.side_effect = RuntimeError("denied")
            result = cc._focus_window("Notepad")
        assert result == "focus_window (Windows) failed: denied"

    def test_mac_uses_osascript(self):
        with _focus_env("mac") as (cc, mock_sub):
            mock_sub.run.return_value = MagicMock()
            result = cc._focus_window("Finder")
        assert result == "Focused window: Finder"
        (args, _) = mock_sub.run.call_args
        assert args[0][0] == "osascript"
        assert "System Events" in args[0][-1]

    def test_linux_uses_wmctrl_on_success(self):
        with _focus_env("linux") as (cc, mock_sub):
            mock_sub.run.return_value = MagicMock(returncode=0)
            result = cc._focus_window("Browser")
        assert result == "Focused window: Browser"
        (args, _) = mock_sub.run.call_args
        assert args[0][0] == "wmctrl"
        assert args[0][1] == "-a"

    def test_linux_falls_back_to_xdotool_when_wmctrl_fails(self):
        with _focus_env("linux") as (cc, mock_sub):
            mock_sub.run.return_value = MagicMock(returncode=1)
            result = cc._focus_window("Browser")
        assert result == "Focused window: Browser"
        commands = [c.args[0] for c in mock_sub.run.call_args_list]
        assert commands[0][0] == "wmctrl"
        assert commands[1][0] == "xdotool"

    def test_linux_returns_help_when_no_tools_installed(self):
        with _focus_env("linux") as (cc, mock_sub):
            mock_sub.run.side_effect = [FileNotFoundError, FileNotFoundError]
            result = cc._focus_window("Browser")
        assert result == "focus_window (Linux) requires wmctrl or xdotool"

    def test_linux_xdotool_failure_returns_message(self):
        with _focus_env("linux") as (cc, mock_sub):
            mock_sub.run.side_effect = [MagicMock(returncode=1), RuntimeError("boom")]
            result = cc._focus_window("Browser")
        assert result == "focus_window (Linux) failed: boom"

    def test_unknown_os_returns_message(self):
        with _focus_env("plan9") as (cc, mock_sub):
            result = cc._focus_window("anything")
        assert result == "focus_window: unknown OS 'plan9'"
        mock_sub.run.assert_not_called()


class TestScreenFindRegression:
    def test_screen_find_returns_none_without_api_key(self):
        """Regression: _screen_find used the undefined _get_api_key() — it must
        now resolve get_api_key() and degrade gracefully when no key is set."""
        import actions.computer_control as cc
        with patch.object(cc, "get_api_key", return_value=""):
            assert cc._screen_find("the submit button") is None


class TestOpenFileExplorer:
    """Regression: open_file_explorer used Path.home() but 'Path' was never imported
    (crashed on macOS/Linux)."""

    def test_module_imports_path(self):
        import actions.computer_settings as cs
        assert hasattr(cs, "Path")

    def test_darwin_opens_home_via_open(self):
        import actions.computer_settings as cs
        with (
            patch.object(cs, "_OS", "Darwin"),
            patch.object(cs, "subprocess") as mock_sub,
        ):
            cs.open_file_explorer()
        (args, _) = mock_sub.Popen.call_args
        assert args[0][0] == "open"
        assert args[0][1] == str(cs.Path.home())

    def test_linux_falls_back_to_xdg_open_home(self):
        import actions.computer_settings as cs
        with (
            patch.object(cs, "_OS", "Linux"),
            patch.object(cs, "subprocess") as mock_sub,
        ):
            # No file manager found via 'which' → must fall back to xdg-open
            mock_sub.run.return_value = MagicMock(returncode=1)
            cs.open_file_explorer()
        (args, _) = mock_sub.Popen.call_args
        assert args[0][0] == "xdg-open"
        assert args[0][1] == str(cs.Path.home())


# ═══════════════════════════════════════════════════════════════════════════════
# screen_processor._compress (vision pipeline)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_png(size=(200, 150), color=(255, 0, 0)) -> bytes:
    from PIL import Image
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestCompress:
    def test_png_becomes_jpeg(self):
        from actions import screen_processor as sp
        out, mime = sp._compress(_make_png())
        assert mime == "image/jpeg"
        assert out.startswith(b"\xff\xd8")  # JPEG magic bytes

    def test_large_image_is_downscaled_to_bounds(self):
        from PIL import Image

        from actions import screen_processor as sp
        out, _ = sp._compress(_make_png(size=(4000, 3000)))
        img = Image.open(io.BytesIO(out))
        img.load()
        assert img.format == "JPEG"
        assert img.width <= sp.IMG_MAX_W
        assert img.height <= sp.IMG_MAX_H

    def test_returns_original_when_pil_unavailable(self):
        from actions import screen_processor as sp
        png = _make_png()
        with patch("actions.screen_processor._PIL", False):
            out, mime = sp._compress(png, "PNG")
        assert out == png
        assert mime == "image/png"

    def test_returns_original_on_invalid_image(self):
        from actions import screen_processor as sp
        garbage = b"this is definitely not an image"
        out, mime = sp._compress(garbage, "PNG")
        assert out == garbage
        assert mime == "image/png"


# ═══════════════════════════════════════════════════════════════════════════════
# dev_agent._get_model / _plan_project
# ═══════════════════════════════════════════════════════════════════════════════

_PLAN_JSON = (
    '{"project_name": "calc", "entry_point": "main.py",'
    ' "files": [{"path": "main.py", "description": "entry", "imports": []}],'
    ' "run_command": "python main.py", "dependencies": []}'
)


class TestGetModel:
    def test_get_model_forwards_to_client_factory(self):
        import actions.dev_agent as da
        fake = object()
        with patch.object(da, "new_gemini_client", return_value=fake) as factory:
            result = da._get_model("gemini-2.5-flash")
        assert result is fake
        factory.assert_called_once_with("gemini-2.5-flash")


class TestPlanProject:
    def test_plan_project_end_to_end(self):
        """Full regression: _plan_project must work now that _get_model exists."""
        import actions.dev_agent as da
        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = _PLAN_JSON
        with patch.object(da, "_get_model", return_value=fake_model) as m:
            plan = da._plan_project("A calculator app", "python")
        assert plan["project_name"] == "calc"
        assert plan["entry_point"] == "main.py"
        assert plan["files"][0]["path"] == "main.py"
        m.assert_called_once_with(da.MODEL_PLANNER)

    def test_plan_project_strips_markdown_fences(self):
        import actions.dev_agent as da
        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = (
            "```json\n" + _PLAN_JSON + "\n```"
        )
        with patch.object(da, "_get_model", return_value=fake_model):
            plan = da._plan_project("A calculator app", "python")
        assert plan["project_name"] == "calc"

    def test_plan_project_raises_rate_limit_on_429(self):
        import actions.dev_agent as da
        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("429 quota exceeded")
        with (
            patch.object(da, "_get_model", return_value=fake_model),
            pytest.raises(da.RateLimitError),
        ):
            da._plan_project("A calculator app", "python")

    def test_plan_project_raises_value_error_on_bad_json(self):
        import actions.dev_agent as da
        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = "not json at all"
        with (
            patch.object(da, "_get_model", return_value=fake_model),
            pytest.raises(ValueError),
        ):
            da._plan_project("A calculator app", "python")
