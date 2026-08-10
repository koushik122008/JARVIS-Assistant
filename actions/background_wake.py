"""
background_wake.py — MARK XLIX Cold-Start Wake Listener

A lightweight, always-on process that lets the user wake JARVIS by saying
"Hey Jarvis" even when the application is closed.

Design:
  - Runs hidden (launched with pythonw.exe from the Windows startup entry).
  - Reuses actions/wake_word.WakeWordDetector (Vosk engine, offline, no key).
  - When the wake word is heard it checks whether the main app is already
    running; if not, it launches  `pythonw main.py --woke`  and logs the
    launched PID to a lock file so it never double-launches.
  - If the app IS already running, it does nothing (the app's own in-process
    wake word detector handles the conversation).

No Qt / Gemini imports here — this process must stay tiny.

Ruff note: this is a long-running daemon where every failure mode must be
swallowed and retried, so blind exception handling is intentional here.
"""
# ruff: noqa: BLE001, S110, S112

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent.parent
MAIN_PY = PROJECT_DIR / "main.py"
LOG_FILE = PROJECT_DIR / "memory" / "background_wake.log"
LOCK_FILE = PROJECT_DIR / "memory" / ".jarvis_wake.lock"

# Allow running as `python actions/background_wake.py` (no package context).
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

STARTUP_NAME = "JARVIS_WAKE"   # registry value / launchd label / .desktop name
COOLDOWN_AFTER_LAUNCH = 15.0   # seconds to ignore the wake word after launching

# Windows creation flags for a fully detached, console-less child.
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# ── Logging ────────────────────────────────────────────────────────────────────


def get_logger() -> logging.Logger:
    log = logging.getLogger("background_wake")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
    except Exception:
        pass
    return log


# ── Launch command construction (pure, testable) ───────────────────────────────


def _pythonw() -> str:
    """pythonw.exe when available (no console window), else plain python."""
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    return str(pythonw if pythonw.exists() else sys.executable)


def _listener_command() -> list[str]:
    """Command that (re)starts this listener at login."""
    return [_pythonw(), str(Path(__file__).resolve())]


def _launch_command() -> list[str]:
    """Command used to start the main JARVIS app in 'woken' mode."""
    return [_pythonw(), str(MAIN_PY), "--woke"]


# ── Startup registration (mirrors ui.py AUTO-START) ────────────────────────────


def _startup_command_string() -> str:
    parts = [f'"{p}"' for p in _listener_command()]
    return " ".join(parts)


def is_registered() -> bool:
    """True if this listener is registered to run at login."""
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, STARTUP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        elif sys.platform == "darwin":
            return (Path.home() / "Library" / "LaunchAgents"
                    / f"{STARTUP_NAME.lower()}.plist").exists()
        else:
            return (Path.home() / ".config" / "autostart"
                    / "jarvis-wake.desktop").exists()
    except Exception:
        return False


def register_startup() -> bool:
    """Register this listener to launch at login. Returns True on success."""
    try:
        if sys.platform == "win32":
            import winreg
            reg = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_ALL_ACCESS)
            winreg.SetValueEx(reg, STARTUP_NAME, 0, winreg.REG_SZ,
                              _startup_command_string())
            winreg.CloseKey(reg)
        elif sys.platform == "darwin":
            plist_dir = Path.home() / "Library" / "LaunchAgents"
            plist_dir.mkdir(parents=True, exist_ok=True)
            (plist_dir / f"{STARTUP_NAME.lower()}.plist").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                f'  <key>Label</key><string>{STARTUP_NAME}</string>\n'
                '  <key>ProgramArguments</key><array>\n'
                + "".join(f'    <string>{p}</string>\n' for p in _listener_command())
                + '  </array>\n'
                '  <key>RunAtLoad</key><true/>\n'
                '</dict></plist>\n')
        else:
            desk_dir = Path.home() / ".config" / "autostart"
            desk_dir.mkdir(parents=True, exist_ok=True)
            (desk_dir / "jarvis-wake.desktop").write_text(
                "[Desktop Entry]\n"
                f"Name=JARVIS Wake Listener\n"
                f"Exec={_startup_command_string()}\n"
                "Type=Application\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\n")
        return True
    except Exception:
        return False


