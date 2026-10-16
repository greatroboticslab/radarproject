// Firmware for a Seeed Studio MR60FDA2 60GHz mmWave fall-detection sensor,
// stacked on its bundled XIAO ESP32C6.
//
// This board was originally assumed to be an MR60BHA2 (breath/heart) per
// the project's kit, but raw protocol diagnostics showed it actually speaks
// the MR60FDA2 (fall detection) frame types (ReportFallDetection 0x0E02,
// HeightUpload 0x0E0E) instead of the BHA2 breath/heart/distance types.
// This sketch uses the matching SEEED_MR60FDA2 class instead.
//
//   FDA,<human 0/1>,<fall 0/1>
//
// Output goes to BOTH the native-USB "Serial" (the XIAO's own "Upload Port",
// COM6 when standalone) AND "Serial1", the chip's separate hardware UART1,
// explicitly remapped to GPIO0 (RX) / GPIO1 (TX) -- the "D0"/"D1" pins on
// the Grove IO expansion, per this board's own pins_arduino.h. "Serial" on
// this chip is native USB-CDC, not a physical UART pin -- it was silently
// unreachable from any wired connection, which is why nothing ever showed
// up on any header pin during shared-ESP32 wiring testing. UART0 is already
// committed internally (mmWaveSerial, talking to the radar chip over the
// boards' stacking connector) and, per pins_arduino.h, defaults to the same
// GPIO16/17 pins labeled "TX"/"RX" on the carrier board's header -- so
// Serial1 must NOT be left on its own default pins (which would collide
// with those same GPIO16/17), hence the explicit remap to GPIO0/GPIO1.

#include <Arduino.h>
#include "Seeed_Arduino_mmWave.h"

#ifdef ESP32
#  include <HardwareSerial.h>
HardwareSerial mmWaveSerial(0);
#else
#  define mmWaveSerial Serial1
#endif

const int RELAY_RX_PIN = 0;  // D0
const int RELAY_TX_PIN = 1;  // D1

SEEED_MR60FDA2 mmWave;

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, RELAY_RX_PIN, RELAY_TX_PIN);
  delay(500);
  mmWave.begin(&mmWaveSerial);
  Serial.println("MR60FDA2 START");
  Serial1.println("MR60FDA2 START");

  // The fall-detection algorithm needs a real installation height/threshold
  // to make sense of "fall" (a mounted radar watching a person's height drop
  // suddenly) -- an unconfigured sensor can read a degenerate/stuck fall
  // state. Values below match Seeed's own documented defaults.
  if (mmWave.setInstallationHeight(2.5)) {
    Serial.println("setInstallationHeight(2.5) OK");
  } else {
    Serial.println("setInstallationHeight(2.5) FAILED");
  }
  if (mmWave.setThreshold(0.6)) {
    Serial.println("setThreshold(0.6) OK");
  } else {
    Serial.println("setThreshold(0.6) FAILED");
  }
  if (mmWave.setSensitivity(3)) {
    Serial.println("setSensitivity(3) OK");
  } else {
    Serial.println("setSensitivity(3) FAILED");
  }
}

void loop() {
  if (mmWave.update(100)) {
    bool is_human = mmWave.getHuman();
    bool is_fall = mmWave.getFall();

    Serial.print("FDA,");
    Serial.print(is_human ? 1 : 0);
    Serial.print(",");
    Serial.println(is_fall ? 1 : 0);

    Serial1.print("FDA,");
    Serial1.print(is_human ? 1 : 0);
    Serial1.print(",");
    Serial1.println(is_fall ? 1 : 0);
  }
}
