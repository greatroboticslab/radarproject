"""
TLV frame parser for the IWR6843 UART data port (processed output path).

This module is used when capturing via UART rather than DCA1000EVM.
The SDK emits binary TLV frames on the data UART port.

Frame wire format (mmWave SDK 3.x out-of-box demo):
  [8-byte magic] [uint32 version] [uint32 totalPacketLen] [uint32 platform]
  [uint32 frameNumber] [uint32 timeCpuCycles] [uint32 numDetectedObj]
  [uint32 numTLVs] [uint32 subFrameNumber]
  then numTLVs × { [uint32 type] [uint32 length] [length bytes payload] }

TLV types used here:
  2 – Range profile  : uint16[] log-magnitude per range bin
  6 – Statistics     : frame statistics struct

LIMITATION: The UART path provides log-magnitude only (no complex phase).
Use the DCA1000 path (dca1000.py) if complex reflection coefficient is required.
"""

import struct
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

MAGIC = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
MAGIC_LEN = len(MAGIC)

_HEADER_FMT = "<8I"  # 8 × uint32 following the magic word
_HEADER_SIZE = MAGIC_LEN + struct.calcsize(_HEADER_FMT)  # 40 bytes

_TLV_HDR_FMT = "<II"
_TLV_HDR_SIZE = struct.calcsize(_TLV_HDR_FMT)  # 8 bytes

TLV_RANGE_PROFILE = 2
TLV_STATISTICS = 6


@dataclass
class FrameHeader:
    version: int
    total_packet_len: int
    platform: int
    frame_number: int
    time_cpu_cycles: int
    num_detected_obj: int
    num_tlvs: int
    subframe_number: int


@dataclass
class ParsedFrame:
    header: FrameHeader
    range_profile: Optional[np.ndarray]  # uint16 log-magnitude, shape (num_bins,)
    raw_tlvs: dict                        # type_id → payload bytes


class FrameParser:
    """
    Reads binary frames from a byte source and parses TLV payloads.

    Usage (UART data path):
        with UARTInterface(cli_port, data_port) as uart:
            parser = FrameParser(uart)
            for frame in parser.iter_frames():
                mag = frame.range_profile   # log-magnitude range profile
    """

    def __init__(self, uart_iface):
        self._uart = uart_iface
        self._buf = b""

    def iter_frames(self):
        """Yield ParsedFrame objects as they arrive on the data port."""
        while True:
            frame = self._read_next_frame()
            if frame is not None:
                yield frame

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill(self, n: int) -> None:
        while len(self._buf) < n:
            self._buf += self._uart.read_data_bytes(64)

    def _consume(self, n: int) -> bytes:
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    def _sync_to_magic(self) -> None:
        """Advance the buffer until MAGIC appears at the start."""
        while True:
            self._fill(MAGIC_LEN)
            idx = self._buf.find(MAGIC)
            if idx == 0:
                return
            if idx == -1:
                self._buf = self._buf[-(MAGIC_LEN - 1):]
            else:
                self._buf = self._buf[idx:]

    def _read_next_frame(self) -> Optional[ParsedFrame]:
        try:
            self._sync_to_magic()
            self._fill(_HEADER_SIZE)

            magic = self._consume(MAGIC_LEN)
            assert magic == MAGIC

            fields = struct.unpack(_HEADER_FMT, self._consume(struct.calcsize(_HEADER_FMT)))
            hdr = FrameHeader(*fields)

            remaining = hdr.total_packet_len - _HEADER_SIZE
            self._fill(remaining)
            payload = self._consume(remaining)

            return self._parse_tlvs(hdr, payload)

        except Exception as exc:
            log.debug("Frame parse error: %s – resyncing", exc)
            return None

    def _parse_tlvs(self, hdr: FrameHeader, payload: bytes) -> ParsedFrame:
        offset = 0
        raw_tlvs: dict = {}
        range_profile: Optional[np.ndarray] = None

        for _ in range(hdr.num_tlvs):
            if offset + _TLV_HDR_SIZE > len(payload):
                break
            tlv_type, tlv_len = struct.unpack_from(_TLV_HDR_FMT, payload, offset)
            offset += _TLV_HDR_SIZE
            tlv_data = payload[offset: offset + tlv_len]
            offset += tlv_len
            raw_tlvs[tlv_type] = tlv_data

            if tlv_type == TLV_RANGE_PROFILE:
                range_profile = np.frombuffer(tlv_data, dtype=np.uint16).copy()

        return ParsedFrame(header=hdr, range_profile=range_profile, raw_tlvs=raw_tlvs)