def unregister_startup() -> bool:
    """Remove the login registration. Returns True on success."""
    try:
        if sys.platform == "win32":
            import winreg
            reg = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.DeleteValue(reg, STARTUP_NAME)
            except FileNotFoundError:
                pass
            winreg.CloseKey(reg)
        elif sys.platform == "darwin":
            (Path.home() / "Library" / "LaunchAgents"
             / f"{STARTUP_NAME.lower()}.plist").unlink(missing_ok=True)
        else:
            (Path.home() / ".config" / "autostart"
             / "jarvis-wake.desktop").unlink(missing_ok=True)
        return True
    except Exception:
        return False


# ── Is JARVIS already running? ─────────────────────────────────────────────────


def _cmdline_is_jarvis(cmdline: list[str], cwd: str | None = None) -> bool:
    """True if a process cmdline (and optionally its cwd) is the main JARVIS app."""
    if not any(os.path.basename(str(arg)).lower() == "main.py" for arg in cmdline):
        return False
    if str(PROJECT_DIR).lower() in " ".join(cmdline).lower():
        return True
    # Relative launch (`cd project && pythonw main.py`) has no project path in
    # the cmdline — fall back to the process working directory.
    try:
        if cwd and str(Path(cwd).resolve()).lower() == str(PROJECT_DIR).lower():
            return True
    except Exception:
        pass
    return False


def _jarvis_running(procs: list | None = None) -> bool:
    """True if the main JARVIS app is already running.

    ``procs`` is injectable for tests: a list of objects with ``cmdline()`` and
    optional ``cwd()`` methods (psutil.Process-like).
    """
    if procs is None:
        try:
            import psutil
            procs = list(psutil.process_iter(["cmdline"]))
        except Exception:
            return False
    for p in procs:
        try:
            cwd = None
            try:
                cwd = p.cwd()
            except Exception:
                pass
            if _cmdline_is_jarvis(p.cmdline(), cwd):
                return True
        except Exception:
            continue
    return False


def _lock_jarvis_running() -> bool:
    """True if the lock file's PID is a live JARVIS process; cleans stale locks."""
    try:
        if not LOCK_FILE.exists():
            return False
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        import psutil
        if not psutil.pid_exists(pid):
            LOCK_FILE.unlink(missing_ok=True)
            return False
        return _jarvis_running()
    except Exception:
        return False


def _write_lock(pid: int) -> None:
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


# ── Wake chime ─────────────────────────────────────────────────────────────────

CHIME_SAMPLE_RATE = 44100
CHIME_NOTES = [(784.0, 0.12), (1046.5, 0.20)]   # G5 → C6, duration in seconds


def _chime_pcm():
    """Build a short two-tone chime (G5→C6) as float32 mono samples.

    Pure and testable — no audio device involved. Each note fades out to
    avoid clicks at the joins.
    """
    import numpy as np

    tones = []
    fade = int(0.02 * CHIME_SAMPLE_RATE)
    for freq, dur in CHIME_NOTES:
        n = int(CHIME_SAMPLE_RATE * dur)
        t = np.linspace(0.0, dur, n, endpoint=False)
        tone = 0.35 * np.sin(2 * np.pi * freq * t)
        if fade > 0:
            env = np.ones(n)
            env[-fade:] = np.linspace(1.0, 0.0, fade)
            tone = tone * env
        tones.append(tone)
    return np.concatenate(tones).astype(np.float32) if tones else np.array([], dtype=np.float32)


def _play_chime(log: logging.Logger | None = None) -> bool:
    """Play the wake confirmation chime through the speakers. Returns True on success.

    Uses sounddevice (already a dependency of wake_word.py); blocks ~0.3 s so
    the chime finishes before the app boots. Never raises — the daemon swallows
    audio failures (no device, muted, etc.).
    """
    try:
        import sounddevice as sd
        sd.play(_chime_pcm(), CHIME_SAMPLE_RATE)
        sd.wait()
        return True
    except Exception as e:
        if log is not None:
            log.warning(f"[Wake] Chime playback failed: {e}")
        return False


