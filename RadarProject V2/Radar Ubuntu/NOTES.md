# mmWave Soil Moisture Sensor – Project Notes

> **For Claude:** Read this file at the start of every session on this project.
> It contains the full context, current state, and gotchas so you can pick up without re-reading the chat history.

---

## What this project is

A soil moisture sensor built around the **Texas Instruments IWR6843AOP EVM** (60–64 GHz mmWave radar).
The radar mounts a few centimetres above bare soil, facing straight down.
It measures the strength of the specular reflection off the soil surface, converts that to a dielectric constant (εr), and applies **Topp's equation** to get volumetric water content (VWC) in m³/m³.

**Physics pipeline:**
```
Raw ADC (IQ samples)
  → Range FFT  →  complex range profile
  → Extract bin at soil surface
  → Normalize by metal-plate reference  →  |Γ| (Fresnel reflection coeff)
  → εr  =  ((1 + |Γ|) / (1 − |Γ|))²
  → VWC  =  −5.3e-2 + 2.92e-2·εr − 5.5e-4·εr² + 4.3e-6·εr³   (Topp 1980)
```

---

## Current project state (as of 2026-07-30)

- **The GUI's Source selector now covers six sources**, not just radar + 3 probes: `📡 Radar (mmWave)`, `🌱 Sensor 0`–`9` (capacitive probes, matching the SmartBox's own numbering), `📶 Doppler Radar`, `📏 A121 Range Sensor`, and `💓 MR60BHA2 (Breath/Heart)`. See "Session findings (2026-07-24, Doppler radar)" and "Session findings (2026-07-30, A121 + MR60BHA2)" below for full detail on the newest three.
- **⚠️ Two separate ESP32 Arduino core environments now exist on this machine, and it matters which one you compile against:**
  - **Default (`~/.arduino15`, core 3.3.11)** — used for anything targeting a chip newer than plain ESP32, e.g. the XIAO ESP32C6 (MR60BHA2 kit). No extra flag needed.
  - **Isolated (`--config-file /home/lab/arduino-cli-2.0.9.yaml`, core 2.0.9)** — **required** for SmartBox and SoilSensor firmware, which use ESP-NOW callback signatures only compatible with 2.0.9. **Compiling SmartBox_V8 or SoilSensor_V3 against the default environment will fail** — always pass that `--config-file` flag for those two. Both environments share the same library folder (`/home/lab/Arduino/libraries`), so libraries only need to be installed once. See "Session findings (2026-07-30)" for how/why this split exists — don't try to consolidate back to one shared core install without re-reading that section, it was a real breakage, not a hypothetical.
  - **DopplerRadar** and the **new MR60BHA2 sketch** both compile fine under the default 3.3.11 environment (already verified).
- **New: capacitive-probe integration added to the GUI.** A separate hardware system ("SmartBox", an ESP32 hub) receives data from soil-moisture probes over ESP-NOW. See "Session findings (2026-07-21)" below for full detail — read that section before touching anything cap-probe related.
- **⚠️ Physical SmartBox hardware currently has V8 firmware flashed on it** (`Smart Box Files/SmartBox_V8/SmartBox_V8.ino`), not V6. V6's `.ino` source file is back to its original, unmodified state (confirmed byte-for-byte) but **has not been reflashed to the device** — V8 is still what's actually running on the box right now. Don't assume "V6" when checking box behavior; check what's actually flashed.
- **⚠️ V6, as originally written, does not compile against the default core install** — fails on `LED_BUILTIN` undefined for the generic "ESP32 Dev Module" board. This turned out to be a **board-selection issue, not a toolchain bug**: compiling with FQBN `esp32:esp32:esp32doit-devkit-v1` ("DOIT ESP32 DEVKIT V1" — the actual board in use) instead of the generic `esp32:esp32:esp32` lets V6 compile with zero code changes. Always use the DOIT FQBN for this project's ESP32 boards.
- **SoilSensor_V3 currently has `sensor_number = 5`** (last probe reflashed). If reflashing "sensor 1" again, remember to change this back — it's easy to flash the wrong probe number if picking this up mid-session.
- **SoilSensor_V3's send interval is temporarily 5 seconds** (`delay(5000)` in `loop()`), not the original 15-minute deep sleep — changed for testing, explicitly meant to be reverted later, still not reverted as of this writing.
- Everything from the 2026-07-17 radar findings below is still current and unchanged (rxGain=30, clutterRemoval=0, calibDcRangeSig `-5 1`, signal drift issue still open).

---

## Previous project state (as of 2026-07-17)

