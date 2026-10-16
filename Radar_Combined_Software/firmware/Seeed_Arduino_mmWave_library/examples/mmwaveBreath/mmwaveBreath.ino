#include <Arduino.h>
#include "Seeed_Arduino_mmWave.h"

// If the board is an ESP32, include the HardwareSerial library and create a
// HardwareSerial object for the mmWave serial communication
#ifdef ESP32
#  include <HardwareSerial.h>
HardwareSerial mmWaveSerial(0);
#else
// Otherwise, define mmWaveSerial as Serial1
#  define mmWaveSerial Serial1
#endif

SEEED_MR60BHA2 mmWave;

void setup() {
  Serial.begin(115200);
  mmWave.begin(&mmWaveSerial);
}

void loop() {
  if (mmWave.update(100)) {
    float total_phase, breath_phase, heart_phase;
    if (mmWave.getHeartBreathPhases(total_phase, breath_phase, heart_phase)) {
      Serial.printf("total_phase: %.2f\t", total_phase);
      Serial.printf("breath_phase: %.2f\t", breath_phase);
      Serial.printf("heart_phase: %.2f\n", heart_phase);
    }

    float breath_rate;
    if (mmWave.getBreathRate(breath_rate)) {
      Serial.printf("breath_rate: %.2f\n", breath_rate);
    }

    float heart_rate;
    if (mmWave.getHeartRate(heart_rate)) {
      Serial.printf("heart_rate: %.2f\n", heart_rate);
    }

    float distance;
    if (mmWave.getDistance(distance)) {
      Serial.printf("distance: %.2f\n", distance);
    }
  }
}