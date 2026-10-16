// A121 relay firmware for a dedicated ESP32 DevKit V1.
// Speaks Acconeer's "Exploration Server" protocol directly over UART1
// (GPIO19=RX1 <- A121 TXD, GPIO18=TX1 -> A121 RXD). The exact protocol
// below was captured by sniffing the real Python acconeer-exptool SDK's
// wire exchange against actual A121 hardware (not guessed/reverse
// engineered from docs alone).
//
// Scope: this does NOT replicate Acconeer's full Detector algorithm
// (calibration, thresholding) -- that's a separate, much bigger task.
// This does simple, uncalibrated peak-amplitude detection from the raw
// I/Q sweep data: real numbers, rougher than the GUI's existing
// Detector-based A121 readout.
//
// Output line format: A121,<distance_m>,<amplitude>

#include <math.h>

HardwareSerial a121Serial(1);
const int A121_RX_PIN = 19; // <- A121 TXD
const int A121_TX_PIN = 18; // -> A121 RXD

// Exact setup command captured from a real session: single sensor, single
// subsweep, 50 points, profile 3, 15.6MHz PRF, start_point 80 -- matches
// acconeer-exptool's SensorConfig(sweeps_per_frame=1, num_points=50).
const char* SETUP_CMD =
  "{\"groups\":[[{\"sensor_id\":1,\"config\":{\"sweep_rate\":0.0,\"frame_rate\":0.0,"
  "\"continuous_sweep_mode\":false,\"double_buffering\":false,"
  "\"inter_frame_idle_state\":\"deep_sleep\",\"inter_sweep_idle_state\":\"ready\","
  "\"sweeps_per_frame\":1,\"subsweeps\":[{\"start_point\":80,\"num_points\":50,"
  "\"step_length\":1,\"profile\":3,\"hwaas\":8,\"receiver_gain\":16,"
  "\"enable_tx\":true,\"enable_loopback\":false,\"phase_enhancement\":false,"
  "\"iq_imbalance_compensation\":false,\"prf\":\"15_6_MHz\"}]}}]],\"cmd\":\"setup\"}\n";

const int START_POINT = 80;
const float BASE_STEP_M = 0.0025f; // approx per-step distance at this config

// Reads one '\n'-terminated line from a121Serial, with an overall timeout.
String readLine(unsigned long timeoutMs) {
  String line;
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (a121Serial.available()) {
      char c = a121Serial.read();
      if (c == '\n') return line;
      line += c;
      start = millis(); // reset timeout while data is actively arriving
    }
  }
  return line; // possibly incomplete on timeout
}

// Extracts the integer value of "payload_size":N from a header line, or -1
// if not present.
int extractPayloadSize(const String& header) {
  int idx = header.indexOf("\"payload_size\":");
  if (idx < 0) return -1;
  idx += strlen("\"payload_size\":");
  int end = idx;
  while (end < (int)header.length() && isDigit(header[end])) end++;
  if (end == idx) return -1;
  return header.substring(idx, end).toInt();
}

// Reads and discards exactly n raw bytes (used to drain unexpected/unused
// payloads during the handshake).
void drainBytes(int n) {
  int remaining = n;
  unsigned long start = millis();
  while (remaining > 0 && millis() - start < 2000) {
    if (a121Serial.available()) {
      a121Serial.read();
      remaining--;
    }
  }
}

// Sends a command, then reads lines (skipping server "log" lines) until one
// containing statusKey is seen, or timeoutMs elapses. Returns true on
// success.
bool sendAndWait(const char* cmd, const char* statusKey, unsigned long timeoutMs) {
  a121Serial.print(cmd);
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    String line = readLine(timeoutMs - (millis() - start));
    if (line.length() == 0) continue;
    if (line.indexOf("\"status\":\"log\"") >= 0) continue;

    int payloadSize = extractPayloadSize(line);
    if (payloadSize > 0) drainBytes(payloadSize);

    if (line.indexOf(statusKey) >= 0) return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("A121 RELAY START");

  a121Serial.begin(115200, SERIAL_8N1, A121_RX_PIN, A121_TX_PIN);
  delay(200);

  Serial.println("Sending get_system_info...");
  if (!sendAndWait("{\"cmd\":\"get_system_info\"}\n", "\"status\":\"ok\"", 3000)) {
    Serial.println("get_system_info FAILED");
  }

  Serial.println("Sending get_sensor_info...");
  if (!sendAndWait("{\"cmd\":\"get_sensor_info\"}\n", "\"status\":\"ok\"", 3000)) {
    Serial.println("get_sensor_info FAILED");
  }

  Serial.println("Sending setup...");
  if (!sendAndWait(SETUP_CMD, "\"status\":\"ok\"", 5000)) {
    Serial.println("setup FAILED");
  }

  Serial.println("Sending start_streaming...");
  if (!sendAndWait("{\"cmd\":\"start_streaming\"}\n", "\"status\":\"start\"", 3000)) {
    Serial.println("start_streaming FAILED");
  }

  Serial.println("Streaming...");
}

void loop() {
  String header = readLine(2000);
  if (header.length() == 0) return;
  if (header.indexOf("\"status\":\"log\"") >= 0) return;

  int payloadSize = extractPayloadSize(header);
  if (payloadSize <= 0) return;

  static uint8_t buf[512];
  if (payloadSize > (int)sizeof(buf)) {
    drainBytes(payloadSize);
    return;
  }

  int received = 0;
  unsigned long start = millis();
  while (received < payloadSize && millis() - start < 1000) {
    if (a121Serial.available()) {
      buf[received++] = a121Serial.read();
    }
  }
  if (received < payloadSize) return; // incomplete frame, drop it

  int numPoints = payloadSize / 4; // 2 bytes I + 2 bytes Q per point
  float peakAmplitude = 0;
  int peakIndex = 0;
  for (int i = 0; i < numPoints; i++) {
    int16_t iVal = (int16_t)(buf[i * 4 + 0] | (buf[i * 4 + 1] << 8));
    int16_t qVal = (int16_t)(buf[i * 4 + 2] | (buf[i * 4 + 3] << 8));
    float amplitude = sqrtf((float)iVal * iVal + (float)qVal * qVal);
    if (amplitude > peakAmplitude) {
      peakAmplitude = amplitude;
      peakIndex = i;
    }
  }

  float distance_m = (START_POINT + peakIndex) * BASE_STEP_M;

  Serial.print("A121,");
  Serial.print(distance_m, 4);
  Serial.print(",");
  Serial.println(peakAmplitude, 1);
}
