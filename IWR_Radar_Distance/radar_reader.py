"""
UART reader/parser for the TI IWR6843 (and other xWR68xx/18xx boards) running
the stock "Out of Box" / mmWave Demo firmware from the mmWave SDK (3.x).

The board enumerates as TWO virtual COM ports over USB:
  - CLI / "Enhanced" port   -> 115200 baud, text.  You send it the .cfg file;
                                it echoes back what it parsed.
  - DATA / "Standard" port  -> 921600 baud, binary. It streams framed TLV
                                packets once `sensorStart` has been sent.

This module has no GUI code in it -- it just knows how to talk to the sensor
and turn the binary stream into a list of detected points (x, y, z, velocity)
per frame, from which distance (range) is computed as sqrt(x^2+y^2+z^2).

Frame layout (mmWave SDK 3.x demo output, 40-byte header):
    magicWord        8 bytes   = 02 01 04 03 06 05 08 07
    version          uint32
    totalPacketLen   uint32    (includes this header)
    platform         uint32
    frameNumber      uint32
    timeCpuCycles    uint32
    numDetectedObj   uint32
    numTLVs          uint32
    subFrameNumber   uint32
  followed by `numTLVs` TLVs, each:
    type             uint32
    length           uint32    (payload length, not including this TLV header)
    <payload>
  TLV type 1 = Detected Points: `numDetectedObj` structs of
    x, y, z, velocity   (4x float32, meters / m/s)
"""

from __future__ import annotations

import math
import queue
import struct
import threading
import time
from dataclasses import dataclass, field

import serial
from serial.tools import list_ports

MAGIC_WORD = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])

TLV_TYPE_DETECTED_POINTS = 1
TLV_TYPE_RANGE_PROFILE = 2
TLV_TYPE_POINT_SIDE_INFO = 7

_HEADER = struct.Struct("<8s8I")   # magic(8) + 8 x uint32 = 40 bytes
_TLV_HEADER = struct.Struct("<2I")  # type, length = 8 bytes
_POINT = struct.Struct("<4f")       # x, y, z, velocity = 16 bytes
_SIDE_INFO = struct.Struct("<2h")   # snr, noise in 0.1 dB units

METERS_TO_FEET = 3.28084


@dataclass
class RadarFrame:
    frame_number: int
    points: list  # list of (x, y, z, velocity)
    snr_db: list = field(default_factory=list)    # per point, same order as points
    noise_db: list = field(default_factory=list)
    range_profile: list = field(default_factory=list)  # raw uint16 per range bin, Q9 log2 magnitude

    @property
    def ranges_m(self) -> list:
        return [math.sqrt(x * x + y * y + z * z) for x, y, z, _v in self.points]

    @property
    def min_range_m(self):
        r = self.ranges_m
        return min(r) if r else None


