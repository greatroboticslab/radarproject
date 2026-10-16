// Combined firmware for one shared ESP32 DevKit V1 talking to two sensors
// at once and routing both over the board's single USB serial connection:
//
//   - CQRobot 10.525GHz Doppler motion sensor, wired to GPIO15 (3.3V mode)
//   - Seeed 60GHz mmWave radar module (MR60FDA2), relayed via its own XIAO
//     ESP32C6 (still stacked on the radar board, running SeeedMR60FDA2.ino)
//     which does the actual protocol decoding and outputs plain ASCII
//     "FDA,<human>,<fall>" lines over its own Serial1 (D1=TX/D0=RX). This
//     board reads that ASCII text on UART1, GPIO19=RX1 (<- XIAO's D1),
//     GPIO18=TX1 (-> XIAO's D0). NOT UART2 -- UART2 was found to be a dead
//     peripheral on this specific board during bring-up (confirmed via a
//     loopback test showing UART1 works correctly on the same pins where
//     UART2 received nothing at all). This board does NOT run the
//     SEEED_MR60FDA2 protocol decoder itself -- the XIAO already did that.
//
// Output format (one shared USB serial link, same line formats the GUI's
// existing DopplerThread/FDA2Thread already parse):
//   MOTION,<0/1>
//   FDA,<human 0/1>,<fall 0/1>
//
// The A121 is NOT part of this sketch -- it stays on its own separate USB
// connection; wiring it into a shared ESP32 would need Acconeer's
// Exploration Server protocol implemented, which doesn't exist here yet.

#include <Arduino.h>

// ---- Doppler (CQRobot) --------------------------------------------------
const int DOPPLER_PIN = 15;
int lastMotionState = -1;
unsigned long lastMotionPrint = 0;
const unsigned long MOTION_PRINT_INTERVAL = 1000; // 1 second

// ---- Seeed radar relay (already-decoded ASCII from the XIAO) -----------
HardwareSerial fda2Relay(1); // UART1, explicitly remapped below
const int FDA2_RX_PIN = 19;  // <- XIAO's D1 (TX)
const int FDA2_TX_PIN = 18;  // -> XIAO's D0 (RX)

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("COMBINED ESP32 START (Doppler + Seeed MR60FDA2 relay)");

  pinMode(DOPPLER_PIN, INPUT);

  fda2Relay.begin(115200, SERIAL_8N1, FDA2_RX_PIN, FDA2_TX_PIN);
}

void loop() {
  // --- Doppler: check every loop iteration, print on change or every 1s ---
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

  // --- Seeed radar: just relay whatever line the XIAO already sends ------
  while (fda2Relay.available()) {
    String line = fda2Relay.readStringUntil('\n');
    line.trim();
    if (line.startsWith("FDA,")) {
      Serial.println(line);
    }
  }
}
