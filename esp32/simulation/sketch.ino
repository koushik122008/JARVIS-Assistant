/*
 * JARVIS-S3 — ESP32-S3 voice-assistant satellite
 * -------------------------------------------------
 * One firmware, two worlds:
 *   DEMO_MODE = 1  -> runs in Wokwi right now. No server needed.
 *                    The "brain" is simulated: press the button and watch
 *                    IDLE -> LISTENING -> THINKING -> SPEAKING on the OLED
 *                    face + LED, then it returns to idle.
 *                    (Short press = talk. Hold ~1.5s = toggle the lamp.)
 *   DEMO_MODE = 0  -> real hardware mode. Connects to Wi-Fi, records from
 *                    the INMP441 mic, POSTs a WAV to your laptop's brain
 *                    (FastAPI /hear), gets WAV + an X-Action header back,
 *                    plays it on the MAX98357A and fires the relay for
 *                    light_on / light_off actions.
 *
 * Pin map (matches the build plan):
 *   INMP441 mic     I2S0 : WS=GPIO15  BCLK=GPIO16  DIN=GPIO17
 *   MAX98357A amp   I2S1 : LRC=GPIO18  BCLK=GPIO19  DIN=GPIO20
 *   SSD1306 OLED    I2C  : SDA=GPIO8   SCL=GPIO9   (addr 0x3C)
 *   WS2812B LED     GPIO4
 *   Push button     GPIO5 (INPUT_PULLUP, other leg to GND)
 *   Relay module    GPIO6 (IN)  -> simulated as a yellow LED in Wokwi
 *
 * Works on ESP32 Arduino core 2.x (Wokwi) and 3.x (real hardware):
 * the I2S driver code is version-guarded below.
 *
 * NOTE (core 3.x): GPIO19/20 are the USB D-/D+ pins on the S3. Fine on the
 * DevKitC-1 (its USB-C is a UART bridge) as long as you don't need native
 * USB at the same time. If you do, move the amp to e.g. GPIO 21/47/48.
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_NeoPixel.h>

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  #include "driver/i2s_std.h"
#else
  #include "driver/i2s.h"
#endif

// ============================ CONFIG ============================
#define DEMO_MODE        1                 // 1 = simulated brain (Wokwi), 0 = real brain over HTTP
#define WIFI_SSID        "YourWiFi"        // real mode only
#define WIFI_PASSWORD    "YourPassword"    // real mode only
#define BRAIN_HOST       "192.168.1.50"    // your laptop's LAN IP (real mode)
#define BRAIN_PORT       8000              // FastAPI brain port
#define BRAIN_PATH       "/hear"

#define PIN_MIC_WS   15
#define PIN_MIC_BCK  16
#define PIN_MIC_DIN  17
#define PIN_AMP_WS   18
#define PIN_AMP_BCK  19
#define PIN_AMP_DOUT 20
#define PIN_OLED_SDA 8
#define PIN_OLED_SCL 9
#define PIN_LED      4
#define PIN_BUTTON   5
#define PIN_RELAY    6

#define SAMPLE_RATE   16000               // mic sample rate (16 kHz)
#define TTS_RATE      24000               // edge-tts default output rate (real mode)
#define RECORD_MS     5000                // max recording length
#define THINK_MS      1800                // demo-mode "thinking" duration
#define SPEAK_MS      2500                // demo-mode "speaking" duration

// ============================ STATE MACHINE ============================
enum State { IDLE, LISTENING, THINKING, SPEAKING };
State state = IDLE;

// OLED
Adafruit_SSD1306 display(128, 64, &Wire, -1);

// WS2812B single pixel
Adafruit_NeoPixel strip(1, PIN_LED, NEO_GRB + NEO_KHZ800);

// Button bookkeeping
const unsigned long LONG_PRESS_MS = 1500;
bool  btnDown = false;
bool  btnHandled = false;
unsigned long btnDownAt = 0;

// Lamp (relay)
bool lampOn = false;

// ============================ I2S (real mode) ============================
#if !DEMO_MODE
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  i2s_chan_handle_t mic_rx = NULL, amp_tx = NULL;
#endif

#if ESP_ARDUINO_VERSION_MAJOR >= 3
bool i2sInitMic() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, NULL, &mic_rx) != ESP_OK) return false;
  // INMP441: tie its L/R select low = left channel. MONO slot mode keeps
  // every frame a single 16-bit sample, matching the mono WAV we upload.
  i2s_std_config_t std_cfg = {
    .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = PIN_MIC_BCK,
      .ws   = PIN_MIC_WS,
      .dout = I2S_GPIO_UNUSED,
      .din  = PIN_MIC_DIN,
      .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false }
    }
  };
  if (i2s_channel_init_std_mode(mic_rx, &std_cfg) != ESP_OK) return false;
  if (i2s_channel_enable(mic_rx) != ESP_OK) return false;
  return true;
}
bool i2sInitAmp() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, &amp_tx, NULL) != ESP_OK) return false;
  i2s_std_config_t std_cfg = {
    .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(TTS_RATE),
    // MAX98357A is mono. If you hear silence, flip ws_inv or the slot mask
    // (some boards expect data on the right slot instead of the left).
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = PIN_AMP_BCK,
      .ws   = PIN_AMP_WS,
      .dout = PIN_AMP_DOUT,
      .din  = I2S_GPIO_UNUSED,
      .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false }
    }
  };
  if (i2s_channel_init_std_mode(amp_tx, &std_cfg) != ESP_OK) return false;
  if (i2s_channel_enable(amp_tx) != ESP_OK) return false;
  return true;
}
#else
bool i2sInitMic() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 512,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };
  i2s_pin_config_t pins = { PIN_MIC_BCK, PIN_MIC_WS, I2S_PIN_NO_CHANGE, PIN_MIC_DIN };
  if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) return false;
  return true;
}
bool i2sInitAmp() {
  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = TTS_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 512,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };
  i2s_pin_config_t pins = { PIN_AMP_BCK, PIN_AMP_WS, PIN_AMP_DOUT, I2S_PIN_NO_CHANGE };
  if (i2s_driver_install(I2S_NUM_1, &cfg, 0, NULL) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_1, &pins) != ESP_OK) return false;
  return true;
}
#endif
#endif // !DEMO_MODE

// ============================ LED ============================
void setLed(uint8_t r, uint8_t g, uint8_t b) {
  strip.setPixelColor(0, strip.Color(r, g, b));
  strip.show();
}

// ============================ OLED FACE ============================
const int EYE_LX = 42, EYE_RX = 86, EYE_Y = 26, EYE_R = 9;

void drawEyesClosed() {
  display.drawLine(EYE_LX - EYE_R, EYE_Y, EYE_LX + EYE_R, EYE_Y, SSD1306_WHITE);
  display.drawLine(EYE_RX - EYE_R, EYE_Y, EYE_RX + EYE_R, EYE_Y, SSD1306_WHITE);
}

void drawEyesOpen(bool blink) {
  if (blink) { drawEyesClosed(); return; }
  display.drawCircle(EYE_LX, EYE_Y, EYE_R, SSD1306_WHITE);
  display.drawCircle(EYE_RX, EYE_Y, EYE_R, SSD1306_WHITE);
  display.fillCircle(EYE_LX, EYE_Y, 3, SSD1306_WHITE);
  display.fillCircle(EYE_RX, EYE_Y, 3, SSD1306_WHITE);
}

void drawFaceIdle(unsigned long t) {
  display.clearDisplay();
  bool open = (t % 5000) < 400;                 // occasional peek
  if (open) drawEyesOpen(false); else drawEyesClosed();
  display.setCursor(30, 52); display.print("sleeping...");
  display.display();
}

void drawFaceListening(unsigned long t) {
  display.clearDisplay();
  bool blink = (t % 2400) < 150;
  drawEyesOpen(blink);
  display.setCursor(34, 52); display.print("listening");
  display.display();
}

void drawFaceThinking(unsigned long t, int dots) {
  display.clearDisplay();
  display.fillCircle(EYE_LX, EYE_Y, 4, SSD1306_WHITE);
  display.fillCircle(EYE_RX, EYE_Y, 4, SSD1306_WHITE);
  display.setCursor(20, 12); display.print("THINKING");
  display.setCursor(52, 52);
  for (int i = 0; i < dots; i++) display.print(".");
  display.display();
}

void drawFaceSpeaking(unsigned long t, const char *line) {
  display.clearDisplay();
  drawEyesOpen(false);
  int mh = 5 + ((t / 120) % 6);                 // mouth bar oscillates
  display.fillRoundRect(52, 44, 24, mh, 3, SSD1306_WHITE);
  if (line) { display.setCursor(2, 58); display.print(line); }
  display.display();
}

// ============================ LAMP ============================
void triggerLamp(bool on) {
  lampOn = on;
  digitalWrite(PIN_RELAY, on ? HIGH : LOW);
  Serial.printf("LAMP %s\n", on ? "ON" : "OFF");
}

// ============================ BRAIN ============================
// --- Demo brain: canned replies + fake latency, no server ---
#if DEMO_MODE
const char* kReplies[] = {
  "ALL SYSTEMS NOMINAL, SIR.",
  "THE WEATHER IS FINE TODAY.",
  "REMEMBER TO DRINK WATER.",
  "JARVIS IS READY FOR ACTION."
};
const char* demoSay() {
  static int n = 0;
  return kReplies[(n++) % 4];
}

// --- Real brain: record -> POST /hear -> hold WAV for playback ---
#else
#include <WiFi.h>
#include <HTTPClient.h>

uint8_t *respWav = nullptr;      // brain's WAV reply (full body, incl. 44-byte header)
size_t   respWavLen = 0;
size_t   respWavPos = 0;
String   respAction = "";

void buildWavHeader(uint8_t *hdr, uint32_t dataLen, uint32_t sampleRate) {
  memcpy(hdr, "RIFF", 4);
  uint32_t chunk = 36 + dataLen; memcpy(hdr + 4, &chunk, 4);
  memcpy(hdr + 8, "WAVE", 4);
  memcpy(hdr + 12, "fmt ", 4);
  uint32_t fmtLen = 16;           memcpy(hdr + 16, &fmtLen, 4);
  uint16_t audioFmt = 1;          memcpy(hdr + 20, &audioFmt, 2);
  uint16_t channels = 1;          memcpy(hdr + 22, &channels, 2);
                                  memcpy(hdr + 24, &sampleRate, 4);
  uint32_t byteRate = sampleRate * 2; memcpy(hdr + 28, &byteRate, 4);
  uint16_t blockAlign = 2;        memcpy(hdr + 32, &blockAlign, 2);
  uint16_t bits = 16;             memcpy(hdr + 34, &bits, 2);
  memcpy(hdr + 36, "data", 4);
                                  memcpy(hdr + 40, &dataLen, 4);
}

// Returns "" on success, otherwise a short error string.
// On success: respWav (heap), respWavLen, respWavPos, respAction are set.
const char* recordAndPost() {
  uint32_t maxSamples = SAMPLE_RATE * RECORD_MS / 1000;
  int16_t *pcm = (int16_t*)malloc(maxSamples * 2);
  if (!pcm) return "OOM";

  uint32_t got = 0, start = millis(), lastLoud = 0;
  while (millis() - start < RECORD_MS) {
    size_t read = 0;
    int16_t buf[512];
    #if ESP_ARDUINO_VERSION_MAJOR >= 3
      if (i2s_channel_read(mic_rx, buf, sizeof(buf), &read, 100 / portTICK_PERIOD_MS) != ESP_OK) continue;
    #else
      if (i2s_read(I2S_NUM_0, buf, sizeof(buf), &read, 100 / portTICK_PERIOD_MS) != ESP_OK) continue;
    #endif
    size_t n = read / 2;
    if (got + n > maxSamples) n = maxSamples - got;
    memcpy(pcm + got, buf, n * 2);
    int64_t sum = 0;
    for (size_t i = 0; i < n; i++) sum += abs(buf[i]);
    if (n > 0 && (sum / n) > 200) lastLoud = millis();   // crude loudness gate
    got += n;
    if (millis() - lastLoud > 900) break;                // early stop on silence
  }

  uint32_t wavLen = 44 + got * 2;
  uint8_t *wav = (uint8_t*)malloc(wavLen);
  if (!wav) { free(pcm); return "OOM"; }
  buildWavHeader(wav, got * 2, SAMPLE_RATE);
  memcpy(wav + 44, pcm, got * 2);
  free(pcm);

  HTTPClient http;
  http.begin(String("http://") + BRAIN_HOST + ":" + BRAIN_PORT + BRAIN_PATH);
  http.addHeader("Content-Type", "audio/wav");
  int code = http.POST(wav, wavLen);
  free(wav);
  if (code != HTTP_CODE_OK) { http.end(); return "BRAIN DOWN"; }

  respAction = http.header("X-Action");
  respWavLen = http.getSize();          // -1 if no Content-Length header
  if (respWavLen <= 0) { http.end(); return "BAD WAV"; }
  http.setTimeout(5000);
  respWav = (uint8_t*)malloc(respWavLen);
  if (!respWav) { http.end(); return "OOM"; }
  WiFiClient *stream = http.getStreamPtr();
  size_t gotB = stream->readBytes(respWav, respWavLen);
  http.end();
  if (gotB < 44) { free(respWav); respWav = nullptr; respWavLen = 0; return "BAD WAV"; }

  respWavPos = 44;   // skip RIFF/fmt header; assume 24 kHz 16-bit mono from edge-tts
  return "";
}

// Push the next chunk of the reply into the amp. Returns remaining bytes.
size_t playWavChunk() {
  if (!respWav || respWavPos >= respWavLen) return 0;
  const size_t CHUNK = 512;
  size_t want = min(CHUNK, respWavLen - respWavPos);
  #if ESP_ARDUINO_VERSION_MAJOR >= 3
    size_t w = 0;
    i2s_channel_write(amp_tx, respWav + respWavPos, want, &w, portMAX_DELAY);
  #else
    size_t w = 0;
    i2s_write(I2S_NUM_1, respWav + respWavPos, want, &w, portMAX_DELAY);
  #endif
  respWavPos += want;
  return respWavLen - respWavPos;
}
#endif // DEMO_MODE

// ============================ STATE MACHINE LOOP ============================
unsigned long stateEntered = 0;
int thinkingDots = 0;
unsigned long lastThinkTick = 0;
const char* speakLine = nullptr;
const char* brainError = nullptr;

void enterState(State s) {
  state = s;
  stateEntered = millis();
  switch (s) {
    case IDLE:      setLed(0, 0, 0);      break;
    case LISTENING: setLed(0, 0, 255);    break;   // blue
    case THINKING:  setLed(255, 150, 0);  break;   // amber
    case SPEAKING:  setLed(0, 255, 0);    break;   // green
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_RELAY, OUTPUT);
  triggerLamp(false);

  strip.begin();
  strip.setBrightness(60);
  setLed(0, 0, 0);

  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED init failed");
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(24, 28);
  display.print("JARVIS-S3");
  display.display();

#if !DEMO_MODE
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 40) delay(500);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi OK, IP: "); Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi FAILED");
  }
  bool mic = i2sInitMic(), amp = i2sInitAmp();
  Serial.printf("I2S mic:%d amp:%d\n", mic, amp);
#endif

  enterState(IDLE);
  Serial.println("JARVIS-S3 ready. Short press = talk, hold = lamp toggle.");
}

void loop() {
  unsigned long now = millis();

  // ---- button (debounced; short = talk, hold = toggle lamp) ----
  bool pressed = (digitalRead(PIN_BUTTON) == LOW);
  if (pressed && !btnDown) {
    btnDown = true; btnHandled = false; btnDownAt = now;
  } else if (!pressed && btnDown) {
    btnDown = false;
    unsigned long held = now - btnDownAt;
    if (held >= LONG_PRESS_MS && !btnHandled) {
      btnHandled = true;
      triggerLamp(!lampOn);
    } else if (state == IDLE && !btnHandled) {
      enterState(LISTENING);
    }
  }

  // ---- state update ----
  switch (state) {
    case IDLE:
      drawFaceIdle(now);
      break;

    case LISTENING:
      drawFaceListening(now);
#if DEMO_MODE
      if (now - stateEntered > 1200) enterState(THINKING);   // "recorded" 1.2s
#else
      if (now - stateEntered > 250) {
        brainError = recordAndPost();
        if (brainError && brainError[0]) {
          // brain unreachable -> recover to IDLE, don't get stuck (test item #4)
          display.clearDisplay();
          display.setCursor(10, 20); display.print(brainError);
          display.setCursor(10, 30); display.print("TRY AGAIN");
          display.display();
          delay(1500);
          enterState(IDLE);
        } else {
          if (respAction == "light_on")  triggerLamp(true);
          if (respAction == "light_off") triggerLamp(false);
          enterState(THINKING);                       // brief visual beat
        }
      }
#endif
      break;

    case THINKING:
      drawFaceThinking(now, thinkingDots);
      if (now - lastThinkTick > 350) { lastThinkTick = now; thinkingDots = (thinkingDots % 3) + 1; }
#if DEMO_MODE
      if (now - stateEntered > THINK_MS) { speakLine = demoSay(); enterState(SPEAKING); }
#else
      if (now - stateEntered > 400) { speakLine = "REPLY"; enterState(SPEAKING); }
#endif
      break;

    case SPEAKING:
      drawFaceSpeaking(now, speakLine);
#if DEMO_MODE
      if (now - stateEntered > SPEAK_MS) { speakLine = nullptr; enterState(IDLE); }
#else
      {
        size_t remaining = playWavChunk();          // stream reply to the amp
        if (remaining == 0) {                       // done speaking
          if (respWav) { free(respWav); respWav = nullptr; respWavLen = respWavPos = 0; }
          speakLine = nullptr;
          enterState(IDLE);
        }
        // note: loop() blocks on i2s_channel_write(portMAX_DELAY) while the
        // amp's DMA queue drains, so the button is unresponsive during speech.
      }
#endif
      break;
  }
}
