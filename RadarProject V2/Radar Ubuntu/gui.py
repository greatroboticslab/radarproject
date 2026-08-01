"""
gui.py  –  Tkinter GUI for the IWR6843AOP mmWave soil moisture sensor.

Run from the project root:
    python gui.py

Requires:  pip install numpy scipy pyserial matplotlib
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# In a PyInstaller onefile bundle sys.frozen is True and the exe lives at
# sys.executable; __file__ points into the temp _MEIPASS extraction dir.
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).parent
else:
    _APP_DIR = Path(__file__).parent
    sys.path.insert(0, str(_APP_DIR))

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog, scrolledtext
import serial.tools.list_ports
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from host.config import ChirpConfig, ProcessingConfig
from host.capture.uart_interface import UARTInterface
from host.capture.dca1000 import DCA1000
from host.capture.frame_parser import FrameParser
from host.processing.range_fft import compute_range_profile
from host.processing.reflection import ReflectionExtractor
from host.calibration.calibrate import CalibrationManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

CAL_SAMPLES = 20        # frames averaged per calibration step
POLL_MS = 150           # GUI poll interval (ms)
MAX_PLOT_POINTS = 300   # rolling plot window
LOG_FILE_INTERVAL_S = 15  # minimum seconds between rows written to the data log file
FONT_MONO = ("Consolas", 10)
FONT_BIG = ("Segoe UI", 28, "bold")
FONT_MED = ("Segoe UI", 11)
FONT_LABEL = ("Segoe UI", 9)

ACCENT = "#0077b6"
GREEN = "#2dc653"
RED = "#d62828"
GRAY = "#888888"

# ---------------------------------------------------------------------------
# Messages from sensor thread → GUI
# ---------------------------------------------------------------------------

@dataclass
class MsgReading:
    vwc: float
    eps_r: float
    gamma: float
    phase: float
    soil_bin: int
    raw_amp: float = 0.0
    timestamp: float = field(default_factory=time.time)

@dataclass
class MsgCalProgress:
    current: int
    total: int

@dataclass
class MsgCalDone:
    amplitudes: list[float]

@dataclass
class MsgStatus:
    level: str   # 'info' | 'warning' | 'error'
    text: str

@dataclass
class MsgConnected:
    pass

@dataclass
class MsgThreadDone:
    pass

@dataclass
class MsgRangeProfile:
    linear_mag: list   # linear amplitude per bin (first 64 bins)
    soil_bin: int      # expected soil bin index


# ---------------------------------------------------------------------------
# Messages from capacitive-probe box thread → GUI
# ---------------------------------------------------------------------------

@dataclass
class MsgCapData:
    moisture: list   # 10 raw ADC ints, index = sensor number

@dataclass
class MsgCapStatus:
    level: str   # 'info' | 'warning' | 'error'
    text: str

@dataclass
class MsgCapConnected:
    pass

@dataclass
class MsgCapThreadDone:
    pass


# ---------------------------------------------------------------------------
# Capacitive-probe box background thread
# ---------------------------------------------------------------------------

class CapBoxThread(threading.Thread):
    """
    Reads DATA,... lines from the SmartBox ESP32 over USB serial and puts
    MsgCapData on the queue. Also supports sending provisioning commands
    (SETIP/CLEARIP/GETIP) back to the box.
    """

    def __init__(self, out_queue: queue.Queue, port: str, baud: int = 115200):
        super().__init__(daemon=True, name="CapBoxThread")
        self._q = out_queue
        self._port = port
        self._baud = baud
        self._stop = threading.Event()
        self._ser: Optional[object] = None
        self._write_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def send_line(self, text: str):
        with self._write_lock:
            if self._ser is not None and self._ser.is_open:
                self._ser.write((text + "\n").encode())

    def run(self):
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=1)
            self._q.put(MsgCapConnected())
            while not self._stop.is_set():
                line = self._ser.readline()
                if not line:
                    continue
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                if text.startswith("DATA,"):
                    parts = text.split(",")[1:]
                    try:
                        values = [int(p) for p in parts]
                    except ValueError:
                        continue
                    if len(values) == 10:
                        self._q.put(MsgCapData(moisture=values))
                else:
                    self._q.put(MsgCapStatus(level="info", text=text))
        except Exception as exc:
            self._q.put(MsgCapStatus(level="error", text=f"Cap box error: {exc}"))
        finally:
            if self._ser is not None and self._ser.is_open:
                self._ser.close()
            self._q.put(MsgCapThreadDone())


# ---------------------------------------------------------------------------
# Messages from Doppler radar ESP32 thread → GUI
# ---------------------------------------------------------------------------

@dataclass
class MsgDopplerData:
    motion: bool

@dataclass
class MsgDopplerStatus:
    level: str   # 'info' | 'warning' | 'error'
    text: str

@dataclass
class MsgDopplerConnected:
    pass

@dataclass
class MsgDopplerThreadDone:
    pass


# ---------------------------------------------------------------------------
# Doppler radar ESP32 background thread
# ---------------------------------------------------------------------------

class DopplerThread(threading.Thread):
    """Reads MOTION,0/1 lines from the CQRobot 10.525GHz sensor's ESP32 over USB serial."""

    def __init__(self, out_queue: queue.Queue, port: str, baud: int = 115200):
        super().__init__(daemon=True, name="DopplerThread")
        self._q = out_queue
        self._port = port
        self._baud = baud
        self._stop = threading.Event()
        self._ser: Optional[object] = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=1)
            self._q.put(MsgDopplerConnected())
            while not self._stop.is_set():
                line = self._ser.readline()
                if not line:
                    continue
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                if text.startswith("MOTION,"):
                    val = text.split(",", 1)[1].strip()
                    if val in ("0", "1"):
                        self._q.put(MsgDopplerData(motion=(val == "1")))
                else:
                    self._q.put(MsgDopplerStatus(level="info", text=text))
        except Exception as exc:
            self._q.put(MsgDopplerStatus(level="error", text=f"Doppler radar error: {exc}"))
        finally:
            if self._ser is not None and self._ser.is_open:
                self._ser.close()
            self._q.put(MsgDopplerThreadDone())


# ---------------------------------------------------------------------------
# Messages from Acconeer A121 (XM125 + XC120, USB) thread → GUI
# ---------------------------------------------------------------------------

@dataclass
class MsgA121Data:
    distances_m: list
    strengths_db: list

@dataclass
class MsgA121Status:
    level: str   # 'info' | 'warning' | 'error'
    text: str

@dataclass
class MsgA121Connected:
    pass

@dataclass
class MsgA121ThreadDone:
    pass


# ---------------------------------------------------------------------------
# Acconeer A121 background thread (direct USB via acconeer-exptool)
# ---------------------------------------------------------------------------

class A121Thread(threading.Thread):
    """Reads distance/strength readings from an Acconeer XM125+XC120 (A121) over USB."""

    def __init__(self, out_queue: queue.Queue, start_m: float = 0.0, end_m: float = 2.0):
        super().__init__(daemon=True, name="A121Thread")
        self._q = out_queue
        self._start_m = start_m
        self._end_m = end_m
        self._stop = threading.Event()
        self._client = None
        self._detector = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            from acconeer.exptool import a121
            from acconeer.exptool.a121.algo.distance import Detector, DetectorConfig, ThresholdMethod

            self._q.put(MsgA121Status(level="info", text="Opening A121 over USB …"))
            self._client = a121.Client.open(usb_device=True)

            detector_config = DetectorConfig(
                start_m=self._start_m,
                end_m=self._end_m,
                max_profile=a121.Profile.PROFILE_3,
                max_step_length=12,
                threshold_method=ThresholdMethod.RECORDED,
            )
            self._detector = Detector(client=self._client, sensor_ids=[1], detector_config=detector_config)

            self._q.put(MsgA121Status(level="info", text="Calibrating detector …"))
            self._detector.calibrate_detector()
            self._detector.start()

            self._q.put(MsgA121Connected())

            while not self._stop.is_set():
                result = self._detector.get_next()
                sensor_result = result[1]
                distances = list(sensor_result.distances) if sensor_result.distances is not None else []
                strengths = list(sensor_result.strengths) if sensor_result.strengths is not None else []
                self._q.put(MsgA121Data(distances_m=distances, strengths_db=strengths))
        except Exception as exc:
            self._q.put(MsgA121Status(level="error", text=f"A121 error: {exc}"))
        finally:
            try:
                if self._detector is not None:
                    self._detector.stop()
            except Exception:
                pass
            try:
                if self._client is not None:
                    self._client.close()
            except Exception:
                pass
            self._q.put(MsgA121ThreadDone())


# ---------------------------------------------------------------------------
# Messages from Seeed MR60BHA2 (XIAO ESP32C6) thread → GUI
# ---------------------------------------------------------------------------

@dataclass
class MsgBHA2Data:
    present: bool
    breath_rate: Optional[float]
    heart_rate: Optional[float]
    distance_m: Optional[float]

@dataclass
class MsgBHA2Status:
    level: str   # 'info' | 'warning' | 'error'
    text: str

@dataclass
class MsgBHA2Connected:
    pass

@dataclass
class MsgBHA2ThreadDone:
    pass


# ---------------------------------------------------------------------------
# Seeed MR60BHA2 background thread
# ---------------------------------------------------------------------------

class BHA2Thread(threading.Thread):
    """Reads BHA,<present>,<breath_rate>,<heart_rate>,<distance> lines from the
    Seeed MR60BHA2's XIAO ESP32C6 over USB serial."""

    def __init__(self, out_queue: queue.Queue, port: str, baud: int = 115200):
        super().__init__(daemon=True, name="BHA2Thread")
        self._q = out_queue
        self._port = port
        self._baud = baud
        self._stop = threading.Event()
        self._ser: Optional[object] = None

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=1)
            self._q.put(MsgBHA2Connected())
            while not self._stop.is_set():
                line = self._ser.readline()
                if not line:
                    continue
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                if text.startswith("BHA,"):
                    parts = text.split(",")
                    if len(parts) == 5:
                        try:
                            present = parts[1].strip() == "1"
                            breath_rate = float(parts[2])
                            heart_rate = float(parts[3])
                            distance_m = float(parts[4])
                            self._q.put(MsgBHA2Data(
                                present=present,
                                breath_rate=breath_rate if breath_rate >= 0 else None,
                                heart_rate=heart_rate if heart_rate >= 0 else None,
                                distance_m=distance_m if distance_m >= 0 else None,
                            ))
                        except ValueError:
                            pass
                else:
                    self._q.put(MsgBHA2Status(level="info", text=text))
        except Exception as exc:
            self._q.put(MsgBHA2Status(level="error", text=f"MR60BHA2 error: {exc}"))
        finally:
            if self._ser is not None and self._ser.is_open:
                self._ser.close()
            self._q.put(MsgBHA2ThreadDone())


