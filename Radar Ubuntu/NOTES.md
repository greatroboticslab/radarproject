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

## Current project state (as of 2026-07-09)

- **Running on Ubuntu now** (this folder, `Radar Ubuntu`) — CLI/Data ports are `/dev/ttyUSB0` / `/dev/ttyUSB1` (not COM ports), set as GUI defaults. Default standoff is `0.10` m.
- **VWC reads pinned at 0.6 (the hard clamp ceiling).** Root-caused this session, see "Session findings (2026-07-09)" below — it's a physical/RF issue (near-antenna leakage swamping the signal at close standoffs), not a software bug. Confirmed with raw UART byte captures: at 10cm standoff (bin 2), dry soil and solid steel produce statistically identical raw sensor values.
- **`calibration.json` now gets saved correctly** — confirmed the "⑤ Save Calibration" button works; earlier apparent failures were just not clicking it.
- **The `Desktop\radar.py` / `RADAR_RESUME.md` referenced above do not exist on this machine.** `gui.py` is the only entry point found.
- **The aperture tube is bare cardboard, not foil-wrapped** (correcting the note below) — still a possible contributor via grazing-incidence reflection off the tube walls, but not via metallic reflectivity.
- **DCA1000 path has not been tested yet.** UART is the active path.
- **Next step: get a larger non-metallic container.** At 30cm standoff the beam footprint is ~35cm diameter (`2 × standoff × tan(30°)`), so soil needs to fill roughly a 35×35cm area, centered under the sensor, with no metal container walls in that zone. Once that's in place, re-run the air/soil/steel raw-value comparison (see below) at 20/25/30cm to check whether soil finally separates from steel outside the near-field/leakage-dominated bins.

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

---

## Running the project

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
- **TODO: remove temporary diagnostic logging in `gui.py`.** `_process()` currently has `log.info("DIAG ...")` and `log.info("DIAG_RAW ...")` calls (added 2026-07-09 to debug the VWC-pinned-at-0.6 issue) that fire on every frame — left in intentionally for the next container test, but should be deleted once that's done, since they spam the log at ~10Hz.

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
