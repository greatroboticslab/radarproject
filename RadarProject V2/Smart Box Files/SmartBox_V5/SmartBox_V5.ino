#include <esp_now.h>
#include <WiFi.h>
#include <Wire.h>
#include <SD.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <RTClib.h>
#include <esp_wifi.h>


// Wi-Fi credentials - replace with your network info
const char* ssid = "Butt Nugget";
const char* password = "t8735yin42%2";

#include <WebServer.h>
WebServer server(80);  // Web server running on port 80

RTC_DS3231 rtc;
LiquidCrystal_I2C lcd(0x27, 20, 4);
long lastMsg = 0;

const int relayPort1 = 2;
const int relayPort2 = 15;
const int Right_Button = 14;
const int Left_Button = 13;
const int Middle_Button = 12;
const int SD_CS = 5;

String filename;
String dataMessage;
String tm;
String day;
int interval = 1000;
int ticker1 = 0;
int ticker2 = 0;
int ticker3 = 0;
int SDcard = 0;
int reconnecting = 0;
bool ledState = LOW;

unsigned long lastBeacon = 0;
const unsigned long beaconInterval = 500;

int Moisture[10] = {0};
int Sensor = 0;

const int numOfScreens = 3;
int currentScreen = 0;

typedef struct struct_message {
  char a[32];
  int b = Moisture[Sensor];
  int c = Sensor;
} struct_message;

struct_message myData;

typedef struct beacon_message {
  char id[8];        // Identifier: helps sensor know it's a beacon
  uint8_t channel;   // The current WiFi channel
} beacon_message;

beacon_message beaconData = { "BEACON", 1 };

bool RTC = false;

bool lastLeftButtonState = HIGH;
bool lastRightButtonState = HIGH;
bool lastMiddleButtonState = HIGH;

bool leftButton = false;
bool rightButton = false;
bool middleButton = false;

unsigned long previousLogTime = 0;
const unsigned long logInterval = 300000; // 5 minutes in milliseconds
uint8_t broadcastAddress[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Function to handle the root URL "/"
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><title>ESP32 Moisture</title>";
  html += "<meta http-equiv='refresh' content='5'>";  // Auto-refresh every 5 seconds
  html += "</head><body>";
  html += "<h1>Soil Moisture Readings</h1>";
  html += "<ul>";
  for (int i = 0; i < 10; i++) {
    html += "<li>Sensor " + String(i) + ": " + String(Moisture[i]) + "</li>";
  }
  html += "</ul>";
  html += "</body></html>";

  server.send(200, "text/html", html);
}

void setup() {
  delay(2000);
  Serial.begin(115200);
  analogReadResolution(12);

  pinMode(relayPort1, OUTPUT);
  pinMode(relayPort2, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT);

  digitalWrite(relayPort1, LOW);
  digitalWrite(relayPort2, LOW);

  pinMode(Right_Button, INPUT_PULLUP);
  pinMode(Middle_Button, INPUT_PULLUP);
  pinMode(Left_Button, INPUT_PULLUP);

  WiFi.mode(WIFI_AP_STA);

  lastLeftButtonState = digitalRead(Left_Button);
  lastRightButtonState = digitalRead(Right_Button);
  lastMiddleButtonState = digitalRead(Middle_Button);

  // Initialize LCD
  lcd.init();
  lcd.clear();
  lcd.backlight();

  // Initialize RTC
  if (!rtc.begin()) {
    Serial.println("Couldn't find RTC");
    Serial.flush();
    while (1);
  }

  // --- Scan WiFi to find channel of the AP ---
  Serial.println("Scanning WiFi networks...");
  int wifiChannel = 1; // default channel fallback
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    String scannedSSID = WiFi.SSID(i);
    if (scannedSSID == String(ssid)) {
      wifiChannel = WiFi.channel(i);
      beaconData.channel = wifiChannel;
      Serial.printf("Found SSID '%s' on channel %d\n", ssid, wifiChannel);
      break;
    }
  }
  WiFi.scanDelete();

  // Connect to WiFi network
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected!");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  // Set ESP32 WiFi channel to match AP channel
  esp_wifi_set_channel(wifiChannel, WIFI_SECOND_CHAN_NONE);
  Serial.printf("Set ESP32 WiFi channel to %d\n", wifiChannel);

  // Initialize ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }

  esp_now_register_recv_cb(OnDataRecv);

  // Add broadcast peer with matched channel
  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, broadcastAddress, 6);
  peerInfo.channel = wifiChannel;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to add peer");
    return;
  }

  // Initialize SD card
  if (!SD.begin(SD_CS)) {
    Serial.println("Card Mount Failed");
    SDcard = 3;
    return;
  }

  uint8_t cardType = SD.cardType();
  if (cardType == CARD_NONE) {
    Serial.println("No SD card attached");
    SDcard = 3;
    return;
  }

  Serial.println("Initializing SD card...");
  getTimeStamp();
  delayMicroseconds(100);
  File file = SD.open(filename);
  writeFile(SD, filename, "Day, Time, Moisture 1, Moisture 2, Moisture 3, Moisture 4, Moisture 5, Moisture 6, Moisture 7, Moisture 8, Moisture 9, Moisture 10, \r\n");

  if (!file) {
    delayMicroseconds(100);
    Serial.println("File doesn't exist");
    Serial.println("Creating file...");
    getTimeStamp();
    writeFile(SD, filename, "Day, Time, Moisture 1, Moisture 2, Moisture 3, Moisture 4, Moisture 5, Moisture 6, Moisture 7, Moisture 8, Moisture 9, Moisture 10, \r\n");
  } else {
    Serial.println("File already exists");
  }
  file.close();

  // Setup web server routes
  server.on("/", handleRoot);

  // Start the web server
  server.begin();
  Serial.println("HTTP server started");
  Serial.println(WiFi.macAddress());
}

