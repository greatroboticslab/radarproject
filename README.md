[README.md](https://github.com/user-attachments/files/33522735/README.md)
# Radar Project

Radar sensing projects built mainly around the **TI IWR6843AOP 60 GHz mmWave radar**,
plus the ESP32/Arduino sensor boxes used with it.

> **Every project folder has its own README.** Open the folder and read that first.
> It covers setup, how to run the code, and project details.

## Folders

| Folder | What it is | Read first |
|---|---|---|
| [`IWR6843_Radar/`](IWR6843_Radar/) | Real-time 3D point-cloud visualizer and hand distance meter for the IWR6843AOP (Python, Windows). | `README.md` |
| [`IWR_Radar_Distance/`](IWR_Radar_Distance/) | Live distance GUI with CSV logging, plus a radar test of metal, plastic, cardboard and felt boxes, with raw data, figures and findings. | `README.md` |
| [`Radar Ubuntu/`](Radar%20Ubuntu/) | mmWave **soil moisture sensor**: the radar measures the soil-surface reflection and converts it to water content. Original Linux version. | `README-UBUNTU.md`, then `NOTES.md` |
| [`RadarProject V2/`](RadarProject%20V2/) | Later snapshot of the soil moisture project (moved to a new machine 2026-07-31), plus the Arduino firmware for the sensor boxes. Contents below. | `README.txt` |
| `GUI.png`, `LOG.png` | Screenshots of the soil moisture GUI and one of its data logs. | – |

### Inside `RadarProject V2/`

| Subfolder | What it is |
|---|---|
| `Radar Ubuntu/` | Newer version of the soil moisture GUI, with recorded sensor data (`.txt` logs) |
| `Smart Box Files/` | ESP32 Smart Box firmware (V2–V8), soil sensor firmware (V2, V3), helper sketches (clock reset, Teensy data switch), wiring diagram, parts list |
| `DopplerRadar/` | ESP32 test sketch for the CQRobot 10.525 GHz Doppler motion sensor |
| `SeeedMR60BHA2/` | Arduino sketch for the Seeed MR60BHA2 60 GHz radar (XIAO ESP32C6) |
| `Seeed_Arduino_mmWave_library/` | Third-party Seeed mmWave Arduino library, copied into your Arduino libraries folder |

## README names differ by folder

`README.md` for the Python projects, `README-UBUNTU.md` + `NOTES.md` for the soil moisture
project (`NOTES.md` has the full background), and `README.txt` for `RadarProject V2`.
