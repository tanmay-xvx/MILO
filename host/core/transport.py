"""
MILO Transport Abstraction Layer.

Defines a common interface for all transport backends (serial, TCP, BLE)
so the host can communicate with any MILO receiver regardless of physical link.
"""

import struct
import time
from abc import ABC, abstractmethod

OP_DISCOVERY = 0x01
OP_BYTECODE_PUSH = 0x02
OP_EXEC_RESULT = 0x03
OP_STREAM_DATA = 0x04
OP_STOP = 0x05
OP_QUERY_STATUS = 0x06
OP_STATUS_RESPONSE = 0x07
OP_SET_PARAM = 0x08
OP_HOT_SWAP = 0x09


class MiloTransport(ABC):
    """Abstract base class for MILO-Link transports."""

    @abstractmethod
    def read_frame(self, timeout: float = 30.0) -> tuple[int, bytes]:
        """Read one MILO-Link frame. Returns (opcode, payload).

        Raises TimeoutError if no complete frame arrives within `timeout` seconds.
        """
        ...

    @abstractmethod
    def write_frame(self, opcode: int, payload: bytes = b"") -> None:
        """Write one MILO-Link frame."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the underlying connection."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""
        ...

    def request_discovery(self, timeout: float = 5.0, retries: int = 3) -> dict | None:
        """Send discovery request and parse JSON response, with retries."""
        import json

        for attempt in range(retries):
            self.write_frame(OP_DISCOVERY)
            try:
                opcode, payload = self.read_frame(timeout=timeout)
                if opcode == OP_DISCOVERY:
                    return json.loads(payload)
            except (TimeoutError, json.JSONDecodeError):
                if attempt < retries - 1:
                    time.sleep(0.5)
        return None

    def push_bytecode(self, wasm_bytes: bytes, timeout: float = 120.0) -> dict:
        """Push wasm bytecode and wait for execution result."""
        import json

        self.write_frame(OP_BYTECODE_PUSH, wasm_bytes)
        opcode, payload = self.read_frame(timeout=timeout)
        if opcode == OP_EXEC_RESULT:
            return json.loads(payload)
        raise RuntimeError(f"unexpected response opcode 0x{opcode:02x}")


class SerialTransport(MiloTransport):
    """MILO-Link over USB serial (pyserial)."""

    def __init__(self, port: str, baud: int = 115200):
        import serial

        self._ser = serial.Serial(port, baud, timeout=5)
        time.sleep(1.0)
        self._ser.reset_input_buffer()

    def read_frame(self, timeout: float = 30.0) -> tuple[int, bytes]:
        deadline = time.time() + timeout
        buf = b""
        while len(buf) < 5:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"header timeout ({len(buf)}/5 bytes)")
            self._ser.timeout = left
            chunk = self._ser.read(5 - len(buf))
            if chunk:
                buf += chunk

        opcode = buf[0]
        plen = struct.unpack(">I", buf[1:5])[0]

        payload = b""
        while len(payload) < plen:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError(f"payload timeout ({len(payload)}/{plen})")
            self._ser.timeout = left
            chunk = self._ser.read(plen - len(payload))
            if chunk:
                payload += chunk

        return opcode, payload

    def write_frame(self, opcode: int, payload: bytes = b"") -> None:
        frame = struct.pack(">BI", opcode, len(payload)) + payload
        self._ser.write(frame)
        self._ser.flush()

    def close(self) -> None:
        self._ser.close()

    @property
    def is_connected(self) -> bool:
        return self._ser.is_open

    def reset_input(self) -> None:
        """Flush pending input data."""
        self._ser.reset_input_buffer()


class TcpTransport(MiloTransport):
    """MILO-Link over persistent TCP connection."""

    def __init__(self, host: str, port: int = 9100):
        import socket

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((host, port))
        self._connected = True

    def _recv_exact(self, n: int, timeout: float) -> bytes:
        self._sock.settimeout(timeout)
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                self._connected = False
                raise ConnectionError("connection closed")
            buf += chunk
        return buf

    def read_frame(self, timeout: float = 30.0) -> tuple[int, bytes]:
        header = self._recv_exact(5, timeout)
        opcode = header[0]
        plen = struct.unpack(">I", header[1:5])[0]
        payload = self._recv_exact(plen, timeout) if plen > 0 else b""
        return opcode, payload

    def write_frame(self, opcode: int, payload: bytes = b"") -> None:
        frame = struct.pack(">BI", opcode, len(payload)) + payload
        self._sock.sendall(frame)

    def close(self) -> None:
        self._connected = False
        self._sock.close()

    @property
    def is_connected(self) -> bool:
        return self._connected
