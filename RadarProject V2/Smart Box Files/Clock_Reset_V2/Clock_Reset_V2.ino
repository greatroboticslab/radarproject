#include <RTClib.h>

RTC_DS3231 rtc;
const int button_mid = 14;
bool clock_reset = false;

void setup() {
  pinMode(button, INPUTPULLUP);   
  Serial.begin(9600);
    delayMicroseconds(1000);
  if (!rtc.begin()) {
    Serial.println("Couldn't find RTC");
    while (1);
  }

  // Optional: print a startup message
  Serial.println("RTC Ready. Press button to reset time.");
}

void reset_time(){
    rtc.adjust(DateTime(__DATE__, __TIME__));
    Serial.println("RTC time reset!");
    clock_reset = false;
    delay(1000);
}

void print_time(){
    DateTime now = rtc.now();
    Serial.print(now.month(), DEC);
    Serial.print("/");
    Serial.print(now.day(), DEC);
    Serial.print("/");
    Serial.print(now.year(), DEC);
    Serial.print(" ");
    Serial.print(now.hour(), DEC);
    Serial.print(":");
    Serial.print(now.minute(), DEC);
    Serial.print(":");
    Serial.println(now.second(), DEC);
    delay(1000);
}

void loop() {
  if (digitalRead(button_mid) == LOW) {
    clock_reset = true;
  }

  if (clock_reset == true) {
    reset_time();
  } else {
    print_time();
  }
}
