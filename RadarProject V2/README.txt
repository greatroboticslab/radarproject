RadarProject — moved from the Ubuntu machine on 2026-07-31.
Read "Radar Ubuntu/NOTES.md" first — it has full project context.

SETUP ON THE NEW COMPUTER:

1. GUI (Python):
   cd "Radar Ubuntu"
   python3 -m venv .venv
   source .venv/bin/activate      (or .venv\Scripts\activate on Windows)
   pip install -r requirements.txt
   pip install --upgrade "acconeer-exptool[app]"   (for the A121 range sensor source)

   NOTE: the .venv folder was intentionally NOT copied — it has absolute
   paths and compiled binaries baked in for the old machine. Always
   recreate it fresh, per NOTES.md.

2. Arduino firmware (SmartBox_V8, SoilSensor_V3, DopplerRadar, SeeedMR60BHA2):
   - Install Arduino IDE / arduino-cli.
   - Board package: esp32 by Espressif Systems.
     - SmartBox_V8 and SoilSensor_V3 need OLDER core version 2.0.9
       specifically (their ESP-NOW callback signatures only match that
       version — see NOTES.md "Session findings (2026-07-30)" for why).
     - DopplerRadar and SeeedMR60BHA2 work fine on newer core versions
       (SeeedMR60BHA2 needs a core new enough to know the XIAO ESP32C6
       board — anything recent, e.g. 3.x, is fine).
   - Copy "Seeed_Arduino_mmWave_library" folder into your Arduino
     libraries folder (normally ~/Arduino/libraries/ or
     Documents/Arduino/libraries/) — this library isn't in the standard
     Library Manager index, it was manually cloned from
     github.com/Love4yzp/Seeed-mmWave-library.
   - Board FQBNs used on the old machine:
     esp32:esp32:esp32doit-devkit-v1   (SmartBox, SoilSensor, DopplerRadar
                                          — "DOIT ESP32 DEVKIT V1")
     esp32:esp32:XIAO_ESP32C6           (SeeedMR60BHA2)

WHY THIS MOVE HAPPENED:
The Acconeer A121 (XM125+XC120) wasn't being detected over USB on the old
machine even with the board powered on — kernel log showed zero USB
enumeration attempts at all, which points to the USB-C cable being a
charge-only cable (no data lines), not a software/driver problem. No
spare data-capable cable was available, so the project was moved here to
continue testing with different hardware/cable.