void loop() {
  delay(1);

  readButton();
  handleButtonActions();
  printScreen();
  updateSensorData();
  if (millis() - previousLogTime >= logInterval) {
    getTimes();
    logSDCard();
    previousLogTime = millis();
  }
  // Handle incoming client requests to web server
server.handleClient();

if (millis() - lastBeacon >= beaconInterval) {
  sendBeacon();
  lastBeacon = millis();
}

}

void readButton() {
  bool currentRightButtonState = digitalRead(Right_Button);
  bool currentLeftButtonState = digitalRead(Left_Button);
  bool currentMiddleButtonState = digitalRead(Middle_Button);

  // Detect LEFT button press event (HIGH to LOW)
  if (currentLeftButtonState == LOW && lastLeftButtonState == HIGH) {
    leftButton = true;
  }
  lastLeftButtonState = currentLeftButtonState;

  // Detect RIGHT button press event (HIGH to LOW)
  if (currentRightButtonState == LOW && lastRightButtonState == HIGH) {
    rightButton = true;
  }
  lastRightButtonState = currentRightButtonState;

  // Detect MIDDLE button press event (HIGH to LOW)
  if (currentMiddleButtonState == LOW && lastMiddleButtonState == HIGH) {
    middleButton = true;
  }
  lastMiddleButtonState = currentMiddleButtonState;

  // Set RTC true if both right and left buttons pressed (on this event)
  RTC = (rightButton && leftButton);
}

void handleButtonActions(){
  if (RTC == true){
    RTC_menu();
  }else{
    if (leftButton == true){
      left_button_pressed();
    }
    if (rightButton == true){
      right_button_pressed();
    }
  }
  middleButton = false;
}

void printScreen(){
  switch (currentScreen) {
    case 0: {
      //lcd.clear();  // Optional: clear screen before printing

      lcd.setCursor(0, 0);
      lcd.print("Sensor: ");
      lcd.print(Sensor);

      lcd.setCursor(0, 1);
      lcd.print("Moisture: ");
      lcd.print(Moisture[Sensor]);

      lcd.setCursor(0, 3);
      if (SDcard == 0) {
        lcd.print("SD: File success");
      } else if (SDcard == 1) {
        lcd.print("SD: Write error");
      } else if (SDcard == 2) {
        lcd.print("SD: Append error");
      } else if (SDcard == 3) {
        lcd.print("SD: Not available");
      } else {
        lcd.print("SD: Unknown error");
      }

      break;
    }


    case 1:
      handleRelayScreen(relayPort1, ticker1);
      break;

    case 2:
      handleRelayScreen(relayPort2, ticker2);
      break;

    default:
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Invalid screen");
      break;
  }
}

void handleRelayScreen(int relayPin, int &ticker) {
  static int lastTicker = -1;   // Track last ticker to avoid unnecessary updates
  static int lastRelayPin = -1; // Track last relay to detect screen changes

  // Only clear and print header if screen or ticker changed
  if (relayPin != lastRelayPin || ticker != lastTicker) {
    lcd.clear();
    if (relayPin == relayPort2) {
      lcd.setCursor(0, 0);
      lcd.print("  Port 2: ");
      lcd.setCursor(10, 0);
      if ((ticker % 2) == 0) {
        lcd.print("OFF");
      } else {
        lcd.print("ON ");
      }
    } else if (relayPin == relayPort1) {
      lcd.setCursor(0, 0);
      lcd.print("  Port 1: ");
      lcd.setCursor(10, 0);
      // Only show ON if relay is HIGH
      if (digitalRead(relayPin) == HIGH) {
        lcd.print("ON ");
      } else {
        lcd.print("OFF");
      }
    }

    lastTicker = ticker;
    lastRelayPin = relayPin;
  }

  lcd.setCursor(2, 2);
  
  if (digitalRead(Middle_Button) == LOW) {
    middleButton = true;
    delay(250);
  }

  if (middleButton) {
    middleButton = false;
    ticker++;
    if ((ticker % 2) == 0) {
      digitalWrite(relayPin, LOW);
    } else {
      digitalWrite(relayPin, HIGH);
      if (relayPin == relayPort1) {
        // For Port 1 ON delay, consider moving this to loop or non-blocking approach to avoid blocking display updates
        lcd.clear();
        lcd.setCursor(0, 0);
        lcd.print("  Port 1: ON ");
        delay(15000);  // This delay blocks the entire program, consider a timer instead!
        digitalWrite(relayPin, LOW);

        lcd.clear();
        lcd.setCursor(0, 0);
        lcd.print("  Port 1: OFF");
      }
    }
  }
}

