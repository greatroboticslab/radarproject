// Combined firmware for one ESP32 DevKit V1 talking to all three sensors:
//
//   - CQRobot 10.525GHz Doppler motion sensor, wired to GPIO15 (3.3V mode)
//   - Seeed 60GHz mmWave radar (MR60FDA2), relayed via its own XIAO
//     ESP32C6 (still stacked on the radar board, running SeeedMR60FDA2.ino,
//     which does the actual protocol decoding), read here on UART2:
//     GPIO16=RX2 (<- XIAO's D1/TX), GPIO17=TX2 (-> XIAO's D0/RX).
//   - Waveshare A121 (Acconeer XM125 inside), reflashed with the
//     "example_detector_distance" UART firmware variant (the factory
//     default "acc_exploration_server_a121" firmware only outputs over
//     USB, never the header pins -- see project notes). This firmware
//     prints plain text lines, not JSON:
//       "<N> detected distances: <d1> m <d2> m ...\n"
//     Read here on UART1 at 921600 baud (the UART firmware's documented
//     default rate): GPIO19=RX1 (<- A121 TXD), GPIO18=TX1 (-> A121 RXD,
//     unused/not needed for this firmware since it just free-runs).
//
// Output format (one shared USB serial link, matching the GUI's existing
// thread parsers):
//   MOTION,<0/1>
//   FDA,<human 0/1>,<fall 0/1>
//   A121,<d1>[,<d2>,<d3>,...]   (one or more comma-separated distances, closest first)

#include <Arduino.h>

// ---- Doppler (CQRobot) --------------------------------------------------
const int DOPPLER_PIN = 15;
int lastMotionState = -1;
unsigned long lastMotionPrint = 0;
const unsigned long MOTION_PRINT_INTERVAL = 1000; // 1 second

// ---- Seeed radar relay (already-decoded ASCII from the XIAO) -----------
HardwareSerial fda2Relay(2); // UART2
const int FDA2_RX_PIN = 16;  // <- XIAO's D1 (TX)
const int FDA2_TX_PIN = 17;  // -> XIAO's D0 (RX)

// ---- A121 (plain-text distance detector firmware) ------------------------
HardwareSerial a121Serial(1); // UART1
const int A121_RX_PIN = 19;   // <- A121 TXD
const int A121_TX_PIN = 18;   // -> A121 RXD
const long A121_BAUD = 921600;

const int A121_MAX_TARGETS = 10;

// Parses "<N> detected distances: <d1> m <d2> m ...\n" and fills out[] with
// every distance found (up to maxOut), in the order the sensor reported
// them (closest first). Returns how many were found.
int a121ParseAllDistances(const String& line, float* out, int maxOut) {
  int idx = line.indexOf("distances:");
  if (idx < 0) return 0;
  String rest = line.substring(idx + strlen("distances:"));
  rest.trim();

  int count = 0;
  int pos = 0;
  while (count < maxOut) {
    int sp = rest.indexOf(' ', pos);
    String tok = (sp < 0) ? rest.substring(pos) : rest.substring(pos, sp);
    tok.trim();
    if (tok.length() > 0) {
      char c0 = tok[0];
      if (isDigit(c0) || (c0 == '-' && tok.length() > 1)) {
        out[count++] = tok.toFloat();
      }
      // else: skip non-numeric tokens like the "m" unit marker
    }
    if (sp < 0) break;
    pos = sp + 1;
  }
  return count;
}

void a121Poll() {
  static float distances[A121_MAX_TARGETS];
  while (a121Serial.available()) {
    String line = a121Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;
    int n = a121ParseAllDistances(line, distances, A121_MAX_TARGETS);
    if (n > 0) {
      Serial.print("A121,");
      for (int i = 0; i < n; i++) {
        if (i > 0) Serial.print(",");
        Serial.print(distances[i], 4);
      }
      Serial.println();
    }
  }
}

// ---- Main setup/loop ------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("ALL THREE COMBINED START (Doppler + Seeed relay + A121)");

  pinMode(DOPPLER_PIN, INPUT);
  fda2Relay.begin(115200, SERIAL_8N1, FDA2_RX_PIN, FDA2_TX_PIN);
  a121Serial.begin(A121_BAUD, SERIAL_8N1, A121_RX_PIN, A121_TX_PIN);
}

void loop() {
  // --- Doppler ---
  int motionState = digitalRead(DOPPLER_PIN);
  if (motionState != lastMotionState) {
    Serial.print("MOTION,");
    Serial.println(motionState);
    lastMotionState = motionState;
    lastMotionPrint = millis();
  } else if (millis() - lastMotionPrint >= MOTION_PRINT_INTERVAL) {
    Serial.print("MOTION,");
    Serial.println(motionState);
    lastMotionPrint = millis();
  }

  // --- Seeed relay: just forward whatever line the XIAO already sends ---
  while (fda2Relay.available()) {
    String line = fda2Relay.readStringUntil('\n');
    line.trim();
    if (line.startsWith("FDA,")) {
      Serial.println(line);
    }
  }

  // --- A121 ---
  a121Poll();
}