- **Running on Ubuntu** (this folder, `Radar Ubuntu`) — CLI/Data ports are `/dev/ttyUSB0` / `/dev/ttyUSB1`. Standoff is now **manually re-measured per session** — see drift issue below — or set via the new **"🎯 Auto-Detect Standoff" button** (scans the live range profile, skips bins 0–1, sets the field to the true peak's bin).
- **Container is confirmed non-metallic (plastic)** for current testing, ~4" deep.
- **`chirp_config.cfg` changes this stretch (all still in effect):**
  - `profileCfg` rxGain: `158` → **`30`**. The original `158` was a real bug — nowhere near a sane dB value for this field — and was partially saturating the receiver, compressing the dynamic range between very different materials. Confirmed measurable improvement after fixing (see below).
  - `calibDcRangeSig -1 1 -5 8 256` → **`calibDcRangeSig -1 1 -5 1 256`**. The original `-5 8` window overlaps with the real target bin at typical standoffs (bin 2–5), so the "background" self-calibration was subtracting out part of the real soil signal, not just leakage. Narrowed to `-5 1` so it only touches bins safely below any realistic target. **If you change standoff, double check the new target bin (probably 2–6) is still outside this window — if not, narrow it further.**
  - `clutterRemoval` — **tested both ways, landed on `0` (off).** `clutterRemoval -1 1` continuously subtracts anything that isn't changing frame-to-frame — but every target in this application (soil, metal calibration plate) is deliberately held motionless, so it got suppressed identically to real leakage. Directly confirmed: with clutter removal on, a real metal plate and real dry soil read the *same* value (both suppressed toward the same floor), regardless of plate size. With it off, metal and soil separated properly (gamma 1.0 vs 0.32ish) using a calibration captured under `clutterRemoval=0`. **Do not re-enable this without re-testing metal vs. soil separation first.**
- **A "Γ=0 open-air reference" calibration step (zero-and-span normalisation) was built, tested, and then fully reverted** at user request — added complexity without net benefit once the config fixes above were in place. `CalibrationData` only has `metal_amplitude` again, no `air_amplitude`. Don't re-add without being asked.
- **Known unresolved issue: significant signal drift over hours-to-days**, even with nothing about the physical setup changed. See "Session findings (2026-07-14 to 2026-07-17)" below — this is the main open problem now, more than any single bug. Calibration captured on one day does not reliably hold up 1–2 days later; expect to recalibrate (metal + dry) close to whenever you actually want a trustworthy reading.
- **Multi-day logging is set up for unattended runs:** system suspend disabled (`gsettings ... sleep-inactive-*-type nothing`), USB autosuspend disabled on the CP2105 adapter and its upstream hub/controller (`power/control` → `on`). Data log file writes are throttled to 1 row per 15s (`LOG_FILE_INTERVAL_S` in `gui.py`) regardless of live measurement rate.
- **TODO: remove remaining lightweight `DIAG` logging** in `gui.py`'s `_process()` (single `log.info(...)` line per reading) once drift investigation is done — see "Known limitations."
- **DCA1000 path still not tested.** UART is the active path.

---

## Hardware

| Component | Part |
|---|---|
| Radar sensor | IWR6843AOP EVM (Antenna-on-Package, ~60° beam) |
| Carrier board | MMWAVEICBOOST (provides 60-pin connector for DCA1000) |
| Raw ADC capture | DCA1000EVM (Ethernet UDP streaming) — not yet tested |
| Active path | UART data port (log-magnitude only, no phase) |
| Host OS | Windows (COM ports for UART, NIC for DCA1000) |

**DCA1000 network setup (must be done manually once):**
- Connect DCA1000 Ethernet to host NIC.
- Set that NIC to static IP `192.168.33.30 / 255.255.255.0`.
- DCA1000 uses `192.168.33.180` by default.

**UART COM ports (confirmed on this machine):**
- **CLI port: COM4** — "XDS110 Class Application/User UART" (responds with `mmwDemo:/>`)
- **Data port: COM3** — "XDS110 Class Auxiliary Data Port" (binary TLV frames at 921600 baud)
- **NOTE:** The NOTES originally said lower COM = CLI. On this machine it is reversed. COM4 is CLI, COM3 is data. The GUI now defaults to this correctly.

**DIP switches (IWR6843AOPEVM standalone):**
- All switches OFF for functional mode (SOP[2:0] = 001).
- Power cycle after any switch change.

---

## Chirp configuration (`config/chirp_config.cfg`)

Uses the same parameters as the TI out-of-box `profile_3d.cfg` — confirmed working.

| Parameter | Value | Notes |
|---|---|---|
| Start frequency | 60 GHz | |
| Freq slope | 166 MHz/µs | |
| ADC start time | 3 µs | settle time |
| Ramp end time | 24 µs | |
| **Active sweep** | **21 µs** | ramp_end − adc_start |
| **Bandwidth** | **≈ 3.5 GHz** | slope × sweep |
| **Range resolution** | **≈ 4.3 cm** | c / (2 × BW) |
| ADC samples | 256 | |
| Sample rate | 12500 ksps | |
| Max range | ≈ 9.7 m | |
| TX antennas | 3 (TDM) | all 3 TX required — single TX causes sensorStart Error -1 |
| RX antennas | all 4 | |
| Chirps/frame | 32 loops × 3 chirps | |
| Frame period | 100 ms | 10 fps |

At 20 cm standoff the soil surface falls on **range bin ~5** (bin = standoff / range_res).

**Important gotchas in chirp_config.cfg:**
- `channelCfg` must be `15 7 0` (3 TX active). Using 1 TX (`15 1 0`) causes `sensorStart Error -1`.
- `compRangeBiasAndRxChanPhase` must have **24 phase values** (3 TX × 4 RX = 12 pairs). Using 8 values (1 TX × 4 RX) causes "Invalid usage" error.
- `lvdsStreamCfg` must be `-1 0 0 0` for UART-only path (dataFmt=0). Setting dataFmt=1 enables LVDS streaming and conflicts with UART operation.
- `bpmCfg` is "not recognized" by this firmware but does not cause a fatal error — it times out silently (2 s delay).

---

## Project structure

```
mmwave-soil-moisture/
│
├── SoilMoistureSensor.exe       ← compiled GUI (NOTE: built before recent fixes — rebuild before shipping)
├── gui.py                       ← run with "python gui.py" for development
├── requirements.txt             ← numpy, scipy, pyserial, matplotlib
├── NOTES.md                     ← this file
│
├── config/
│   ├── chirp_config.cfg         ← radar CLI commands sent at connect-time
│   └── calibration.json         ← written by calibration workflow (does not exist yet)
│
├── data_YYYYMMDD_HHMMSS.txt     ← raw reading logs (created by "Log to File" button)
│
├── host/
│   ├── config.py                ← ChirpConfig, ProcessingConfig, CalibrationData dataclasses
│   ├── main.py                  ← CLI entry point (alternative to GUI)
│   │
│   ├── capture/
│   │   ├── uart_interface.py    ← sends .cfg commands to CLI port; reads data port
│   │   ├── dca1000.py           ← UDP driver for DCA1000EVM (raw ADC streaming)
│   │   └── frame_parser.py      ← TLV binary frame parser for UART data path
│   │
│   ├── processing/
│   │   ├── range_fft.py         ← windowed FFT, coherent RX sum + chirp average
│   │   ├── reflection.py        ← soil-bin extraction, rolling average, |Γ| normalisation
│   │   └── vwc.py               ← Fresnel → εr; Topp's equation → VWC
│   │
│   └── calibration/
│       └── calibrate.py         ← CalibrationManager: load/save JSON, fit polynomial
│
└── tests/
    └── test_processing.py       ← unit tests (synthetic data, no hardware needed)
```

**Related firmware/hardware, outside this folder (all on `~/Desktop`):**
```
~/Desktop/
├── Smart Box Files/
│   ├── SmartBox_V6/SmartBox_V6.ino    ← original baseline, untouched, needs isolated 2.0.9 core to compile
│   ├── SmartBox_V7/                   ← superseded, not in use
│   ├── SmartBox_V8/SmartBox_V8.ino    ← currently flashed on the physical box, needs isolated 2.0.9 core
│   └── SoilSensor_V3/SoilSensor_V3.ino ← probe firmware, needs isolated 2.0.9 core, sensor_number = 5 currently
├── DopplerRadar/DopplerRadar.ino      ← CQRobot 10.525GHz Doppler sensor firmware (ESP32 DevKit V1)
└── SeeedMR60BHA2/SeeedMR60BHA2.ino    ← Seeed MR60BHA2 relay firmware (XIAO ESP32C6), not yet flashed
```

---

## Running the project

### Moving to a new computer
The `.venv` folder in this directory is **not portable** — it has absolute paths and compiled binaries baked in for this specific machine/OS/Python version. Don't copy it; recreate it fresh on the new machine:
```
cd "Radar Ubuntu"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Everything else in this folder (code, config, NOTES.md) is plain text and portable as-is.

### GUI (normal use)
```
python gui.py          (from the mmwave-soil-moisture directory)
```
The `.exe` was built before recent bug fixes — use `python gui.py` until it is rebuilt.

### CLI (alternative, no GUI)
```
python -m host.main --cli-port COM4 --data-port COM3 --mode uart --standoff 0.20
```

### Tests (no hardware needed)
```
cd mmwave-soil-moisture
python -m pytest tests/ -v
```

---

## GUI walkthrough

1. GUI opens with **COM4 (CLI), COM3 (Data), UART mode** pre-selected — do not change these.
2. **Set standoff** (metres) = physical distance from antenna face to soil surface.
3. **Click "Connect & Send Config"** — config sends in ~4 seconds if the board is responsive.
4. **Live readings** update in the right panel every ~1 second (10 frames averaged).
5. **"⏺ Log to File"** button — click to start writing readings to `data_YYYYMMDD_HHMMSS.txt`. Click again to stop.
6. **Calibration** — use buttons ① → ⑤ in order (see calibration section below).

**If the board becomes unresponsive (config takes >30 seconds):**
- Unplug USB, wait 5 seconds, replug, then reconnect.

---

## Calibration workflow

Must be done before readings are physically meaningful. Steps:

| Button | Action | Notes |
|---|---|---|
| ① Metal Plate | Place flat aluminium sheet (≥ 20×20 cm) at standoff → collect 20 frames | Sets the \|Γ\|=1 reference amplitude |
| ② Dry Soil | Remove plate, dry soil surface → collect 20 frames | VWC = 0 anchor point |
| ③ Add Ref Point | Enter gravimetric/TDR VWC → collect 20 frames | Repeat at 3–5 moisture levels |
| ④ Fit Polynomial | Degree-2 poly fit from all reference points | Needs ≥ 3 points |
| ⑤ Save | Writes `config/calibration.json` | Loaded automatically on next launch |

**If you skip ③/④:** the physical Fresnel + Topp model is used automatically. It works but may have a systematic bias at 60 GHz vs. Topp's original 20–200 MHz TDR data.

**calibration.json schema:**
```json
{
  "metal_amplitude": 12345.6,
  "calibration_points": [{"amplitude": 1000.0, "vwc": 0.02}, ...],
  "poly_coeffs": [a0, a1, a2],
  "notes": "Sandy loam, 20 cm standoff",
  "created_at": "2026-06-23T15:00:00"
}
```

---

## Session findings (2026-07-30) — Acconeer A121 + Seeed MR60BHA2 integration

### Context: a third mystery part turned out to be an LD2450, and was NOT integrated
User had an unlabeled part ("EC Buying SKU 87786") — turned out to be a rebranded **Hi-Link HLK-LD2450**, a 24GHz FMCW multi-target tracking radar (reports up to 3 targets' X/Y position + speed over UART, 256000 baud). Read the official Hi-Link serial protocol PDF directly to confirm its data fields. **Conclusion: like the Doppler sensor, it has no reflectivity/signal-strength output** — its reported fields are only X, Y, speed, and distance-gate resolution per target, nothing amplitude-related. **This part was only identified/discussed, not integrated into the GUI** — no source was added for it, and nothing should be assumed about it being wired in.

### A121 Range Sensor — direct-USB integration
Hardware: **Acconeer A121 (XM125 module + XC120 connector board)** — confirmed by the user to have a USB-C port directly on the XC120 board, so it connects straight to whatever computer runs the GUI, no ESP32 intermediary.

- Installed the official **`acconeer-exptool`** Python SDK (`pip install acconeer-exptool[app]`, v7.18.2) into the project's `.venv`. This gives direct Python access to the sensor over USB — no custom serial protocol needed.
- Confirmed via the official example (`examples/a121/algo/distance/detector.py` in `acconeer/acconeer-python-exploration` on GitHub) the exact API pattern used: `a121.Client.open(usb_device=True)` → `Detector(client, sensor_ids=[1], detector_config=...)` → `detector.calibrate_detector()` → `detector.start()` → loop on `detector.get_next()`.
- **The distance detector's `DetectorResult` includes a `strengths` field** (dB, peak reflector amplitude at each detected distance) — confirmed straight from the library source (`_detector.py`). This is genuine reflection-amplitude data, unlike the Doppler sensor or LD2450 — worth keeping in mind if reflectivity-adjacent sensing is wanted from one of the newer radars, though the A121 is still a short-range (≤2m) presence/tracking part, not purpose-built for material characterization.
- `gui.py` changes: new `MsgA121Data`/`MsgA121Status`/`MsgA121Connected`/`MsgA121ThreadDone` dataclasses, `A121Thread` class (runs the SDK client/detector loop, pushes `MsgA121Data(distances_m, strengths_db)` per update), `📏 A121 Range Sensor` added to the source list, `_build_a121_panel()` (Connect/Disconnect only — no port picker, since `usb_device=True` auto-detects), status display shows each detected target's distance + strength.
- Refactored the source-selector plumbing: `_on_source_change()` and the capacitive-probe reading guard previously used fragile positional indexing (`self._source_names[-1]`) to detect "is this the Doppler source" — broke as soon as a second non-radar/non-cap source was appended. Replaced with explicit name attributes (`self._doppler_source_name`, `self._a121_source_name`, `self._bha2_source_name`) checked by identity instead of position. Any future source addition should follow this same pattern, not positional indexing.
- **Not yet tested against real hardware** — user doesn't have a USB-C cable yet, brings one tomorrow (2026-07-31). Code is written and syntax-verified, GUI launches cleanly with the new source visible, but the actual USB connection / calibration / live data path is unverified.

### MR60BHA2 — Seeed 60GHz breathing/heartbeat sensor, ESP32-relay integration
Hardware: **Seeed MR60BHA2** (60GHz mmWave breathing/heart-rate/presence sensor), stacked pre-assembled on a **XIAO ESP32C6**. Two USB-C ports exist on the assembled kit — the one **on the XIAO module** is for flashing/data (what matters for us); the one **on the main sensor board** is power-only (5V/1A in), carries no data. The MR60BHA2 itself only speaks UART to the XIAO — it has no USB of its own — so the XIAO must run firmware that relays parsed values out over its own USB serial connection, same pattern as the Doppler radar and capacitive probes.

- New sketch: **`/home/lab/Desktop/SeeedMR60BHA2/SeeedMR60BHA2.ino`**. Uses the official `Seeed_Arduino_mmWave` library (cloned from `github.com/Love4yzp/Seeed-mmWave-library` into `/home/lab/Arduino/libraries/Seeed_Arduino_mmWave` — **not available via the Arduino Library Manager index under that name**, had to be installed manually via git clone). API confirmed directly from the library's header (`SEEED_MR60BHA2.h`) and its official `breath_demo.ino` example: `mmWave.begin(&mmWaveSerial)`, `mmWave.update(100)`, `getBreathRate()`, `getHeartRate()`, `getDistance()`, `isHumanDetected()`.
- Firmware prints one CSV line per update: `BHA,<present 0/1>,<breath_rate>,<heart_rate>,<distance_m>` (a field is `-1` if the sensor didn't have a value that cycle). Mirrors the plain-text-line-over-serial pattern already used by `DopplerRadar.ino` and the SmartBox.
- `gui.py` changes: new `MsgBHA2Data`/`MsgBHA2Status`/`MsgBHA2Connected`/`MsgBHA2ThreadDone` dataclasses, `BHA2Thread` class (parses the `BHA,...` lines), `💓 MR60BHA2 (Breath/Heart)` added to the source list, `_build_bha2_panel()` (port picker + Connect/Disconnect, same style as the Doppler panel, since this one *is* a plain serial connection unlike the A121), status display shows presence + breath rate + heart rate + distance, with file logging.
- **Compiles clean** against `esp32:esp32:XIAO_ESP32C6` (270190 bytes, 20% flash) under the default (3.3.11) core environment. **Not yet flashed to the physical board** — needs the same USB-C cable the A121 is waiting on, arriving 2026-07-31. Once flashed, connect from the GUI same as any other serial source.

### The toolchain incident: installing C6 support wiped out core 2.0.9
The XIAO ESP32C6 needs a newer ESP32 Arduino core than what was installed (2.0.9 predates the C6 chip entirely — `board listall` didn't even list it). Running `arduino-cli core install esp32:esp32@3.3.11` to add C6 support was expected to install *alongside* 2.0.9. **It didn't — it replaced 2.0.9 outright**, deleting its toolchain directory. This was only caught because SmartBox_V8 and SoilSensor_V3 were test-compiled afterward specifically to check — both failed immediately, since their ESP-NOW callback signatures only match 2.0.9's older API (see the 2026-07-21 section below for why that signature choice was made in the first place).

**Fix:** rather than keep flipping the shared install back and forth, created a second, fully isolated arduino-cli environment:
```
arduino-cli config init --dest-dir /home/lab   # wrote /home/lab/arduino-cli.yaml, renamed to arduino-cli-2.0.9.yaml
# edited directories.data / directories.downloads in that file to point at
# /home/lab/.arduino15-esp32-2.0.9 (a fresh, separate location) while leaving
# directories.user (the shared /home/lab/Arduino sketchbook/libraries) unchanged
arduino-cli --config-file /home/lab/arduino-cli-2.0.9.yaml config set board_manager.additional_urls \
    https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
arduino-cli --config-file /home/lab/arduino-cli-2.0.9.yaml core update-index
arduino-cli --config-file /home/lab/arduino-cli-2.0.9.yaml core install esp32:esp32@2.0.9
```
Verified both directions afterward: SoilSensor_V3/SmartBox_V8 compile clean again using `--config-file /home/lab/arduino-cli-2.0.9.yaml`, and the MR60BHA2 sketch still compiles clean under the default (no-flag) environment against `XIAO_ESP32C6`. **Nothing on the physical box/probes was touched or reflashed** — this was purely a build-toolchain problem on the development machine, caught and fixed before anything downstream was affected. Lesson: when a project's firmware depends on a specific toolchain version for compatibility reasons (as this one does, see 2026-07-21 notes), installing a *different* version of the same platform for unrelated work can silently replace it rather than coexist — always test-compile the version-sensitive sketches afterward if a core install/upgrade happens for any reason.

---

## Session findings (2026-07-24) — Doppler radar integration

### Hardware and wiring
User has a separate **CQRobot 10.525GHz Doppler Effect Microwave Motion Sensor** (SKU: CQRSENWB01) they wanted wired to a new **ESP32 DevKit V1** ("DOIT ESP32 DEVKIT V1") and integrated as another GUI source. Wiring was cross-verified against the official CQRobot PDF datasheet (a web source — Cirkit Designer — gave an incorrect 4-pin pinout including a nonexistent "EN" pin; the real datasheet confirms only 3 wires):
```
Sensor Red (V)   → ESP32 3V3
Sensor Black (G) → ESP32 GND
Sensor Green (S) → ESP32 D15 (GPIO15)
```
Sensor's own onboard voltage switch must be set to 3.3V mode (it also supports 5V, but ESP32 GPIOs are 3.3V-only/not 5V-tolerant — do not use 5V mode wired directly to a GPIO). GPIO15 is a "strapping pin" (sampled at boot for low-level config) but using it as a regular input post-boot is safe — cosmetic-only effect on boot log verbosity.

**Safety incident during setup:** the ESP32 got noticeably hot on USB power shortly after first connecting the sensor; user unplugged it immediately without being asked to reconnect for testing. Given the go-ahead only after they'd independently inspected wiring/polarity/voltage-switch setting and confirmed a normal power-on afterward. No root cause was identified for the heat (never recurred) — if it happens again, check wiring polarity and the voltage-switch position again before assuming it's safe.

### Firmware and GUI integration
New sketch: **`/home/lab/Desktop/DopplerRadar/DopplerRadar.ino`** — reads GPIO15 digitally, prints `MOTION,1`/`MOTION,0` once per second plus immediately on any state change. Compiles under both the default and isolated-2.0.9 core environments (doesn't use ESP-NOW, so it's not version-sensitive).

`gui.py`: added `MsgDopplerData`/`MsgDopplerStatus`/`MsgDopplerConnected`/`MsgDopplerThreadDone` + `DopplerThread` (reads `MOTION,` lines over serial), `📶 Doppler Radar` added to the source list, `_build_doppler_panel()` (port picker + Connect/Disconnect, live "● Motion Detected" (red) / "○ Clear" (gray) status, file logging). This was the first time a second non-radar, non-cap source existed alongside the capacitive probes — surfaced (and at the time, patched inline rather than refactored) the positional-indexing fragility in `_on_source_change()`/`_on_cap_reading()` that was later properly fixed during the 2026-07-30 A121/BHA2 work (see above) once a *third* source made the pattern break outright.

### Capability findings — this sensor cannot be used for soil moisture, at all
This turned into an important scoping conversation, worth preserving in full since the same reasoning will apply to any future "extra radar" hardware:

- **No ranging.** This is a **CW (Continuous Wave) Doppler radar**, not FMCW like the IWR6843AOP. CW radar has no time-of-flight reference (unmodulated carrier), so it is physically incapable of measuring distance — only motion/velocity via Doppler shift. This specific board further reduces even that down to a bare thresholded digital 0/1 "something moved" output — no raw analog speed/amplitude data is exposed at all.
- **No reflectivity either.** The raw analog Doppler IF signal (whose *amplitude* would correlate with reflected energy) is processed internally by an onboard comparator and never exposed on this board — only the S pin's final binary result is broken out.
- **Root cause of "motion detected while stationary":** most likely genuine tiny movement near the sensor that isn't consciously noticeable — it sits right next to the keyboard, so breathing, hand tremor while resting near the keys, or desk vibration are all plausible triggers. An early test also showed rapid toggling in the first ~0.7s after boot, suggesting some electrical-noise/settling behavior too, though this was never conclusively isolated from the "genuine environmental motion" explanation. Not fully resolved either way.
- **Bottom line, stated directly to and accepted by the user:** this sensor is fundamentally unable to contribute to soil moisture sensing — it cannot characterize what it's looking at (wet/dry, moving/still are indistinguishable to it in the way that matters), it only flags "did something move." It remains integrated in the GUI as a motion/presence source for whatever else it might be useful for, but it is **not** and **cannot become** a moisture-sensing modality. The same "no reflectivity, no ranging, digital-only" evaluation was the deciding factor when the LD2450 came up in the 2026-07-30 session too (see above) — CW/simplified-output radars in general are a dead end for this project's core goal, only FMCW parts with real amplitude/phase access (like the IWR6843AOP, or potentially the A121's `strengths` field) are worth pursuing for that.

---

## Session findings (2026-07-21) — capacitive probe integration

### What was added
A separate hardware system, the **SmartBox** (ESP32 hub, files in `~/Desktop/Smart Box Files/`), receives moisture readings from 3 capacitive soil probes over **ESP-NOW** (not WiFi — a separate, lower-level radio protocol built into the ESP32 chip) and was integrated into `gui.py` alongside the radar:

- **Source selector** at the top of the GUI window: `📡 Radar (mmWave)` / `🌱 Capacitive Probe 1` / `2` / `3`. Selecting a probe swaps the whole panel — Connection/Calibration/Plots/Log (radar) hide, a capacitive-probe view shows instead. Implemented via a shared `self._cap_frame` and `self._radar_mid_frame` etc. gridded in the same cell, toggled with `.grid()`/`.grid_remove()` in `_on_source_change()`.
- **Per-probe panel**: title, live raw ADC value, a moisture % readout, **Calibrate Dry/Air** and **Calibrate Water** buttons (2-point linear calibration: `pct = (raw - dry_raw) / (wet_raw - dry_raw) * 100`, clamped 0–100 — works regardless of the probe's actual polarity since it's just linear interpolation between two measured endpoints), and a **Log to File** button that writes `cap_probe{N}_<timestamp>.txt`, throttled to the same `LOG_FILE_INTERVAL_S` (15s) as the radar log.
- **Shared "ESP32 Sensor Box Connection" card** (one instance, sits above the per-probe panel, visible regardless of which probe # is selected — there's only one physical box/one USB connection for all 3 probes): serial port picker (defaults to `/dev/ttyUSB2`), Connect/Disconnect, and **IP / Gateway / Subnet fields + "📤 Push Network Config to ESP32" button** that provisions the box's WiFi over the same USB serial link.
- **`CapBoxThread`** (mirrors the radar's `SensorThread` pattern): reads `DATA,<m0>,...,<m9>` lines off the serial port, puts `MsgCapData` on the shared GUI queue; `_dispatch()` fans each probe's value out via `_on_cap_reading(probe_num, raw_adc)`. Also exposes `send_line()` for pushing `SETIP`/`CLEARIP` commands.

### SmartBox firmware — versions and current state
- **V6** (`SmartBox_V6/`) — the version the user considers the last known-good baseline. As of this session it's back to **byte-for-byte original** (confirmed — do not modify without being asked). **Does not compile with this machine's installed toolchain** (arduino-cli 0.32.2 + esp32 core 2.0.9): fails on `LED_BUILTIN` undefined for this board variant. User says it compiled fine before, elsewhere — this is a toolchain/environment difference that was never resolved this session. If picking this up on a different computer, check what ESP32 core version is installed there before assuming this still applies.
- **V7** (`SmartBox_V7/`) — added a hardcoded static-IP block using guessed placeholder network values. **Superseded, not in use** — the static-IP-over-serial approach in V8 replaced this since the real network subnet was never actually known.
- **V8** (`SmartBox_V8/`) — **currently flashed on the physical box** (`/dev/ttyUSB2`), built and flashed this session via `arduino-cli` (bundled inside the Arduino IDE install at `/opt/arduino-ide/resources/app/node_modules/arduino-ide-extension/build/arduino-cli`; ESP32 core + `LiquidCrystal I2C`/`RTClib`/`Adafruit BusIO` libraries already installed there). Adds, relative to V6:
  - Always-on `DATA,<m0>,...,<m9>` line over USB serial once per second (`sendSerialData()`), independent of WiFi state.
  - `SETIP,<ip>,<gateway>,<subnet>` / `CLEARIP` / `GETIP` serial commands, backed by `Preferences` (ESP32 flash-persisted key-value storage) so a pushed static IP survives power cycles. Applies on next boot (`SETIP`/`CLEARIP` both trigger `ESP.restart()`).
  - The `OnDataRecv` callback signature was changed from `esp_now_recv_info_t*` to the older `(mac_addr, data, len)` form — **required to compile** against the installed 2.0.9 core, which predates the newer signature.
- **Bug found via live testing, then reverted per user instruction:** a malformed/oversized ESP-NOW packet was observed being received (`Sensor 69376`, nonsense value) — `OnDataRecv` does `memcpy(&myData, incomingData, sizeof(myData))` with no length check, and `updateSensorData()` writes `Moisture[myData.c]` with no bounds check, so a bad packet can write far outside the 10-element array. **This is a real, reproducible bug, confirmed live, still present in V6/V7/V8 as currently flashed/written.** A fix (length + bounds check) was added, tested working, but the user explicitly asked for it to be reverted since it wasn't something they'd asked for — **do not re-add without being asked.** If probes seem to behave erratically or the box crashes/glitches, this unfixed bug is a likely suspect and worth revisiting *if the user wants that specific fix*.

### Important process note for future sessions
This session went past scope on the SmartBox firmware — asked to "get the box to connect to the computer to send data to the GUI," also modified the ESP-NOW receive-handling code (the signature change was unavoidable to compile; the length/bounds-check additions were not requested). The user caught this, asked for V6 to be restored exactly, and it was. **Lesson for next time: don't modify code paths beyond what's explicitly asked, even when a real bug is found in passing — flag it and ask first**, especially on hardware/firmware the user didn't ask to have touched.

### Diagnosis attempted, not completed
User reported the box's LCD shows "Sensor 1" stuck at 0 (no probe data arriving). Live serial monitoring confirmed **zero ESP-NOW packets arriving at all** (not even malformed ones) while V8 was running — this happened before the conversation was interrupted over the scope concern above, so the actual cause (probe not powered, out of range, or a WiFi/ESP-NOW channel mismatch — the hub locks to whatever channel "TaterWiFi" is on at boot, which may not match a probe that isn't doing the same channel-follow logic) was **not resolved**. Worth checking the boot serial log for which channel the hub actually locked onto, and confirming the remote probe firmware (not present in this repo — only the hub/box side is here) targets the same channel.

---

## Session findings (2026-07-14 to 2026-07-17)

### Non-metallic container alone didn't fix it — found two more real config bugs
- Switched to a plastic container; soil/steel still didn't separate well. Investigated the actual radar config (`chirp_config.cfg`), not just the Python pipeline.
- **`profileCfg` rxGain was `158`** — TI demo configs typically use ~30 dB here; `158` is far outside any sane range and was likely saturating the receiver front-end for any target. Changed to `30`. Confirmed real improvement: εr dropped from pegged-at-39601 to a fluctuating few hundred immediately after the fix (still not correct on its own, but a genuine, measurable change).
- **`calibDcRangeSig`/`clutterRemoval` enabled together produced the single best reading of the whole project** (dry soil εr≈3.83, right in the expected 3–4 range for dry sand) — but this turned out to be a coincidence of comparing a *suppressed* live reading against an *old, unsuppressed* metal reference, not a validated fix (see below).
- **`clutterRemoval` suppresses real static targets, not just leakage.** Directly proven: with `clutterRemoval=1`, a metal plate held in front of the sensor read the *same* as dry soil (gamma≈0.32 both), because clutter removal treats anything motionless — including your calibration target — as clutter to subtract. Tested with two different plate sizes (small, then a properly footprint-covering large plate); both collapsed to the same suppressed range (~2.3–2.5 raw amplitude) regardless of size. Conclusion: `clutterRemoval` is fundamentally the wrong tool here — this application's entire premise is measuring motionless targets. **Left off (`0`).**
- **`calibDcRangeSig`'s correction window overlapped the real target bin.** Window was `-5 8` (bins -5 through +8 around DC). At a corrected standoff putting the true target at bin 4, this window was subtracting out part of the real target return, and empirically caused the apparent "peak" in the range profile to shift to bin 9 (just outside the window) rather than sitting at the true bin 4. Narrowed to `-5 1`. **This window needs to stay clear of whatever bin the real target is at — re-check any time standoff changes.**

### Signal drift over time — the main open problem now
- Ran two ~23-hour unattended logging sessions (`data_20260715_110524.txt`, `data_20260716_103425.txt`) with VWC/εr/gamma logged every 15s, to watch a wet soil sample dry out.
- **Run 1 (plain wet-soil dry-down):** εr swung from 25 → peak 276 (6:37 PM, ~7hrs in) → trough 63 (4:40 AM) → back up to 244 by the next morning. 98.5% of readings sat at the VWC=0.6 clamp the whole time (uninformative on its own — had to look at εr directly).
- **Run 2 (added a water trench + dry paper towel meant to soak up pooled water, off to the side, ~16cm clear of the sensor):** εr swung even higher — mean 341.8 vs run 1's 188.1, peak 490.2 vs 276.1, and every metric elevated vs run 1. Within run 2 itself, first-quarter mean (188.2) roughly doubled to last-quarter mean (369.2) — i.e. it was trending *wetter*, not drier, the whole time. Consistent with the trench continuing to feed moisture in (paper towel absorbing trench water, itself becoming a wet object, or genuine soil redistribution) rather than actual drying.
- **Leading hypotheses for the sinusoidal shape**, roughly in order of suspicion (room is climate-controlled, so ambient air temperature is likely NOT the driver):
  1. **Soil physics (redistribution + capillary rise)** — after heavy watering, gravity keeps draining water down through the profile for hours (surface reading falls), then capillary action pulls some of it back up toward the surface as a gradient forms (surface reading partially recovers). Real, well-documented soil behavior, doesn't require any sensor problem. Best fit for run 1's shape/timing.
  2. **Humidity cycling** — "climate controlled" typically governs temperature, not necessarily relative humidity. AC dehumidifies while the compressor runs, drifts back up while it's off — could still produce a roughly daily rhythm in local humidity/condensation at the soil surface even with rock-solid temperature control.
  3. **Sensor self-heating** — continuous operation could take many hours to reach thermal equilibrium in the RF front-end, independent of room temperature.
  4. Note: run 2's peak landed at 1:39 AM vs. run 1's peak at 6:37 PM — a ~7hr shift between runs. If the driver were purely a fixed daily rhythm (temperature or humidity tied to clock time), you'd expect the peak at roughly the same clock time both times. The shift is a point in favor of (1) over (2)/(3) for run 2 specifically, since it fits better with "however long it took water to wick through the paper towel" than a fixed daily cycle.
  5. **Not yet ruled out:** haven't logged ambient temperature/humidity alongside VWC to actually correlate against any of these. Would need external instrumentation (even a cheap thermometer/hygrometer checked periodically) to test directly.
- **Practical consequence:** a calibration (metal + dry soil) captured on 7/15 no longer matched a fresh dry sample tested on 7/17 — same metal reference (±4%, not the cause), but raw signal for "dry soil" was ~2.5x higher than on 7/15. Re-measuring standoff on 7/17 found the true peak had moved bins entirely (see below), which may itself be a big part of the "drift" story — worth re-examining whether some of the apparent day-to-day drift is actually standoff/bin misalignment rather than genuine signal drift.

### Standoff/bin alignment is not stable across sessions
- On reconnecting to test the 7/17 dry sample, the true peak (per the range-profile bar chart) did not match the locked bin — peak was at bin 4 while standoff was set for bin 2. Corrected standoff to target bin 4; on the *next* reconnect, the peak had moved to bin 9 (this turned out to be the `calibDcRangeSig` window overlap bug above). After narrowing that window and reconnecting again, the peak was back at bin 2.
- **Added a "🎯 Auto-Detect Standoff" button** (`gui.py`, next to Connect/Disconnect) to remove the manual guess-and-check cycle: scans the live range profile, searches from bin 2 onward (skips bins 0–1, established leakage-dominated territory), and sets the Standoff field to the true peak's corresponding distance. Still a one-shot snapshot, not continuous — you need to re-run it (and reconnect) any time you suspect the peak has moved. Doesn't fix the underlying drift, just makes re-syncing to it fast.

### Housekeeping for long unattended runs
- Disabled system suspend (`gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing`, same for `-battery-type`) and USB autosuspend on the CP2105 UART adapter + its upstream hub/controller (`power/control` files set to `on`), so multi-day logging survives without the connection dropping.
- Data log file writes throttled to 1 row per 15s (`LOG_FILE_INTERVAL_S` constant in `gui.py`) — was writing multiple times/second before, unnecessarily large files for a multi-day run.
- Built, tested, then fully reverted a "Γ=0 open-air reference" (zero-and-span normalisation) calibration mode at user request. Don't re-add without being asked — `CalibrationData` is back to just `metal_amplitude`.

---

## Session findings (2026-07-09)

### Real software regression found and fixed
- **`ChirpConfig.bandwidth_hz` in `host/config.py` was wrong by a factor of 1,000,000.** Formula was `freq_slope_mhz_us * 1e6 * sweep_time_us * 1e-6` — the two unit-conversion factors cancel out, returning `3486` Hz instead of `3.486 GHz`. This made `range_resolution_m` compute to ~43,000 m instead of ~4.3 cm, which made `soil_bin_index()` round down to **bin 0** for any realistic standoff. Fixed to `freq_slope_mhz_us * sweep_time_us * 1e6`. Verified: `range_resolution_m` now correctly ≈0.043 m, and a 0.20 m standoff now gives bin 5 (matches this file's own "bin ~5 at 20cm" comment, and confirmed empirically — the live range-profile plot peak lines up with the locked bin after the fix).
- **This bug predates this session** — confirmed present in an untouched `Radar Ubuntu` backup copy dated 2026-07-01 (`~/Downloads/Radar Ubuntu`), so it wasn't introduced today. Textual evidence in the 2026-06-30 "Bugs fixed" note below (search window "bins 3–7" only makes sense if the computed bin was 5, i.e. correct) suggests the regression was introduced sometime between 2026-06-30 and 2026-07-01 — likely around the same time the `soil_bin_tolerance` 2→0 change was made, which is probably why tightening the tolerance appeared to "break" things rather than fix them at the time.
- **Calibration wasn't actually saving** — steps ①–④ only update `CalibrationManager.data` in memory; only clicking "⑤ Save Calibration" persists to `calibration.json`. Several early "recalibration" attempts in this session silently didn't take effect because Save wasn't clicked before reconnecting.

### Root cause of VWC pinned at 0.6 — physical, not software
- `permittivity_to_vwc()` in `host/processing/vwc.py` clamps output to `[0, 0.6]`. Topp's cubic exceeds 0.6 once εr passes ~55 (gamma ≈ 0.76), and clamps there regardless of how much higher εr goes — confirmed with direct function calls (εr=81 through εr=39,601 all return exactly 0.6000). This is why the display looked "static" — it's recomputing every cycle, just saturating to the same ceiling.
- Verified the pipeline itself updates live (no caching/frozen-value bug): added temporary diagnostic logging (`DIAG` / `DIAG_RAW` lines in `gui.py`'s `_process()` — **should be removed once physical setup is fixed**) and captured raw `uint16` values straight off the UART wire, before any Python conversion. Confirmed the parser and conversion math are working correctly (no bug in `frame_parser.py` or the `sqrt(10^(x/2560))` linearization).
- **The actual problem: insufficient dynamic range at close standoffs.** At 10cm standoff (bin 2), raw sensor bytes for open air read ~4142–4149, while dry soil AND solid steel both read ~4305–4320 — soil and steel were statistically indistinguishable from each other. This is the signature of near-antenna leakage/near-field coupling dominating the return at close range, not a real reflectivity measurement. Per this file's own near-field estimate (`R_ff ≈ 0.3m`), standoffs tested so far (10cm, 20cm, 26cm) are all within the near field.
- Container is metal, ≥6 inches of soil depth — beam footprint spillover onto the metal walls is a second, compounding suspect (not yet isolated from the near-field effect since no non-metallic container has been tested).
- **Next step (in progress): non-metallic container ≥35×35cm, standoff ~30cm** (footprint = `2 × standoff × tan(30°)` ≈ 34.6cm at 30cm). Re-run the air/soil/steel raw-value comparison at 20/25/30cm standoffs and check whether soil finally separates from steel once further from the antenna's near field.

---

## Session findings (2026-06-30)

### Physical setup constraints discovered
- **Beam footprint at 20 cm standoff is ~23 cm diameter** (60° beam, ±30° from boresight). Any container narrower than ~25 cm will have the beam spill over the rim, causing the pot rim/walls to dominate the return at the soil bin. A 5-inch (12.7 cm) pot is too small.
- **Cardboard tube as aperture limiter** — a cardboard tube (~10 cm long × 5 cm dia) wrapped in foil, placed between sensor and soil, narrows the effective footprint to ~10 cm. This worked but must be **mounted rigidly** — hand-holding introduces motion that swamps the tiny wet/dry signal difference.
- **Surface roughness matters as much as dielectric properties.** After wet soil absorbs water, the surface becomes irregular and scatters the beam diffusely. This can cause wet soil to read *lower* amplitude than dry soil. Always level the soil surface with a flat edge before measuring.
- **Calibration must be done in-situ** — changing the container, tube, or standoff invalidates the calibration. The 2026-06-23 calibration values (dry=4032, wet=4110) are stale. Current in-situ measurements:
  - Dry soil (new setup, tube): Amp ≈ **3888** (very stable)
  - Wet soil (new setup, tube): Amp ≈ **4230** at initial wetting; drops to ~3850 after water soaks in (surface roughening)
  - Calibration still needs a proper re-run with mounted tube and leveled, settled soil.
- **Free water on the surface reads very high** (~4230) then drops as it soaks in. The "wet" calibration point should be taken a few minutes after watering, not immediately.

### Bugs fixed (2026-06-30)
- **`soil_bin_tolerance = 2`** caused `_best_bin()` in `reflection.py` to search bins 3–7 and latch onto a clutter return with higher amplitude, giving a flat VWC line regardless of soil moisture. Fixed: changed to `soil_bin_tolerance = 0` in `host/config.py`. Now reads exact expected bin only.
- **`calibration.json` was missing** — GUI was using default physical model (metal_amplitude=1.0). Manually created from measured amplitude values. Polynomial fit is degree-1 in gamma space (VWC = -11.204 + 13.400·γ).

---

## Bugs fixed in this session (2026-06-23)

- **Double log-magnitude conversion:** `_run_uart()` in `gui.py` was pre-converting the range profile to linear before passing it to `push_magnitude_profile()`, which also converts internally. Fixed by passing the raw uint16 profile directly.
- **GUI defaulted to DCA1000 mode with wrong COM ports:** Changed defaults to UART, COM4 (CLI), COM3 (Data).
- **`lvdsStreamCfg` dataFmt was 1:** Changed to 0 for UART-only operation.
- **`compRangeBiasAndRxChanPhase` had wrong token count:** Must always be 25 tokens (bias + 24 phase values for 3TX×4RX physical antennas), regardless of active TX count.
- **`chirp_config.cfg` was missing required commands:** Added `clutterRemoval`, `aoaFovCfg`, `cfarFovCfg` ×2, `CQRxSatMonitor`, `CQSigImgMonitor`, `analogMonitor`, `bpmCfg`, `calibData`.
- **Custom chirp parameters caused `sensorStart Error -1`:** Reverted to TI profile_3d.cfg parameters (166 MHz/µs slope, 12500 ksps) which are confirmed working. Custom parameters (77 MHz/µs) can be investigated later.

---

## Known limitations / things to improve

- **Single TX not working:** Using `channelCfg 15 1 0` with 1 TX causes `sensorStart Error -1`. Root cause unknown — possibly a firmware constraint on this SDK version. Currently using 3 TX (TDM).
- **60 GHz vs Topp frequency:** Topp's equation was derived at 20–200 MHz. At 60 GHz, bound water relaxation shifts εr lower for the same VWC. Empirical calibration (steps ③–④) corrects this.
- **Surface roughness:** rough soil scatters the specular return. Level the soil with a flat edge before every measurement. Wet soil surface after absorption is rougher than dry — this can cause wet to read *lower* than dry if not leveled.
- **Container size vs beam footprint:** beam is ~23 cm wide at 20 cm standoff. Use a container ≥ 25 cm across, or use a rigidly-mounted aperture tube to narrow the footprint. A 5-inch pot is too small without a tube.
- **Calibration drift:** calibration values shift if the physical setup changes (container, standoff, tube). Always re-calibrate after any hardware repositioning.
- **Near-field effects:** at 10–30 cm the sensor is in its own near field. The metal-plate calibration absorbs the constant offset this introduces.
- **Phase calibration:** `compRangeBiasAndRxChanPhase` is set to identity. After mounting in final position, run `measureRangeBiasAndRxChanPhase` from the radar CLI and paste the results in.
- **UART data path:** TLV type 2 gives only log-magnitude (uint16). Phase is not available; only amplitude-based |Γ| estimation is possible. Use DCA1000 for full complex data.
- **`SoilMoistureSensor.exe` is stale:** Built before the bug fixes in this session. Rebuild before distributing.
- **Occasional TLV parse corruption:** Rare garbage TLV type IDs (e.g. 2332459264) appear in frame logs — benign, the parser recovers automatically.
- **TODO: remove remaining diagnostic logging in `gui.py`.** `_process()` still has one lightweight `log.info("DIAG ...")` line per reading (raw_amp/gamma/eps_r/vwc/bin). The heavier `DIAG_RAW`/`DIAG_SWEEP` per-bin-array versions were already removed 2026-07-15 after they appeared to destabilize the UART read loop during a long session (stopped a calibration collection from progressing past 0/20 — heavy per-frame array logging is a likely suspect, not confirmed). Keep the remaining single-line `DIAG` log until the drift investigation above is resolved, then remove it.

---

## How to rebuild the exe

Run from the project directory (Python must be installed):
```powershell
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "SoilMoistureSensor" `
    --distpath "." --workpath "build" --specpath "build" `
    --paths "." `
    --hidden-import "serial.tools.list_ports" `
    --hidden-import "matplotlib.backends.backend_tkagg" `
    --hidden-import "scipy.optimize" --hidden-import "scipy.signal" `
    --hidden-import "host" --hidden-import "host.config" `
    --hidden-import "host.capture.uart_interface" `
    --hidden-import "host.capture.dca1000" `
    --hidden-import "host.capture.frame_parser" `
    --hidden-import "host.processing.range_fft" `
    --hidden-import "host.processing.reflection" `
    --hidden-import "host.processing.vwc" `
    --hidden-import "host.calibration.calibrate" `
    gui.py
Remove-Item -Recurse -Force build
```

---

## Key equations reference

**Range resolution:**  `Δr = c / (2 × BW)`  where BW = slope × sweep_time

**Soil-surface range bin:**  `bin = round(standoff_m / Δr)`

**Fresnel (normal incidence, lossless):**
```
|Γ| = (√εr − 1) / (√εr + 1)
εr  = ((1 + |Γ|) / (1 − |Γ|))²
```

**Topp's equation (Topp et al., 1980):**
```
θ = −5.3×10⁻² + 2.92×10⁻²·εr − 5.5×10⁻⁴·εr² + 4.3×10⁻⁶·εr³
```
Valid for εr ≈ 1–40, θ ≈ 0–0.5 m³/m³.

**Typical εr values:**
| Soil state | εr | VWC |
|---|---|---|
| Dry sand | 3–4 | 0.01–0.05 |
| Field capacity (sandy) | 10–15 | 0.15–0.25 |
| Field capacity (clay) | 20–30 | 0.30–0.45 |
| Saturated | 30–40 | 0.40–0.55 |
| Free water | ~80 | — |
