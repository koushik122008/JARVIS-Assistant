"""
Tests for the cold-start wake feature: background listener (actions/background_wake.py),
the Vosk keyword matcher in actions/wake_word.py, and the config helpers.
"""

import os
import subprocess
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from actions import background_wake as bw
from actions.wake_word import _text_contains_keyword
from memory import config_manager as cm

# ── Config helpers ─────────────────────────────────────────────────────────────


@pytest.fixture
def saved_config():
    """Snapshot the three settings and restore them after the test."""
    snap = (
        cm.get_background_wake_enabled(),
        cm.get_wake_word_keyword(),
        cm.get_wake_word_sensitivity(),
    )
    yield
    cm.save_background_wake_enabled(snap[0])
    cm.save_config({"wake_word_keyword": snap[1]})
    cm.save_config({"wake_word_sensitivity": snap[2]})


def test_background_wake_config_roundtrip(saved_config):
    cm.save_background_wake_enabled(True)
    assert cm.get_background_wake_enabled() is True
    cm.save_background_wake_enabled(False)
    assert cm.get_background_wake_enabled() is False


# ── Keyword matching (Vosk partial-result text) ────────────────────────────────


@pytest.mark.parametrize("text,keyword,expected", [
    ("hey jarvis", "jarvis", True),
    ("hey jarvis what's the weather", "jarvis", True),
    ("HEY  JARVIS", "jarvis", True),
    ("hey jarv", "jarvis", False),
    ("jarvis", "jarvis", True),
    ("hey jarvis", "hey jarvis", True),
    ("just some weather", "jarvis", False),
    ("", "jarvis", False),
    ("hello world", "", False),
])
def test_text_contains_keyword(text, keyword, expected):
    assert _text_contains_keyword(text, keyword) is expected


# ── Single-instance detection ──────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, cmdline):
        self._c = cmdline

    def cmdline(self):
        return self._c


def _jarvis_cmdline():
    main = str(bw.MAIN_PY)
    return [sys.executable, main, "--woke"]


def test_jarvis_running_detects_main_py():
    procs = [
        _FakeProc(["python", "other.py"]),
        _FakeProc(_jarvis_cmdline()),
    ]
    assert bw._jarvis_running(procs=procs) is True


def test_jarvis_running_false_when_absent():
    procs = [_FakeProc(["python", "other.py"]), _FakeProc(["C:/nope/main.py"])]
    assert bw._jarvis_running(procs=procs) is False


def test_jarvis_running_ignores_broken_procs():
    class _Broken:
        def cmdline(self):
            raise OSError("access denied")

    assert bw._jarvis_running(procs=[_Broken()]) is False


def test_cmdline_is_jarvis():
    assert bw._cmdline_is_jarvis(_jarvis_cmdline()) is True
    assert bw._cmdline_is_jarvis(["python", "main.py"]) is False  # not this project
    assert bw._cmdline_is_jarvis([]) is False


def test_cmdline_is_jarvis_matches_relative_launch_via_cwd():
    # `cd project && pythonw main.py` has no project path in the cmdline —
    # the working directory is the deciding signal.
    assert bw._cmdline_is_jarvis(["pythonw", "main.py"],
                                 cwd=str(bw.PROJECT_DIR)) is True
    assert bw._cmdline_is_jarvis(["pythonw", "main.py"], cwd="C:/other") is False
    assert bw._cmdline_is_jarvis(["pythonw", "main.py"]) is False


def test_jarvis_running_uses_cwd():
    class _FakeProc:
        def __init__(self, cmdline, cwd=None):
            self._c = cmdline
            self._w = cwd

        def cmdline(self):
            return self._c

        def cwd(self):
            return self._w

    procs = [_FakeProc(["pythonw", "main.py"], cwd=str(bw.PROJECT_DIR))]
    assert bw._jarvis_running(procs=procs) is True


# ── Lock file logic ────────────────────────────────────────────────────────────