# ── Launching the app ──────────────────────────────────────────────────────────


def launch_jarvis(log: logging.Logger) -> int | None:
    """Launch ``main.py --woke`` detached; returns the child PID or None."""
    try:
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW | DETACHED_PROCESS
        proc = subprocess.Popen(
            _launch_command(),
            cwd=str(PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        _write_lock(proc.pid)
        log.info(f"[Wake] Launched JARVIS (pid {proc.pid}).")
        return proc.pid
    except Exception as e:
        log.error(f"[Wake] Launch failed: {e}")
        return None


# ── Listener loop ──────────────────────────────────────────────────────────────


def _listener_running(exclude_pid: int | None = None) -> bool:
    """True if another instance of this listener is already active."""
    try:
        import psutil
        me = Path(__file__).resolve()
        for p in psutil.process_iter(["cmdline", "pid"]):
            try:
                if p.info.get("pid") == exclude_pid:
                    continue
                args = p.cmdline()
                if not args:
                    continue
                if Path(args[0]).resolve() == me or (
                    len(args) > 1 and Path(args[1]).resolve() == me
                ):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _handle_detection(log: logging.Logger, launch: bool,
                      last_launch: list[float]) -> None:
    """Wake word heard — launch the app unless it is already running."""
    now = time.time()
    if now - last_launch[0] < COOLDOWN_AFTER_LAUNCH:
        return
    if _jarvis_running() or _lock_jarvis_running():
        log.info("[Wake] JARVIS already running — ignoring.")
        return
    # Heard it — chime first so the user gets feedback before the app boots.
    _play_chime(log)
    if launch:
        pid = launch_jarvis(log)
        if pid is not None:
            last_launch[0] = now
    else:
        log.info("[Wake] Wake word heard — would launch JARVIS (no-launch mode).")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    launch = "--no-launch" not in argv
    once = "--once" in argv
    log = get_logger()

    log.info("[Wake] Listener starting…")
    if _listener_running(exclude_pid=os.getpid()):
        log.info("[Wake] Another listener is active — exiting.")
        return 0

    # Load wake word settings from the shared config (stdlib-light).
    try:
        from memory.config_manager import (
            get_wake_word_keyword,
            get_wake_word_sensitivity,
        )
        keyword = get_wake_word_keyword()
        sensitivity = get_wake_word_sensitivity()
    except Exception:
        keyword, sensitivity = "jarvis", 0.5

    from actions.wake_word import WakeWordDetector
    detector = WakeWordDetector(keyword=keyword, sensitivity=sensitivity)
    last_launch: list[float] = [0.0]
    detector.on_detected = lambda: _handle_detection(log, launch, last_launch)

    def engine_name() -> str:
        if detector._vosk_ok:
            return "Vosk"
        if detector._porcupine_ok:
            return "Porcupine"
        return "VAD"

    # (Re)start the detector forever — survives mic hiccups, and pauses while
    # the app itself is running so we don't burn CPU on a second mic stream.
    while True:
        try:
            app_up = _jarvis_running() or _lock_jarvis_running()
            if app_up:
                if detector._thread and detector._thread.is_alive():
                    detector.stop()
                    log.info("[Wake] JARVIS is running — listener paused.")
            else:
                if not (detector._thread and detector._thread.is_alive()):
                    detector.start()
                    engine = engine_name()
                    if engine == "VAD" and launch:
                        # A real keyword engine is required to launch the app —
                        # VAD fires on any loud sound and would open JARVIS on noise.
                        launch = False
                        log.warning("[Wake] No keyword engine (Vosk/Porcupine) available — "
                                    "launch disabled to avoid false triggers. "
                                    "Install vosk or restore the offline model.")
                    log.info(f"[Wake] Listening for '{keyword}' (engine: {engine}).")
            if once and last_launch[0]:
                log.info("[Wake] --once satisfied, exiting.")
                return 0
        except Exception as e:
            log.error(f"[Wake] Detector error: {e}")
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main())
