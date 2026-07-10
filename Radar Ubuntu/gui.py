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

        if not complex_mode:
            log.info("DIAG_RAW bin=%d raw_uint16=%s", soil_bin, profile[lo:hi + 1].tolist())

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
        self.root.rowconfigure(2, weight=1)

        # Runtime state
        self._thread: Optional[SensorThread] = None
        self._queue: queue.Queue = queue.Queue()
        self._cal_mgr: Optional[CalibrationManager] = None
        self._cal_pending: Optional[str] = None   # 'metal' | 'dry' | 'ref'
        self._cal_ref_vwc: float = 0.0
        self._connected = False
        self._measuring = False

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
        self._build_connection_panel(row=1)

        mid = ttk.Frame(self.root)
        mid.grid(row=2, column=0, sticky="nsew", padx=6, pady=4)
        mid.columnconfigure(0, weight=0, minsize=270)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        self._build_calibration_panel(mid)
        self._build_right_panel(mid)
        self._build_log_panel(row=3)

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
        self._disconnect_btn.pack(side="left")

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

        self._log_txt = scrolledtext.ScrolledText(
            frm, height=5, font=FONT_MONO, state="disabled",
            wrap="word", background="#1e1e1e", foreground="#d4d4d4",
        )
        self._log_txt.grid(row=0, column=0, sticky="ew")
        self._log_txt.tag_config("info",    foreground="#9cdcfe")
        self._log_txt.tag_config("warning", foreground="#dcdcaa")
        self._log_txt.tag_config("error",   foreground="#f48771")

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

        # Write to log file if active
        if self._log_file:
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
