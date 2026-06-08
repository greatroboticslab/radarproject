#!/usr/bin/env python3
"""
IWR6843 AOP EVM - Hand Distance Meter
Shows real-time distance from sensor to hand, up to 1 meter.
pip install pyserial numpy matplotlib
Run as Administrator.
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
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque

# ── Protocol ───────────────────────────────────────────────────────────────────
MAGIC_WORD  = bytes([2, 1, 4, 3, 6, 5, 8, 7])
CONFIG_BAUD = 115200
DATA_BAUD   = 921600
HEADER_SIZE = 40
TLV_DETECTED_POINTS = 1

# ── Sensor config tuned for hand ≤1 m ─────────────────────────────────────────
HAND_CONFIG = """\
sensorStop
flushCfg
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 1
adcbufCfg -1 0 1 1 1
profileCfg 0 60 100 5 30 0 0 99 1 64 6000 0 0 158
chirpCfg 0 0 0 0 0 0 0 1
chirpCfg 1 1 0 0 0 0 0 4
chirpCfg 2 2 0 0 0 0 0 2
frameCfg 0 2 16 0 50 1 0
lowPower 0 0
guiMonitor -1 1 1 0 0 0 1
cfarCfg -1 0 2 8 4 3 0 10 1
cfarCfg -1 1 0 4 2 3 1 10 1
multiObjBeamForming -1 1 0.3
clutterRemoval -1 0
calibDcRangeSig -1 0 -5 8 256
extendedMaxVelocity -1 0
lvdsStreamCfg -1 0 0 0
compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0
measureRangeBiasAndRxChanPhase 0 1.5 0.2
CQRxSatMonitor 0 3 5 121 0
CQSigImgMonitor 0 127 4
analogMonitor 0 0
aoaFovCfg -1 -60 60 -60 60
cfarFovCfg -1 0 0 1.0
cfarFovCfg -1 1 -3.0 3.0
calibData 0 0 0
sensorStart
"""

# ── Colors (Catppuccin Mocha) ──────────────────────────────────────────────────
BG      = '#1e1e2e'
PANEL   = '#181825'
SURFACE = '#313244'
TEXT    = '#cdd6f4'
ACCENT  = '#89b4fa'
GREEN   = '#a6e3a1'
YELLOW  = '#f9e2af'
ORANGE  = '#fab387'
RED     = '#f38ba8'
SUBTEXT = '#585b70'

HISTORY_LEN = 150  # ~7.5 seconds at 20 fps

# ── Frame parsing ──────────────────────────────────────────────────────────────
def parse_frame(data: bytes):
    if len(data) < HEADER_SIZE or data[:8] != MAGIC_WORD:
        return None
    try:
        (_, _, _, _, _, num_obj, num_tlvs, _) = struct.unpack_from('<8I', data, 8)
    except struct.error:
        return None

    points = np.empty((0, 4), dtype=np.float32)
    offset = HEADER_SIZE
    for _ in range(num_tlvs):
        if offset + 8 > len(data):
            break
        tlv_type, tlv_len = struct.unpack_from('<II', data, offset)
        offset += 8
        tlv_data = data[offset:offset + tlv_len]
        offset += tlv_len
        if tlv_type == TLV_DETECTED_POINTS and num_obj > 0:
            try:
                points = np.frombuffer(tlv_data, dtype=np.float32).reshape(-1, 4)
            except ValueError:
                pass
    return {'points': points}


def frame_reader(ser: serial.Serial, q: queue.Queue, stop: threading.Event):
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
            parsed = parse_frame(bytes(buf[:total_len]))
            buf = buf[total_len:]
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
def dist_color(cm):
    if cm < 25:   return RED
    if cm < 50:   return ORANGE
    if cm < 75:   return YELLOW
    return GREEN


class DistanceMeter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IWR6843 – Hand Distance Meter")
        self.geometry("680x620")
        self.configure(bg=BG)
        self.resizable(True, True)

        self.smooth_dist = None
        self.alpha = 0.35          # EMA factor — raise for faster response, lower for smoother
        self.history = deque([None] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self.no_detect_frames = 0

        self.ser_cfg  = None
        self.ser_data = None
        self.stop_evt = threading.Event()
        self.frame_q  = queue.Queue(maxsize=5)
        self.reader_thread = None
        self.anim = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Layout ─────────────────────────────────────────────────────────────────
    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',               background=BG)
        style.configure('TLabel',               background=BG, foreground=TEXT)
        style.configure('TButton',              background=SURFACE, foreground=TEXT, borderwidth=0)
        style.map('TButton',                    background=[('active', ACCENT)])
        style.configure('TCombobox',            fieldbackground=SURFACE, foreground=TEXT)
        style.configure('TLabelframe',          background=BG, bordercolor=SURFACE)
        style.configure('TLabelframe.Label',    background=BG, foreground=ACCENT)

        # ── Connection bar ─────────────────────────────────────────────────────
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=10, pady=8)

        ports = [p.device for p in serial.tools.list_ports.comports()]

        ttk.Label(top, text="Config COM:").pack(side=tk.LEFT)
        self.cfg_var = tk.StringVar(value=ports[0] if ports else '')
        self.cfg_combo = ttk.Combobox(top, textvariable=self.cfg_var, values=ports, width=9)
        self.cfg_combo.pack(side=tk.LEFT, padx=(2, 10))

        ttk.Label(top, text="Data COM:").pack(side=tk.LEFT)
        self.data_var = tk.StringVar(
            value=ports[1] if len(ports) > 1 else (ports[0] if ports else ''))
        self.data_combo = ttk.Combobox(top, textvariable=self.data_var, values=ports, width=9)
        self.data_combo.pack(side=tk.LEFT, padx=(2, 10))

        ttk.Button(top, text="Refresh", command=self._refresh_ports, width=8).pack(side=tk.LEFT, padx=4)

        self.connect_btn = ttk.Button(top, text="Connect", command=self._toggle, width=10)
        self.connect_btn.pack(side=tk.LEFT, padx=8)

        self.status_lbl = ttk.Label(top, text="● Disconnected", foreground=RED)
        self.status_lbl.pack(side=tk.LEFT, padx=8)

        # ── Big distance display ───────────────────────────────────────────────
        disp = tk.Frame(self, bg=PANEL)
        disp.pack(fill=tk.X, padx=10, pady=(0, 6))

        self.dist_lbl = tk.Label(
            disp, text="— cm",
            font=('Segoe UI', 80, 'bold'),
            bg=PANEL, fg=TEXT, width=7, anchor='center')
        self.dist_lbl.pack(pady=(20, 2))

        self.status_txt = tk.Label(
            disp, text="Point sensor at your hand and connect",
            font=('Segoe UI', 13), bg=PANEL, fg=SUBTEXT)
        self.status_txt.pack(pady=(0, 10))

        self.pts_lbl = tk.Label(
            disp, text="",
            font=('Segoe UI', 10), bg=PANEL, fg=SUBTEXT)
        self.pts_lbl.pack(pady=(0, 8))

        # Gauge bar
        self.gauge = tk.Canvas(disp, height=36, bg=PANEL, highlightthickness=0)
        self.gauge.pack(fill=tk.X, padx=30)

        # Tick labels
        ticks = tk.Frame(disp, bg=PANEL)
        ticks.pack(fill=tk.X, padx=30, pady=(2, 16))
        for lbl in ['0', '25 cm', '50 cm', '75 cm', '100 cm']:
            tk.Label(ticks, text=lbl, font=('Segoe UI', 8),
                     bg=PANEL, fg=SUBTEXT).pack(side=tk.LEFT, expand=True)

        # ── History chart ──────────────────────────────────────────────────────
        self.fig, self.ax = plt.subplots(figsize=(6.6, 2.4), facecolor=BG)
        self.fig.subplots_adjust(left=0.09, right=0.98, top=0.85, bottom=0.18)
        self._style_chart()

        self.hist_line, = self.ax.plot([], [], color=ACCENT, linewidth=2)
        self.hist_fill  = None

        chart_lf = ttk.LabelFrame(self, text=" Distance History (last ~7 sec) ")
        chart_lf.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_lf)
        self.canvas.get_tk_widget().pack(fill=tk.X, padx=2, pady=2)

        # ── Log ───────────────────────────────────────────────────────────────
        log_lf = ttk.LabelFrame(self, text=" Log ")
        log_lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        self.log = scrolledtext.ScrolledText(
            log_lf, height=4, bg=PANEL, fg=GREEN,
            font=('Consolas', 8), state=tk.DISABLED,
            insertbackground='white')
        self.log.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _style_chart(self):
        ax = self.ax
        ax.set_facecolor(PANEL)
        ax.set_title('Distance over Time', color=ACCENT, fontsize=9, pad=3)
        ax.set_ylabel('cm', color=TEXT, fontsize=8)
        ax.set_xlim(0, HISTORY_LEN)
        ax.set_ylim(0, 110)
        ax.tick_params(colors=SUBTEXT, labelsize=7)
        ax.set_xticks([])
        ax.axhline(100, color=SURFACE, linewidth=0.8, linestyle='--', alpha=0.6)
        ax.text(HISTORY_LEN - 2, 103, '100 cm', color=SUBTEXT,
                fontsize=7, ha='right', va='bottom')
        ax.grid(True, color=SURFACE, linewidth=0.4, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_edgecolor(SURFACE)

    # ── Logging ────────────────────────────────────────────────────────────────
    def _log(self, msg):
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
    def _toggle(self):
        if self.ser_data and self.ser_data.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        cfg_p  = self.cfg_var.get().strip()
        data_p = self.data_var.get().strip()
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
        threading.Thread(target=self._send_config, daemon=True).start()

        self.stop_evt.clear()
        self.reader_thread = threading.Thread(
            target=frame_reader,
            args=(self.ser_data, self.frame_q, self.stop_evt),
            daemon=True)
        self.reader_thread.start()

        self.anim = FuncAnimation(
            self.fig, self._update,
            interval=80, blit=False, cache_frame_data=False)
        self.canvas.draw()

        self.connect_btn.config(text="Disconnect")
        self.status_lbl.config(text="● Connected", foreground=GREEN)

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
        self.smooth_dist = None
        self.history = deque([None] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self.connect_btn.config(text="Connect")
        self.status_lbl.config(text="● Disconnected", foreground=RED)
        self.dist_lbl.config(text="— cm", fg=TEXT)
        self.status_txt.config(text="Disconnected", fg=SUBTEXT)
        self.pts_lbl.config(text="")
        self._log("Disconnected.")

    def _send_config(self):
        if not (self.ser_cfg and self.ser_cfg.is_open):
            return
        lines = [l.strip() for l in HAND_CONFIG.splitlines()
                 if l.strip() and not l.startswith('%')]
        self._log(f"Sending {len(lines)} config lines…")
        for line in lines:
            try:
                self.ser_cfg.write((line + '\n').encode())
                time.sleep(0.025)
            except serial.SerialException as e:
                self._log(f"Config error: {e}")
                return
        self._log("Config sent. Ready — point sensor at your hand.")

    # ── Animation update ───────────────────────────────────────────────────────
    def _update(self, _):
        # Drain queue, keep latest frame only
        latest = None
        while True:
            try:
                latest = self.frame_q.get_nowait()
            except queue.Empty:
                break

        if latest is None:
            return

        pts = latest['points']

        if len(pts) == 0:
            self.no_detect_frames += 1
            if self.no_detect_frames >= 4:
                self.smooth_dist = None
                self.dist_lbl.config(text="— cm", fg=TEXT)
                self.status_txt.config(text="No hand detected", fg=SUBTEXT)
                self.pts_lbl.config(text="")
                self.history.append(None)
                self._draw_gauge(None)
                self._draw_chart()
            return

        self.no_detect_frames = 0

        # Minimum Y (range depth) across all points — Y is the direct FMCW
        # measurement and gives the closest object rather than an average
        # pulled by far-away detections.
        y_vals = pts[:, 1]
        y_vals = y_vals[y_vals > 0]
        if len(y_vals) == 0:
            return
        raw_cm = float(np.min(y_vals)) * 100
        raw_cm = min(max(raw_cm, 0.0), 100.0)

        # Exponential moving average
        if self.smooth_dist is None:
            self.smooth_dist = raw_cm
        else:
            self.smooth_dist = self.alpha * raw_cm + (1 - self.alpha) * self.smooth_dist

        self.history.append(self.smooth_dist)

        # ── Labels ────────────────────────────────────────────────────────────
        color = dist_color(self.smooth_dist)

        if self.smooth_dist < 25:
            status = "Very close"
        elif self.smooth_dist < 50:
            status = "Close"
        elif self.smooth_dist < 75:
            status = "Medium range"
        else:
            status = "Far"

        self.dist_lbl.config(text=f"{self.smooth_dist:.1f} cm", fg=color)
        self.status_txt.config(text=status, fg=color)
        self.pts_lbl.config(
            text=f"{len(pts)} point{'s' if len(pts) != 1 else ''} detected  |  "
                 f"raw {raw_cm:.1f} cm")

        self._draw_gauge(self.smooth_dist)
        self._draw_chart()
        self.fig.canvas.draw_idle()

    def _draw_gauge(self, dist_cm):
        c = self.gauge
        c.update_idletasks()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 2:
            return
        c.delete('all')
        r = 10

        # Track
        c.create_rectangle(r, h // 2 - 8, w - r, h // 2 + 8,
                            fill=SURFACE, outline='')
        c.create_oval(0, h // 2 - 8, r * 2, h // 2 + 8,
                      fill=SURFACE, outline='')
        c.create_oval(w - r * 2, h // 2 - 8, w, h // 2 + 8,
                      fill=SURFACE, outline='')

        if dist_cm is None:
            return

        fill_w = int((dist_cm / 100.0) * (w - r * 2)) + r
        fill_w = max(r, min(fill_w, w - r))
        color  = dist_color(dist_cm)

        # Fill
        c.create_rectangle(r, h // 2 - 8, fill_w, h // 2 + 8,
                            fill=color, outline='')
        c.create_oval(0, h // 2 - 8, r * 2, h // 2 + 8,
                      fill=color, outline='')
        # Thumb
        c.create_oval(fill_w - 10, h // 2 - 11, fill_w + 10, h // 2 + 11,
                      fill=color, outline=BG, width=2)

    def _draw_chart(self):
        vals   = list(self.history)
        x_vals = list(range(HISTORY_LEN))
        y_vals = [v if v is not None else np.nan for v in vals]

        self.hist_line.set_data(x_vals, y_vals)

        # Colored fill under line
        if self.hist_fill:
            self.hist_fill.remove()
            self.hist_fill = None

        # Find contiguous non-nan segments for fill
        y_arr = np.array(y_vals, dtype=float)
        x_arr = np.array(x_vals, dtype=float)
        mask  = ~np.isnan(y_arr)
        if mask.any():
            color = dist_color(float(np.nanmean(y_arr)))
            self.hist_fill = self.ax.fill_between(
                x_arr, 0, y_arr,
                where=mask, color=color, alpha=0.15)

    # ── Close ──────────────────────────────────────────────────────────────────
    def _on_close(self):
        self._disconnect()
        self.destroy()


if __name__ == '__main__':
    app = DistanceMeter()
    app.mainloop()
