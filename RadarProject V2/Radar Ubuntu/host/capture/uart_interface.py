"""
UART interface for IWR6843AOP.

Two serial ports are used:
  CLI port  – send config commands, read 'Done' / 'Error' echoes
  Data port – receive binary TLV frames (processed range profile etc.)

HARDWARE NOTE: The CLI port typically appears first (lower COM number) and
the data port second.  Typical baud rates: CLI 115200, data 921600.
"""

import time
import struct
import logging
from pathlib import Path
from typing import Iterator, Optional

import serial

log = logging.getLogger(__name__)

CLI_BAUD = 115_200
DATA_BAUD = 921_600
CMD_DELAY_S = 0.05   # pause between CLI commands
DONE_TIMEOUT_S = 2.0


class UARTInterface:
    def __init__(self, cli_port: str, data_port: Optional[str] = None):
        self.cli_port = cli_port
        self.data_port = data_port
        self._cli: Optional[serial.Serial] = None
        self._data: Optional[serial.Serial] = None

    def __enter__(self):
        self._cli = serial.Serial(self.cli_port, CLI_BAUD, timeout=DONE_TIMEOUT_S)
        if self.data_port:
            self._data = serial.Serial(self.data_port, DATA_BAUD, timeout=1.0)
        log.info("UART open: CLI=%s  Data=%s", self.cli_port, self.data_port or "—")
        return self

    def __exit__(self, *_):
        for port in (self._cli, self._data):
            if port and port.is_open:
                port.close()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def send_config(self, cfg_path: str) -> None:
        """Parse a .cfg file and send each command, waiting for 'Done'."""
        path = Path(cfg_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {cfg_path}")

        commands = [
            ln.strip()
            for ln in path.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("%")
        ]
        log.info("Sending %d commands from %s", len(commands), path.name)
        for cmd in commands:
            self._send_command(cmd)

    def _send_command(self, cmd: str) -> None:
        assert self._cli is not None
        line = (cmd + "\n").encode()
        self._cli.write(line)
        self._cli.flush()
        time.sleep(CMD_DELAY_S)

        deadline = time.monotonic() + DONE_TIMEOUT_S
        response = b""
        while time.monotonic() < deadline:
            chunk = self._cli.read(self._cli.in_waiting or 1)
            response += chunk
            if b"Done" in response:
                log.debug("CMD OK: %s", cmd)
                return
            if b"Error" in response:
                raise RuntimeError(f"Radar rejected command '{cmd}': {response!r}")

        log.warning("No 'Done' echo for command '%s' within %.1f s", cmd, DONE_TIMEOUT_S)

    # ------------------------------------------------------------------
    # Data port helpers (used by frame_parser)
    # ------------------------------------------------------------------

    def read_data_bytes(self, n: int) -> bytes:
        """Block until exactly n bytes are available on the data port."""
        if self._data is None:
            raise RuntimeError("Data port not open")
        buf = b""
        while len(buf) < n:
            chunk = self._data.read(n - len(buf))
            if chunk:
                buf += chunk
        return buf

    def iter_data_bytes(self) -> Iterator[bytes]:
        """Yield bytes one at a time from the data port."""
        if self._data is None:
            raise RuntimeError("Data port not open")
        while True:
            b = self._data.read(1)
            if b:
                yield b
