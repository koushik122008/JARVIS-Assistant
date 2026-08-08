"""
JARVIS — Raspberry Pi 5 local voice unit.

Loop:  wake word ("Hey Jarvis") → record → Whisper (STT)
       → Ollama local LLM (function calling) → Kokoro TTS → speaker

Fully local by default — no cloud, no API keys. Swap engines in config.json
(see config.example.json). This is Phase 1 of the Pi roadmap and a faithful
port of the desktop assistant's tool-calling pattern (main.py, core/llm_client.py).

Usage:
    python assistant.py               # run the voice loop
    python assistant.py --list-devices  # print audio devices, then exit
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import audio as audio_mod
import llm
import tools

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
PROMPT_PATH = BASE_DIR.parent / "core" / "prompt.txt"

FALLBACK_PROMPT = (
    "You are JARVIS, an efficient, professional and slightly witty voice "
    "assistant running on a Raspberry Pi 5. Keep replies short and natural "
    "for speech (1-3 sentences). Respond in English by default. Use tools "
    "when they help."
)

CONFIG: dict = {}
SYSTEM_PROMPT: str = FALLBACK_PROMPT

# Lazy singletons so models load exactly once per session
_STT = None
_KOKORO = None


# ── Config / prompt ─────────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(
            f"[JARVIS] No config.json found.\n"
            f"  Copy {BASE_DIR / 'config.example.json'} -> {CONFIG_PATH} and edit it.\n"
        )
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_system_prompt() -> str:
    """Reuse the desktop JARVIS protocol when present, else fall back."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8", errors="replace")
    return FALLBACK_PROMPT


# ── Wake word ───────────────────────────────────────────────────────────────
def wait_for_wake_word(mic: audio_mod.Mic) -> None:
    from openwakeword import Model

    model = Model(wakeword_models=["hey_jarvis"])
    threshold = float(CONFIG.get("wake_word_sensitivity", 0.5))
    chunk_s = 0.5

    print("[JARVIS] Listening for wake word 'Hey Jarvis'… (Ctrl+C to quit)")
    while True:
        pcm = np.frombuffer(mic.read(chunk_s), dtype=np.int16).astype(np.float32) / 32768.0
        scores = model.predict(pcm)
        score = float(scores.get("hey_jarvis", 0.0))
        if score >= threshold:
            print(f"[JARVIS] Wake word detected ({score:.2f})")
            return


# ── STT ─────────────────────────────────────────────────────────────────────
def get_stt():
    global _STT
    if _STT is None:
        from faster_whisper import WhisperModel

        name = CONFIG.get("stt", {}).get("model", "small.int8")
        print(f"[JARVIS] Loading Whisper model '{name}' (one time)…")
        _STT = WhisperModel(name, device="cpu", compute_type="int8")
    return _STT


def transcribe(pcm_bytes: bytes) -> str:
    model = get_stt()
    audio_in = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(audio_in, beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    print(f"[STT] {text!r}")
    return text


# ── TTS ─────────────────────────────────────────────────────────────────────
def speak(text: str) -> None:
    tts = CONFIG.get("tts", {})
    engine = tts.get("engine", "kokoro")
    spk_idx = CONFIG.get("speaker_index")

    if engine == "none":
        print(f"[TTS] {text}")
        return

    if engine == "kokoro":
        global _KOKORO
        if _KOKORO is None:
            from kokoro_onnx import Kokoro

            model = str(BASE_DIR / tts.get("model", "kokoro-v0_19.onnx"))
            voices = str(BASE_DIR / tts.get("voices", "voices-v1.0.bin"))
            print("[JARVIS] Loading Kokoro TTS (one time)…")
            _KOKORO = Kokoro(model, voices)
        samples, rate = _KOKORO.create(
            text,
            voice=tts.get("voice", "af_sarah"),
            speed=float(tts.get("speed", 1.0)),
        )
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        audio_mod.play_pcm(pcm, rate, device_index=spk_idx)
        return

    if engine == "edge":
        # Cloud fallback — no API key, needs internet. Decodes MP3 via ffmpeg.
        import asyncio
        import subprocess
        import tempfile

        import edge_tts

        async def _save(path: str) -> None:
            com = edge_tts.Communicate(text, tts.get("voice", "en-US-GuyNeural"))
            await com.save(path)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            asyncio.run(_save(tmp.name))
            raw = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", tmp.name,
                 "-f", "s16le", "-ar", "24000", "-ac", "1", "-"],
                capture_output=True,
                check=True,
            ).stdout
        audio_mod.play_pcm(raw, 24000, device_index=spk_idx)
        return

    print(f"[TTS] Unknown engine '{engine}' — set tts.engine to kokoro, edge or none")


# ── LLM + tools loop ────────────────────────────────────────────────────────
def answer(user_text: str) -> None:
    lcfg = CONFIG.get("llm", {})
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    for _ in range(4):  # max tool-call rounds before forcing an answer
        done = None
        for ev in llm.chat_stream(
            messages,
            tools=tools.TOOLS,
            base_url=lcfg.get("url", "http://localhost:11434"),
            model=lcfg.get("model", "qwen2.5:3b"),
        ):
            if ev["type"] == "sentence" and ev["text"].strip():
                speak(ev["text"])  # low latency: speak each sentence as it arrives
            else:
                done = ev

        tool_calls = done.get("tool_calls") or []
        if not tool_calls:
            return

        # Tool round: record what the model said + the tool results, then retry.
        messages.append(
            {"role": "assistant", "content": done.get("content") or "", "tool_calls": tool_calls}
        )
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments") or {}
            if isinstance(args, str):  # tolerate stringified JSON from some models
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            print(f"[TOOL] {name}({args})")
            result = tools.run_tool(name, args)
            messages.append({"role": "tool", "content": result, "name": name})

    print("[JARVIS] Max tool rounds reached without a final answer.")


# ── Main loop ───────────────────────────────────────────────────────────────
def main() -> None:
    global CONFIG, SYSTEM_PROMPT

    if "--list-devices" in sys.argv:
        audio_mod.list_devices()
        return

    CONFIG = load_config()
    SYSTEM_PROMPT = load_system_prompt()
    tools.set_timer_speaker_callback(lambda msg: speak(msg))
    mic_idx = CONFIG.get("mic_index")
    threshold = int(CONFIG.get("silence_threshold", 500))

    while True:
        try:
            mic = audio_mod.Mic(device_index=mic_idx)
        except Exception as e:
            print(f"[JARVIS] Cannot open microphone: {e}")
            print("  Run `python assistant.py --list-devices` and set mic_index in config.json")
            return

        try:
            wait_for_wake_word(mic)
            pcm = audio_mod.record_until_silence(mic, threshold=threshold)
            text = transcribe(pcm)
            if text:
                answer(text)
        except KeyboardInterrupt:
            break
        finally:
            mic.close()

    print("[JARVIS] Bye.")


if __name__ == "__main__":
    main()
