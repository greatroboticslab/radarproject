# Radar Project

Radar sensing work built mainly around the **TI IWR6843AOP 60 GHz mmWave radar**,
plus the ESP32/Arduino sensor boards used alongside it.

> **Every project folder has its own README.** Open the folder and read that
> first — it covers setup, how to run the code, and the project's details.
> This page only explains what lives where.

---

## Active projects

Two projects are under active development. They are independent — different
programs, different radar configs, different goals.

### 📏 [`IWR_Radar_Distance/`](IWR_Radar_Distance/) — distance measurement

A desktop app that reads **distance to the nearest object** from the IWR6843AOP
and displays it live with a scrolling history graph and CSV logging. Also
contains a scripted data-collection tool and a completed experiment on how
different box materials (metal, plastic, cardboard, felt) appear to the radar.

Uses the stock TI mmWave demo firmware's own point-cloud output.

```
IWR_Radar_Distance/
├── README.md              Start here — annotated GUI walkthrough
├── NOTES.txt              Setup notes and troubleshooting
├── radar_gui.py           The GUI application
├── radar_reader.py        Serial handling + TLV frame parsing
├── experiment.py          Scripted data collection
├── make_report_figures.py Generates the report figures
├── default_config.cfg     Radar config (exported from TI Demo Visualizer)
├── docs/screenshots/      Annotated interface images
├── logs/experiment/       Raw captured data
└── report/                FINDINGS.txt + figures/
```

### 🌱 [`Radar_Combined_Software/`](Radar_Combined_Software/) — combined radar system

The **soil moisture sensor** plus a combined GUI that runs several radars from
one application. Measures the soil-surface reflection and converts it to
volumetric water content via the Fresnel equation and Topp's equation.

Includes the ESP32 firmware that multiplexes three sensors (CQRobot Doppler,
Seeed MR60FDA2, Acconeer A121) onto a single USB connection.

Uses a custom range-FFT pipeline — **not** the same approach or config as the
distance project above.

```
Radar_Combined_Software/
├── README.md              Start here — full setup and usage
├── NOTES.md               Engineering log: findings, bugs, why things are as they are
├── README-UBUNTU.md       Extra Linux setup detail
├── gui.py                 THE MAIN PROGRAM
├── setup.sh / run.sh      Linux setup and launch
├── setup.ps1 / run.ps1    Windows setup and launch
├── config/                Radar chirp config + saved calibration
├── host/                  Signal processing — FFT, reflection/Γ, moisture conversion
├── firmware/              Arduino sketches (AllThreeCombined is the production one)
├── data/                  Captured measurement logs
└── tests/                 Unit tests, no hardware needed
```

---

## Earlier snapshots

Kept for history. **Superseded by `Radar_Combined_Software/`** above — use that
for any new work on the soil moisture system.

| Folder | What it is |
|---|---|
| [`Radar Ubuntu/`](Radar%20Ubuntu/) | First Linux version of the soil moisture GUI. |
| [`RadarProject V2/`](RadarProject%20V2/) | Later snapshot (2026-07-31) of the soil moisture project, plus Arduino firmware for the sensor boxes. See its `README.txt`. |
| [`IWR6843_Radar/`](IWR6843_Radar/) | Real-time 3D point-cloud visualizer and hand distance meter (Python, Windows). |
| `GUI.png`, `LOG.png` | Screenshots of the soil moisture GUI and one of its data logs. |

### Inside `RadarProject V2/`

| Subfolder | What it is |
|---|---|
| `Radar Ubuntu/` | Soil moisture GUI with recorded sensor data (`.txt` logs) |
| `Smart Box Files/` | ESP32 Smart Box firmware (V2–V8), soil sensor firmware, helper sketches, wiring diagram, parts list |
| `DopplerRadar/` | ESP32 sketch for the CQRobot 10.525 GHz Doppler motion sensor |
| `SeeedMR60BHA2/` | Sketch for the Seeed MR60BHA2 60 GHz radar (XIAO ESP32C6) |
| `Seeed_Arduino_mmWave_library/` | Third-party Seeed mmWave Arduino library |

---

## Which folder do I want?

| I want to… | Go to |
|---|---|
| Measure distance to an object | `IWR_Radar_Distance/` |
| Measure soil moisture | `Radar_Combined_Software/` |
| Run several radars at once from one GUI | `Radar_Combined_Software/` |
| Flash the three-sensor ESP32 board | `Radar_Combined_Software/firmware/AllThreeCombined/` |
| See the box-material experiment results | `IWR_Radar_Distance/report/FINDINGS.txt` |
| Understand why the soil moisture code is the way it is | `Radar_Combined_Software/NOTES.md` |

---

## A note on README names

They differ by folder: `README.md` for the Python projects,
`README-UBUNTU.md` + `NOTES.md` for the soil moisture project (`NOTES.md` has
the full background), and `README.txt` for `RadarProject V2`.

---

## Hardware

| Component | Used by |
|---|---|
| TI IWR6843AOP EVM (60–64 GHz mmWave) | Both active projects |
| ESP32 DevKit V1 | Combined system — hosts all three secondary sensors |
| Acconeer A121 (XM125) | Combined system — distance + reflector strength |
| Seeed MR60FDA2 / MR60BHA2 (XIAO ESP32C6) | Combined system |
| CQRobot 10.525 GHz Doppler | Combined system — motion only |
| Capacitive soil probes + ESP32 hub | Soil moisture, via ESP-NOW |
