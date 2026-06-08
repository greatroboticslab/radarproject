#!/usr/bin/env python3
"""
IWR6843 AOP EVM – Real-time mmWave Radar Visualizer
pip install pyserial numpy matplotlib

COM port assignment (USB):
  Lower COM# → Enhanced COM  → CONFIG port  (115200 baud)
  Higher COM# → Standard COM → DATA port    (921600 baud)
"""

import serial
import serial.tools.list_ports
import numpy as np
import struct
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from collections import deque
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 – registers 3d projection
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.gridspec import GridSpec
from matplotlib.colors import Normalize
import matplotlib.cm as cm

# ── Protocol constants ─────────────────────────────────────────────────────────
MAGIC_WORD  = bytes([2, 1, 4, 3, 6, 5, 8, 7])
CONFIG_BAUD = 115200
DATA_BAUD   = 921600
HEADER_SIZE = 40       # 8-byte magic + 8×uint32

TLV_DETECTED_POINTS = 1
TLV_RANGE_PROFILE   = 2
TLV_NOISE_PROFILE   = 3
TLV_STATISTICS      = 6
TLV_SIDE_INFO       = 7

# ── Default sensor configuration (IWR6843 AOP, 60 GHz, ~100 ms frame) ─────────
DEFAULT_CONFIG = """\
sensorStop
flushCfg
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 1
adcbufCfg -1 0 1 1 1
profileCfg 0 60 347 7 57.14 0 0 70 1 256 5209 0 0 158
chirpCfg 0 0 0 0 0 0 0 1
chirpCfg 1 1 0 0 0 0 0 4
chirpCfg 2 2 0 0 0 0 0 2
frameCfg 0 2 16 0 100 1 0
lowPower 0 0
guiMonitor -1 1 1 0 0 0 1
cfarCfg -1 0 2 8 4 3 0 15 1
cfarCfg -1 1 0 4 2 3 1 15 1
multiObjBeamForming -1 1 0.5
clutterRemoval -1 0
calibDcRangeSig -1 0 -5 8 256
extendedMaxVelocity -1 0
lvdsStreamCfg -1 0 0 0
compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0
measureRangeBiasAndRxChanPhase 0 1.5 0.2
CQRxSatMonitor 0 3 5 121 0
CQSigImgMonitor 0 127 4
analogMonitor 0 0
aoaFovCfg -1 -90 90 -90 90
cfarFovCfg -1 0 0 8.92
cfarFovCfg -1 1 -1 1.00
calibData 0 0 0
sensorStart
"""

# ── Frame parser ───────────────────────────────────────────────────────────────
def parse_frame(data: bytes):
    """Parse one IWR6843 SDK output frame. Returns dict or None."""
    if len(data) < HEADER_SIZE or data[:8] != MAGIC_WORD:
        return None
    try:
        (version, total_len, platform, frame_num, time_cpu,
         num_obj, num_tlvs, sub_frame) = struct.unpack_from('<8I', data, 8)
    except struct.error:
        return None

    result = {
        'frame_num':     frame_num,
        'num_obj':       num_obj,
        'points':        np.empty((0, 4), dtype=np.float32),
        'snr':           np.empty(0, dtype=np.float32),
        'stats':         None,
        'range_profile': None,  # uint16 log-mag per range bin, static-target-capable
    }

    offset = HEADER_SIZE
    for _ in range(num_tlvs):
        if offset + 8 > len(data):
            break
        tlv_type, tlv_len = struct.unpack_from('<II', data, offset)
        offset += 8
        tlv_data = data[offset:offset + tlv_len]
        offset  += tlv_len

        if tlv_type == TLV_RANGE_PROFILE:
            try:
                result['range_profile'] = np.frombuffer(
                    tlv_data, dtype=np.uint16).astype(np.float32)
            except ValueError:
                pass

        elif tlv_type == TLV_DETECTED_POINTS and num_obj > 0:
            try:
                pts = np.frombuffer(tlv_data, dtype=np.float32).reshape(-1, 4)
                result['points'] = pts
            except ValueError:
                pass

        elif tlv_type == TLV_SIDE_INFO and num_obj > 0:
            try:
                side = np.frombuffer(tlv_data, dtype=np.int16).reshape(-1, 2)
                result['snr'] = side[:, 0].astype(np.float32) * 0.1
            except ValueError:
                pass

        elif tlv_type == TLV_STATISTICS and len(tlv_data) >= 24:
            s = struct.unpack_from('<6I', tlv_data)
            result['stats'] = {
                'activeFrameCPULoad':  s[4],
                'interFrameCPULoad':   s[5],
                'interFrameMargin':    s[2],
            }

    return result


