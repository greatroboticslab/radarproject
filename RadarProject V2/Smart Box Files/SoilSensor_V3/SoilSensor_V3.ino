#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>

const int AOUT_PIN = 36;
const int VbatPin = 34;
const int BattConst = 603.45;

int moistvalue = 0;
int sensor_number = 5;
float VbatValue = 0.0000;

uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

typedef struct struct_message {
  char a[32];
  int b;
  int c;
} struct_message;

struct_message myData;

typedef struct beacon_message {
  char id[8];
  uint8_t channel;
} beacon_message;

beacon_message receivedBeacon;

esp_now_peer_info_t peerInfo;

bool beaconReceived = false;

void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  Serial.print("Send Status: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Success" : "Fail");
}

void OnDataRecv(const uint8_t *mac_addr, const uint8_t *data, int len) {
  if (len == sizeof(beacon_message)) {
    memcpy(&receivedBeacon, data, sizeof(receivedBeacon));
    if (strcmp(receivedBeacon.id, "BEACON") == 0) {
      beaconReceived = true;
      Serial.print("Beacon received on channel: ");
      Serial.println(receivedBeacon.channel);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("ESP32 SENSOR START");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_now_init();
  esp_now_register_recv_cb(OnDataRecv);

  for (int ch = 1; ch <= 13; ch++) {
    esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
    Serial.print("Scanning channel: ");
    Serial.println(ch);

    beaconReceived = false;
    unsigned long scanStart = millis();

    while (millis() - scanStart < 300) {
      if (beaconReceived) break;
      delay(10);
    }

    if (beaconReceived) break;
  }

  if (!beaconReceived) {
    Serial.println("Beacon not found. Sleeping...");
    esp_deep_sleep_start();
  }

  esp_wifi_set_channel(receivedBeacon.channel, WIFI_SECOND_CHAN_NONE);
  Serial.print("Switched to channel: ");
  Serial.println(receivedBeacon.channel);

  esp_now_register_send_cb(OnDataSent);

  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = receivedBeacon.channel;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to add peer");
    return;
  }
}

void loop() {
  moistvalue = analogRead(AOUT_PIN);
  VbatValue = analogRead(VbatPin) / BattConst;

  strcpy(myData.a, "The Soil Moisture is: ");
  myData.b = moistvalue;
  myData.c = sensor_number;

  Serial.print("Moisture: ");
  Serial.println(moistvalue);
  Serial.print("Voltage: ");
  Serial.println(VbatValue, 2);

  esp_err_t result = esp_now_send(broadcastAddress, (uint8_t *)&myData, sizeof(myData));

  if (result == ESP_OK) {
    Serial.println("Data sent");
  } else {
    Serial.println("Send failed");
  }

  // TEMPORARY: sending every 5s for testing — restore the 15-min deep sleep below when done.
  delay(5000);
  // delay(1000);
  // esp_sleep_enable_timer_wakeup(900000000); // 15 minutes
  // esp_deep_sleep_start();
}