def test_lock_jarvis_running_with_live_pid(tmp_path, monkeypatch):
    lock = tmp_path / ".jarvis_wake.lock"
    lock.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(bw, "LOCK_FILE", lock)
    with (
        mock.patch("psutil.pid_exists", return_value=True),
        mock.patch.object(bw, "_jarvis_running", return_value=True),
    ):
        assert bw._lock_jarvis_running() is True


def test_lock_jarvis_running_cleans_stale_pid(tmp_path, monkeypatch):
    lock = tmp_path / ".jarvis_wake.lock"
    lock.write_text("99999", encoding="utf-8")
    monkeypatch.setattr(bw, "LOCK_FILE", lock)
    with mock.patch("psutil.pid_exists", return_value=False):
        assert bw._lock_jarvis_running() is False
    assert not lock.exists()


# ── Wake chime ─────────────────────────────────────────────────────────────────


def test_chime_pcm_is_valid_audio():
    pcm = bw._chime_pcm()
    import numpy as np
    assert isinstance(pcm, np.ndarray)
    assert pcm.dtype == np.float32
    expected = int(bw.CHIME_SAMPLE_RATE * sum(d for _, d in bw.CHIME_NOTES))
    assert len(pcm) == expected
    assert float(np.max(np.abs(pcm))) > 0.1          # actually audible
    assert float(np.max(np.abs(pcm))) <= 0.5         # not clipping


def test_play_chime_plays_and_waits():
    with (
        mock.patch("sounddevice.play") as play,
        mock.patch("sounddevice.wait") as wait,
    ):
        assert bw._play_chime() is True
    assert play.call_count == 1
    assert wait.call_count == 1


def test_play_chime_swallows_audio_errors():
    log = mock.MagicMock()
    with mock.patch("sounddevice.play", side_effect=RuntimeError("no device")):
        assert bw._play_chime(log) is False
    log.warning.assert_called_once()


def test_handle_detection_chimes_before_launch():
    log = mock.MagicMock()
    last = [0.0]
    with (
        mock.patch.object(bw, "_jarvis_running", return_value=False),
        mock.patch.object(bw, "_lock_jarvis_running", return_value=False),
        mock.patch.object(bw, "_play_chime") as chime,
        mock.patch.object(bw, "launch_jarvis", return_value=1234) as launch,
    ):
        bw._handle_detection(log, launch=True, last_launch=last)
    chime.assert_called_once()
    launch.assert_called_once_with(log)


def test_handle_detection_no_chime_when_app_running():
    log = mock.MagicMock()
    with (
        mock.patch.object(bw, "_jarvis_running", return_value=True),
        mock.patch.object(bw, "_play_chime") as chime,
        mock.patch.object(bw, "launch_jarvis") as launch,
    ):
        bw._handle_detection(log, launch=True, last_launch=[0.0])
    chime.assert_not_called()
    launch.assert_not_called()


# ── Launch command construction ────────────────────────────────────────────────


def test_launch_command_uses_pythonw_and_woke():
    cmd = bw._launch_command()
    assert cmd[-2:] == [str(bw.MAIN_PY), "--woke"]
    assert "python" in os.path.basename(cmd[0]).lower()  # interpreter, any OS


# ── Startup registration (registry mocked) ─────────────────────────────────────


