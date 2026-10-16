# Radar Combined Software — mmWave Soil Moisture Sensor

Part of [`greatroboticslab/radarproject`](https://github.com/greatroboticslab/radarproject).

A radar-based soil moisture sensing platform built around the **TI IWR6843AOP**
(60–64 GHz FMCW mmWave radar), plus a combined GUI for running several other
radar sensors alongside it from one application.

**Everything in this folder is self-contained** — the GUI, the signal
processing, the sensor firmware, the documentation, and sample data. Start with
[Install](#install), then [Running the GUI](#running-the-gui).

The core idea: water has a dielectric constant of roughly 80, while dry soil
minerals sit around 3–5. That contrast changes how strongly radio waves reflect
off a soil surface, so measuring the reflection strength lets you infer water
content. This is the same physical principle NASA's SMAP and ESA's SMOS
satellites use for orbital soil moisture mapping, applied at close range.

```
Raw ADC (IQ samples)
  → Range FFT                     → complex range profile
  → Detect soil-surface bin       → live standoff distance
  → Normalise vs metal reference  → |Γ| (Fresnel reflection coefficient)
  → εr = ((1 + |Γ|) / (1 − |Γ|))²
  → VWC = −5.3e-2 + 2.92e-2·εr − 5.5e-4·εr² + 4.3e-6·εr³   (Topp 1980)
```

---

## Contents

- [What's in the box](#whats-in-the-box)
- [Install](#install)
- [Running the GUI](#running-the-gui)
- [The sensor sources](#the-sensor-sources)
- [Calibration (required for meaningful VWC)](#calibration-required-for-meaningful-vwc)
- [Data output formats](#data-output-formats)
- [Firmware](#firmware)
- [Repository layout](#repository-layout)
- [Tests](#tests)
- [Known limitations](#known-limitations)

---

## What's in the box

This repo holds one main application plus the firmware it talks to.

**`gui.py` — the combined program.** A Tkinter GUI with a single **Source**
dropdown at the top. Picking a source swaps the whole panel below it. One app,
many sensors, one connection at a time per source.

| Source | Hardware | What it gives you |
|---|---|---|
| 📡 Radar (mmWave) | TI IWR6843AOP EVM | Soil moisture (VWC, εr, \|Γ\|) **and** raw distance — two tabs |
| 🌱 Sensor 0–9 | Capacitive probes via a separate ESP32 hub (ESP-NOW) | Raw ADC + calibrated moisture % per probe — *hub firmware not included, see note* |
| 📶 Doppler Radar | CQRobot 10.525 GHz CW sensor → ESP32 | Binary motion / no-motion |
| 📏 A121 Range Sensor | Acconeer A121 (XM125 + XC120 / Waveshare box) | Distance **and reflector strength (dB)** per target |
| 💓 MR60BHA2 | Seeed 60 GHz breathing/heartbeat → XIAO ESP32C6 | Presence, breath rate, heart rate, distance |
| 🚨 MR60FDA2 | Seeed 60 GHz fall detection → XIAO ESP32C6 | Presence + fall flag |
| 🔌 Combined ESP32 | Doppler + MR60FDA2 + A121, all on **one** shared ESP32 | All three at once over a single USB serial link |

---

## Install

### Ubuntu / Linux

```bash
chmod +x setup.sh run.sh
./setup.sh
```

`setup.sh` is a one-shot installer: it installs `python3-tk` (Tkinter isn't
bundled with system Python on Ubuntu), creates a `.venv`, installs
`requirements.txt`, adds your user to the `dialout` group for serial port
access, and launches the GUI. It's safe to re-run. Expect a `sudo` prompt for
the apt install and group change.

After the first run, just use:

```bash
./run.sh
```

### Windows

```powershell
.\setup.ps1     # first time
.\run.ps1       # afterwards
```

You'll also need the **Silicon Labs CP2105 USB-to-UART driver** on Windows, or
the IWR6843AOP board won't produce COM ports at all.

### Manual

```bash
python3 -m venv .venv
source .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -r requirements.txt
python gui.py
```

### Optional: A121 support

The Acconeer A121 source needs Acconeer's SDK, which is not in
`requirements.txt` because it's a large dependency only one source uses:

```bash
pip install --upgrade "acconeer-exptool[app]"
```

---

## Running the GUI

```bash
python gui.py
```

Pick your sensor from the **Source** dropdown, choose its serial port, connect.
Every source has its own **⏺ Log to File** button that writes a tab-separated
`.txt` into the project root (opens directly in Excel). Logging is throttled to
one row per 15 s (`LOG_FILE_INTERVAL_S` in `gui.py`).

### Finding your serial ports

The port dropdowns populate automatically. On Linux the IWR6843AOP shows up as
two devices — typically `/dev/ttyUSB0` (CLI) and `/dev/ttyUSB1` (data). On
Windows they're two consecutive COM ports: the CP2105 "Enhanced" port is CLI,
"Standard" is data. If a connection fails, try swapping the two.

```bash
python -m serial.tools.list_ports -v    # list everything with descriptions
```

---

## The sensor sources

### 📡 Radar (mmWave) — the main event

Select the source, confirm the CLI and Data ports, and click **Connect & Send
Config**. The config in `config/chirp_config.cfg` is pushed to the board over
the CLI port at connect time (~4 seconds), then data streams on the data port.

This source has **two tabs**:

**Soil Moisture tab** — the calibration workflow and live VWC/εr/|Γ| readout,
plus a rolling VWC plot and a range-profile bar chart. Press **Start
Measurement** to begin computing readings.

**Distance tab** — raw live distance to the strongest return, plus its signal
amplitude. This runs continuously and independently of calibration and of the
Start/Stop state — it's a plain ranging readout, useful for comparing how
different materials (soil, plastic sheeting, foliage) present to the radar. It
has its own **Log to File** button.

> **Standoff is detected automatically.** You don't set a distance anywhere.
> The soil-surface range bin is re-detected on every frame, so the sensor can
> move freely (uneven ground, a rover crossing a field) without reconfiguring.
> Bins inside the radar's near-field boundary (~0.30 m, `near_field_boundary_m`
> in `host/config.py`) are always excluded — inside that radius, antenna
> leakage swamps the real return and soil is physically indistinguishable from
> solid metal.

### 🌱 Sensor 0–9 — capacitive probes

> **Note:** the GUI supports these, but the probe-hub firmware is a separate
> project and is **not included in this repo**. This section documents the
> interface the GUI expects, for anyone wiring up their own hub.

Capacitive soil probes report to an ESP32 hub over ESP-NOW; the hub forwards all
ten slots to the GUI over USB as `DATA,<m0>,…,<m9>` lines once per second. Probe
numbering matches the hub's own `Moisture[0..9]` indices, so "Sensor 5" in the
GUI is the same probe the hub labels 5.

Each probe has a two-point calibration: **Calibrate Dry/Air** and **Calibrate
Water**, which linearly interpolate a moisture percentage between those
endpoints. The shared connection card also lets you push network config to the
hub over the same serial link.

### 📶 Doppler Radar

Reads `MOTION,0/1` lines. Note the raw signal is **active-low** — `0` means
motion. This is a CW Doppler sensor: it detects *movement only*. It cannot
measure distance, and it exposes no amplitude data, so it cannot contribute to
moisture sensing.

### 📏 A121 Range Sensor

Acconeer pulsed-coherent radar. The GUI drives it through Acconeer's Python SDK
and reports **distance and reflector strength in dB** for each detected target —
the strength field is real reflection-amplitude data, which is why this sensor
is interesting for material comparison and not just ranging.

Leave the port field blank to attempt raw-USB auto-detection, or name a port
explicitly. Note that on some boards (the Waveshare A121 box in particular) the
module enumerates as a generic WCH USB-serial chip rather than Acconeer's own
VID/PID, in which case auto-detect fails and you must specify the port.

Default minimum range is 25 cm. The config used here (CFAR threshold with
close-range leakage cancellation disabled) follows Acconeer's documented recipe
for reaching closer, but expect degradation as you approach the sensor.

### 💓 MR60BHA2 / 🚨 MR60FDA2

Seeed 60 GHz modules, each stacked on a XIAO ESP32C6 that decodes the sensor's
binary protocol and relays plain CSV lines over USB. BHA2 gives breathing and
heart rate; FDA2 gives presence and a fall flag.

> **FDA2 caveat:** fall detection is a *relative* measure — it watches for a
> sudden drop in a tracked person's height, inferred from the sensor's
> configured installation height. It expects a ceiling or high-wall mount
> (1–5 m) looking down over where people walk. Mounted low and aimed at soil,
> the `human` flag still works but `fall` is meaningless.

### 🔌 Combined ESP32

All three of the Doppler, MR60FDA2 and A121 running off **one** shared ESP32
DevKit V1, multiplexed as tagged lines over a single USB serial connection:

```
MOTION,<0/1>
FDA,<human 0/1>,<fall 0/1>
A121,<d1>[,<d2>,<d3>,…]      one or more distances, closest first
```

The panel shows three separate status cards so each sensor stays individually
visible. There's also a shared **Test Object** metadata block (name, size,
distance, material) — fill it in before you start logging and those four fields
are written as a header at the top of the log file, which makes systematic
material comparisons much easier to keep straight afterwards.

---

## Calibration (required for meaningful VWC)

Without calibration the radar reports raw amplitude with no physical anchor.
The buttons on the Soil Moisture tab run in order:

| Step | What to do | Why |
|---|---|---|
| ① **Metal Plate** | Put a flat metal sheet (≥ 20×20 cm) under the sensor at **any** distance | Defines \|Γ\| = 1, a perfect reflector |
| ② **Dry Soil** | Remove the plate, level dry soil | VWC = 0 anchor |
| ③ **Add Reference Point** | Prepare soil at a known VWC (gravimetric or TDR-verified), enter the value | Real ground truth |
| ④ **Fit Polynomial** | Degree-2 fit across all reference points | Corrects systematic bias |
| ⑤ **Save** | Writes `config/calibration.json` | Loaded automatically next launch |

**Run step ① more than once, at different distances.** With a single metal
point the system falls back to a physically-motivated default for how signal
strength decays with distance (`amplitude = k / distance²`, appropriate for a
flat, beam-filling reflector). With two or more points at genuinely different
distances it fits that curve *empirically* for your specific setup, which is
what makes readings hold up as the standoff varies.

Steps ③ and ④ are optional. Skip them and the physical Fresnel + Topp model is
used directly — it works, but Topp's equation was derived from 20–200 MHz TDR
data, and at 60 GHz bound-water relaxation shifts εr for the same moisture
content, so expect a systematic bias that empirical calibration corrects.

**Surface preparation matters as much as the electronics.** Wet soil often has
a rougher surface than dry soil, which scatters the beam diffusely and can make
wet soil read *weaker* than dry — the opposite of the dielectric effect you're
trying to measure. Level the surface with a straight edge before every reading.

---

## Data output formats

All logs are tab-separated `.txt`, written to the project root, one row per
15 seconds. Historical captures from development live in [`data/`](data/).

| File pattern | Source | Columns |
|---|---|---|
| `data_*.txt` | Radar — Soil Moisture | `timestamp, vwc_m3m3, eps_r, gamma, phase_rad, soil_bin, standoff_m` |
| `distance_*.txt` | Radar — Distance tab | `timestamp, distance_m, raw_amp` |
| `sensor<N>_*.txt` | Capacitive probe N | `timestamp, raw_adc, moisture_pct` |
| `doppler_*.txt` | Doppler | `timestamp, motion` |
| `a121_*.txt` | A121 | `timestamp, distances_m, strengths_db` |
| `bha2_*.txt` | MR60BHA2 | `timestamp, present, breath_rate, heart_rate, distance_m` |
| `fda2_*.txt` | MR60FDA2 | `timestamp, human, fall` |
| `combined_*.txt` | Combined ESP32 | `timestamp, source, value1, value2` — `source` is `doppler`/`fda2`/`a121` |

Combined-ESP32 and A121 logs may begin with `#`-prefixed Test Object metadata
lines before the header row.

### What's in `data/`

Captures kept from development, useful as format references and as a record of
the drift problem described under [Known limitations](#known-limitations):

- `data_20260715_110524.txt`, `data_20260716_103425.txt` — two ~23-hour
  unattended soil dry-down runs, VWC/εr/|Γ| every 15 s. These are the runs that
  surfaced the long-term signal drift issue.
- `data_20260710_*.txt`, `data_20260714_*.txt` — shorter early radar captures.
- `combined_20260814_133414.txt` — a long multi-sensor capture through the
  Combined ESP32 (16k+ rows, all three sensors interleaved).

---

## Firmware

All sketches are under [`firmware/`](firmware/). Flash with the Arduino IDE or
`arduino-cli`.

| Sketch | Board (FQBN) | Purpose |
|---|---|---|
| `AllThreeCombined/` | `esp32:esp32:esp32doit-devkit-v1` | **Production** — Doppler + Seeed relay + A121 on one ESP32 |
| `DopplerRadar/` | `esp32:esp32:esp32doit-devkit-v1` | Doppler sensor standalone |
| `SeeedMR60FDA2/` | `esp32:esp32:XIAO_ESP32C6` | MR60FDA2 decoder + relay |
| `SeeedMR60BHA2/` | `esp32:esp32:XIAO_ESP32C6` | MR60BHA2 decoder |
| `CombinedESP32/`, `A121Relay/` | — | Superseded earlier iterations, kept for reference |

### Wiring for `AllThreeCombined`

```
CQRobot Doppler    signal → GPIO15        (sensor's own switch MUST be in 3.3V mode)
Seeed XIAO relay   RX2 GPIO16 ← XIAO D1 (TX)
                   TX2 GPIO17 → XIAO D0 (RX)      115200 baud
Acconeer A121      RX1 GPIO19 ← A121 TXD
                   TX1 GPIO18 → A121 RXD          921600 baud
```

### Two gotchas worth knowing before you flash

**1. Core versions can collide.** The sketches here need a *recent* ESP32 core
(the XIAO ESP32C6 doesn't exist in older ones at all). If you also build
ESP-NOW projects that require core 2.0.9, note that installing one core version
replaces the other — keep them in separate arduino-cli environments, pointing
`directories.data` at different folders so both can coexist.

**2. The Seeed library isn't in the Arduino Library Manager.** Clone it
manually into your Arduino libraries folder:

```bash
git clone https://github.com/Love4yzp/Seeed-mmWave-library \
  ~/Arduino/libraries/Seeed_Arduino_mmWave
```

### A121 firmware variants

The A121 needs a different firmware image depending on how it's wired, and
these are **mutually exclusive**:

- **`acc_exploration_server_a121.bin`** (factory default) — speaks Acconeer's
  Exploration Server protocol over **USB only**. Required for the standalone
  A121 source in the GUI. Gives distance *and* strength.
- **`example_detector_distance.bin`** ("UART firmware") — the only variant that
  outputs on the header pins, which is what the Combined ESP32 wiring needs.
  Plain text, **distance only, no strength data**.

Images come from Acconeer/Waveshare and are not redistributed here — download
them from the vendor. Flash with:

```bash
python -m acconeer.exptool.flash flash XM125 --port <PORT> --image <file.bin>
```

Getting into DFU mode needs a genuine **power cycle while holding BOOT** — a
soft reset button tap is not reliable. If the CLI flasher fights the timing,
`python -m acconeer.exptool.app.new` has a GUI flash flow that handles it
better.

---

## Repository layout

```
Radar_Combined_Software/
│
├── gui.py                       THE MAIN PROGRAM — run this
├── requirements.txt             Python dependencies
│
├── setup.sh / run.sh            First-time setup / launch  (Linux)
├── setup.ps1 / run.ps1          First-time setup / launch  (Windows)
│
├── README.md                    This file
├── README-UBUNTU.md             Extra Linux setup detail (udev, dialout, ports)
├── NOTES.md                     Engineering log — read before changing the signal path
│
├── config/
│   ├── chirp_config.cfg         Radar settings pushed to the board at connect time
│   └── calibration.json         Saved calibration (written by the ⑤ Save button)
│
├── host/                        Signal processing — the physics lives here
│   ├── config.py                ChirpConfig / ProcessingConfig / CalibrationData
│   ├── main.py                  Headless CLI entry point (no GUI)
│   ├── capture/
│   │   ├── uart_interface.py    Sends the .cfg over the CLI port, reads data port
│   │   ├── frame_parser.py      Parses the radar's binary TLV frames
│   │   └── dca1000.py           DCA1000EVM raw-ADC driver (untested path)
│   ├── processing/
│   │   ├── range_fft.py         Windowed FFT → complex range profile
│   │   ├── reflection.py        Finds the soil bin, computes |Γ|, range compensation
│   │   └── vwc.py               Fresnel → εr, Topp's equation → moisture
│   └── calibration/
│       └── calibrate.py         Calibration workflow + JSON load/save
│
├── firmware/                    Arduino sketches for the ESP32-side sensors
│   ├── AllThreeCombined/        ← PRODUCTION: Doppler + Seeed + A121 on one ESP32
│   ├── DopplerRadar/            CQRobot Doppler, standalone
│   ├── SeeedMR60FDA2/           Seeed fall-detection decoder + relay
│   ├── SeeedMR60BHA2/           Seeed breathing/heartbeat decoder
│   ├── CombinedESP32/           Earlier two-sensor version (superseded)
│   └── A121Relay/               Earlier A121 approach (superseded)
│
├── data/                        Captured measurement logs (see table above)
└── tests/
    └── test_processing.py       19 unit tests, synthetic data, no hardware needed
```

### Where to look for what

| If you want to… | Go to |
|---|---|
| Run the program | `gui.py` |
| Change radar chirp settings | `config/chirp_config.cfg` |
| Understand/modify the moisture math | `host/processing/vwc.py`, `reflection.py` |
| Change how the soil surface is detected | `host/processing/reflection.py` (`find_soil_peak_bin`) |
| Adjust the near-field exclusion | `host/config.py` (`near_field_boundary_m`) |
| Change calibration behaviour | `host/calibration/calibrate.py` |
| Add a new sensor source to the GUI | `gui.py` — follow the existing `*Thread` + panel pattern |
| Flash the combined sensor board | `firmware/AllThreeCombined/` |
| See past measurements | `data/` |
| Know why something is the way it is | `NOTES.md` |

**[`NOTES.md`](NOTES.md) is worth reading** before changing anything in the
signal path. It's a dated log of what was tried, what broke, and why specific
values were chosen — including several non-obvious bugs that took real
debugging to find (a bandwidth calculation off by 10⁶, an rxGain setting
saturating the receiver, a background-subtraction window that was eating the
real target, and a peak search that locked onto antenna leakage).

### Headless CLI

The pipeline also runs without the GUI:

```bash
python -m host.main --cli-port /dev/ttyUSB0 --data-port /dev/ttyUSB1 --mode uart
python -m host.main --cli-port /dev/ttyUSB0 --mode dca1000 --calibrate
```

---

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

19 tests, all synthetic — no hardware required. They cover the range FFT, the
Fresnel/Topp conversions, live peak detection, near-field exclusion, and the
range-compensation curve fitting.

---

## Known limitations

Documented honestly, because several of these are physical rather than
software problems:

- **Signal drift over hours to days.** The single biggest open issue. Two
  ~23-hour runs showed εr swinging by factors of 2–4× with nothing physically
  touched, and a calibration from one day did not hold two days later. Leading
  suspects are soil water redistribution, humidity cycling, and sensor
  self-heating — never conclusively separated, because ambient temperature and
  humidity were never logged alongside the readings. That's the concrete next
  step for anyone picking this up.
- **Near-field floor.** Below roughly 0.30 m, antenna leakage dominates and
  soil reads the same as solid metal. No config fixes this; it's geometry.
- **Beam footprint grows with distance** (`≈ 2 × standoff × tan(30°)`). At 20 cm
  the footprint is already ~23 cm across. Use a container ≥ 25 cm wide or the
  rim dominates the return.
- **VWC output is clamped to 0.6 m³/m³.** Topp's cubic exceeds that once εr
  passes ~55, so very wet readings all saturate at the same ceiling. Look at εr
  directly when working near saturation.
- **The UART path gives magnitude only, no phase.** Only amplitude-based |Γ|
  estimation is possible. The DCA1000 raw-ADC path would provide full complex
  data but has never been tested.
- **Vegetation confounds soil readings.** Leaf water content drives reflectivity
  the same way soil moisture does, so foliage in the beam measures the plant,
  not the ground.
- **Single TX doesn't work** — `channelCfg 15 1 0` causes `sensorStart Error -1`
  on this SDK version. All three TX antennas are used (TDM).

### Not resolved

- Whether this radar can reliably distinguish plastic sheeting from foliage —
  the Distance tab exists partly to investigate this; no conclusion yet.
- Soil under plastic mulch is plausible on paper (plastic is largely
  RF-transparent), but condensation forming on the underside of the sheeting
  would have a dielectric constant near water's and could dominate the reading.
  Untested.

---

## Hardware reference

| Component | Part |
|---|---|
| Primary radar | IWR6843AOP EVM (antenna-on-package, ~60° beam) |
| Carrier board | MMWAVEICBOOST (needed only for the DCA1000 path) |
| Raw ADC capture | DCA1000EVM — untested |
| Active data path | UART (log-magnitude TLV frames) |

Radar chirp config (`config/chirp_config.cfg`), derived from TI's
`profile_3d.cfg`: 60 GHz start, 166 MHz/µs slope, 21 µs active sweep,
**≈3.5 GHz bandwidth → ≈4.3 cm range resolution**, 256 ADC samples at
12500 ksps, 3 TX / 4 RX, 100 ms frames.

DIP switches on the EVM: all OFF for functional mode (SOP[2:0] = 001). Power
cycle after any change.
