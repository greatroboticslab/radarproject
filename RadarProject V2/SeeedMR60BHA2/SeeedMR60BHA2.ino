// Firmware for the Seeed Studio MR60BHA2 60GHz mmWave breathing/heartbeat
// sensor, stacked on its bundled XIAO ESP32C6.
//
// The MR60BHA2 talks to the XIAO over its onboard UART (the two boards are
// pre-assembled via the stacked pin headers — no manual wiring needed).
// This firmware reads breathing rate, heart rate, distance, and presence
// via the official Seeed_Arduino_mmWave library, then forwards them to the
// host GUI over the XIAO's own USB serial connection as a simple CSV line:
//
//   BHA,<present 0/1>,<breath_rate>,<heart_rate>,<distance_m>
//
// A field reads -1 if the sensor didn't have a value for it on that update.

#include <Arduino.h>
#include "Seeed_Arduino_mmWave.h"

#ifdef ESP32
#  include <HardwareSerial.h>
HardwareSerial mmWaveSerial(0);
#else
#  define mmWaveSerial Serial1
#endif

SEEED_MR60BHA2 mmWave;

void setup() {
  Serial.begin(115200);
  delay(500);
  mmWave.begin(&mmWaveSerial);
  Serial.println("MR60BHA2 START");
}

void loop() {
  if (mmWave.update(100)) {
    bool present = mmWave.isHumanDetected();

    float breath_rate = -1;
    bool have_breath = mmWave.getBreathRate(breath_rate);

    float heart_rate = -1;
    bool have_heart = mmWave.getHeartRate(heart_rate);

    float distance = -1;
    bool have_distance = mmWave.getDistance(distance);

    Serial.print("BHA,");
    Serial.print(present ? 1 : 0);
    Serial.print(",");
    Serial.print(have_breath ? breath_rate : -1, 2);
    Serial.print(",");
    Serial.print(have_heart ? heart_rate : -1, 2);
    Serial.print(",");
    Serial.println(have_distance ? distance : -1, 2);
  }
}