def frame_reader(ser: serial.Serial, q: queue.Queue, stop: threading.Event):
    """Background thread – syncs to magic word, reconstructs frames."""
    buf = bytearray()
    while not stop.is_set():
        try:
            chunk = ser.read(ser.in_waiting or 1)
        except serial.SerialException:
            break
        buf.extend(chunk)

        while True:
            idx = buf.find(MAGIC_WORD)
            if idx == -1:
                buf = buf[-7:] if len(buf) > 7 else buf
                break
            if idx > 0:
                buf = buf[idx:]
            if len(buf) < HEADER_SIZE:
                break

            try:
                total_len = struct.unpack_from('<I', buf, 12)[0]
            except struct.error:
                buf = buf[1:]
                continue

            if not (HEADER_SIZE <= total_len <= 65536):
                buf = buf[1:]
                continue
            if len(buf) < total_len:
                break

            frame_bytes = bytes(buf[:total_len])
            buf = buf[total_len:]
            parsed = parse_frame(frame_bytes)
            if parsed:
                try:
                    q.put_nowait(parsed)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(parsed)
                    except queue.Empty:
                        pass


# ── GUI ────────────────────────────────────────────────────────────────────────
BG       = '#1e1e2e'
PANEL    = '#181825'
SURFACE  = '#313244'
TEXT     = '#cdd6f4'
ACCENT   = '#89b4fa'
GREEN    = '#a6e3a1'
RED      = '#f38ba8'
YELLOW   = '#f9e2af'
ORANGE   = '#fab387'
SUBTEXT  = '#585b70'


class RadarApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IWR6843 AOP – Real-time Radar Visualizer")
        self.geometry("1280x820")
        self.configure(bg=BG)

        self.ser_cfg  = None
        self.ser_data = None
        self.stop_evt = threading.Event()
        self.frame_q  = queue.Queue(maxsize=5)
        self.reader_thread = None
        self.anim          = None
        self.point_history = []
        self.total_frames  = 0

        self.smooth_dist      = None
        self.no_detect_frames = 0
        self.dist_history     = deque(maxlen=9)
        self.range_res_m      = 0.0427  # updated when config is parsed

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Layout ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',               background=BG)
        style.configure('TLabel',               background=BG, foreground=TEXT)
        style.configure('TButton',              background=SURFACE, foreground=TEXT, borderwidth=0)
        style.map('TButton',                    background=[('active', ACCENT), ('pressed', ACCENT)])
        style.configure('TCombobox',            fieldbackground=SURFACE, foreground=TEXT,
                                                selectbackground=ACCENT)
        style.configure('TLabelframe',          background=BG,    bordercolor=SURFACE)
        style.configure('TLabelframe.Label',    background=BG,    foreground=ACCENT)
        style.configure('Horizontal.TScale',    background=BG,    troughcolor=SURFACE)

        # ── Top bar ────────────────────────────────────────────────────────────
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ports = [p.device for p in serial.tools.list_ports.comports()]

        ttk.Label(top, text="Config COM:").pack(side=tk.LEFT, padx=(0, 2))
        self.cfg_port_var = tk.StringVar(value=ports[0] if ports else '')
        self.cfg_combo = ttk.Combobox(top, textvariable=self.cfg_port_var,
                                      values=ports, width=9)
        self.cfg_combo.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(top, text="Data COM:").pack(side=tk.LEFT, padx=(0, 2))
        self.data_port_var = tk.StringVar(value=ports[1] if len(ports) > 1 else (ports[0] if ports else ''))
        self.data_combo = ttk.Combobox(top, textvariable=self.data_port_var,
                                       values=ports, width=9)
        self.data_combo.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(top, text="Refresh", command=self._refresh_ports, width=8).pack(side=tk.LEFT, padx=4)

        self.connect_btn = ttk.Button(top, text="Connect & Start",
                                      command=self._toggle_connection, width=14)
        self.connect_btn.pack(side=tk.LEFT, padx=8)

        self.status_lbl = ttk.Label(top, text="● Disconnected", foreground=RED)
        self.status_lbl.pack(side=tk.LEFT, padx=8)

        # Stats on the right
        self.frame_lbl = ttk.Label(top, text="Frame: —")
        self.frame_lbl.pack(side=tk.RIGHT, padx=8)
        self.pts_lbl = ttk.Label(top, text="Points: —")
        self.pts_lbl.pack(side=tk.RIGHT, padx=8)
        self.cpu_lbl = ttk.Label(top, text="CPU: —")
        self.cpu_lbl.pack(side=tk.RIGHT, padx=8)

        # ── Main pane ──────────────────────────────────────────────────────────
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=BG,
                              sashrelief=tk.FLAT, sashwidth=5)
        pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Plot area
        plot_frame = ttk.Frame(pane)
        pane.add(plot_frame, width=920)

        self.fig = plt.figure(facecolor=BG, figsize=(12, 7))
        gs = GridSpec(2, 2, figure=self.fig, wspace=0.38, hspace=0.40,
                      left=0.07, right=0.96, top=0.95, bottom=0.07)

        self.ax3d = self.fig.add_subplot(gs[:, 0], projection='3d')
        self.ax_xy = self.fig.add_subplot(gs[0, 1])
        self.ax_xz = self.fig.add_subplot(gs[1, 1])

        self._init_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Right panel: config + log
        right = ttk.Frame(pane)
        pane.add(right, width=330)

        # History slider
        hist_row = ttk.Frame(right)
        hist_row.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(hist_row, text="Trail frames:").pack(side=tk.LEFT)
        self.hist_var = tk.IntVar(value=5)
        hist_slider = ttk.Scale(hist_row, from_=1, to=20, orient=tk.HORIZONTAL,
                                variable=self.hist_var, length=120)
        hist_slider.pack(side=tk.LEFT, padx=6)
        self.hist_lbl = ttk.Label(hist_row, text="5")
        self.hist_lbl.pack(side=tk.LEFT)
        self.hist_var.trace_add('write', lambda *_: self.hist_lbl.config(
            text=str(self.hist_var.get())))

        # ── Distance meter panel ───────────────────────────────────────────────
        dist_lf = ttk.LabelFrame(right, text=" Hand Distance ")
        dist_lf.pack(fill=tk.X, padx=6, pady=(0, 4))

        self.dist_lbl = tk.Label(
            dist_lf, text="— cm",
            font=('Segoe UI', 38, 'bold'), bg=BG, fg=TEXT)
        self.dist_lbl.pack(pady=(6, 0))

        self.dist_status = tk.Label(
            dist_lf, text="No detection",
            font=('Segoe UI', 9), bg=BG, fg=SUBTEXT)
        self.dist_status.pack(pady=(0, 4))

        self.dist_gauge = tk.Canvas(dist_lf, height=22, bg=BG, highlightthickness=0)
        self.dist_gauge.pack(fill=tk.X, padx=10)

        tick_f = tk.Frame(dist_lf, bg=BG)
        tick_f.pack(fill=tk.X, padx=10, pady=(1, 2))
        for t in ['0', '25 cm', '50 cm', '75 cm', '100 cm']:
            tk.Label(tick_f, text=t, font=('Segoe UI', 7),
                     bg=BG, fg=SUBTEXT).pack(side=tk.LEFT, expand=True)

        ctrl_row = tk.Frame(dist_lf, bg=BG)
        ctrl_row.pack(fill=tk.X, padx=10, pady=(0, 2))

        def _make_row(parent, label, var, from_, to, length, fmt, default_text, width=3):
            tk.Label(parent, text=label, font=('Segoe UI', 8),
                     bg=BG, fg=SUBTEXT).pack(side=tk.LEFT)
            ttk.Scale(parent, from_=from_, to=to, orient=tk.HORIZONTAL,
                      variable=var, length=length).pack(side=tk.LEFT, padx=2)
            lbl = tk.Label(parent, text=default_text, font=('Segoe UI', 8),
                           bg=BG, fg=SUBTEXT, width=width)
            lbl.pack(side=tk.LEFT, padx=(0, 8))
            return lbl

        # Smooth — temporal median window
        self.smooth_var = tk.IntVar(value=7)
        self.smooth_val_lbl = _make_row(ctrl_row, "Smooth:", self.smooth_var,
                                        1, 15, 70, 'd', '7', width=2)
        self.smooth_var.trace_add('write', lambda *_: (
            self.smooth_val_lbl.config(text=str(self.smooth_var.get())),
            self.dist_history.clear()))

        # Sensitivity — N-sigma threshold above noise
        self.sens_var = tk.DoubleVar(value=2.5)
        self.sens_val_lbl = _make_row(ctrl_row, "Sens:", self.sens_var,
                                      1.0, 6.0, 70, '.1f', '2.5', width=3)
        self.sens_var.trace_add('write', lambda *_:
            self.sens_val_lbl.config(text=f"{self.sens_var.get():.1f}"))

        # Width — minimum consecutive bins required (filters out single-bin spikes)
        self.width_var = tk.IntVar(value=2)
        self.width_val_lbl = _make_row(ctrl_row, "Width:", self.width_var,
                                       1, 8, 70, 'd', '2', width=2)
        self.width_var.trace_add('write', lambda *_:
            self.width_val_lbl.config(text=str(self.width_var.get())))

        # Config editor
        cfg_lf = ttk.LabelFrame(right, text=" Sensor Configuration ")
        cfg_lf.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.cfg_text = scrolledtext.ScrolledText(
            cfg_lf, width=36, height=24,
            bg=PANEL, fg=TEXT, insertbackground='white',
            font=('Consolas', 9), wrap=tk.NONE,
            selectbackground=ACCENT)
        self.cfg_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.cfg_text.insert(tk.END, DEFAULT_CONFIG.strip())

        ttk.Button(cfg_lf, text="Send Config Now", command=self._send_config_thread).pack(pady=4)

        # Log
        log_lf = ttk.LabelFrame(right, text=" Log ")
        log_lf.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.log = scrolledtext.ScrolledText(
            log_lf, width=36, height=7,
            bg=PANEL, fg=GREEN, insertbackground='white',
            font=('Consolas', 8), state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, padx=2, pady=2)

    def _init_axes(self):
        # 3-D
        ax = self.ax3d
        ax.set_facecolor(PANEL)
        ax.set_title('3-D Point Cloud', color=ACCENT, pad=4, fontsize=10)
        ax.set_xlabel('X (m)', color=TEXT, fontsize=8, labelpad=2)
        ax.set_ylabel('Y (m)', color=TEXT, fontsize=8, labelpad=2)
        ax.set_zlabel('Z (m)', color=TEXT, fontsize=8, labelpad=2)
        ax.tick_params(colors='#585b70', labelsize=7)
        for pane_obj in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane_obj.fill = False
            pane_obj.set_edgecolor(SURFACE)
        ax.grid(True, color=SURFACE, linewidth=0.4)

        # X-Y
        for ax2, title, xl, yl in [
            (self.ax_xy, 'Top-Down (X–Y)',  'X (m)', 'Y (m)'),
            (self.ax_xz, 'Front View (X–Z)', 'X (m)', 'Z (m)'),
        ]:
            ax2.set_facecolor(PANEL)
            ax2.set_title(title, color=ACCENT, pad=3, fontsize=9)
            ax2.set_xlabel(xl, color=TEXT, fontsize=8)
            ax2.set_ylabel(yl, color=TEXT, fontsize=8)
            ax2.tick_params(colors='#585b70', labelsize=7)
            ax2.grid(True, color=SURFACE, linewidth=0.4)
            for spine in ax2.spines.values():
                spine.set_edgecolor(SURFACE)

    # ── Range resolution ───────────────────────────────────────────────────────
    def _compute_range_res(self):
        """Parse profileCfg from the config editor and compute range_res_m."""
        for line in self.cfg_text.get("1.0", tk.END).splitlines():
            parts = line.strip().split()
            if parts and parts[0] == 'profileCfg' and len(parts) >= 12:
                try:
                    adc_start   = float(parts[4])   # µs
                    ramp_end    = float(parts[5])    # µs
                    slope       = float(parts[8])    # MHz/µs
                    n_samples   = int(parts[10])
                    sample_rate = float(parts[11])   # ksps
                    useful_us   = min(ramp_end - adc_start,
                                     n_samples / sample_rate * 1000)
                    # slope [MHz/µs] × useful_us [µs] = bandwidth [MHz] × 1e6 = [Hz]
                    bw_hz = slope * useful_us * 1e6
                    self.range_res_m = 3e8 / (2 * bw_hz)
                    self._log(f"Range resolution: {self.range_res_m*100:.2f} cm  "
                              f"| Max range: {self.range_res_m * n_samples * 100:.0f} cm")
                    return
                except (IndexError, ValueError):
                    pass
        self.range_res_m = 0.0427
        self._log("profileCfg not found — using default range res 4.27 cm")

    # ── Distance helpers ───────────────────────────────────────────────────────
    def _dist_color(self, cm):
        if cm < 25: return RED
        if cm < 50: return ORANGE
        if cm < 75: return YELLOW
        return GREEN

    def _draw_gauge(self, dist_cm):
        c = self.dist_gauge
        c.update_idletasks()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 2:
            return
        c.delete('all')
        r = 7
        c.create_rectangle(r, h//2-5, w-r, h//2+5, fill=SURFACE, outline='')
        c.create_oval(0,     h//2-5, r*2,   h//2+5, fill=SURFACE, outline='')
        c.create_oval(w-r*2, h//2-5, w,     h//2+5, fill=SURFACE, outline='')
        if dist_cm is None:
            return
        fill_x = int((dist_cm / 100.0) * (w - r * 2)) + r
        fill_x = max(r, min(fill_x, w - r))
        color  = self._dist_color(dist_cm)
        c.create_rectangle(r, h//2-5, fill_x, h//2+5, fill=color, outline='')
        c.create_oval(0, h//2-5, r*2, h//2+5, fill=color, outline='')
        c.create_oval(fill_x-8, h//2-8, fill_x+8, h//2+8,
                      fill=color, outline=BG, width=2)

    # ── Logging ────────────────────────────────────────────────────────────────
    def _log(self, msg, color=None):
        def _do():
            self.log.config(state=tk.NORMAL)
            self.log.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.log.see(tk.END)
            self.log.config(state=tk.DISABLED)
        self.after(0, _do)

    # ── Ports ──────────────────────────────────────────────────────────────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cfg_combo['values']  = ports
        self.data_combo['values'] = ports
        self._log(f"Found: {ports}")

    # ── Connection ─────────────────────────────────────────────────────────────
    def _toggle_connection(self):
        if self.ser_data and self.ser_data.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        cfg_p  = self.cfg_port_var.get().strip()
        data_p = self.data_port_var.get().strip()
        if not cfg_p or not data_p:
            messagebox.showerror("Error", "Select both COM ports.")
            return
        try:
            self.ser_cfg  = serial.Serial(cfg_p,  CONFIG_BAUD, timeout=1)
            self.ser_data = serial.Serial(data_p, DATA_BAUD,   timeout=0.1)
        except serial.SerialException as e:
            messagebox.showerror("Serial Error", str(e))
            return

        self._log(f"Opened {cfg_p} (config) + {data_p} (data)")
        self.connect_btn.config(text="Disconnect")
        self.status_lbl.config(text="● Connected", foreground=GREEN)

        self._send_config_thread()
        self._start_reader()
        self._start_animation()

    def _disconnect(self):
        self.stop_evt.set()
        if self.anim:
            self.anim.event_source.stop()
        if self.reader_thread:
            self.reader_thread.join(timeout=2)
        for s in (self.ser_cfg, self.ser_data):
            try:
                if s and s.is_open:
                    s.close()
            except Exception:
                pass
        self.ser_cfg = self.ser_data = None
        self.stop_evt.clear()
        self.point_history.clear()
        self.smooth_dist = None
        self.no_detect_frames = 0
        self.dist_history.clear()
        self.connect_btn.config(text="Connect & Start")
        self.status_lbl.config(text="● Disconnected", foreground=RED)
        self.dist_lbl.config(text="— cm", fg=TEXT)
        self.dist_status.config(text="Disconnected", fg=SUBTEXT)
        self._log("Disconnected.")

    # ── Config ─────────────────────────────────────────────────────────────────
    def _send_config_thread(self):
        t = threading.Thread(target=self._send_config, daemon=True)
        t.start()

    def _send_config(self):
        if not (self.ser_cfg and self.ser_cfg.is_open):
            self._log("Config port not open – connect first.")
            return
        lines = [l.strip() for l in self.cfg_text.get("1.0", tk.END).splitlines()
                 if l.strip() and not l.startswith('%')]
        self._log(f"Sending {len(lines)} config commands…")
        for line in lines:
            try:
                self.ser_cfg.write((line + '\n').encode())
                time.sleep(0.025)
                resp = self.ser_cfg.read_all().decode(errors='ignore').strip()
                if resp:
                    self._log(f"  → {resp[:80]}")
            except serial.SerialException as e:
                self._log(f"ERROR: {e}")
                return
        self._log("Config complete.")
        self._compute_range_res()

    # ── Reader thread ──────────────────────────────────────────────────────────
    def _start_reader(self):
        self.stop_evt.clear()
        self.reader_thread = threading.Thread(
            target=frame_reader,
            args=(self.ser_data, self.frame_q, self.stop_evt),
            daemon=True)
        self.reader_thread.start()

    # ── Animation ──────────────────────────────────────────────────────────────
    def _start_animation(self):
        self.anim = animation.FuncAnimation(
            self.fig, self._update_plots,
            interval=80, blit=False, cache_frame_data=False)
        self.canvas.draw()

    def _update_plots(self, _):
        # Drain queue – keep only latest
        latest = None
        while True:
            try:
                latest = self.frame_q.get_nowait()
            except queue.Empty:
                break

        if latest is None:
            return

        self.total_frames += 1
        pts = latest['points']

        # Rolling history
        max_hist = self.hist_var.get()
        self.point_history.append(pts)
        while len(self.point_history) > max_hist:
            self.point_history.pop(0)

        if self.point_history:
            all_pts = np.vstack(self.point_history)
        else:
            all_pts = np.empty((0, 4), dtype=np.float32)

        # Update header labels
        self.frame_lbl.config(text=f"Frame: {latest['frame_num']}")
        self.pts_lbl.config(text=f"Points: {len(pts)}")
        if latest['stats']:
            s = latest['stats']
            self.cpu_lbl.config(
                text=f"CPU active/idle: {s['activeFrameCPULoad']}% / {s['interFrameCPULoad']}%")

        # ── Distance meter — uses range profile (detects static targets) ─────────
        profile = latest.get('range_profile')
        frame_cm = None

        if profile is not None and len(profile) > 4:
            skip      = 3                      # skip DC/near-field coupling zone
            p         = profile[skip:]
            noise     = np.mean(p)
            sigma     = np.std(p)
            thresh    = noise + sigma * self.sens_var.get()
            min_width = self.width_var.get()   # consecutive bins required

            # Walk bins looking for a run of min_width consecutive hits.
            # Real objects (hand, wall) reflect across multiple adjacent bins.
            # Single-bin spikes (noise, DC artifact) are rejected.
            run_start = None
            run_len   = 0
            for i, val in enumerate(p):
                if val > thresh:
                    if run_start is None:
                        run_start = i
                    run_len += 1
                    if run_len >= min_width:
                        frame_cm = (run_start + skip) * self.range_res_m * 100
                        break
                else:
                    run_start = None
                    run_len   = 0

        if frame_cm is None:
            self.no_detect_frames += 1
            if self.no_detect_frames >= 4:
                self.smooth_dist = None
                self.dist_history.clear()
                self.dist_lbl.config(text="— cm", fg=TEXT)
                self.dist_status.config(text="No detection", fg=SUBTEXT)
                self._draw_gauge(None)
        else:
            self.no_detect_frames = 0
            window = self.smooth_var.get()
            if self.dist_history.maxlen != window:
                self.dist_history = deque(self.dist_history, maxlen=window)
            self.dist_history.append(frame_cm)
            self.smooth_dist = float(np.median(self.dist_history))

            display_cm = min(self.smooth_dist, 100.0)
            color = self._dist_color(display_cm)
            if self.smooth_dist < 25:     label = "Very close"
            elif self.smooth_dist < 50:   label = "Close"
            elif self.smooth_dist < 75:   label = "Medium range"
            elif self.smooth_dist <= 100: label = "Far"
            else:                         label = f"{self.smooth_dist:.0f} cm"
            self.dist_lbl.config(text=f"{self.smooth_dist:.1f} cm", fg=color)
            self.dist_status.config(text=label, fg=color)
            self._draw_gauge(display_cm)

        n = len(all_pts)
        cmap = plt.get_cmap('coolwarm')
        norm = Normalize(vmin=-2, vmax=2)

        if n:
            x, y, z, dop = all_pts[:, 0], all_pts[:, 1], all_pts[:, 2], all_pts[:, 3]
            colors = cmap(norm(dop))
            sizes  = np.full(n, 18)
        else:
            x = y = z = dop = colors = np.empty(0)
            sizes = np.empty(0)

        # ── 3-D ───────────────────────────────────────────────────────────────
        self.ax3d.cla()
        self._init_axes()
        if n:
            self.ax3d.scatter(x, y, z, c=colors, s=sizes, alpha=0.85, depthshade=True)
        self.ax3d.set_xlim(-6, 6)
        self.ax3d.set_ylim(0, 9)
        self.ax3d.set_zlim(-2, 3)

        # ── X-Y top-down ──────────────────────────────────────────────────────
        self.ax_xy.cla()
        self.ax_xy.set_facecolor(PANEL)
        self.ax_xy.set_title('Top-Down (X–Y)', color=ACCENT, pad=3, fontsize=9)
        self.ax_xy.set_xlabel('X (m)', color=TEXT, fontsize=8)
        self.ax_xy.set_ylabel('Y (m)', color=TEXT, fontsize=8)
        self.ax_xy.tick_params(colors='#585b70', labelsize=7)
        self.ax_xy.grid(True, color=SURFACE, linewidth=0.4)
        self.ax_xy.set_xlim(-6, 6)
        self.ax_xy.set_ylim(0, 9)
        if n:
            self.ax_xy.scatter(x, y, c=colors, s=20, alpha=0.85)
        self.ax_xy.plot(0, 0, marker='^', color=YELLOW, markersize=9, label='Sensor')
        self.ax_xy.legend(fontsize=7, facecolor=SURFACE, labelcolor=TEXT, framealpha=0.7)

        # ── X-Z front ─────────────────────────────────────────────────────────
        self.ax_xz.cla()
        self.ax_xz.set_facecolor(PANEL)
        self.ax_xz.set_title('Front View (X–Z)', color=ACCENT, pad=3, fontsize=9)
        self.ax_xz.set_xlabel('X (m)', color=TEXT, fontsize=8)
        self.ax_xz.set_ylabel('Z (m)', color=TEXT, fontsize=8)
        self.ax_xz.tick_params(colors='#585b70', labelsize=7)
        self.ax_xz.grid(True, color=SURFACE, linewidth=0.4)
        self.ax_xz.set_xlim(-6, 6)
        self.ax_xz.set_ylim(-2, 3)
        if n:
            self.ax_xz.scatter(x, z, c=colors, s=20, alpha=0.85)
        self.ax_xz.axhline(0, color=YELLOW, linewidth=0.8, linestyle='--', alpha=0.5)

        # Color bar label (drawn once per update – cheap with cla)
        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])

        self.fig.canvas.draw_idle()

    # ── Close ──────────────────────────────────────────────────────────────────
    def _on_close(self):
        self._disconnect()
        self.destroy()


if __name__ == '__main__':
    app = RadarApp()
    app.mainloop()
