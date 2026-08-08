"""
MARK XLIX — Music player control plugin

Controls music playback using OS media keys (works with Spotify, YouTube,
VLC, browsers, etc.):
  - Windows: virtual key codes via SendInput
  - macOS:   AppleScript against Spotify (or 'System Events')
  - Linux:   playerctl (if installed)

'play_song' opens Spotify with the search query via a spotify: deep link.
Tool name: music_control
"""

import ctypes
import platform
import shutil
import subprocess
import webbrowser
from urllib.parse import quote

PLUGIN = {
    "name": "music_control",
    "description": (
        "Controls music playback: play/pause, next, previous, stop, mute, "
        "and 'play X' to search and play a song on Spotify. Use for any "
        "playback request — play, pause, resume, next song, skip, mute."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "play_pause | next | previous | stop | mute | "
                    "play_song | open | volume_up | volume_down"
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Song/artist to search for the 'play_song' action"
            },
            "dry_run": {
                "type": "BOOLEAN",
                "description": "Simulate only — don't actually press keys (default: false)"
            },
        },
        "required": ["action"],
    },
}

_WIN_VK = {
    "play_pause": 0xB3,  # VK_MEDIA_PLAY_PAUSE
    "next":        0xB0,  # VK_MEDIA_NEXT_TRACK
    "previous":    0xB1,  # VK_MEDIA_PREV_TRACK
    "stop":        0xB2,  # VK_MEDIA_STOP
    "mute":        0xAD,  # VK_VOLUME_MUTE
    "volume_up":   0xAF,  # VK_VOLUME_UP
    "volume_down": 0xAE,  # VK_VOLUME_DOWN
}


def _os_name() -> str:
    s = platform.system()
    return {"Darwin": "mac", "Linux": "linux"}.get(s, "windows")


def _send_windows_key(vk: int) -> bool:
    try:
        KEYEVENTF_KEYUP = 0x0002
        INPUT_KEYBOARD = 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t),   # ULONG_PTR on 64-bit
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT)]

        def _send(flags: int) -> None:
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki.wVk = vk
            inp.ki.dwFlags = flags
            ctypes.windll.user32.SendInput(
                1, ctypes.byref(inp), ctypes.sizeof(inp)
            )

        _send(0)
        _send(KEYEVENTF_KEYUP)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Music] ⚠️ SendInput failed: {e}")
        return False


def _mac_script(command: str) -> bool:
    scripts = {
        "play_pause": 'tell application "Spotify" to playpause',
        "next":       'tell application "Spotify" to next track',
        "previous":   'tell application "Spotify" to previous track',
        "stop":       'tell application "Spotify" to pause',
        "mute":       'set volume output muted not (output muted of (get volume settings))',
        "volume_up":  'set volume output volume (output volume of (get volume settings) + 10)',
        "volume_down": 'set volume output volume (output volume of (get volume settings) - 10)',
    }
    script = scripts.get(command)
    if not script:
        return False
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=10, check=False,
        )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Music] ⚠️ osascript failed: {e}")
        return False


def _linux_playerctl(command: str) -> bool:
    mapping = {
        "play_pause": "play-pause",
        "next":       "next",
        "previous":   "previous",
        "stop":       "stop",
        "mute":       "play-pause",  # no mute key in playerctl — use toggle
    }
    cmd = mapping.get(command)
    if not cmd or not shutil.which("playerctl"):
        return False
    try:
        subprocess.run(
            ["playerctl", cmd],
            capture_output=True, timeout=10, check=False,
        )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[Music] ⚠️ playerctl failed: {e}")
        return False


def _press(action: str) -> bool:
    os_name = _os_name()
    if os_name == "windows":
        vk = _WIN_VK.get(action)
        return bool(vk and _send_windows_key(vk))
    if os_name == "mac":
        return _mac_script(action)
    return _linux_playerctl(action)


def _open_spotify_search(query: str) -> bool:
    """Open Spotify app focused on a search for the query (no API key needed)."""
    q = quote(query)
    if _os_name() == "mac":
        safe = query.replace('\\', '\\\\').replace('"', '\\"')
        script = (
            'tell application "Spotify" to activate\n'
            f'tell application "Spotify" to play track "{safe}"'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=10, check=False,
            )
            return True
        except Exception:
            pass
    try:
        return webbrowser.open(f"spotify:search:{q}")
    except Exception as e:  # noqa: BLE001
        print(f"[Music] ⚠️ spotify: link failed: {e}")
        return False


def _launch_spotify() -> bool:
    if _os_name() == "mac":
        try:
            subprocess.run(
                ["open", "-a", "Spotify"], capture_output=True, timeout=10, check=False,
            )
            return True
        except Exception:
            return False
    try:
        return webbrowser.open("spotify:")
    except Exception:
        return False


def handle(args: dict, ctx: dict) -> str:
    action = (args or {}).get("action", "").strip().lower()
    query  = (args or {}).get("query", "").strip()
    dry    = bool((args or {}).get("dry_run"))

    if action == "play_song":
        if not query:
            return "What should I play? Tell me a song or artist, like 'play Shape of You'."
        if dry:
            return f"[DRY RUN] Would search Spotify for '{query}'."
        ok = _open_spotify_search(query)
        return f"Playing '{query}' on Spotify." if ok else (
            "I couldn't open Spotify. Make sure it's installed, then try again."
        )

    if action == "open":
        ok = _launch_spotify()
        return "Opening Spotify." if ok else "I couldn't open Spotify."

    if action == "volume_up":
        ok = _press("volume_up")
        return "Volume up." if ok else "I couldn't change the volume on this system."
    if action == "volume_down":
        ok = _press("volume_down")
        return "Volume down." if ok else "I couldn't change the volume on this system."

    if action in ("play_pause", "next", "previous", "stop", "mute"):
        if dry:
            return f"[DRY RUN] Would press {action}."
        ok = _press(action)
        labels = {
            "play_pause": "Playback toggled.",
            "next":       "Skipped to the next track.",
            "previous":   "Going back to the previous track.",
            "stop":       "Music stopped.",
            "mute":       "Mute toggled.",
        }
        if ok:
            return labels.get(action, "Done.")
        if _os_name() == "linux":
            return (
                "I couldn't control playback. Install playerctl "
                "(sudo apt install playerctl) and try again."
            )
        return "I couldn't send the media key. Make sure a music app is open."

    return (
        "Unknown music action. Try: play_pause, next, previous, stop, mute, "
        "play_song, open, volume_up, volume_down."
    )