def test_register_and_unregister_startup_windows(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("Windows-only registry path")

    fake_winreg = mock.MagicMock()

    def fake_open(*a, **k):
        return mock.MagicMock()

    fake_winreg.OpenKey.side_effect = fake_open
    monkeypatch.setattr(bw.sys, "platform", "win32")

    with mock.patch.dict(sys.modules, {"winreg": fake_winreg}):
        assert bw.register_startup() is True
        assert fake_winreg.SetValueEx.call_count == 1
        # value written is a string with quotes around pythonw + listener path
        args = fake_winreg.SetValueEx.call_args[0]
        assert args[1] == bw.STARTUP_NAME
        assert "background_wake.py" in args[4]

        assert bw.unregister_startup() is True
        assert fake_winreg.DeleteValue.call_count == 1


def test_startup_command_string_contains_listener():
    s = bw._startup_command_string()
    assert "background_wake.py" in s
    assert s.count('"') >= 4  # both exe and script quoted


# ── Listener lifecycle (start / stop on demand) ────────────────────────────────


def test_start_listener_launches_hidden_detached():
    with mock.patch("subprocess.Popen") as popen:
        popen.return_value.pid = 4242
        pid = bw.start_listener()
    assert pid == 4242
    args, kwargs = popen.call_args
    assert str(bw.PROJECT_DIR) in args[0][1]      # points at background_wake.py
    assert kwargs["cwd"] == str(bw.PROJECT_DIR)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_start_listener_returns_none_on_failure():
    with mock.patch("subprocess.Popen", side_effect=OSError("boom")):
        assert bw.start_listener() is None


def test_listener_pids_finds_instances():
    class _P:
        def __init__(self, pid, cmdline):
            self._info = {"pid": pid, "cmdline": cmdline}
            self.info = self._info

        def cmdline(self):
            return self._info["cmdline"]

    procs = [
        _P(1, [sys.executable, str(bw.__file__)]),
        _P(2, [sys.executable, "other.py"]),
    ]
    with mock.patch("psutil.process_iter", return_value=procs):
        pids = bw._listener_pids()
    assert pids == [1]


def test_listener_pids_skips_self(monkeypatch):
    class _P:
        def __init__(self, pid, cmdline):
            self._info = {"pid": pid, "cmdline": cmdline}
            self.info = self._info

        def cmdline(self):
            return self._info["cmdline"]

    procs = [_P(os.getpid(), [sys.executable, str(bw.__file__)])]
    with mock.patch("psutil.process_iter", return_value=procs):
        assert bw._listener_pids() == []


def test_listener_pids_skips_own_parent_shim_and_children():
    """uv-venv quirk: pythonw.exe is a shim that spawns the real interpreter
    as a child with the same cmdline — neither should count as a duplicate."""
    class _P:
        def __init__(self, pid, ppid, cmdline):
            self._info = {"pid": pid, "ppid": ppid, "cmdline": cmdline}
            self.info = self._info

        def cmdline(self):
            return self._info["cmdline"]

    fake_self = mock.MagicMock()
    fake_self.ppid.return_value = 4242
    procs = [
        _P(4242, 100, [sys.executable, str(bw.__file__)]),                 # our shim parent
        _P(4243, os.getpid(), [sys.executable, str(bw.__file__)]),          # our own child
        _P(4244, 999, [sys.executable, str(bw.__file__)]),                  # real duplicate
    ]
    with (
        mock.patch("psutil.process_iter", return_value=procs),
        mock.patch("psutil.Process", return_value=fake_self),
    ):
        assert bw._listener_pids() == [4244]


def test_stop_listener_terminates_all_instances():
    fake = mock.MagicMock()
    with (
        mock.patch.object(bw, "_listener_pids", return_value=[100, 200]),
        mock.patch("psutil.Process", return_value=fake),
    ):
        killed = bw.stop_listener()
    assert killed == 2
    assert fake.terminate.call_count == 2
    assert fake.wait.call_count == 2


def test_stop_listener_force_kills_unresponsive():
    fake = mock.MagicMock()
    fake.wait.side_effect = Exception("timeout")
    with (
        mock.patch.object(bw, "_listener_pids", return_value=[100]),
        mock.patch("psutil.Process", return_value=fake),
    ):
        killed = bw.stop_listener()
    assert killed == 1
    fake.kill.assert_called_once()


def test_stop_listener_no_instances():
    with (
        mock.patch.object(bw, "_listener_pids", return_value=[]),
        mock.patch("psutil.Process") as proc,
    ):
        assert bw.stop_listener() == 0
    proc.assert_not_called()
