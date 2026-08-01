// Simple test sketch for the CQRobot 10.525GHz Doppler Effect Microwave Motion
// Sensor (SKU: CQRSENWB01), wired to an ESP32 DevKit V1.
//
// Wiring (sensor switch set to 3.3V mode):
//   Sensor Red (V)   -> ESP32 3V3
//   Sensor Black (G) -> ESP32 GND
//   Sensor Green (S) -> ESP32 D15 (GPIO15)
//
// Prints "MOTION,1" or "MOTION,0" once per second, plus an immediate line
// whenever the state changes, so you can watch it react in real time.

const int SENSOR_PIN = 15;

int lastState = -1;
unsigned long lastPrint = 0;
const unsigned long printInterval = 1000; // 1 second

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(SENSOR_PIN, INPUT);
  Serial.println("DOPPLER RADAR TEST START");
}

void loop() {
  int state = digitalRead(SENSOR_PIN);

  if (state != lastState) {
    Serial.print("MOTION,");
    Serial.println(state);
    lastState = state;
    lastPrint = millis();
  } else if (millis() - lastPrint >= printInterval) {
    Serial.print("MOTION,");
    Serial.println(state);
    lastPrint = millis();
  }
}