void RTC_menu(){
  //button states for left and right getting stuck, unable to exit

  while(RTC){
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("RTC Menu");
    lcd.setCursor(0, 1);
    lcd.print("Hold L=Reset R=Check");
    lcd.setCursor(0, 2);
    lcd.print("Check RTC for EXIT");
    delay(250);

    unsigned long start = millis();
    bool resetHeld = false;
    bool checkHeld = false;

    while (millis() - start < 3000) { // Wait 3 seconds for button hold
      if (digitalRead(Left_Button) == LOW) {
        resetHeld = true;
      }
      if (digitalRead(Right_Button) == LOW) {
        checkHeld = true;
      }
      delay(50);
    }

    if (resetHeld) {
      rtc.adjust(DateTime(__DATE__, __TIME__));
      Serial.println("RTC time reset.");
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("RTC Reset Done");
      delay(1500);
    }

    if (checkHeld) {
      DateTime now = rtc.now();
      Serial.print(now.month(), DEC); Serial.print("/");
      Serial.print(now.day(), DEC); Serial.print("/");
      Serial.print(now.year(), DEC); Serial.print(" ");
      Serial.print(now.hour(), DEC); Serial.print(":");
      Serial.print(now.minute(), DEC); Serial.print(":");
      Serial.println(now.second(), DEC);

      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("Date: ");
      lcd.print(now.month());
      lcd.print("/");
      lcd.print(now.day());
      lcd.print("/");
      lcd.print(now.year());

      lcd.setCursor(0, 1);
      lcd.print("Time: ");
      if (now.hour() < 10) lcd.print("0");
      lcd.print(now.hour());
      lcd.print(":");
      if (now.minute() < 10) lcd.print("0");
      lcd.print(now.minute());
      lcd.print(":");
      if (now.second() < 10) lcd.print("0");
      lcd.print(now.second());

      delay(2000);
      lcd.clear();
      leftButton = false;
      rightButton = false;
      lastLeftButtonState = digitalRead(Left_Button);
      lastRightButtonState = digitalRead(Right_Button);

      RTC = false;
    }
  }
}

void right_button_pressed(){
  if (currentScreen == 0) {
    currentScreen = numOfScreens-1;
  } else {
    currentScreen--;
  }
  ticker1 = 0;
  ticker2 = 0;
  ticker3 = 0;
  lcd.clear();
  rightButton = false;
}

void left_button_pressed(){
  if (currentScreen == numOfScreens-1) {
    currentScreen = 0;
  } else {
    currentScreen++;
  }
  ticker1 = 0;
  ticker2 = 0;
  ticker3 = 0;
  lcd.clear();
  leftButton = false;
}

void updateSensorData() {
  Sensor = myData.c;
  Moisture[Sensor] = myData.b;
}

void writeFile(fs::FS &fs, String path, const char * message) {
  Serial.printf("Writing file: %s\n", path.c_str());
  File file = fs.open(path, FILE_WRITE);
  if(!file) {
    Serial.println("Failed to open file for writing");
    SDcard = 1;
    return;
  }
  if(file.print(message)) {
    Serial.println("File written");
    SDcard = 0; 
  } else {
    Serial.println("Write failed");
    SDcard = 1;
  }
  file.close();
}

void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
  memcpy(&myData, incomingData, sizeof(myData));
  Serial.print("Data received: ");
  Serial.println(len);
  Serial.print("Sensor ");
  Serial.print(myData.c);
  Serial.print(" Soil Moisture 1: ");
  Serial.println(myData.b);
}

void sendBeacon() {
  esp_now_send(broadcastAddress, (uint8_t *) &beaconData, sizeof(beaconData));
}

void getTimeStamp(){
  DateTime now = rtc.now();
  tm = String(now.hour(), DEC) + "-" + String(now.minute(), DEC) + "-" + String(now.second(), DEC);
  day = String(now.month(), DEC) + "-" + String(now.day(), DEC) + "-" + String(now.year(), DEC);
  filename = "/" + day + "_" + tm + ".csv";
  Serial.println(filename);
}

void getTimes(){
  DateTime now = rtc.now();
  tm = String(now.hour(), DEC) + "-" + String(now.minute(), DEC) + "-" + String(now.second(), DEC);
  day = String(now.year(), DEC) + "-" + String(now.month(), DEC) + "-" + String(now.day(), DEC);
}

void logSDCard() {
  File file = SD.open(filename, FILE_APPEND);
  if(file) {
    String data = day + ", " + tm + ", ";
    for (int i = 0; i < 10; i++) {
      data += String(Moisture[i]) + ", ";
    }
    file.println(data);
    file.close();
    SDcard = 0;
  } else {
    Serial.println("Error opening file for appending");
    SDcard = 2;
  }
}
