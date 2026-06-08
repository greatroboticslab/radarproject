# IWR6843 AOP EVM — Real-time mmWave Radar Visualizer

A Python GUI for the **Texas Instruments IWR6843 AOP EVM** (60 GHz millimeter-wave radar).  
Reads live radar frames over USB serial and displays a 3D point cloud, 2D projections, and a real-time distance meter that works on both moving and stationary targets.

---

## Features

- **3D point cloud** with top-down and front-view 2D projections
- **Hand distance meter** — live cm readout, color-coded gauge, and scrolling history
- **Static target detection** via raw range profile (no Doppler required — detects still hands, walls, objects)
- **In-app config editor** — paste any TI mmWave config, send without disconnecting
- **Auto range resolution** — computed from the active config, shown in the log
- **Tunable detection** — Smooth, Sensitivity, and Width sliders for noise rejection
- Dark UI (Catppuccin Mocha)

---

## Hardware

| Item | Detail |
|------|--------|
| Sensor | Texas Instruments IWR6843 AOP EVM (60 GHz) |
| Interface | USB → Silicon Labs CP2105 dual UART |
| Config port | Enhanced COM Port — **lower** COM number, 115200 baud |
| Data port | Standard COM Port — **higher** COM number, 921600 baud |
| DIP switches | All 8 **OFF** (SOP = 000, flash boot mode) |
| USB driver | CP210x VCP — download from silabs.com |

---

## Requirements

- Python 3.9+
- Windows (tested on Windows 10)

Install dependencies:

```
pip install -r requirements.txt
```

---

## Running

**Must be run as Administrator** for COM port access on Windows.

Option 1 — right-click a PowerShell shortcut → Run as administrator, then:
```
python iwr6843_visualizer.py
```

Option 2 — from a normal PowerShell window:
```
Start-Process powershell -ArgumentList "-NoExit","-Command","python iwr6843_visualizer.py" -Verb RunAs
```

---

## Quick start

1. Install the CP210x driver from silabs.com
2. Plug in the EVM (all DIP switches OFF)
3. Open Device Manager — note the two COM ports (Enhanced = config, Standard = data)
4. Run the script as Administrator
5. Set Config COM and Data COM in the dropdowns, click **Refresh** if empty
6. Click **Connect & Start**
7. The log panel confirms config is accepted and shows computed range resolution
8. Point the sensor at your hand — distance appears in the right panel

---

## GUI layout

```
┌─ Top bar ────────────────────────────────────────────────────────┐
│  Config COM | Data COM | Refresh | Connect & Start | Status      │
├─ Plot area (left) ──────────────┬─ Right panel ─────────────────┤
│                                 │  Trail frames slider           │
│  3D Point Cloud (large, left)   │  ┌─ Hand Distance ──────────┐  │
│                                 │  │  42.3 cm                 │  │
│  Top-Down X–Y  │  Front X–Z    │  │  [gauge bar]             │  │
│                                 │  │  Smooth | Sens | Width   │  │
│                                 │  └──────────────────────────┘  │
│                                 │  Sensor Configuration editor   │
│                                 │  [Send Config Now]             │
│                                 │  Log                           │
└─────────────────────────────────┴────────────────────────────────┘
```

---

## Distance meter — how detection works

The distance meter reads from the **range profile** (TLV type 2), not the detected point cloud. This means it detects **stationary targets** — a still hand, a wall, any object — without requiring Doppler motion.

### Detection algorithm
1. Skip the first 3 range bins (TX-to-RX near-field coupling zone)
2. Estimate noise floor = mean + N×std of remaining bins
3. Walk bins looking for **Width** consecutive bins above the threshold
4. Report the start of the first qualifying run as the target distance
5. Apply temporal **median filter** over the last Smooth frames for stability

### Tuning sliders

| Slider | Effect |
|--------|--------|
| **Smooth** (1–15) | Temporal window for median filter. Higher = more stable, slower to react |
| **Sens** (1.0–6.0) | Sigma multiplier for threshold. Higher = less sensitive, fewer false reads |
| **Width** (1–8) | Minimum consecutive bins above threshold. Higher = requires a physically larger object |

### Practical tuning guide
- Start: Smooth=7, Sens=2.5, Width=2
- Getting false reads on empty space → raise Sens or Width
- Missing a still hand → lower Sens, or lower Width to 1
- Reading jumps around → raise Smooth

### Physical limits
- **Minimum reliable range**: ~8–10 cm (TX/RX antenna coupling creates noise below this)
- **Maximum range**: set by `cfarFovCfg -1 0 0 X.X` in the config (X.X = meters)
- **Range resolution**: auto-computed from profileCfg, shown in log after connect

---

## Configuration

The config editor accepts standard TI mmWave CLI commands. Key lines:

```
# Max detection range (meters)
cfarFovCfg -1 0 0 1.0

# Velocity gate (m/s) — does NOT affect range profile distance meter
cfarFovCfg -1 1 -3.0 3.0

# Angular field of view (degrees)
aoaFovCfg -1 -60 60 -60 60

# Static clutter removal — OFF keeps stationary targets, ON removes them
clutterRemoval -1 0
```

### Preset configs

**Hand detection (≤1 m, current default):**
```
profileCfg 0 60 100 5 30 0 0 99 1 64 6000 0 0 158
frameCfg 0 2 16 0 50 1 0
cfarFovCfg -1 0 0 1.0
cfarFovCfg -1 1 -3.0 3.0
```
Range resolution: ~14 cm | Frame rate: 20 fps

**Room / people tracking (≤9 m):**
```
profileCfg 0 60 347 7 57.14 0 0 70 1 256 5209 0 0 158
frameCfg 0 2 16 0 100 1 0
cfarFovCfg -1 0 0 8.92
cfarFovCfg -1 1 -1 1.00
```
Range resolution: ~4.3 cm | Frame rate: 10 fps

---

## Files

| File | Purpose |
|------|---------|
| `iwr6843_visualizer.py` | Main application — 3D visualizer + distance meter |
| `hand_distance_meter.py` | Standalone distance-only app (simpler, no 3D plots) |
| `requirements.txt` | pip dependencies |
| `CONFIG_NOTES.txt` | Detailed config parameter reference |
| `AI_CONTEXT.md` | Full technical handoff document for AI assistants |
| `README.md` | This file |

---

## Known issues / tips

- COM ports don't appear → wrong USB cable (must be data-capable), or CP210x driver not installed
- COM3 on this development machine is an Arduino — do not confuse it with the radar ports
- Distance stuck at a fixed value → raise Sens slider (noise floor being exceeded constantly)
- No detection → lower Sens slider, or check that range profile is enabled in config (`guiMonitor -1 1 1 0 0 0 1`, second `1` = enable range profile)
- 3D plots show nothing but distance works → normal, point cloud requires Doppler (moving targets), range profile does not

---

## License

MIT
