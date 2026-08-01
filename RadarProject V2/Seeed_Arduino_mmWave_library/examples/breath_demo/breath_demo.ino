#include <Arduino.h>
#include <U8x8lib.h>
#include "Seeed_Arduino_mmWave.h"
#include <Adafruit_NeoPixel.h>

#ifdef ESP32
#  include <HardwareSerial.h>
HardwareSerial mmWaveSerial(0);
#else
#  define mmWaveSerial Serial1
#endif

U8X8_SSD1306_128X64_NONAME_SW_I2C u8x8(
    /* clock=*/D0, /* data=*/D10,
    /* reset=*/U8X8_PIN_NONE);  // OLEDs without Reset of the Display

Adafruit_NeoPixel pixels = Adafruit_NeoPixel(1, D1, NEO_GRB + NEO_KHZ800);

typedef enum { LABEL_BREATH, LABEL_HEART, LABEL_DISTANCE } Label;

void updateDisplay(Label label, float value) {
  static float last_breath_rate = -1.0;
  static float last_heart_rate  = -1.0;
  static float last_distance    = -1.0;
  switch (label) {
    case LABEL_BREATH:
      if (value == last_breath_rate)
        break;
      u8x8.setCursor(11, 3);
      u8x8.print(value);
      last_breath_rate = value;
      break;
    case LABEL_HEART:
      if (value == last_heart_rate)
        break;
      u8x8.setCursor(11, 5);
      u8x8.print(value);
      last_heart_rate = value;
      break;
    case LABEL_DISTANCE:
      if (value == last_distance)
        break;

      u8x8.setCursor(11, 7);
      u8x8.print(value);
      last_distance = value;
      break;
    default:
      break;
  }
}

void create_page(void) {}

SEEED_MR60BHA2 mmWave;
static const char* TAG_Breath   = "BreathRate";
static const char* TAG_Heart    = "HeartRate";
static const char* TAG_Distance = "Distance";

void setup() {
  Serial.begin(115200);
  mmWave.begin(&mmWaveSerial);
  Serial.println("Welcome, my heart is beatin'");

  pixels.begin();                                    // 初始化 RGB 灯控制
  pixels.setPixelColor(0, pixels.Color(0, 125, 0));  // 初始化 RGB 灯为Green
  pixels.setBrightness(8);
  pixels.show();

  u8x8.begin();
  u8x8.setFlipMode(
      3);  // set number from 1 to 3, the screen word will rotary 180
  u8x8.clearDisplay();
  u8x8.setFont(u8x8_font_victoriamedium8_r);
  u8x8.setCursor(1, 0);
  u8x8.print("Rate & Distance");

  u8x8.setCursor(0, 3);
  u8x8.print(TAG_Breath);

  u8x8.setCursor(0, 5);
  u8x8.print(TAG_Heart);

  u8x8.setCursor(0, 7);
  u8x8.print(TAG_Distance);
  u8x8.setFont(u8x8_font_chroma48medium8_n);
}

void loop() {
  if (mmWave.update(100)) {
    float breath_rate = 0;
    float heart_rate  = 0;
    float distance    = 0;

    if (mmWave.getBreathRate(breath_rate)) {
      updateDisplay(LABEL_BREATH, breath_rate);
    }

    if (mmWave.getHeartRate(heart_rate)) {
      if ((heart_rate - 75) < 0) {
        pixels.setPixelColor(
            0, pixels.Color(125, 0, 0));  // if heart is lower than 75, red
      } else {
        pixels.setPixelColor(0, pixels.Color(0, 125, 0));
      }
      pixels.show();
      updateDisplay(LABEL_HEART, heart_rate);
    }

    if (mmWave.getDistance(distance)) {
      updateDisplay(LABEL_DISTANCE, distance);
      if ((70 - distance) < 0) {
        Serial.print("No one here\n");
      }
    }
    Serial.printf("breath_rate: %.2f\n", breath_rate);
    Serial.printf("heart_rate : %.2f\n", heart_rate);
    Serial.printf("distance   : %.2f\n", distance);
  }
}
