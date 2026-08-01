#include <esp_now.h>
#include <WiFi.h>
 
const int AOUT_PIN = 36; // ESP32 pin GIOP36 (ADC0) that connects to AOUT pin of moisture sensor

int moistvalue = 0;
int sensor_number = 1;

const int VbatPin = 34;
const int BattConst = 603.45;
float VbatValue = 0.0000;

uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
 
// Define a data structure
typedef struct struct_message {
  char a[32];
  int b; //moistvalue
  int c; //sensor_number

} struct_message;
 
// Create a structured object
struct_message myData;
 
// Peer info
esp_now_peer_info_t peerInfo;
 
void OnDataSent(const esp_now_send_info_t *info, esp_now_send_status_t status) {
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           info->des_addr[0], info->des_addr[1], info->des_addr[2],
           info->des_addr[3], info->des_addr[4], info->des_addr[5]);

  Serial.print("Last Packet Sent to: ");
  Serial.print(macStr);
  Serial.print(" | Status: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
}
 
void setup() {
  
  Serial.begin(115200);
  delay(2000);
  Serial.println("ESP32 ONLINE");
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.setChannel(11);

  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }

  esp_now_register_send_cb(OnDataSent);

  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = 11;  
  peerInfo.encrypt = false;
       
  if (esp_now_add_peer(&peerInfo) != ESP_OK){
    Serial.println("Failed to add peer");
    return;
  }
}
 
void loop() {
  moistvalue = analogRead(AOUT_PIN); 
  VbatValue = analogRead(VbatPin) / BattConst;

  Serial.print("Input Voltage = ");
  Serial.println(VbatValue, 2);
  
  strcpy(myData.a, "The Soil Moisture is: ");
    myData.b = moistvalue;
    myData.c = sensor_number;

  Serial.print ("Moisture Value: ");
    Serial.println(moistvalue);
  Serial.print("Sensor Number: ");
    Serial.println(sensor_number);
  Serial.print("Voltage: ");
    Serial.println(VbatValue);

  esp_err_t result = esp_now_send(broadcastAddress, (uint8_t *) &myData, sizeof(myData));
   
  if (result == ESP_OK) {
    Serial.println("Sending confirmed");
  }else {
    Serial.println("Sending error");
  }
  delay(1000);

  //WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup(900000000); // 15 minutes in microseconds
  esp_deep_sleep_start();
}
