# JARVIS-S3 — ESP32 satellite + simulations

The hardware side of the "JARVIS from scratch" plan. This folder contains a
**ready-to-run Wokwi simulation** of the full unit plus the firmware that runs
on the real ESP32-S3.

```
esp32/
├── simulation/
│   ├── diagram.json      # Wokwi project: ESP32-S3 + OLED + WS2812B + button + lamp LED
│   ├── sketch.ino        # Firmware (state machine, OLED face, LED, relay, brain client)
│   └── libraries.txt     # Arduino libs Wokwi needs
└── README.md
```

---

## 1. Run the simulation (Wokwi — free, no hardware needed)

1. Go to **https://wokwi.com/projects/new** (ESP32-S3 template).
2. Open the editor split: paste `diagram.json` into the **Diagram** tab and
   `sketch.ino` into the **Code** tab (replace `main.cpp` with `sketch.ino`).
3. Make sure `libraries.txt` lists `Adafruit SSD1306` and `Adafruit NeoPixel`
   (or use the library panel).
4. Click **▶ Play**.

What you'll see: the OLED shows a sleeping face → press the button →
**blue** LED + open eye (LISTENING) → **amber** pulsing + "THINKING…" →
**green** + talking mouth + a canned reply line (SPEAKING) → back to idle.
**Hold the button ~1.5 s** to toggle the yellow lamp LED (your relay on real
hardware).

> 💡 **Optional audio upgrade:** in the parts picker, search for *I2S Audio*
> and *Microphone* and wire them to GPIO 18/19/20 and GPIO 15/16/17. The
> firmware is already I2S-ready (`DEMO_MODE = 0` path), but the demo loop
> deliberately doesn't touch I2S so the base project runs with zero setup.

### Other simulators worth having in your toolbox
| Tool | Use it for |
|---|---|
| **Wokwi** (above) | Full system: firmware logic, states, OLED/LED/button wiring |
| **Falstad** (falstad.com/circuit) | Analog sanity: relay flyback path, button RC debounce, WS2812 data line |
| **KiCad + freeDFM** | Phase-7 PCB "hat" — turn the breadboard into a real layout |

---

## 2. Firmware modes

```c
#define DEMO_MODE   1   // 1 = simulated brain (works in Wokwi now)
                        // 0 = real hardware: Wi-Fi + INMP441 + MAX98357A + relay
```

| Mode | Mic | Brain | Speaker | Lamp |
|---|---|---|---|---|
| `DEMO_MODE 1` | not used | built-in canned replies | not used | button hold toggles relay |
| `DEMO_MODE 0` | INMP441 I2S | `POST http://<brain-ip>:8000/hear` | WAV → MAX98357A | `X-Action: light_on/off` |

Real mode flow: record up to 5 s (stops early on silence) → POST a 16 kHz
WAV → receive WAV + `X-Action` header → stream audio to the amp while the
OLED "talks" → toggle the relay for `light_on` / `light_off`. If the brain is
unreachable it shows the error on the OLED and returns to IDLE (never stuck).

The I2S code is version-guarded and compiles on both ESP32 Arduino core 2.x
(Wokwi) and 3.x (hardware). Set `WIFI_SSID`, `WIFI_PASSWORD` and `BRAIN_HOST`
before flashing. (WiFiManager captive portal = the Phase-5 polish, add it
later.)

## 3. Pin map (matches the build plan)

| Signal | Pin | Device |
|---|---|---|
| I2S0 WS / BCLK / DIN | 15 / 16 / 17 | INMP441 mic |
| I2S1 LRC / BCLK / DIN | 18 / 19 / 20 | MAX98357A amp |
| I2C SDA / SCL | 8 / 9 | SSD1306 OLED (0x3C) |
| LED data | 4 | WS2812B |
| Button (pull-up) | 5 | tactile switch → GND |
| Relay IN | 6 | 1-ch 5 V relay module |

⚠️ On the S3, GPIO 19/20 are the native-USB D-/D+ pins. Fine on the DevKitC-1
(its USB-C is a UART bridge) unless you need native USB at the same time.

---

## 4. Physical layout ideas

### Perfboard zone map
```
┌──────────────────────────────────────────────────────────────┐
│  ZONE 1: POWER        ZONE 2: DIGITAL      ZONE 3: AUDIO      │
│  USB-C 5V ──► [10µF]  ESP32-S3 DevKit     ┌─ INMP441 (mic)    │
│   │  + 100nF           OLED (top edge)    │  (far from amp)   │
│   ├─ 5V rail           WS2812B LED        └─ (signal + ground │
│   └─ 3.3V reg (on      Push button              return, kept  │
│      board)             Relay module         short & apart)   │
│  ── single star-point ground ──               MAX98357A (amp) │
│  ZONE 4: OUTPUT        (all grounds return  + speaker grille  │
│  Relay → lamp           to ONE point)       (other edge)      │
└──────────────────────────────────────────────────────────────┘
```
*Rule of thumb: the amp and the mic live on **opposite edges**; their grounds
both return to one star point at the ESP32 GND pin — never daisy-chain the mic
ground through the amp ground.*

### Front panel (enclosure face)
```
        ┌──────────────────────┐
        │      [ OLED ]        │   status "face" — top center
        │       0.96" 128x64   │
        │                      │
        │      O  O  O  O      │   WS2812B ring or 4 pixels
        │   O      ●       O   │   around the button:
        │   O    (BTN)     O   │   blue=listening, amber=thinking,
        │   O              O   │   green=speaking, off=idle
        │      O  O  O  O      │
        │      ▮ mic port      │   sound port at bottom, away from speaker
        │ ────────────────     │
        │   (speaker grille    │   speaker + relay on the BACK/base
        │    + relay on back)  │
        └──────────────────────┘
```

### Key electrical notes (from the research pass)
- **Decoupling:** 100 nF across INMP441 VDD-GND; 10 µF *and* 100 nF within
  2–3 mm of MAX98357A VDD; 100 nF on the 3.3 V rail.
- **Antenna:** keep the S3's PCB antenna at the board edge with a **15 mm
  keep-out** — no copper/traces under it, and keep the amp/relay ≥ 20–30 mm
  away (their fields de-tune it).
- **WS2812B:** 330–470 Ω series resistor on the data line right at the LED.
  A single pixel on 5 V with a 3.3 V GPIO *usually* works with the resistor;
  if you see glitches, the bulletproof fix is a 74AHCT125 level shifter
  (datasheet wants ≥ 3.5 V high on a 5 V pixel; ESP32 outputs 3.3 V).
- **Relay module:** flyback diode + optocoupler are built into cheap 1-ch
  modules; a 3.3 V GPIO drives `IN` fine (~2–5 mA). For true isolation remove
  the **VCC↔JD-VCC jumper** and feed the coil side from 5 V.
- **I2C pull-ups:** 4.7 kΩ on SDA/SCL at the ESP32 end. Most OLED modules
  already carry them — check you don't end up with two parallel sets.
- **Mic mounting:** don't hard-mount the mic against the enclosure; use a
  foam/rubber gasket and point its port away from the speaker to kill
  vibration feedback.