class FrameParser:
    """Feed it raw bytes from the data port; get back a list of RadarFrame."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list:
        self._buf.extend(data)
        frames = []
        while True:
            idx = self._buf.find(MAGIC_WORD)
            if idx == -1:
                # No magic word yet. Keep a small tail in case it's split
                # across two reads; drop everything before that.
                if len(self._buf) > len(MAGIC_WORD):
                    del self._buf[: len(self._buf) - (len(MAGIC_WORD) - 1)]
                break
            if idx > 0:
                del self._buf[:idx]

            if len(self._buf) < _HEADER.size:
                break  # wait for the rest of the header

            (magic, version, total_len, platform, frame_num,
             cpu_cycles, num_obj, num_tlv, subframe) = _HEADER.unpack(
                bytes(self._buf[: _HEADER.size])
            )

            if total_len < _HEADER.size or total_len > 500_000:
                # Corrupt / desynced. Skip this magic word and resync.
                del self._buf[: len(MAGIC_WORD)]
                continue

            if len(self._buf) < total_len:
                break  # wait for the rest of the frame

            frame_bytes = bytes(self._buf[:total_len])
            del self._buf[:total_len]

            frame = self._parse_frame(frame_bytes, frame_num, num_tlv)
            frames.append(frame)
        return frames

    @staticmethod
    def _parse_frame(frame_bytes: bytes, frame_num: int, num_tlv: int) -> RadarFrame:
        offset = _HEADER.size
        frame = RadarFrame(frame_number=frame_num, points=[])
        for _ in range(num_tlv):
            if offset + _TLV_HEADER.size > len(frame_bytes):
                break
            tlv_type, tlv_len = _TLV_HEADER.unpack_from(frame_bytes, offset)
            offset += _TLV_HEADER.size
            payload = frame_bytes[offset: offset + tlv_len]

            if tlv_type == TLV_TYPE_DETECTED_POINTS:
                for i in range(len(payload) // _POINT.size):
                    frame.points.append(_POINT.unpack_from(payload, i * _POINT.size))
            elif tlv_type == TLV_TYPE_POINT_SIDE_INFO:
                for i in range(len(payload) // _SIDE_INFO.size):
                    snr, noise = _SIDE_INFO.unpack_from(payload, i * _SIDE_INFO.size)
                    frame.snr_db.append(snr / 10.0)
                    frame.noise_db.append(noise / 10.0)
            elif tlv_type == TLV_TYPE_RANGE_PROFILE:
                frame.range_profile = list(struct.unpack(f"<{len(payload) // 2}H", payload[: len(payload) // 2 * 2]))

            offset += tlv_len
        return frame


def list_serial_ports():
    """Returns serial.tools.list_ports ListPortInfo objects for all COM ports."""
    return sorted(list_ports.comports(), key=lambda p: p.device)


class RadarConnection:
    """Owns both serial ports and a background thread reading the data port."""

    def __init__(self, cli_port: str, data_port: str,
                 cli_baud: int = 115200, data_baud: int = 921600):
        self.cli_port_name = cli_port
        self.data_port_name = data_port
        self.cli_baud = cli_baud
        self.data_baud = data_baud

        self.cli_ser: serial.Serial | None = None
        self.data_ser: serial.Serial | None = None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.frame_queue: "queue.Queue[RadarFrame]" = queue.Queue()
        self.log_queue: "queue.Queue[str]" = queue.Queue()

    # -- configuration -----------------------------------------------------

    def send_config(self, cfg_path: str):
        """Open the CLI port and send every line of the .cfg file to the sensor."""
        self.cli_ser = serial.Serial(self.cli_port_name, self.cli_baud, timeout=1)
        time.sleep(0.4)  # let the port settle

        with open(cfg_path, "r") as f:
            lines = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("%")
            ]

        for line in lines:
            self.cli_ser.write((line + "\n").encode())
            time.sleep(0.05)
            resp = bytearray(self.cli_ser.read(self.cli_ser.in_waiting or 1))
            time.sleep(0.05)
            resp += self.cli_ser.read(self.cli_ser.in_waiting or 0)
            text = resp.decode(errors="replace")
            self.log_queue.put(f"> {line}\n{text.strip()}")
            if "Error" in text or "not recognized" in text:
                self.log_queue.put(f"  ^ WARNING: sensor rejected: {line}")

    # -- data streaming ------------------------------------------------------

    def start_streaming(self):
        self.data_ser = serial.Serial(self.data_port_name, self.data_baud, timeout=0.1)
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        parser = FrameParser()
        while not self._stop.is_set():
            try:
                data = self.data_ser.read(4096)
            except serial.SerialException as exc:
                self.log_queue.put(f"Data port error: {exc}")
                break
            if data:
                for frame in parser.feed(data):
                    self.frame_queue.put(frame)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

        if self.cli_ser is not None and self.cli_ser.is_open:
            try:
                self.cli_ser.write(b"sensorStop\n")
                time.sleep(0.1)
            except Exception:
                pass
            self.cli_ser.close()

        if self.data_ser is not None and self.data_ser.is_open:
            self.data_ser.close()