# ---------------------------------------------------------------------------
# Sensor background thread
# ---------------------------------------------------------------------------

class SensorThread(threading.Thread):
    """
    Runs the full capture → FFT → reflection → VWC pipeline in a daemon thread.
    Communicates with the GUI via a Queue of Msg* objects.
    """

    def __init__(
        self,
        out_queue: queue.Queue,
        cli_port: str,
        data_port: Optional[str],
        mode: str,
        config_path: str,
        chirp_cfg: ChirpConfig,
        proc_cfg: ProcessingConfig,
        cal_mgr: CalibrationManager,
    ):
        super().__init__(daemon=True, name="SensorThread")
        self._q = out_queue
        self._cli_port = cli_port
        self._data_port = data_port
        self._mode = mode
        self._config_path = config_path
        self._chirp_cfg = chirp_cfg
        self._proc_cfg = proc_cfg
        self._cal_mgr = cal_mgr

        self._stop = threading.Event()
        self._measuring = threading.Event()
        self._measuring.set()

        self._cal_lock = threading.Lock()
        self._cal_active = False
        self._cal_target = 0
        self._cal_buffer: list[float] = []
        self._frame_count = 0

    def stop(self):
        self._stop.set()

    def pause(self):
        self._measuring.clear()

    def resume(self):
        self._measuring.set()

    def start_cal_collection(self, n: int = CAL_SAMPLES):
        with self._cal_lock:
            self._cal_buffer.clear()
            self._cal_target = n
            self._cal_active = True

    def _log(self, level: str, text: str):
        self._q.put(MsgStatus(level=level, text=text))

    def run(self):
        try:
            if self._mode == "dca1000":
                self._run_dca1000()
            else:
                self._run_uart()
        except Exception as exc:
            self._log("error", f"Sensor error: {exc}")
        finally:
            self._q.put(MsgThreadDone())

    # ---- capture loops ---------------------------------------------------

    def _run_dca1000(self):
        extractor = ReflectionExtractor(self._chirp_cfg, self._proc_cfg, self._cal_mgr.data)
        self._log("info", f"Opening CLI port {self._cli_port} …")
        with UARTInterface(self._cli_port) as uart:
            uart.send_config(self._config_path)
            self._log("info", "Config sent. Opening DCA1000EVM …")
            with DCA1000() as dca:
                self._log("info", "DCA1000 ready. Streaming …")
                self._q.put(MsgConnected())
                for frame_iq in dca.capture_frames(
                    self._chirp_cfg, num_frames=999_999, stop_event=self._stop
                ):
                    if self._stop.is_set():
                        break
                    profile = compute_range_profile(frame_iq, self._proc_cfg.window)
                    self._process(profile, extractor, complex_mode=True)

    def _run_uart(self):
        if not self._data_port:
            self._log("error", "Data port required for UART mode")
            return
        extractor = ReflectionExtractor(self._chirp_cfg, self._proc_cfg, self._cal_mgr.data)
        self._log("info", f"Opening CLI {self._cli_port} / Data {self._data_port} …")
        with UARTInterface(self._cli_port, self._data_port) as uart:
            uart.send_config(self._config_path)
            self._log("info", "Config sent. Reading UART frames …")
            self._q.put(MsgConnected())
            parser = FrameParser(uart)
            for frame in parser.iter_frames():
                if self._stop.is_set():
                    break
                if frame.range_profile is None:
                    continue
                self._process(frame.range_profile, extractor, complex_mode=False)

    # ---- per-frame logic -------------------------------------------------

    def _process(self, profile: np.ndarray, extractor: ReflectionExtractor,
                 complex_mode: bool):
        soil_bin = self._proc_cfg.soil_bin_index(self._chirp_cfg)
        tol = self._proc_cfg.soil_bin_tolerance
        lo = max(0, soil_bin - tol)
        hi = min(len(profile) - 1, soil_bin + tol)

        # Convert to linear amplitude once — used for calibration, range display, and UART op path
        if complex_mode:
            linear_profile = np.abs(profile).astype(float)
        else:
            linear_profile = np.sqrt(np.power(10.0, profile.astype(float) / 2560.0))

        raw_amp = float(np.max(linear_profile[lo:hi + 1]))

        # Feed calibration buffer
        with self._cal_lock:
            if self._cal_active:
                self._cal_buffer.append(raw_amp)
                n = len(self._cal_buffer)
                self._q.put(MsgCalProgress(current=n, total=self._cal_target))
                if n >= self._cal_target:
                    self._cal_active = False
                    self._q.put(MsgCalDone(amplitudes=list(self._cal_buffer)))
                    self._cal_buffer.clear()

        # Send range profile snapshot every 5 frames for the range plot
        self._frame_count += 1
        if self._frame_count % 5 == 0:
            n_bins = min(64, len(linear_profile))
            self._q.put(MsgRangeProfile(
                linear_mag=linear_profile[:n_bins].tolist(),
                soil_bin=soil_bin,
            ))

        if not self._measuring.is_set():
            return

        if complex_mode:
            result = extractor.push_frame_profile(profile)
            if result is not None:
                eps_r, vwc = self._cal_mgr.apply(result.gamma_magnitude)
                log.info("DIAG raw_amp=%.6f gamma=%.6f eps_r=%.3f vwc=%.4f bin=%d",
                          result.amplitude, result.gamma_magnitude, eps_r, vwc, result.soil_bin)
                self._q.put(MsgReading(
                    vwc=vwc, eps_r=eps_r, gamma=result.gamma_magnitude,
                    phase=result.phase_rad, soil_bin=result.soil_bin,
                    raw_amp=result.amplitude,
                ))
        else:
            gamma = extractor.push_magnitude_profile(profile)
            if gamma is not None:
                eps_r, vwc = self._cal_mgr.apply(gamma)
                raw_amp = gamma * self._cal_mgr.data.metal_amplitude
                log.info("DIAG raw_amp=%.6f gamma=%.6f eps_r=%.3f vwc=%.4f bin=%d",
                          raw_amp, gamma, eps_r, vwc, soil_bin)
                self._q.put(MsgReading(
                    vwc=vwc, eps_r=eps_r, gamma=gamma,
                    phase=0.0, soil_bin=soil_bin,
                    raw_amp=raw_amp,
                ))


# ---------------------------------------------------------------------------
# GUI application
# ---------------------------------------------------------------------------

class SoilMoistureGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("mmWave Soil Moisture Sensor – IWR6843AOP")
        self.root.minsize(960, 680)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        # Runtime state
        self._thread: Optional[SensorThread] = None
        self._queue: queue.Queue = queue.Queue()
        self._cal_mgr: Optional[CalibrationManager] = None
        self._cal_pending: Optional[str] = None   # 'metal' | 'dry' | 'ref'
        self._cal_ref_vwc: float = 0.0
        self._connected = False
        self._measuring = False
        self._chirp_cfg: Optional[ChirpConfig] = None
        self._last_range_profile: Optional[list] = None
        # Sensor numbering mirrors the SmartBox's own Moisture[0..9] indices exactly —
        # e.g. if the box's LCD shows "Sensor 5" reading, GUI source "Sensor 5" is that same value.
        self._doppler_source_name = "📶 Doppler Radar"
        self._a121_source_name = "📏 A121 Range Sensor"
        self._bha2_source_name = "💓 MR60BHA2 (Breath/Heart)"
        self._source_names = (["📡 Radar (mmWave)"] + [f"🌱 Sensor {n}" for n in range(10)]
                               + [self._doppler_source_name, self._a121_source_name, self._bha2_source_name])
        # Per-sensor state: raw ADC calibration references, last reading, log file handle.
        self._cap_probes = {
            n: {"dry_raw": None, "wet_raw": None, "last_raw": None,
                "log_file": None, "log_path": None, "last_file_log_time": 0.0}
            for n in range(10)
        }
        self._cap_thread: Optional[CapBoxThread] = None
        self._cap_connected = False
        self._doppler_thread: Optional[DopplerThread] = None
        self._doppler_connected = False
        self._a121_thread: Optional[A121Thread] = None
        self._a121_connected = False
        self._bha2_thread: Optional[BHA2Thread] = None
        self._bha2_connected = False

        self._vwc_times: deque[float] = deque(maxlen=MAX_PLOT_POINTS)
        self._vwc_vals: deque[float] = deque(maxlen=MAX_PLOT_POINTS)
        self._t0 = time.time()

        self._build_ui()
        self._set_state("disconnected")
        self._poll()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self._build_header()
        self._build_source_selector(row=1)
        self._build_connection_panel(row=2)

        mid = ttk.Frame(self.root)
        mid.grid(row=3, column=0, sticky="nsew", padx=6, pady=4)
        mid.columnconfigure(0, weight=0, minsize=270)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)
        self._radar_mid_frame = mid

        self._build_calibration_panel(mid)
        self._build_right_panel(mid)
        self._build_log_panel(row=4)
        self._build_cap_probe_panel(row=3)
        self._build_doppler_panel(row=3)
        self._build_a121_panel(row=3)
        self._build_bha2_panel(row=3)
        self._on_source_change()

    # ---- Source selector ---------------------------------------------------

    def _build_source_selector(self, row: int):
        frm = ttk.Frame(self.root, padding=(6, 4))
        frm.grid(row=row, column=0, sticky="ew")
        ttk.Label(frm, text="Source:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        self._source_var = tk.StringVar(value=self._source_names[0])
        self._source_cb = ttk.Combobox(frm, textvariable=self._source_var, values=self._source_names,
                                        state="readonly", width=24)
        self._source_cb.pack(side="left")
        self._source_cb.bind("<<ComboboxSelected>>", lambda e: self._on_source_change())

    def _on_source_change(self):
        selection = self._source_var.get()
        is_radar = selection == self._source_names[0]
        is_doppler = selection == self._doppler_source_name
        is_a121 = selection == self._a121_source_name
        is_bha2 = selection == self._bha2_source_name

        self._cap_frame.grid_remove()
        self._doppler_frame.grid_remove()
        self._a121_frame.grid_remove()
        self._bha2_frame.grid_remove()
        self._connection_frame.grid_remove()
        self._radar_mid_frame.grid_remove()
        self._log_frame.grid_remove()

        if is_radar:
            self._connection_frame.grid()
            self._radar_mid_frame.grid()
            self._log_frame.grid()
        elif is_doppler:
            self._doppler_frame.grid()
        elif is_a121:
            self._a121_frame.grid()
        elif is_bha2:
            self._bha2_frame.grid()
        else:
            self._cap_probe_title_var.set(f"Sensor {self._current_cap_probe_num()}")
            self._cap_frame.grid()
            self._refresh_cap_panel()

    def _current_cap_probe_num(self) -> int:
        return int(self._source_var.get()[-1])

    def _build_header(self):
        hdr = tk.Frame(self.root, bg=ACCENT, pady=7)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="mmWave Soil Moisture Sensor",
                 font=("Segoe UI", 13, "bold"), fg="white", bg=ACCENT).pack()
        tk.Label(hdr, text="IWR6843AOP · 60–64 GHz · Topp's equation",
                 font=FONT_LABEL, fg="#d0eaff", bg=ACCENT).pack()

    # ---- Connection panel ------------------------------------------------

    def _build_connection_panel(self, row: int):
        frm = ttk.LabelFrame(self.root, text="Connection", padding=8)
        frm.grid(row=row, column=0, sticky="ew", padx=6, pady=(6, 2))
        frm.columnconfigure(7, weight=1)
        self._connection_frame = frm

        ports = self._list_ports()

        # Row 0: ports + mode
        ttk.Label(frm, text="CLI Port:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._cli_var = tk.StringVar(value="/dev/ttyUSB0" if "/dev/ttyUSB0" in ports else (ports[0] if ports else ""))
        self._cli_cb = ttk.Combobox(frm, textvariable=self._cli_var, values=ports, width=10)
        self._cli_cb.grid(row=0, column=1, padx=(0, 10))

        ttk.Label(frm, text="Data Port:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self._data_var = tk.StringVar(value="/dev/ttyUSB1" if "/dev/ttyUSB1" in ports else (ports[1] if len(ports) > 1 else ""))
        self._data_cb = ttk.Combobox(frm, textvariable=self._data_var, values=ports, width=10)
        self._data_cb.grid(row=0, column=3, padx=(0, 10))

        ttk.Label(frm, text="Mode:").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self._mode_var = tk.StringVar(value="uart")
        mode_frm = ttk.Frame(frm)
        mode_frm.grid(row=0, column=5, padx=(0, 10))
        ttk.Radiobutton(mode_frm, text="DCA1000", variable=self._mode_var,
                        value="dca1000").pack(side="left")
        ttk.Radiobutton(mode_frm, text="UART", variable=self._mode_var,
                        value="uart").pack(side="left", padx=(6, 0))

        ttk.Button(frm, text="↺ Refresh Ports", command=self._refresh_ports
                   ).grid(row=0, column=6, padx=(0, 10))

        # Row 1: standoff, avg frames, config path
        ttk.Label(frm, text="Standoff (m):").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._standoff_var = tk.StringVar(value="0.10")
        ttk.Entry(frm, textvariable=self._standoff_var, width=7
                  ).grid(row=1, column=1, sticky="w", pady=(4, 0))

        ttk.Label(frm, text="Avg Frames:").grid(row=1, column=2, sticky="w",
                                                  padx=(0, 4), pady=(4, 0))
        self._avg_var = tk.StringVar(value="30")
        ttk.Entry(frm, textvariable=self._avg_var, width=6
                  ).grid(row=1, column=3, sticky="w", pady=(4, 0))

        ttk.Label(frm, text="Config:").grid(row=1, column=4, sticky="w", pady=(4, 0))
        self._cfg_var = tk.StringVar(value=str(_APP_DIR / "config" / "chirp_config.cfg"))
        ttk.Entry(frm, textvariable=self._cfg_var, width=30
                  ).grid(row=1, column=5, sticky="ew", pady=(4, 0))
        ttk.Button(frm, text="…", width=2,
                   command=lambda: self._browse(self._cfg_var, [("Config", "*.cfg")])
                   ).grid(row=1, column=6, pady=(4, 0))

        # Row 2: cal file + connect buttons
        ttk.Label(frm, text="Cal file:").grid(row=2, column=4, sticky="w", pady=(4, 0))
        self._cal_path_var = tk.StringVar(value=str(_APP_DIR / "config" / "calibration.json"))
        ttk.Entry(frm, textvariable=self._cal_path_var, width=30
                  ).grid(row=2, column=5, sticky="ew", pady=(4, 0))
        ttk.Button(frm, text="…", width=2,
                   command=lambda: self._browse(self._cal_path_var, [("JSON", "*.json")])
                   ).grid(row=2, column=6, pady=(4, 0))

        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self._connect_btn = ttk.Button(btn_frm, text="⚡ Connect & Send Config",
                                        command=self._on_connect)
        self._connect_btn.pack(side="left", padx=(0, 8))
        self._disconnect_btn = ttk.Button(btn_frm, text="✕ Disconnect",
                                           command=self._on_disconnect)
        self._disconnect_btn.pack(side="left", padx=(0, 8))
        self._auto_standoff_btn = ttk.Button(btn_frm, text="🎯 Auto-Detect Standoff",
                                              command=self._on_auto_standoff)
        self._auto_standoff_btn.pack(side="left")

    # ---- Calibration panel -----------------------------------------------

    def _build_calibration_panel(self, parent):
        frm = ttk.LabelFrame(parent, text="Calibration", padding=10)
        frm.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        frm.columnconfigure(0, weight=1)

        desc = (
            "Follow steps 1–5 in order.\n"
            "Sensor must be connected first.\n"
        )
        ttk.Label(frm, text=desc, font=FONT_LABEL, foreground=GRAY,
                  wraplength=230, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 8))

        # Step buttons
        steps = [
            ("① Metal Plate  (Γ = 1 reference)", "_cal_metal_btn", self._on_cal_metal),
            ("② Dry Soil  (VWC ≈ 0)",            "_cal_dry_btn",   self._on_cal_dry),
            ("③ Add Reference Point",             "_cal_ref_btn",   self._on_cal_ref),
            ("④ Fit Empirical Polynomial",        "_cal_fit_btn",   self._on_cal_fit),
            ("⑤ Save Calibration",                "_cal_save_btn",  self._on_cal_save),
        ]
        for i, (label, attr, cmd) in enumerate(steps):
            btn = ttk.Button(frm, text=label, command=cmd, width=30)
            btn.grid(row=i + 1, column=0, sticky="ew", pady=3)
            setattr(self, attr, btn)

        # Progress label (hidden until collecting)
        self._cal_progress_var = tk.StringVar(value="")
        self._cal_progress_lbl = ttk.Label(frm, textvariable=self._cal_progress_var,
                                            font=FONT_LABEL, foreground=ACCENT)
        self._cal_progress_lbl.grid(row=6, column=0, sticky="w", pady=(6, 2))

        # Calibration summary
        ttk.Separator(frm, orient="horizontal").grid(row=7, column=0, sticky="ew", pady=8)
        ttk.Label(frm, text="Calibration Status", font=("Segoe UI", 9, "bold")
                  ).grid(row=8, column=0, sticky="w")
        self._cal_summary_var = tk.StringVar(value="No calibration loaded.")
        self._cal_summary_lbl = ttk.Label(frm, textvariable=self._cal_summary_var,
                                           font=FONT_LABEL, foreground=GRAY,
                                           wraplength=230, justify="left")
        self._cal_summary_lbl.grid(row=9, column=0, sticky="w", pady=(4, 0))

    # ---- Right panel: readings + plot ------------------------------------

    def _build_right_panel(self, parent):
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_readings_panel(right, row=0)
        self._build_plot_panel(right, row=1)

    def _build_readings_panel(self, parent, row: int):
        frm = ttk.LabelFrame(parent, text="Live Readings", padding=10)
        frm.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        frm.columnconfigure(1, weight=1)

        # Large VWC display
        vwc_frm = ttk.Frame(frm)
        vwc_frm.grid(row=0, column=0, columnspan=3, pady=(0, 8))
        ttk.Label(vwc_frm, text="VWC", font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        self._vwc_var = tk.StringVar(value="—")
        self._vwc_lbl = tk.Label(vwc_frm, textvariable=self._vwc_var,
                                  font=FONT_BIG, fg=GRAY, width=8, anchor="e")
        self._vwc_lbl.pack(side="left")
        ttk.Label(vwc_frm, text="m³/m³", font=("Segoe UI", 10)).pack(side="left", padx=(4, 0))

        # Secondary readings grid
        fields = [
            ("Signal amp (lin.)", "_raw_amp_var"),
            ("εr (permittivity)", "_eps_var"),
            ("|Γ| (reflection coeff)", "_gamma_var"),
            ("Phase (rad)", "_phase_var"),
            ("Soil bin", "_bin_var"),
        ]
        for i, (label, attr) in enumerate(fields):
            ttk.Label(frm, text=label + ":", font=FONT_LABEL).grid(
                row=i + 1, column=0, sticky="w", padx=(0, 12), pady=1)
            var = tk.StringVar(value="—")
            setattr(self, attr, var)
            ttk.Label(frm, textvariable=var, font=FONT_MONO).grid(
                row=i + 1, column=1, sticky="w", pady=1)

        # Start / Stop / Log buttons
        btn_frm = ttk.Frame(frm)
        btn_frm.grid(row=1, column=2, rowspan=4, sticky="e", padx=(20, 0))
        self._start_btn = ttk.Button(btn_frm, text="▶  Start", width=10,
                                      command=self._on_start)
        self._start_btn.pack(pady=4)
        self._stop_btn = ttk.Button(btn_frm, text="■  Stop", width=10,
                                     command=self._on_stop)
        self._stop_btn.pack(pady=4)
        self._log_file_btn = ttk.Button(btn_frm, text="⏺  Log to File", width=12,
                                         command=self._on_toggle_log_file)
        self._log_file_btn.pack(pady=4)
        self._log_file: Optional[object] = None
        self._last_file_log_time: float = 0.0

        # Connection status indicator
        self._status_var = tk.StringVar(value="● Disconnected")
        self._status_lbl = ttk.Label(frm, textvariable=self._status_var,
                                      font=FONT_LABEL)
        self._status_lbl.grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_plot_panel(self, parent, row: int):
        frm = ttk.LabelFrame(parent, text="Plots", padding=6)
        frm.grid(row=row, column=0, sticky="nsew")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        self._fig = Figure(figsize=(5, 4.2), dpi=96)
        self._fig.patch.set_facecolor("#f8f9fa")

        # Top: VWC history
        self._ax = self._fig.add_subplot(211)
        self._ax.set_facecolor("#f0f4f8")
        self._ax.set_xlabel("Time (s)", fontsize=8)
        self._ax.set_ylabel("VWC (m³/m³)", fontsize=8)
        self._ax.set_ylim(0, 0.6)
        self._ax.tick_params(labelsize=7)
        self._ax.grid(True, alpha=0.4, linewidth=0.5)
        (self._plot_line,) = self._ax.plot([], [], "-o", color=ACCENT,
                                            linewidth=1.5, markersize=3)

        # Bottom: range profile
        self._ax_range = self._fig.add_subplot(212)
        self._ax_range.set_facecolor("#f0f4f8")
        self._ax_range.set_xlabel("Range bin", fontsize=8)
        self._ax_range.set_ylabel("Amplitude", fontsize=8)
        self._ax_range.tick_params(labelsize=7)
        self._ax_range.grid(True, alpha=0.4, linewidth=0.5)
        self._range_bars = self._ax_range.bar([], [], color=ACCENT, alpha=0.7)
        self._soil_bin_line = self._ax_range.axvline(x=5, color=RED, linewidth=1.5,
                                                      linestyle="--", label="soil bin")
        self._ax_range.legend(fontsize=7, loc="upper right")

        self._fig.tight_layout(pad=1.2, h_pad=1.5)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frm)
        self._canvas.draw()
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def _build_log_panel(self, row: int):
        frm = ttk.LabelFrame(self.root, text="Log", padding=4)
        frm.grid(row=row, column=0, sticky="ew", padx=6, pady=(2, 6))
        frm.columnconfigure(0, weight=1)
        self.root.rowconfigure(row, weight=0)
        self._log_frame = frm

        self._log_txt = scrolledtext.ScrolledText(
            frm, height=5, font=FONT_MONO, state="disabled",
            wrap="word", background="#1e1e1e", foreground="#d4d4d4",
        )
        self._log_txt.grid(row=0, column=0, sticky="ew")
        self._log_txt.tag_config("info",    foreground="#9cdcfe")
        self._log_txt.tag_config("warning", foreground="#dcdcaa")
        self._log_txt.tag_config("error",   foreground="#f48771")

    # ---- Capacitive probe box connection card -------------------------------

    def _build_cap_connection_card(self, parent, row: int):
        frm = ttk.LabelFrame(parent, text="ESP32 Sensor Box Connection", padding=10)
        frm.grid(row=row, column=0, sticky="ew")
        frm.columnconfigure(5, weight=1)

        ports = self._list_ports()
        ttk.Label(frm, text="Serial Port:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        cap_port_default = "/dev/ttyUSB2" if "/dev/ttyUSB2" in ports else (ports[0] if ports else "")
        self._cap_port_var = tk.StringVar(value=cap_port_default)
        self._cap_port_cb = ttk.Combobox(frm, textvariable=self._cap_port_var, values=ports, width=14)
        self._cap_port_cb.grid(row=0, column=1, padx=(0, 10))
        ttk.Button(frm, text="↺", width=2, command=self._refresh_cap_ports
                   ).grid(row=0, column=2, padx=(0, 10))

        self._cap_connect_btn = ttk.Button(frm, text="⚡ Connect", command=self._on_cap_connect)
        self._cap_connect_btn.grid(row=0, column=3, padx=(0, 6))
        self._cap_disconnect_btn = ttk.Button(frm, text="✕ Disconnect", command=self._on_cap_disconnect,
                                               state=tk.DISABLED)
        self._cap_disconnect_btn.grid(row=0, column=4, padx=(0, 6))

        self._cap_conn_status_var = tk.StringVar(value="● Disconnected")
        ttk.Label(frm, textvariable=self._cap_conn_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=0, column=5, sticky="e")

        ttk.Separator(frm, orient="horizontal").grid(row=1, column=0, columnspan=6, sticky="ew", pady=8)

        ttk.Label(frm, text="IP:").grid(row=2, column=0, sticky="w")
        self._cap_ip_var = tk.StringVar(value="192.168.1.184")
        ttk.Entry(frm, textvariable=self._cap_ip_var, width=16).grid(row=2, column=1, sticky="w", padx=(0, 10))

        ttk.Label(frm, text="Gateway:").grid(row=2, column=2, sticky="w")
        self._cap_gw_var = tk.StringVar(value="192.168.1.1")
        ttk.Entry(frm, textvariable=self._cap_gw_var, width=16).grid(row=2, column=3, sticky="w", padx=(0, 10))

        ttk.Label(frm, text="Subnet:").grid(row=2, column=4, sticky="w")
        self._cap_sn_var = tk.StringVar(value="255.255.255.0")
        ttk.Entry(frm, textvariable=self._cap_sn_var, width=16).grid(row=2, column=5, sticky="w")

        push_frm = ttk.Frame(frm)
        push_frm.grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self._cap_push_btn = ttk.Button(push_frm, text="📤 Push Network Config to ESP32",
                                         command=self._on_cap_push_config, state=tk.DISABLED)
        self._cap_push_btn.pack(side="left", padx=(0, 8))
        self._cap_clear_btn = ttk.Button(push_frm, text="Clear (use DHCP)",
                                          command=self._on_cap_clear_config, state=tk.DISABLED)
        self._cap_clear_btn.pack(side="left")

        ttk.Label(frm, text="Push saves the config to the ESP32's flash and reboots it to apply — "
                             "only needed once, or whenever you want to change networks.",
                  font=FONT_LABEL, foreground=GRAY, wraplength=680, justify="left"
                  ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _refresh_cap_ports(self):
        ports = self._list_ports()
        self._cap_port_cb["values"] = ports

    # ---- Capacitive probe panel (placeholder — ESP32 link not wired up yet) ----

    def _build_cap_probe_panel(self, row: int):
        frm = ttk.Frame(self.root, padding=6)
        frm.grid(row=row, column=0, sticky="nsew", padx=6, pady=4)
        frm.columnconfigure(0, weight=1)
        self._cap_frame = frm

        self._build_cap_connection_card(frm, row=0)

        card = ttk.LabelFrame(frm, text="Capacitive Sensor", padding=16)
        card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card.columnconfigure(0, weight=1)

        self._cap_probe_title_var = tk.StringVar(value="Sensor 0")
        ttk.Label(card, textvariable=self._cap_probe_title_var,
                  font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self._cap_status_var = tk.StringVar(value="● Not connected")
        ttk.Label(card, textvariable=self._cap_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=1, column=0, sticky="w", pady=(0, 16))

        moist_frm = ttk.Frame(card)
        moist_frm.grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Label(moist_frm, text="Moisture", font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        self._cap_moisture_var = tk.StringVar(value="—")
        self._cap_moisture_lbl = tk.Label(moist_frm, textvariable=self._cap_moisture_var,
                                           font=FONT_BIG, fg=GRAY, width=8, anchor="e")
        self._cap_moisture_lbl.pack(side="left")
        ttk.Label(moist_frm, text="%", font=("Segoe UI", 10)).pack(side="left", padx=(4, 0))

        ttk.Label(card, text="Raw ADC:", font=FONT_LABEL).grid(row=3, column=0, sticky="w", pady=1)
        self._cap_raw_var = tk.StringVar(value="—")
        ttk.Label(card, textvariable=self._cap_raw_var, font=FONT_MONO
                  ).grid(row=4, column=0, sticky="w", pady=(0, 16))

        # ---- Calibration ----
        ttk.Separator(card, orient="horizontal").grid(row=5, column=0, sticky="ew", pady=8)
        ttk.Label(card, text="Calibration", font=("Segoe UI", 9, "bold")).grid(
            row=6, column=0, sticky="w", pady=(0, 4))

        cal_btn_frm = ttk.Frame(card)
        cal_btn_frm.grid(row=7, column=0, sticky="w", pady=(0, 4))
        self._cap_cal_dry_btn = ttk.Button(cal_btn_frm, text="🌬️ Calibrate Dry/Air",
                                            command=self._on_cap_calibrate_dry)
        self._cap_cal_dry_btn.pack(side="left", padx=(0, 8))
        self._cap_cal_wet_btn = ttk.Button(cal_btn_frm, text="💧 Calibrate Water",
                                            command=self._on_cap_calibrate_wet)
        self._cap_cal_wet_btn.pack(side="left")

        self._cap_cal_summary_var = tk.StringVar(value="Dry ref: —    Wet ref: —")
        ttk.Label(card, textvariable=self._cap_cal_summary_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=8, column=0, sticky="w", pady=(0, 16))

        # ---- Logging ----
        ttk.Separator(card, orient="horizontal").grid(row=9, column=0, sticky="ew", pady=8)
        log_frm = ttk.Frame(card)
        log_frm.grid(row=10, column=0, sticky="w", pady=(0, 4))
        self._cap_log_btn = ttk.Button(log_frm, text="⏺  Log to File",
                                        command=self._on_cap_toggle_log)
        self._cap_log_btn.pack(side="left")
        self._cap_log_status_var = tk.StringVar(value="Not logging")
        ttk.Label(card, textvariable=self._cap_log_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=11, column=0, sticky="w", pady=(0, 16))

        frm.grid_remove()

    # ---- Doppler radar panel -------------------------------------------------

    def _build_doppler_panel(self, row: int):
        frm = ttk.Frame(self.root, padding=6)
        frm.grid(row=row, column=0, sticky="nsew", padx=6, pady=4)
        frm.columnconfigure(0, weight=1)
        self._doppler_frame = frm

        conn = ttk.LabelFrame(frm, text="Doppler Radar Connection", padding=10)
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(4, weight=1)

        ports = self._list_ports()
        ttk.Label(conn, text="Serial Port:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        dop_default = "/dev/ttyUSB0" if "/dev/ttyUSB0" in ports else (ports[0] if ports else "")
        self._doppler_port_var = tk.StringVar(value=dop_default)
        self._doppler_port_cb = ttk.Combobox(conn, textvariable=self._doppler_port_var,
                                              values=ports, width=14)
        self._doppler_port_cb.grid(row=0, column=1, padx=(0, 10))
        ttk.Button(conn, text="↺", width=2, command=self._refresh_doppler_ports
                   ).grid(row=0, column=2, padx=(0, 10))

        self._doppler_connect_btn = ttk.Button(conn, text="⚡ Connect", command=self._on_doppler_connect)
        self._doppler_connect_btn.grid(row=0, column=3, padx=(0, 6))
        self._doppler_disconnect_btn = ttk.Button(conn, text="✕ Disconnect",
                                                   command=self._on_doppler_disconnect, state=tk.DISABLED)
        self._doppler_disconnect_btn.grid(row=0, column=4, sticky="w")

        self._doppler_conn_status_var = tk.StringVar(value="● Disconnected")
        ttk.Label(conn, textvariable=self._doppler_conn_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        card = ttk.LabelFrame(frm, text="10.525GHz Doppler Motion Sensor", padding=16)
        card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card.columnconfigure(0, weight=1)

        self._doppler_status_var = tk.StringVar(value="—")
        self._doppler_status_lbl = tk.Label(card, textvariable=self._doppler_status_var,
                                             font=FONT_BIG, fg=GRAY, anchor="w")
        self._doppler_status_lbl.grid(row=0, column=0, sticky="w", pady=(0, 16))

        log_frm = ttk.Frame(card)
        log_frm.grid(row=1, column=0, sticky="w")
        self._doppler_log_btn = ttk.Button(log_frm, text="⏺  Log to File",
                                            command=self._on_doppler_toggle_log)
        self._doppler_log_btn.pack(side="left")
        self._doppler_log_status_var = tk.StringVar(value="Not logging")
        ttk.Label(card, textvariable=self._doppler_log_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self._doppler_log_file: Optional[object] = None
        self._doppler_log_path: Optional[Path] = None
        self._doppler_last_file_log_time: float = 0.0

        frm.grid_remove()

    def _refresh_doppler_ports(self):
        ports = self._list_ports()
        self._doppler_port_cb["values"] = ports

    # ---- A121 range sensor panel -------------------------------------------

    def _build_a121_panel(self, row: int):
        frm = ttk.Frame(self.root, padding=6)
        frm.grid(row=row, column=0, sticky="nsew", padx=6, pady=4)
        frm.columnconfigure(0, weight=1)
        self._a121_frame = frm

        conn = ttk.LabelFrame(frm, text="A121 Range Sensor Connection (USB)", padding=10)
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(2, weight=1)

        ttk.Label(conn, text="Connects directly over USB — no port selection needed."
                  ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._a121_connect_btn = ttk.Button(conn, text="⚡ Connect", command=self._on_a121_connect)
        self._a121_connect_btn.grid(row=1, column=0, padx=(0, 6))
        self._a121_disconnect_btn = ttk.Button(conn, text="✕ Disconnect",
                                                command=self._on_a121_disconnect, state=tk.DISABLED)
        self._a121_disconnect_btn.grid(row=1, column=1, sticky="w")

        self._a121_conn_status_var = tk.StringVar(value="● Disconnected")
        ttk.Label(conn, textvariable=self._a121_conn_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        card = ttk.LabelFrame(frm, text="Acconeer A121 (XM125 + XC120, 60GHz PCR)", padding=16)
        card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card.columnconfigure(0, weight=1)

        self._a121_status_var = tk.StringVar(value="—")
        self._a121_status_lbl = tk.Label(card, textvariable=self._a121_status_var,
                                          font=FONT_BIG, fg=GRAY, anchor="w", justify="left")
        self._a121_status_lbl.grid(row=0, column=0, sticky="w", pady=(0, 16))

        log_frm = ttk.Frame(card)
        log_frm.grid(row=1, column=0, sticky="w")
        self._a121_log_btn = ttk.Button(log_frm, text="⏺  Log to File",
                                         command=self._on_a121_toggle_log)
        self._a121_log_btn.pack(side="left")
        self._a121_log_status_var = tk.StringVar(value="Not logging")
        ttk.Label(card, textvariable=self._a121_log_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self._a121_log_file: Optional[object] = None
        self._a121_log_path: Optional[Path] = None
        self._a121_last_file_log_time: float = 0.0

        frm.grid_remove()

    # ---- MR60BHA2 (Seeed 60GHz breath/heart) panel -------------------------

    def _build_bha2_panel(self, row: int):
        frm = ttk.Frame(self.root, padding=6)
        frm.grid(row=row, column=0, sticky="nsew", padx=6, pady=4)
        frm.columnconfigure(0, weight=1)
        self._bha2_frame = frm

        conn = ttk.LabelFrame(frm, text="MR60BHA2 Connection (XIAO ESP32C6, USB)", padding=10)
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(4, weight=1)

        ports = self._list_ports()
        ttk.Label(conn, text="Serial Port:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        bha2_default = "/dev/ttyUSB0" if "/dev/ttyUSB0" in ports else (ports[0] if ports else "")
        self._bha2_port_var = tk.StringVar(value=bha2_default)
        self._bha2_port_cb = ttk.Combobox(conn, textvariable=self._bha2_port_var,
                                           values=ports, width=14)
        self._bha2_port_cb.grid(row=0, column=1, padx=(0, 10))
        ttk.Button(conn, text="↺", width=2, command=self._refresh_bha2_ports
                   ).grid(row=0, column=2, padx=(0, 10))

        self._bha2_connect_btn = ttk.Button(conn, text="⚡ Connect", command=self._on_bha2_connect)
        self._bha2_connect_btn.grid(row=0, column=3, padx=(0, 6))
        self._bha2_disconnect_btn = ttk.Button(conn, text="✕ Disconnect",
                                                command=self._on_bha2_disconnect, state=tk.DISABLED)
        self._bha2_disconnect_btn.grid(row=0, column=4, sticky="w")

        self._bha2_conn_status_var = tk.StringVar(value="● Disconnected")
        ttk.Label(conn, textvariable=self._bha2_conn_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        card = ttk.LabelFrame(frm, text="Seeed MR60BHA2 (60GHz Breath/Heart)", padding=16)
        card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card.columnconfigure(0, weight=1)

        self._bha2_status_var = tk.StringVar(value="—")
        self._bha2_status_lbl = tk.Label(card, textvariable=self._bha2_status_var,
                                          font=FONT_BIG, fg=GRAY, anchor="w", justify="left")
        self._bha2_status_lbl.grid(row=0, column=0, sticky="w", pady=(0, 16))

        log_frm = ttk.Frame(card)
        log_frm.grid(row=1, column=0, sticky="w")
        self._bha2_log_btn = ttk.Button(log_frm, text="⏺  Log to File",
                                         command=self._on_bha2_toggle_log)
        self._bha2_log_btn.pack(side="left")
        self._bha2_log_status_var = tk.StringVar(value="Not logging")
        ttk.Label(card, textvariable=self._bha2_log_status_var, font=FONT_LABEL,
                  foreground=GRAY).grid(row=2, column=0, sticky="w", pady=(6, 0))

        self._bha2_log_file: Optional[object] = None
        self._bha2_log_path: Optional[Path] = None
        self._bha2_last_file_log_time: float = 0.0

        frm.grid_remove()

    def _refresh_bha2_ports(self):
        ports = self._list_ports()
        self._bha2_port_cb["values"] = ports

    # ---- Capacitive probe handlers -----------------------------------------

    def _refresh_cap_panel(self):
        """Repaint the shared cap-probe panel from the currently selected probe's state."""
        probe = self._cap_probes[self._current_cap_probe_num()]

        if not self._cap_connected:
            self._cap_status_var.set("● Box not connected")
        elif probe["last_raw"] is None:
            self._cap_status_var.set("● Connected — waiting for a reading")
        else:
            self._cap_status_var.set("● Receiving live data")

        dry = f"{probe['dry_raw']:.0f}" if probe["dry_raw"] is not None else "—"
        wet = f"{probe['wet_raw']:.0f}" if probe["wet_raw"] is not None else "—"
        self._cap_cal_summary_var.set(f"Dry ref: {dry}    Wet ref: {wet}")

        raw = probe["last_raw"]
        self._cap_raw_var.set(f"{raw:.0f}" if raw is not None else "—")

        pct = self._cap_moisture_pct(probe)
        self._cap_moisture_var.set(f"{pct:.1f}" if pct is not None else "—")
        self._cap_moisture_lbl.config(fg=GREEN if pct is not None and pct > 1.0 else GRAY)

        if probe["log_file"]:
            self._cap_log_btn.config(text="⏹  Stop Logging")
            self._cap_log_status_var.set(f"Logging to {probe['log_path'].name}")
        else:
            self._cap_log_btn.config(text="⏺  Log to File")
            self._cap_log_status_var.set("Not logging")

    @staticmethod
    def _cap_moisture_pct(probe: dict) -> Optional[float]:
        raw, dry, wet = probe["last_raw"], probe["dry_raw"], probe["wet_raw"]
        if raw is None or dry is None or wet is None or dry == wet:
            return None
        pct = (raw - dry) / (wet - dry) * 100.0
        return max(0.0, min(100.0, pct))

    # ---- Capacitive box connection handlers ---------------------------------

    def _on_cap_connect(self):
        port = self._cap_port_var.get()
        if not port:
            messagebox.showwarning("No port selected", "Choose the ESP32's serial port first.")
            return
        self._cap_thread = CapBoxThread(out_queue=self._queue, port=port)
        self._cap_thread.start()
        self._cap_conn_status_var.set("◌ Connecting …")
        self._log("info", f"[CapBox] Connecting on {port} …")

    def _on_cap_disconnect(self):
        if self._cap_thread:
            self._cap_thread.stop()
            self._log("info", "[CapBox] Disconnecting …")

    def _on_cap_push_config(self):
        if not self._cap_thread or not self._cap_connected:
            messagebox.showwarning("Not connected", "Connect to the ESP32 first.")
            return
        ip, gw, sn = self._cap_ip_var.get().strip(), self._cap_gw_var.get().strip(), self._cap_sn_var.get().strip()
        try:
            for part in (ip, gw, sn):
                octets = part.split(".")
                if len(octets) != 4 or not all(0 <= int(o) <= 255 for o in octets):
                    raise ValueError
        except ValueError:
            messagebox.showerror("Invalid address", "IP, Gateway, and Subnet must each be a valid "
                                  "dotted IPv4 address (e.g. 192.168.1.184).")
            return
        if messagebox.askokcancel(
            "Push network config",
            f"This saves the following to the ESP32's flash and reboots it:\n\n"
            f"  IP:      {ip}\n  Gateway: {gw}\n  Subnet:  {sn}\n\n"
            "The box will disconnect briefly while it restarts. Continue?",
        ):
            self._cap_thread.send_line(f"SETIP,{ip},{gw},{sn}")
            self._log("info", f"[CapBox] Sent SETIP,{ip},{gw},{sn}")

    def _on_cap_clear_config(self):
        if not self._cap_thread or not self._cap_connected:
            messagebox.showwarning("Not connected", "Connect to the ESP32 first.")
            return
        if messagebox.askokcancel(
            "Clear network config",
            "This clears the saved static IP and reboots the ESP32 back to plain DHCP. Continue?",
        ):
            self._cap_thread.send_line("CLEARIP")
            self._log("info", "[CapBox] Sent CLEARIP")

    def _on_cap_calibrate_dry(self):
        probe = self._cap_probes[self._current_cap_probe_num()]
        if probe["last_raw"] is None:
            messagebox.showwarning("No live data", "No reading from this probe yet — "
                                    "connect the ESP32 first.")
            return
        probe["dry_raw"] = probe["last_raw"]
        self._log("info", f"Cap probe {self._current_cap_probe_num()}: "
                          f"dry/air reference set to {probe['dry_raw']:.0f}")
        self._refresh_cap_panel()

    def _on_cap_calibrate_wet(self):
        probe = self._cap_probes[self._current_cap_probe_num()]
        if probe["last_raw"] is None:
            messagebox.showwarning("No live data", "No reading from this probe yet — "
                                    "connect the ESP32 first.")
            return
        probe["wet_raw"] = probe["last_raw"]
        self._log("info", f"Cap probe {self._current_cap_probe_num()}: "
                          f"water reference set to {probe['wet_raw']:.0f}")
        self._refresh_cap_panel()

    def _on_cap_toggle_log(self):
        n = self._current_cap_probe_num()
        probe = self._cap_probes[n]
        if probe["log_file"]:
            probe["log_file"].close()
            probe["log_file"] = None
            probe["log_path"] = None
            self._log("info", f"Cap probe {n}: file logging stopped.")
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = _APP_DIR / f"sensor{n}_{ts}.txt"
            probe["log_file"] = open(path, "w")
            probe["log_path"] = path
            probe["last_file_log_time"] = 0.0
            probe["log_file"].write("timestamp\traw_adc\tmoisture_pct\n")
            self._log("info", f"Cap probe {n}: logging to {path.name}")
        self._refresh_cap_panel()

    def _on_cap_reading(self, probe_num: int, raw_adc: float):
        """Hook for live ESP32 data — call this whenever a fresh raw ADC value
        arrives for a probe. Updates its state and, if that probe is the one
        currently shown, refreshes the display. Writes a throttled log row if
        that probe's logging is active. Not yet wired to any live data source."""
        probe = self._cap_probes[probe_num]
        probe["last_raw"] = raw_adc
        if probe["log_file"] and (time.time() - probe["last_file_log_time"]) >= LOG_FILE_INTERVAL_S:
            probe["last_file_log_time"] = time.time()
            pct = self._cap_moisture_pct(probe)
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            probe["log_file"].write(f"{ts}\t{raw_adc:.1f}\t{pct if pct is not None else ''}\n")
            probe["log_file"].flush()
        is_cap_source = self._source_var.get() not in (
            self._source_names[0], self._doppler_source_name,
            self._a121_source_name, self._bha2_source_name
        )
        if is_cap_source and probe_num == self._current_cap_probe_num():
            self._refresh_cap_panel()

    # ---- Doppler radar handlers ----------------------------------------------

    def _on_doppler_connect(self):
        port = self._doppler_port_var.get()
        if not port:
            messagebox.showwarning("No port selected", "Choose the Doppler radar's serial port first.")
            return
        self._doppler_thread = DopplerThread(out_queue=self._queue, port=port)
        self._doppler_thread.start()
        self._doppler_conn_status_var.set("◌ Connecting …")
        self._log("info", f"[Doppler] Connecting on {port} …")

    def _on_doppler_disconnect(self):
        if self._doppler_thread:
            self._doppler_thread.stop()
            self._log("info", "[Doppler] Disconnecting …")

    def _on_doppler_toggle_log(self):
        if self._doppler_log_file:
            self._doppler_log_file.close()
            self._doppler_log_file = None
            self._doppler_log_path = None
            self._doppler_log_btn.config(text="⏺  Log to File")
            self._doppler_log_status_var.set("Not logging")
            self._log("info", "[Doppler] File logging stopped.")
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = _APP_DIR / f"doppler_{ts}.txt"
            self._doppler_log_file = open(path, "w")
            self._doppler_log_path = path
            self._doppler_last_file_log_time = 0.0
            self._doppler_log_file.write("timestamp\tmotion\n")
            self._doppler_log_btn.config(text="⏹  Stop Logging")
            self._doppler_log_status_var.set(f"Logging to {path.name}")
            self._log("info", f"[Doppler] Logging to {path.name}")

    def _on_doppler_reading(self, motion: bool):
        if motion:
            self._doppler_status_var.set("● Motion Detected")
            self._doppler_status_lbl.config(fg=RED)
        else:
            self._doppler_status_var.set("○ Clear")
            self._doppler_status_lbl.config(fg=GRAY)
        if self._doppler_log_file and (time.time() - self._doppler_last_file_log_time) >= LOG_FILE_INTERVAL_S:
            self._doppler_last_file_log_time = time.time()
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._doppler_log_file.write(f"{ts}\t{int(motion)}\n")
            self._doppler_log_file.flush()

    # ---- A121 range sensor handlers ----------------------------------------

    def _on_a121_connect(self):
        self._a121_thread = A121Thread(out_queue=self._queue)
        self._a121_thread.start()
        self._a121_conn_status_var.set("◌ Connecting …")
        self._a121_connect_btn.config(state=tk.DISABLED)
        self._log("info", "[A121] Connecting over USB …")

    def _on_a121_disconnect(self):
        if self._a121_thread:
            self._a121_thread.stop()
            self._log("info", "[A121] Disconnecting …")

    def _on_a121_toggle_log(self):
        if self._a121_log_file:
            self._a121_log_file.close()
            self._a121_log_file = None
            self._a121_log_path = None
            self._a121_log_btn.config(text="⏺  Log to File")
            self._a121_log_status_var.set("Not logging")
            self._log("info", "[A121] File logging stopped.")
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = _APP_DIR / f"a121_{ts}.txt"
            self._a121_log_file = open(path, "w")
            self._a121_log_path = path
            self._a121_last_file_log_time = 0.0
            self._a121_log_file.write("timestamp\tdistances_m\tstrengths_db\n")
            self._a121_log_btn.config(text="⏹  Stop Logging")
            self._a121_log_status_var.set(f"Logging to {path.name}")
            self._log("info", f"[A121] Logging to {path.name}")

    def _on_a121_reading(self, distances_m: list, strengths_db: list):
        if not distances_m:
            self._a121_status_var.set("○ No target detected")
            self._a121_status_lbl.config(fg=GRAY)
        else:
            lines = [f"{d:.3f} m  ({s:+.1f} dB)" for d, s in zip(distances_m, strengths_db)]
            self._a121_status_var.set("● Target(s):\n" + "\n".join(lines))
            self._a121_status_lbl.config(fg=GREEN)
        if self._a121_log_file and (time.time() - self._a121_last_file_log_time) >= LOG_FILE_INTERVAL_S:
            self._a121_last_file_log_time = time.time()
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            d_str = ",".join(f"{d:.4f}" for d in distances_m)
            s_str = ",".join(f"{s:.2f}" for s in strengths_db)
            self._a121_log_file.write(f"{ts}\t{d_str}\t{s_str}\n")
            self._a121_log_file.flush()

    # ---- MR60BHA2 handlers --------------------------------------------------

    def _on_bha2_connect(self):
        port = self._bha2_port_var.get()
        if not port:
            messagebox.showwarning("No port selected", "Choose the MR60BHA2's serial port first.")
            return
        self._bha2_thread = BHA2Thread(out_queue=self._queue, port=port)
        self._bha2_thread.start()
        self._bha2_conn_status_var.set("◌ Connecting …")
        self._log("info", f"[MR60BHA2] Connecting on {port} …")

    def _on_bha2_disconnect(self):
        if self._bha2_thread:
            self._bha2_thread.stop()
            self._log("info", "[MR60BHA2] Disconnecting …")

    def _on_bha2_toggle_log(self):
        if self._bha2_log_file:
            self._bha2_log_file.close()
            self._bha2_log_file = None
            self._bha2_log_path = None
            self._bha2_log_btn.config(text="⏺  Log to File")
            self._bha2_log_status_var.set("Not logging")
            self._log("info", "[MR60BHA2] File logging stopped.")
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = _APP_DIR / f"bha2_{ts}.txt"
            self._bha2_log_file = open(path, "w")
            self._bha2_log_path = path
            self._bha2_last_file_log_time = 0.0
            self._bha2_log_file.write("timestamp\tpresent\tbreath_rate\theart_rate\tdistance_m\n")
            self._bha2_log_btn.config(text="⏹  Stop Logging")
            self._bha2_log_status_var.set(f"Logging to {path.name}")
            self._log("info", f"[MR60BHA2] Logging to {path.name}")

    def _on_bha2_reading(self, present: bool, breath_rate, heart_rate, distance_m):
        if not present:
            self._bha2_status_var.set("○ No person detected")
            self._bha2_status_lbl.config(fg=GRAY)
        else:
            br = f"{breath_rate:.1f} bpm" if breath_rate is not None else "—"
            hr = f"{heart_rate:.1f} bpm" if heart_rate is not None else "—"
            dist = f"{distance_m:.2f} m" if distance_m is not None else "—"
            self._bha2_status_var.set(
                f"● Person detected\nBreath rate: {br}\nHeart rate: {hr}\nDistance: {dist}"
            )
            self._bha2_status_lbl.config(fg=GREEN)
        if self._bha2_log_file and (time.time() - self._bha2_last_file_log_time) >= LOG_FILE_INTERVAL_S:
            self._bha2_last_file_log_time = time.time()
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            br_s = f"{breath_rate:.2f}" if breath_rate is not None else ""
            hr_s = f"{heart_rate:.2f}" if heart_rate is not None else ""
            d_s = f"{distance_m:.3f}" if distance_m is not None else ""
            self._bha2_log_file.write(f"{ts}\t{int(present)}\t{br_s}\t{hr_s}\t{d_s}\n")
            self._bha2_log_file.flush()

    # -----------------------------------------------------------------------
    # State management
    # -----------------------------------------------------------------------

    def _set_state(self, state: str):
        """
        state: 'disconnected' | 'connecting' | 'connected' | 'measuring'
        """
        d = tk.DISABLED
        n = tk.NORMAL

        is_conn = state in ("connected", "measuring", "calibrating")
        is_meas = state == "measuring"
        is_cal  = state == "calibrating"

        self._connect_btn.config(state=d if is_conn else n)
        self._disconnect_btn.config(state=n if is_conn else d)
        self._auto_standoff_btn.config(state=n if is_conn else d)
        self._start_btn.config(state=n if (is_conn and not is_meas) else d)
        self._stop_btn.config(state=n if is_meas else d)

        cal_state = n if is_conn else d
        for attr in ("_cal_metal_btn", "_cal_dry_btn", "_cal_ref_btn",
                     "_cal_fit_btn", "_cal_save_btn"):
            getattr(self, attr).config(state=cal_state)

        colour_map = {
            "disconnected": (GRAY, "● Disconnected"),
            "connecting":   (ACCENT, "◌ Connecting …"),
            "connected":    (GREEN, "● Connected (paused)"),
            "measuring":    (GREEN, "● Measuring"),
            "calibrating":  (ACCENT, "◌ Collecting calibration …"),
        }
        fg, txt = colour_map.get(state, (GRAY, state))
        self._status_var.set(txt)
        self._status_lbl.config(foreground=fg)

    # -----------------------------------------------------------------------
    # Connection handlers
    # -----------------------------------------------------------------------

    def _on_connect(self):
        try:
            standoff = float(self._standoff_var.get())
            avg_frames = int(self._avg_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Standoff and Avg Frames must be numbers.")
            return

        cal_path = self._cal_path_var.get().strip() or "config/calibration.json"
        self._cal_mgr = CalibrationManager(cal_path)
        self._update_cal_summary()

        chirp_cfg = ChirpConfig()
        self._chirp_cfg = chirp_cfg
        proc_cfg = ProcessingConfig(standoff_m=standoff, num_frames_average=avg_frames)

        self._thread = SensorThread(
            out_queue=self._queue,
            cli_port=self._cli_var.get(),
            data_port=self._data_var.get() or None,
            mode=self._mode_var.get(),
            config_path=self._cfg_var.get(),
            chirp_cfg=chirp_cfg,
            proc_cfg=proc_cfg,
            cal_mgr=self._cal_mgr,
        )
        self._thread.start()
        self._set_state("connecting")
        self._log("info", f"Connecting via {self._mode_var.get().upper()} "
                          f"on {self._cli_var.get()} …")

    def _on_auto_standoff(self):
        """Scan the live range profile and set Standoff (m) to the true peak bin.

        Skips the first MIN_SEARCH_BIN bins, which this project has repeatedly
        found to be dominated by near-antenna leakage rather than the real
        target — see NOTES.md session findings.
        """
        MIN_SEARCH_BIN = 2
        if not self._chirp_cfg or not self._last_range_profile:
            messagebox.showwarning(
                "No data yet",
                "Connect first and wait a moment for the range profile to populate.",
            )
            return
        profile = self._last_range_profile
        if len(profile) <= MIN_SEARCH_BIN:
            messagebox.showwarning("No data yet", "Range profile too short to search.")
            return
        search = profile[MIN_SEARCH_BIN:]
        peak_bin = MIN_SEARCH_BIN + int(np.argmax(search))
        new_standoff = peak_bin * self._chirp_cfg.range_resolution_m
        self._standoff_var.set(f"{new_standoff:.4f}")
        self._log("info", f"Auto-detected peak at bin {peak_bin} "
                          f"→ standoff set to {new_standoff:.4f} m. "
                          f"Reconnect for it to take effect.")
        messagebox.showinfo(
            "Standoff auto-detected",
            f"Peak found at bin {peak_bin} (searched bins {MIN_SEARCH_BIN}+).\n"
            f"Standoff field set to {new_standoff:.4f} m.\n\n"
            "Disconnect and reconnect for the new value to take effect.",
        )

    def _on_disconnect(self):
        if self._thread:
            self._thread.stop()
            self._log("info", "Disconnecting …")
        if self._log_file:
            self._log_file.close()
            self._log_file = None
            self._log_file_btn.config(text="⏺  Log to File")
        self._connected = False
        self._measuring = False
        self._set_state("disconnected")

    def _on_start(self):
        if self._thread:
            self._thread.resume()
            self._measuring = True
            self._set_state("measuring")
            self._log("info", "Measurement started.")

    def _on_stop(self):
        if self._thread:
            self._thread.pause()
            self._measuring = False
            self._set_state("connected")
            self._log("info", "Measurement paused.")

    # -----------------------------------------------------------------------
    # Calibration handlers
    # -----------------------------------------------------------------------

    def _start_collection(self, cal_type: str, vwc: float = 0.0):
        """Begin a CAL_SAMPLES-frame collection run."""
        if not self._thread:
            messagebox.showwarning("Not connected", "Connect to the sensor first.")
            return
        self._cal_pending = cal_type
        self._cal_ref_vwc = vwc
        self._set_state("calibrating")
        # Disable specific cal buttons during collection
        for attr in ("_cal_metal_btn", "_cal_dry_btn", "_cal_ref_btn"):
            getattr(self, attr).config(state=tk.DISABLED)
        self._cal_progress_var.set(f"Collecting 0 / {CAL_SAMPLES} …")
        self._thread.start_cal_collection(CAL_SAMPLES)

    def _on_cal_metal(self):
        if messagebox.askokcancel(
            "Metal plate calibration",
            "Place a FLAT METAL PLATE (≥ 20 × 20 cm) at the standoff distance,\n"
            "directly under the sensor.\n\nClick OK when ready to collect.",
        ):
            self._start_collection("metal")

    def _on_cal_dry(self):
        if messagebox.askokcancel(
            "Dry soil calibration",
            "Remove the metal plate.\n"
            "Ensure the soil surface is DRY (VWC ≈ 0).\n\nClick OK when ready to collect.",
        ):
            self._start_collection("dry", vwc=0.0)

    def _on_cal_ref(self):
        vwc_str = simpledialog.askstring(
            "Reference point",
            "Enter the gravimetric or TDR-measured VWC at the current soil state (m³/m³):\n"
            "  e.g. 0.15 for moderately moist soil",
            parent=self.root,
        )
        if vwc_str is None:
            return
        try:
            vwc = float(vwc_str)
            if not 0.0 <= vwc <= 0.6:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid VWC", "Enter a value between 0.0 and 0.6 m³/m³.")
            return
        if messagebox.askokcancel(
            "Reference point",
            f"Prepare soil at VWC = {vwc:.3f} m³/m³.\n\nClick OK when ready to collect.",
        ):
            self._start_collection("ref", vwc=vwc)

    def _on_cal_fit(self):
        if not self._cal_mgr:
            messagebox.showwarning("No calibration", "Connect and run metal + dry steps first.")
            return
        pts = self._cal_mgr.data.calibration_points
        if len(pts) < 3:
            messagebox.showwarning(
                "Not enough points",
                f"Need at least 3 calibration points for a polynomial fit.\n"
                f"Currently have {len(pts)}.\n\nAdd more reference points (step ③).",
            )
            return
        try:
            coeffs = self._cal_mgr.fit_polynomial(degree=2)
            self._update_cal_summary()
            messagebox.showinfo(
                "Polynomial fit complete",
                f"Degree-2 polynomial fitted from {len(pts)} points.\n"
                f"Coefficients (a0, a1, a2): {[f'{c:.4g}' for c in coeffs]}",
            )
            self._log("info", f"Polynomial fit complete: {coeffs.tolist()}")
        except Exception as exc:
            messagebox.showerror("Fit failed", str(exc))

    def _on_cal_save(self):
        if not self._cal_mgr:
            messagebox.showwarning("No calibration", "Nothing to save yet.")
            return
        notes = simpledialog.askstring(
            "Save calibration", "Notes (soil type, standoff, date, …):", parent=self.root
        ) or ""
        self._cal_mgr.save(notes=notes)
        self._log("info", f"Calibration saved to {self._cal_mgr._path}")
        messagebox.showinfo("Saved", f"Calibration written to:\n{self._cal_mgr._path}")

    # -----------------------------------------------------------------------
    # Queue polling and message dispatch
    # -----------------------------------------------------------------------

    def _poll(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self._dispatch(msg)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _dispatch(self, msg):
        if isinstance(msg, MsgConnected):
            self._connected = True
            self._measuring = True
            self._set_state("measuring")
            self._log("info", "Connected and streaming.")

        elif isinstance(msg, MsgReading):
            self._on_reading(msg)

        elif isinstance(msg, MsgCalProgress):
            pct = int(100 * msg.current / msg.total)
            self._cal_progress_var.set(
                f"Collecting {msg.current} / {msg.total}  ({pct} %)"
            )

        elif isinstance(msg, MsgCalDone):
            self._handle_cal_done(msg.amplitudes)

        elif isinstance(msg, MsgStatus):
            self._log(msg.level, msg.text)

        elif isinstance(msg, MsgRangeProfile):
            self._update_range_plot(msg.linear_mag, msg.soil_bin)

        elif isinstance(msg, MsgThreadDone):
            self._thread = None
            self._connected = False
            self._set_state("disconnected")
            self._log("info", "Sensor thread stopped.")

        elif isinstance(msg, MsgCapConnected):
            self._cap_connected = True
            self._cap_conn_status_var.set("● Connected")
            self._cap_connect_btn.config(state=tk.DISABLED)
            self._cap_disconnect_btn.config(state=tk.NORMAL)
            self._cap_push_btn.config(state=tk.NORMAL)
            self._cap_clear_btn.config(state=tk.NORMAL)
            self._log("info", "[CapBox] Connected and streaming.")

        elif isinstance(msg, MsgCapData):
            for i in range(10):
                self._on_cap_reading(i, msg.moisture[i])

        elif isinstance(msg, MsgCapStatus):
            self._log(msg.level, f"[CapBox] {msg.text}")

        elif isinstance(msg, MsgCapThreadDone):
            self._cap_thread = None
            self._cap_connected = False
            self._cap_conn_status_var.set("● Disconnected")
            self._cap_connect_btn.config(state=tk.NORMAL)
            self._cap_disconnect_btn.config(state=tk.DISABLED)
            self._cap_push_btn.config(state=tk.DISABLED)
            self._cap_clear_btn.config(state=tk.DISABLED)
            self._log("info", "[CapBox] Thread stopped.")

        elif isinstance(msg, MsgDopplerConnected):
            self._doppler_connected = True
            self._doppler_conn_status_var.set("● Connected")
            self._doppler_connect_btn.config(state=tk.DISABLED)
            self._doppler_disconnect_btn.config(state=tk.NORMAL)
            self._log("info", "[Doppler] Connected and streaming.")

        elif isinstance(msg, MsgDopplerData):
            self._on_doppler_reading(msg.motion)

        elif isinstance(msg, MsgDopplerStatus):
            self._log(msg.level, f"[Doppler] {msg.text}")

        elif isinstance(msg, MsgDopplerThreadDone):
            self._doppler_thread = None
            self._doppler_connected = False
            self._doppler_conn_status_var.set("● Disconnected")
            self._doppler_connect_btn.config(state=tk.NORMAL)
            self._doppler_disconnect_btn.config(state=tk.DISABLED)
            self._log("info", "[Doppler] Thread stopped.")

        elif isinstance(msg, MsgA121Connected):
            self._a121_connected = True
            self._a121_conn_status_var.set("● Connected")
            self._a121_connect_btn.config(state=tk.DISABLED)
            self._a121_disconnect_btn.config(state=tk.NORMAL)
            self._log("info", "[A121] Connected and streaming.")

        elif isinstance(msg, MsgA121Data):
            self._on_a121_reading(msg.distances_m, msg.strengths_db)

        elif isinstance(msg, MsgA121Status):
            self._log(msg.level, f"[A121] {msg.text}")

        elif isinstance(msg, MsgA121ThreadDone):
            self._a121_thread = None
            self._a121_connected = False
            self._a121_conn_status_var.set("● Disconnected")
            self._a121_connect_btn.config(state=tk.NORMAL)
            self._a121_disconnect_btn.config(state=tk.DISABLED)
            self._log("info", "[A121] Thread stopped.")

        elif isinstance(msg, MsgBHA2Connected):
            self._bha2_connected = True
            self._bha2_conn_status_var.set("● Connected")
            self._bha2_connect_btn.config(state=tk.DISABLED)
            self._bha2_disconnect_btn.config(state=tk.NORMAL)
            self._log("info", "[MR60BHA2] Connected and streaming.")

        elif isinstance(msg, MsgBHA2Data):
            self._on_bha2_reading(msg.present, msg.breath_rate, msg.heart_rate, msg.distance_m)

        elif isinstance(msg, MsgBHA2Status):
            self._log(msg.level, f"[MR60BHA2] {msg.text}")

        elif isinstance(msg, MsgBHA2ThreadDone):
            self._bha2_thread = None
            self._bha2_connected = False
            self._bha2_conn_status_var.set("● Disconnected")
            self._bha2_connect_btn.config(state=tk.NORMAL)
            self._bha2_disconnect_btn.config(state=tk.DISABLED)
            self._log("info", "[MR60BHA2] Thread stopped.")

    def _on_toggle_log_file(self):
        if self._log_file:
            self._log_file.close()
            self._log_file = None
            self._log_file_btn.config(text="⏺  Log to File")
            self._log("info", "File logging stopped.")
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = _APP_DIR / f"data_{ts}.txt"
            self._log_file = open(path, "w")
            self._log_file.write("timestamp\tvwc_m3m3\teps_r\tgamma\tphase_rad\tsoil_bin\n")
            self._log_file_btn.config(text="⏹  Stop Logging")
            self._last_file_log_time = 0.0
            self._log("info", f"Logging to {path.name}")

    def _on_reading(self, msg: MsgReading):
        # Update large VWC label
        vwc_str = f"{msg.vwc:.4f}"
        self._vwc_var.set(vwc_str)
        colour = GREEN if msg.vwc > 0.01 else GRAY
        self._vwc_lbl.config(fg=colour)

        self._raw_amp_var.set(f"{msg.raw_amp:.4f}")
        self._eps_var.set(f"{msg.eps_r:.2f}")
        self._gamma_var.set(f"{msg.gamma:.4f}")
        self._phase_var.set(f"{msg.phase:.3f}")
        self._bin_var.set(str(msg.soil_bin))

        # Write to log file if active, throttled to one row per LOG_FILE_INTERVAL_S
        if self._log_file and (time.time() - self._last_file_log_time) >= LOG_FILE_INTERVAL_S:
            self._last_file_log_time = time.time()
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._log_file.write(
                f"{ts}\t{msg.vwc:.6f}\t{msg.eps_r:.4f}\t{msg.gamma:.6f}\t{msg.phase:.4f}\t{msg.soil_bin}\n"
            )
            self._log_file.flush()

        # Append to plot history
        self._vwc_times.append(msg.timestamp)
        self._vwc_vals.append(msg.vwc)
        self._update_plot()

    def _handle_cal_done(self, amplitudes: list[float]):
        mean_amp = float(np.mean(amplitudes))
        cal_type = self._cal_pending
        self._cal_pending = None
        self._cal_progress_var.set("")

        if cal_type == "metal":
            self._cal_mgr.data.metal_amplitude = mean_amp
            self._log("info", f"Metal reference amplitude = {mean_amp:.1f}")
            messagebox.showinfo(
                "Metal calibration done",
                f"Metal plate amplitude recorded: {mean_amp:.1f}\n\n"
                "Now remove the plate and run step ② (Dry Soil).",
            )

        elif cal_type == "dry":
            self._cal_mgr.data.calibration_points.append((mean_amp, 0.0))
            gamma = mean_amp / max(self._cal_mgr.data.metal_amplitude, 1e-12)
            self._log("info", f"Dry soil amplitude = {mean_amp:.1f}  |Γ| = {gamma:.4f}")
            messagebox.showinfo(
                "Dry soil done",
                f"Dry soil point recorded (|Γ| = {gamma:.4f}).\n\n"
                "Use step ③ to add moist reference points,\n"
                "or skip to step ④ to use the physical model only.",
            )

        elif cal_type == "ref":
            vwc = self._cal_ref_vwc
            self._cal_mgr.data.calibration_points.append((mean_amp, vwc))
            gamma = mean_amp / max(self._cal_mgr.data.metal_amplitude, 1e-12)
            self._log("info",
                      f"Reference point: amplitude={mean_amp:.1f}  |Γ|={gamma:.4f}  VWC={vwc:.3f}")
            messagebox.showinfo(
                "Reference point added",
                f"VWC = {vwc:.3f} m³/m³  →  |Γ| = {gamma:.4f}\n"
                f"Total reference points: {len(self._cal_mgr.data.calibration_points)}",
            )

        self._update_cal_summary()

        # Restore calibration buttons
        state = tk.NORMAL if self._connected else tk.DISABLED
        for attr in ("_cal_metal_btn", "_cal_dry_btn", "_cal_ref_btn",
                     "_cal_fit_btn", "_cal_save_btn"):
            getattr(self, attr).config(state=state)

        cur_state = "measuring" if self._measuring else "connected"
        self._set_state(cur_state)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _update_plot(self):
        times = list(self._vwc_times)
        vals = list(self._vwc_vals)
        if not times:
            return
        t0 = times[0]
        rel = [t - t0 for t in times]
        self._plot_line.set_data(rel, vals)
        self._ax.set_xlim(0, max(rel[-1], 10))
        self._canvas.draw_idle()

    def _update_range_plot(self, linear_mag: list, soil_bin: int):
        self._last_range_profile = linear_mag
        bins = list(range(len(linear_mag)))
        # Rebuild bar chart
        self._ax_range.cla()
        self._ax_range.set_facecolor("#f0f4f8")
        self._ax_range.set_xlabel("Range bin", fontsize=8)
        self._ax_range.set_ylabel("Amplitude", fontsize=8)
        self._ax_range.tick_params(labelsize=7)
        self._ax_range.grid(True, alpha=0.4, linewidth=0.5)
        self._ax_range.bar(bins, linear_mag, color=ACCENT, alpha=0.7, width=0.8)
        self._ax_range.axvline(x=soil_bin, color=RED, linewidth=1.5,
                               linestyle="--", label=f"soil bin {soil_bin}")
        self._ax_range.legend(fontsize=7, loc="upper right")
        if linear_mag:
            self._ax_range.set_ylim(0, max(linear_mag) * 1.15)
        self._canvas.draw_idle()

    def _update_cal_summary(self):
        if not self._cal_mgr:
            self._cal_summary_var.set("No calibration loaded.")
            return
        d = self._cal_mgr.data
        model = "polynomial" if d.poly_coeffs else "physical (Fresnel + Topp)"
        lines = [
            f"Metal amp : {d.metal_amplitude:.1f}",
            f"Ref points: {len(d.calibration_points)}",
            f"Model     : {model}",
        ]
        if d.calibration_points:
            for amp, vwc in d.calibration_points[-3:]:
                gamma = amp / max(d.metal_amplitude, 1e-12)
                lines.append(f"  |Γ|={gamma:.3f}  VWC={vwc:.3f}")
        self._cal_summary_var.set("\n".join(lines))

    def _log(self, level: str, text: str):
        ts = time.strftime("%H:%M:%S")
        self._log_txt.config(state="normal")
        self._log_txt.insert("end", f"{ts}  {text}\n", level)
        self._log_txt.see("end")
        self._log_txt.config(state="disabled")

    def _refresh_ports(self):
        ports = self._list_ports()
        self._cli_cb["values"] = ports
        self._data_cb["values"] = ports
        self._log("info", f"Found ports: {ports or ['none']}")

    @staticmethod
    def _list_ports() -> list[str]:
        return sorted(p.device for p in serial.tools.list_ports.comports())

    @staticmethod
    def _browse(var: tk.StringVar, filetypes: list):
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    root = tk.Tk()
    app = SoilMoistureGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
