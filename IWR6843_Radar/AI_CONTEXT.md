# AI Handoff Document — IWR6843 AOP Radar Project
**Last updated:** 2026-06-08  
**Original AI:** Claude (Anthropic, claude-sonnet-4-6)  
**Owner:** SPUDMAN (farmerjacobr@gmail.com)

---

## What this project is

A real-time radar visualizer for the **Texas Instruments IWR6843 AOP EVM** — a 60 GHz millimeter-wave radar sensor. The Python GUI reads raw binary frames from the sensor over USB serial, parses them, and displays live data.

The user's **current goal** is detecting a human hand within 1 meter and showing the distance in real-time.

## File structure

```
Desktop/IWR6843_Radar/
├── hand_distance_meter.py  # MAIN ACTIVE SCRIPT — big distance display, gauge, history chart
├── iwr6843_visualizer.py   # Full 3D point cloud visualizer (reference / future use)
├── CONFIG_NOTES.txt        # Human-readable config parameter reference
└── AI_CONTEXT.md           # This file
```

### hand_distance_meter.py — what it does
- COM port selector + connect/disconnect
- Giant number display showing distance in cm (0–100)
- Colored gauge bar: red (<25 cm), orange (<50), yellow (<75), green (far)
- Scrolling history line chart (~7.5 seconds)
- EMA smoothing (alpha=0.35) to reduce jitter
- Distance = Euclidean distance of point cloud centroid from sensor origin
- Config is hardcoded (HAND_CONFIG constant) — no editor needed for basic use

---

## Hardware

| Item | Detail |
|------|--------|
| Sensor | IWR6843 AOP EVM (antenna-on-package, 60 GHz) |
| Interface | USB → Silicon Labs CP2105 dual UART |
| Config port | Enhanced COM Port, 115200 baud |
| Data port | Standard COM Port, 921600 baud |
| DIP switches | All 8 OFF (SOP=000 = flash boot mode) |
| Driver needed | CP210x VCP from silabs.com |
| OS | Windows 10 Home |

**Important:** The script must be run as Administrator for COM port access. Use:
```
Start-Process powershell -ArgumentList "-NoExit","-Command","& 'C:\Users\SPUDMAN\AppData\Local\Programs\Python\Python313\python.exe' iwr6843_visualizer.py" -Verb RunAs
```

---

## File structure

```
Desktop/IWR6843_Radar/
├── iwr6843_visualizer.py   # Main GUI application
├── CONFIG_NOTES.txt        # Human-readable config reference
└── AI_CONTEXT.md           # This file
```

---

## Software stack

- **Python 3.13** at `C:\Users\SPUDMAN\AppData\Local\Programs\Python\Python313\python.exe`
- **pyserial** — serial communication
- **numpy** — frame data processing
- **matplotlib (TkAgg)** — embedded GUI plots
- **tkinter** — main application window

---

## How the code works

### Architecture
```
[IWR6843 sensor]
      │ USB serial (921600 baud)
      ▼
[frame_reader() thread]     ← reads raw bytes, finds magic word, enqueues complete frames
      │ queue.Queue (max 5)
      ▼
[FuncAnimation callback]    ← drains queue, updates matplotlib plots every 80ms
      │
      ▼
[3 plots: 3D scatter, X-Y top-down, X-Z front view]
```

### Frame format
Every frame starts with magic word `02 01 04 03 06 05 08 07` (8 bytes).
Header is 40 bytes total (8 magic + 8× uint32 little-endian).
Header fields: version, totalPacketLen, platform, frameNumber, timeCpuCycles, numDetectedObj, numTLVs, subFrameNumber.
Then N TLV blocks: type(uint32) + length(uint32) + data.

Key TLV types parsed:
- **Type 1**: Detected points — array of (x, y, z, doppler) as float32
- **Type 6**: Statistics — CPU load percentages
- **Type 7**: Side info — SNR and noise per point as int16 (×0.1 = dB)

### Key functions
- `parse_frame(data)` — parses one complete frame from raw bytes, returns dict
- `frame_reader(ser, queue, stop_event)` — background thread, syncs to magic word
- `RadarApp` — tkinter main window with embedded matplotlib
- `_update_plots()` — called by FuncAnimation, drains queue and redraws all 3 axes

---

## Current sensor configuration (hand detection, ≤1m)

```
profileCfg 0 60 100 5 30 0 0 99 1 64 6000 0 0 158
frameCfg 0 2 16 0 50 1 0
cfarCfg -1 0 2 8 4 3 0 10 1
cfarCfg -1 1 0 4 2 3 1 10 1
multiObjBeamForming -1 1 0.3
clutterRemoval -1 0
cfarFovCfg -1 0 0 1.0
cfarFovCfg -1 1 -3.0 3.0
```

**Derived values:**
- Bandwidth: 99 MHz/µs × 25 µs = 2475 MHz
- Range resolution: ~6 cm
- Max range: ~3.8 m (FOV capped at 1.0 m)
- Frame rate: 20 fps

---

## What has been tried / decisions made

- **All DIP switches OFF** is confirmed correct for this EVM (SOP=000 flash boot)
- **COM3 was an Arduino**, not the radar — do not confuse them
- The radar COM ports had `Error` status due to missing CP210x driver; after driver install they appear as Enhanced/Standard COM Port
- The GUI config editor lets the user paste and resend config without disconnecting
- `clutterRemoval -1 0` (OFF) is intentional — hands can hover still and would be filtered out if ON
- Point colors use `coolwarm` colormap mapped to doppler velocity ±2 m/s

---

## Potential next steps (user has not committed to these)

- Gesture recognition layer on top of the point cloud data
- Reinforcement learning / neural network for gesture classification (user has prior RL/NN interest — see MTG simulator project)
- Velocity vector visualization (arrows instead of dots)
- SNR-based point filtering (use TLV type 7 side info to drop low-quality points)
- Range-Doppler heatmap plot (TLV type 5, not yet parsed)
- Recording frames to file for offline analysis

---

## Codebase notes for an incoming AI

- The `parse_frame` function is conservative — it returns `None` on any malformed data rather than crashing
- `frame_reader` keeps a rolling buffer and slides forward on sync loss; it handles fragmented USB reads
- The queue has `maxsize=5` with drop-oldest logic to prevent memory growth if the UI falls behind
- `ax3d.cla()` is called every frame (expensive but necessary for 3D — matplotlib doesn't support efficient 3D scatter updates)
- All axes are re-styled after `cla()` via `_init_axes()` — this is intentional, not a bug
- The config is sent from a background thread (`_send_config_thread`) to avoid blocking the tkinter event loop
- Color scheme is Catppuccin Mocha (dark purple/blue tones) — the user didn't ask for this explicitly but hasn't objected

---

## If something is broken

1. **No COM ports in dropdown** → driver not installed or wrong USB cable or wrong USB connector on board
2. **Connected but no points** → COM ports are swapped (try reversing config/data), or config wasn't accepted (check log panel)
3. **GUI won't open** → not running as Administrator
4. **Points appear then disappear** → clutterRemoval may be ON, or CFAR threshold too high
5. **`python` not found in admin shell** → use full path `C:\Users\SPUDMAN\AppData\Local\Programs\Python\Python313\python.exe`
