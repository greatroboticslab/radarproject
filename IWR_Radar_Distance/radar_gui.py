"""
Tkinter GUI that shows live distance readings from a TI IWR6843 (AOP) board
running the stock mmWave Demo firmware.

Usage:
    python radar_gui.py

Steps in the GUI:
    1. Click "Refresh Ports", pick the CLI port and the Data port.
       (In Device Manager these usually show up as two consecutive COM ports,
       e.g. "XDS110 Class Application/User UART" = CLI and
       "XDS110 Class Auxiliary Data Port" = Data. If unsure, try one
       combination -- if it doesn't work, swap them and try again.)
    2. Pick a .cfg file (default_config.cfg is bundled as a starting point --
       see the note in that file).
    3. Click "Connect & Start". Distance updates live in the big readout.
    4. Click "Stop" to stop the sensor and close the ports.
"""

import csv
import os
import time
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk

from radar_reader import RadarConnection, list_serial_ports, METERS_TO_FEET

HISTORY_LEN = 150  # points kept for the scrolling graph
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


class RadarGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IWR6843 Distance Monitor")
        self.geometry("760x560")
        self.minsize(640, 480)

        self.conn: RadarConnection | None = None
        self.history = deque(maxlen=HISTORY_LEN)
        self.running = False
        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None

        self._build_widgets()
        self._refresh_ports()
        self.after(50, self._poll)

    # ---------------------------------------------------------------- UI --

    def _build_widgets(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="CLI port:").grid(row=0, column=0, sticky="w")
        self.cli_combo = ttk.Combobox(top, width=42, state="readonly")
        self.cli_combo.grid(row=0, column=1, sticky="w")

        ttk.Label(top, text="Data port:").grid(row=1, column=0, sticky="w")
        self.data_combo = ttk.Combobox(top, width=42, state="readonly")
        self.data_combo.grid(row=1, column=1, sticky="w")

        ttk.Button(top, text="Refresh Ports", command=self._refresh_ports).grid(
            row=0, column=2, rowspan=2, padx=8
        )

        cfg_row = ttk.Frame(self)
        cfg_row.pack(fill="x", **pad)
        ttk.Label(cfg_row, text="Config file:").pack(side="left")
        default_cfg = os.path.join(os.path.dirname(__file__), "default_config.cfg")
        self.cfg_path_var = tk.StringVar(value=default_cfg)
        ttk.Entry(cfg_row, textvariable=self.cfg_path_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(cfg_row, text="Browse...", command=self._browse_cfg).pack(side="left")

        log_row = ttk.Frame(self)
        log_row.pack(fill="x", **pad)
        self.log_csv_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            log_row, text="Log readings to CSV (in logs/ folder)", variable=self.log_csv_var
        ).pack(side="left")
        self.csv_path_var = tk.StringVar(value="")
        ttk.Label(log_row, textvariable=self.csv_path_var, foreground="gray").pack(
            side="left", padx=8
        )

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btn_row, text="Connect & Start", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(btn_row, textvariable=self.status_var).pack(side="left", padx=12)

        # Big distance readout
        readout = ttk.Frame(self)
        readout.pack(fill="x", pady=12)
        self.dist_var = tk.StringVar(value="-- m")
        tk.Label(readout, textvariable=self.dist_var, font=("Segoe UI", 56, "bold")).pack()
        self.dist_ft_var = tk.StringVar(value="")
        tk.Label(readout, textvariable=self.dist_ft_var, font=("Segoe UI", 14)).pack()
        self.obj_count_var = tk.StringVar(value="")
        tk.Label(readout, textvariable=self.obj_count_var, font=("Segoe UI", 10)).pack()

        # Scrolling graph of nearest-object distance
        self.canvas = tk.Canvas(self, bg="white", height=180)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=4)

        # Log box
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        ttk.Label(log_frame, text="Sensor log:").pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _browse_cfg(self):
        path = filedialog.askopenfilename(
            title="Select radar .cfg file",
            filetypes=[("Config files", "*.cfg"), ("All files", "*.*")],
        )
        if path:
            self.cfg_path_var.set(path)

    def _refresh_ports(self):
        ports = list_serial_ports()
        labels = [f"{p.device} - {p.description}" for p in ports]
        self.cli_combo["values"] = labels
        self.data_combo["values"] = labels
        if not labels:
            return

        # TI boards with a CP2105/XDS110 USB bridge expose an "Enhanced"
        # COM port (CLI/config) and a "Standard" COM port (binary data).
        # Auto-select those when present; otherwise fall back to first/second.
        cli_idx = next((i for i, p in enumerate(ports) if "enhanced" in p.description.lower()), 0)
        data_idx = next(
            (i for i, p in enumerate(ports) if "standard" in p.description.lower()),
            1 if len(labels) > 1 else 0,
        )
        self.cli_combo.current(cli_idx)
        self.data_combo.current(data_idx)

    @staticmethod
    def _port_from_label(label: str) -> str:
        return label.split(" - ", 1)[0].strip()

    # ------------------------------------------------------------ actions --

    def _start(self):
        cli_label = self.cli_combo.get()
        data_label = self.data_combo.get()
        cfg_path = self.cfg_path_var.get()

        if not cli_label or not data_label:
            messagebox.showerror("Missing port", "Select both a CLI port and a Data port.")
            return
        if self._port_from_label(cli_label) == self._port_from_label(data_label):
            messagebox.showerror("Same port", "CLI port and Data port must be different.")
            return
        if not os.path.isfile(cfg_path):
            messagebox.showerror("Missing config", f"Config file not found:\n{cfg_path}")
            return

        cli_port = self._port_from_label(cli_label)
        data_port = self._port_from_label(data_label)

        self.conn = RadarConnection(cli_port, data_port)
        self.status_var.set("Sending config...")
        self.start_btn.config(state="disabled")
        self.update_idletasks()

        try:
            self.conn.send_config(cfg_path)
            self.conn.start_streaming()
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))
            self.status_var.set("Not connected")
            self.start_btn.config(state="normal")
            self.conn = None
            return

        self.running = True
        self.history.clear()
        self.status_var.set(f"Streaming: CLI={cli_port}  DATA={data_port}")
        self.stop_btn.config(state="normal")

        if self.log_csv_var.get():
            self._open_csv_log()
        else:
            self.csv_path_var.set("")

    def _open_csv_log(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        fname = time.strftime("radar_log_%Y-%m-%d_%H-%M-%S.csv")
        self.csv_path = os.path.join(LOG_DIR, fname)
        self.csv_file = open(self.csv_path, "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["wall_time", "frame_number", "num_objects", "min_range_m",
             "min_range_ft", "all_ranges_m"]
        )
        self.csv_path_var.set(f"-> {self.csv_path}")

    def _close_csv_log(self):
        if self.csv_file is not None:
            self.csv_file.close()
            self._append_log(f"Saved CSV log: {self.csv_path}")
        self.csv_file = None
        self.csv_writer = None

    def _stop(self):
        if self.conn is not None:
            self.conn.stop()
            self.conn = None
        self.running = False
        self._close_csv_log()
        self.status_var.set("Stopped")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.dist_var.set("-- m")
        self.dist_ft_var.set("")
        self.obj_count_var.set("")

    def _on_close(self):
        self._stop()
        self.destroy()

    # -------------------------------------------------------------- poll --

    def _poll(self):
        if self.conn is not None:
            # Drain log queue
            drained_log = False
            while not self.conn.log_queue.empty():
                msg = self.conn.log_queue.get_nowait()
                self._append_log(msg)
                drained_log = True

            # Drain frame queue, keep only the latest for display
            latest_frame = None
            while not self.conn.frame_queue.empty():
                latest_frame = self.conn.frame_queue.get_nowait()

            if latest_frame is not None:
                self._update_reading(latest_frame)

        self.after(50, self._poll)

    def _append_log(self, msg: str):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _update_reading(self, frame):
        min_r = frame.min_range_m
        n = len(frame.points)
        self.obj_count_var.set(f"{n} object(s) detected  |  frame #{frame.frame_number}")

        if min_r is None:
            self.dist_var.set("-- m")
            self.dist_ft_var.set("no target")
            self.history.append(None)
        else:
            self.dist_var.set(f"{min_r:.2f} m")
            self.dist_ft_var.set(f"{min_r * METERS_TO_FEET:.2f} ft")
            self.history.append(min_r)

        if self.csv_writer is not None:
            ranges = frame.ranges_m
            self.csv_writer.writerow([
                f"{time.time():.3f}",
                frame.frame_number,
                n,
                f"{min_r:.4f}" if min_r is not None else "",
                f"{min_r * METERS_TO_FEET:.4f}" if min_r is not None else "",
                ";".join(f"{r:.4f}" for r in ranges),
            ])

        self._draw_graph()

    def _draw_graph(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 700
        h = self.canvas.winfo_height() or 180
        if not self.history:
            return

        values = [v for v in self.history if v is not None]
        max_v = max(values) if values else 1.0
        max_v = max(max_v, 1.0) * 1.15  # headroom

        n = len(self.history)
        step_x = w / max(HISTORY_LEN - 1, 1)

        points = []
        for i, v in enumerate(self.history):
            x = i * step_x
            if v is None:
                points.append(None)
            else:
                y = h - (v / max_v) * (h - 20) - 10
                points.append((x, y))

        last = None
        for p in points:
            if p is not None and last is not None:
                self.canvas.create_line(last[0], last[1], p[0], p[1], fill="#1f77b4", width=2)
            last = p

        # y-axis labels
        self.canvas.create_text(4, 10, anchor="w", text=f"{max_v:.1f} m", fill="gray")
        self.canvas.create_text(4, h - 10, anchor="w", text="0 m", fill="gray")


if __name__ == "__main__":
    app = RadarGUI()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()
