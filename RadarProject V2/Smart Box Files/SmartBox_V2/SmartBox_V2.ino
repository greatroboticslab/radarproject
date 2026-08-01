#include <esp_now.h>
#include <WiFi.h>
#include <Wire.h>
#include <SD.h>
//#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <RTClib.h>

RTC_DS3231 rtc;
LiquidCrystal_I2C lcd(0x27, 20, 4);  // LCD address and size
long lastMsg = 0;

const int relayPort1 = 2;  // Relay pins
const int relayPort2 = 15;
// LED_BUILTIN already defined for ESP32, don't redefine it
const int Right_Button = 13;
const int Left_Button = 14;
const int Middle_Button = 12;
const int SD_CS = 5;  // SD card chip select pin

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

int Moisture[10] = {0};
int Sensor = 0;

const int numOfScreens = 3;
int currentScreen = 0;

typedef struct struct_message {
  char a[32];
  int b = Sensor;
  int c = Moisture[Sensor];
} struct_message;

struct_message myData;

bool leftButton = false;
bool middleButton = false;
bool rightButton = false;
bool RTC = false;

void setup() {
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

  WiFi.mode(WIFI_STA);

  lcd.init();
  lcd.clear();         
  lcd.backlight();

  if (! rtc.begin()) {
      Serial.println("Couldn't find RTC");
      return;
  }

  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  esp_now_register_recv_cb(OnDataRecv);

  if(!SD.begin(SD_CS)) {
    Serial.println("Card Mount Failed");
    return;
  }

  uint8_t cardType = SD.cardType();
  if(cardType == CARD_NONE) {
    Serial.println("No SD card attached");
    return;
  }

  Serial.println("Initializing SD card...");
  getTimeStamp();
  delayMicroseconds(100);
  File file = SD.open(filename);
  writeFile(SD, filename, "Day, Time, Moisture 1, Moisture 2, Moisture 3, Moisture 4, Moisture 5, Moisture 6, Moisture 7, Moisture 8, Moisture 9, Moisture 10, \r\n");

  if(!file) {
    delayMicroseconds(100);
    Serial.println("File doesn't exist");
    Serial.println("Creating file...");
    getTimeStamp();
    writeFile(SD, filename, "Day, Time, Moisture 1, Moisture 2, Moisture 3, Moisture 4, Moisture 5, Moisture 6, Moisture 7, Moisture 8, Moisture 9, Moisture 10, \r\n");
  } else {
    Serial.println("File already exists");  
  }
  file.close();
}

void loop() {

  readButton();
  handleButtonActions();
  printScreen();
  updateSensorData();
  getTimes();
  logSDCard();
  delayMicroseconds(100);
}

void readButton(){
  rightButton = (digitalRead(Right_Button) == HIGH);
  leftButton = (digitalRead(Left_Button) == HIGH);
  middleButton = (digitalRead(Middle_Button) == HIGH);
  RTC = rightButton && leftButton;
}

void handleButtonActions(){
  if (RTC == true){
    RTC_menu();
    RTC = false;
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
  lcd.clear();
  switch (currentScreen) {
    case 0: {
      lcd.setCursor(0, 0);
      String wetness = String(Moisture[Sensor]);
      String wet = "Moisture " + String(Sensor + 1) + ": " + wetness;
      lcd.print(wet);
      lcd.setCursor(2, 2);
      if (SDcard == 0) {
        lcd.print("File success");
      } else {
        lcd.print("File error");
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
  lcd.clear();
  lcd.setCursor(0, 0);

  if (relayPin == relayPort1) {
    lcd.print("  Port 1: ");
  } else if (relayPin == relayPort2) {
    lcd.print("  Port 2: ");
  } else {
    lcd.print(" Unknown Port ");
  }

  lcd.setCursor(2, 2);

  if (digitalRead(Middle_Button) == HIGH) {
    middleButton = true;
  }

  if (middleButton) {
    ticker++;
    if ((ticker % 2) == 0) {
      digitalWrite(relayPin, LOW);
      lcd.setCursor(10, 0);
      lcd.print("OFF");
    } else {
      digitalWrite(relayPin, HIGH);
      lcd.setCursor(10, 0);
      lcd.print("ON ");
      if (relayPin == relayPort1) {
        delay(15000);
        digitalWrite(relayPin, LOW);
        lcd.setCursor(10, 0);
        lcd.print("OFF");
      }
    }
  }
}

void RTC_menu(){
  bool reset_time = false;
  bool check_time = false;

  lcd.clear();
  lcd.setCursor(0,0);
  lcd.print("RTC Menu");
  lcd.setCursor(0,1);
  lcd.print("Left: Reset Right: Check");

  delayMicroseconds(500);

  if (digitalRead(Left_Button) == HIGH) {
    reset_time = true;
  }

  if (digitalRead(Right_Button) == HIGH) {
    check_time = true;
  }

  if (reset_time) {
    rtc.adjust(DateTime(__DATE__, __TIME__));
    Serial.println("RTC time reset.");
    reset_time = false;
    delayMicroseconds(500);
  }

  if (check_time) {
    DateTime now = rtc.now();
    Serial.print(now.month(), DEC); Serial.print("/");
    Serial.print(now.day(), DEC); Serial.print("/");
    Serial.print(now.year(), DEC); Serial.print(" ");
    Serial.print(now.hour(), DEC); Serial.print(":");
    Serial.print(now.minute(), DEC); Serial.print(":");
    Serial.println(now.second(), DEC);
    check_time = false;
    delayMicroseconds(500);
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

void updateSensorData(){
  Sensor = myData.b;
  Moisture[Sensor] = myData.c;
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
  } else {
    Serial.println("Write failed");
  }
  file.close();
}

void OnDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
  memcpy(&myData, incomingData, sizeof(myData));
  Serial.print("Data received: ");
  Serial.println(len);
  Serial.print("Sensor ");
  Serial.print(myData.b);
  Serial.print(" Soil Moisture 1: ");
  Serial.println(myData.c);
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
  } else {
    Serial.println("Error opening file for appending");
  }
}
