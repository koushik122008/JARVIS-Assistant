# JARVIS on Raspberry Pi 5 (local voice unit)

A fully **local** voice-assistant starter for the Raspberry Pi 5 — the same
architecture as the desktop JARVIS (`wake word → STT → LLM + tools → TTS`),
running 100% on-device with no cloud and no API keys.

> This is **Phase 1 scaffold**: wake word + speech-to-text + a local LLM with
> tool calling + text-to-speech. It is a faithful port of the desktop
> `TOOL_DECLARATIONS` pattern from `main.py` and the Ollama client from
> `core/llm_client.py`.

---

## 🧾 Hardware kit

| Part | Choice |
|---|---|
| Brain | Raspberry Pi 5 — 8GB (16GB for headroom) + official 27W USB-C PSU |
| Storage | 64GB+ microSD (A2) — or NVMe HAT + SSD for faster model loads |
| Mic | **ReSpeaker USB Mic Array v2.0** (4-mic far-field, built-in AEC/noise suppression) |
| Speaker | USB speaker 3–5W (e.g. Logitech S150) |
| Cooling | Official active cooler — LLM inference gets hot |

**Budget alt:** a USB conference puck (mic + speaker in one) — zero wiring.

---

## 🖥️ OS setup (once)

```bash
# Raspberry Pi OS Bookworm 64-bit, then:
sudo apt update && sudo apt upgrade -y
sudo apt install -y portaudio19-dev espeak-ng ffmpeg python3-venv

# Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# LLM (fully local)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b        # 3B class — the sweet spot for Pi 5 8GB

# Kokoro TTS models (local, ~80 MB)
wget -O kokoro-v0_19.onnx https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/kokoro-v0_19.onnx
wget -O voices-v1.0.bin https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/voices-v1.0.bin
```

## ⚙️ Configure

```bash
cp config.example.json config.json
nano config.json
```

Set at minimum `llm.model` (default `qwen2.5:3b`). If the mic/speaker aren't
picked up automatically, run `python assistant.py --list-devices` and set
`mic_index` / `speaker_index`.

## 🚀 Run

```bash
source .venv/bin/activate
python assistant.py
```

Say **"Hey Jarvis"**, then e.g. *"what time is it"*, *"set a timer for 30 seconds"*,
*"what's the weather in Istanbul"*.

---

## 🔧 Components

| File | Purpose |
|---|---|
| `assistant.py` | Main loop: wake word → record → Whisper → LLM+tools → Kokoro |
| `audio.py` | PyAudio mic/speaker helpers + silence detection |
| `llm.py` | Tiny Ollama client (sentence streaming + tool calls) — mirrors `core/llm_client.py` |
| `tools.py` | On-device tools (`get_time`, `get_date`, `get_weather`, `set_timer`) — mirrors `TOOL_DECLARATIONS` in `main.py` |
| `config.example.json` | Config template (copy to `config.json`) |

## ⚙️ Config options

| Key | Default | Notes |
|---|---|---|
| `wake_word_sensitivity` | `0.5` | Raise to reduce false triggers |
| `llm.url` / `llm.model` | `http://localhost:11434` / `qwen2.5:3b` | Any Ollama model — 3B class recommended |
| `stt.model` | `small.int8` | faster-whisper: `tiny.int8` (fastest) … `base.int8`, `small.int8` |
| `tts.engine` | `kokoro` | `kokoro` (local) · `edge` (cloud fallback, needs internet) · `none` (debug) |
| `tts.voice` | `af_sarah` | Kokoro voice id — or `en-US-GuyNeural` for edge |
| `mic_index` / `speaker_index` | `null` | Set from `--list-devices` if auto-detect fails |
| `silence_threshold` | `500` | RMS level below which the mic is considered silent |

## 🛠️ Troubleshooting

- **Assistants talking to itself / echo:** the ReSpeaker v2.0 has built-in AEC —
  if you hear loops, lower speaker volume and/or `silence_threshold`.
- **"No audio device"** → `python assistant.py --list-devices`, set the indexes.
- **Slow replies:** use `tiny.int8` for STT; a 3B model replies in ~2–4 s on a
  Pi 5; avoid 7B+ models.
- **phonemizer errors (Kokoro):** `sudo apt install espeak-ng`.

## 🗺️ Roadmap

1. ✅ Wake word → STT → local LLM → TTS (this scaffold)
2. ⏭ Add **Gemini Live** cloud brain (reuse `core/llm_client.py` patterns) + offline fallback
3. ⏭ Port more tools: crypto/stock prices, unit/currency conversion, web search
4. ⏭ ESP32-S3 satellite pucks (M5Stack Atom Echo) that stream to this Pi over WiFi
