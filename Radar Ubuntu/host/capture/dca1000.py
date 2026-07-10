"""
DCA1000EVM interface – raw ADC capture over Ethernet UDP.

Network topology (defaults):
  DCA1000 IP  : 192.168.33.180   (configure via Ethernet adapter on host)
  Host IP     : 192.168.33.30
  Config port : 4096  (host → DCA1000, UDP)
  Data port   : 4098  (DCA1000 → host, UDP)

HARDWARE SETUP NOTE:
  1. Connect DCA1000EVM to the MMWAVEICBOOST 60-pin connector.
  2. Connect DCA1000 Ethernet to the host NIC.  Set the NIC to static IP
     192.168.33.30 / 255.255.255.0.
  3. Ensure lvdsStreamCfg in chirp_config.cfg has dataFmt 1 (ADC only).
  4. Power cycle DCA1000 before each session.

Packet wire format (DCA1000 → host, data port):
  Bytes 0–3   : sequence number (uint32 LE)
  Bytes 4–9   : cumulative byte count (uint48 LE)
  Bytes 10+   : raw ADC data (max 1456 bytes per packet)

ADC data layout (adcbufCfg -1 0 1 1 1 → complex, Q-before-I, interleaved RX):
  For each sample n, for each RX channel r:
    int16 Q_n_r, int16 I_n_r
  So one chirp = numAdcSamples × numRX × 4 bytes.
"""

import socket
import struct
import logging
import time
from typing import Generator, Tuple

import numpy as np

from host.config import ChirpConfig

log = logging.getLogger(__name__)

DCA_IP = "192.168.33.180"
HOST_IP = "192.168.33.30"
CFG_PORT = 4096
DATA_PORT = 4098

_HDR = b"\xA5\x5A"
_FTR = b"\x5A\xA5"

# DCA1000 command codes
_CMD_RESET_FPGA = 0x0001
_CMD_RESET_AR_DEV = 0x0002
_CMD_CONFIG_FPGA = 0x0003
_CMD_RECORD_START = 0x0005
_CMD_RECORD_STOP = 0x0006
_CMD_READ_VERSION = 0x000C

_DATA_HEADER_SIZE = 10   # 4 (seq) + 6 (byte count)
_MAX_PAYLOAD = 1456


class DCA1000:
    """Thin wrapper around DCA1000EVM UDP API."""

    def __init__(
        self,
        dca_ip: str = DCA_IP,
        host_ip: str = HOST_IP,
        cfg_port: int = CFG_PORT,
        data_port: int = DATA_PORT,
    ):
        self._dca_addr = (dca_ip, cfg_port)
        self._data_port = data_port
        self._host_ip = host_ip
        self._cfg_sock: socket.socket | None = None
        self._data_sock: socket.socket | None = None

    def __enter__(self):
        self._cfg_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cfg_sock.settimeout(3.0)
        self._cfg_sock.bind((self._host_ip, CFG_PORT))

        self._data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._data_sock.settimeout(1.0)
        self._data_sock.bind((self._host_ip, self._data_port))

        self.reset()
        self.configure()
        return self

    def __exit__(self, *_):
        self.stop_record()
        for s in (self._cfg_sock, self._data_sock):
            if s:
                s.close()

    # ------------------------------------------------------------------
    # DCA1000 command helpers
    # ------------------------------------------------------------------

    def _send_cmd(self, code: int, data: bytes = b"") -> int:
        """Send a command and return the status word (0 = success)."""
        pkt = _HDR + struct.pack("<HH", code, len(data)) + data + _FTR
        self._cfg_sock.sendto(pkt, self._dca_addr)
        try:
            resp, _ = self._cfg_sock.recvfrom(256)
        except socket.timeout:
            raise TimeoutError(f"No response to DCA1000 command 0x{code:04X}")
        # Response: header(2) + cmd_echo(2) + status(2) + footer(2)
        if len(resp) < 8:
            raise RuntimeError(f"Short DCA1000 response: {resp!r}")
        status = struct.unpack_from("<H", resp, 4)[0]
        if status != 0:
            log.warning("DCA1000 cmd 0x%04X returned status 0x%04X", code, status)
        return status

    def reset(self) -> None:
        log.info("DCA1000: resetting FPGA")
        self._send_cmd(_CMD_RESET_FPGA)
        time.sleep(0.5)

    def configure(self) -> None:
        """Configure FPGA for raw-ADC Ethernet streaming."""
        # dataFmt 1=ADC, dataTransferMode 1=Capture, dataLoggingMode 0=Raw
        # lvdsMode 1=4-lane LVDS, ethernetDataPort 0→port 4098
        cfg_data = bytes([1, 1, 0, 1, 0, 0])
        log.info("DCA1000: sending FPGA config")
        self._send_cmd(_CMD_CONFIG_FPGA, cfg_data)

    def start_record(self) -> None:
        log.info("DCA1000: start recording")
        self._send_cmd(_CMD_RECORD_START)

    def stop_record(self) -> None:
        log.info("DCA1000: stop recording")
        try:
            self._send_cmd(_CMD_RECORD_STOP)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Frame capture
    # ------------------------------------------------------------------

    def capture_frames(
        self, chirp_cfg: ChirpConfig, num_frames: int, stop_event=None
    ) -> Generator[np.ndarray, None, None]:
        """
        Yield one numpy array per frame.

        Each yielded array has shape
            (num_chirps_per_frame, num_adc_samples, num_rx)
        and dtype complex64.  I + jQ convention.

        VERIFY: the Q-before-I reordering below matches your adcbufCfg SampleSwap
        setting.  If the reflection phase looks wrong, swap the +1j / +0j terms.
        """
        frame_bytes = chirp_cfg.bytes_per_frame
        buf = bytearray()

        self.start_record()
        frames_yielded = 0

        try:
            while frames_yielded < num_frames:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    pkt, _ = self._data_sock.recvfrom(_DATA_HEADER_SIZE + _MAX_PAYLOAD)
                except socket.timeout:
                    continue  # re-check stop_event on next iteration

                if len(pkt) <= _DATA_HEADER_SIZE:
                    continue

                seq_num = struct.unpack_from("<I", pkt, 0)[0]
                # bytes 4-9: 48-bit cumulative byte count (little-endian)
                byte_count = int.from_bytes(pkt[4:10], "little")
                payload = pkt[_DATA_HEADER_SIZE:]
                buf.extend(payload)

                while len(buf) >= frame_bytes:
                    frame_raw = np.frombuffer(buf[:frame_bytes], dtype=np.int16).copy()
                    buf = buf[frame_bytes:]

                    # Reshape: (chirps, samples, rx_channels, iq_pair)
                    frame_raw = frame_raw.reshape(
                        chirp_cfg.num_chirps_per_frame,
                        chirp_cfg.num_adc_samples,
                        chirp_cfg.num_rx,
                        2,
                    )
                    # adcbufCfg SampleSwap=1 → index 0 = Q, index 1 = I
                    I = frame_raw[..., 1].astype(np.float32)
                    Q = frame_raw[..., 0].astype(np.float32)
                    frame_iq = (I + 1j * Q).astype(np.complex64)
                    # shape: (num_chirps, num_samples, num_rx)

                    yield frame_iq
                    frames_yielded += 1
                    if frames_yielded >= num_frames:
                        break
        finally:
            self.stop_record()
