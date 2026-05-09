"""
MILO Device — high-level async interface for controlling a MILO receiver.

Supports all extended opcodes (0x04-0x09) for runtime control:
stop, hot-swap, query status, set parameters, stream data.
"""

import asyncio
import json
import struct
from dataclasses import dataclass

from core.transport import (
    MiloTransport,
    OP_BYTECODE_PUSH,
    OP_DISCOVERY,
    OP_EXEC_RESULT,
    OP_STOP,
    OP_QUERY_STATUS,
    OP_STATUS_RESPONSE,
    OP_SET_PARAM,
    OP_HOT_SWAP,
    OP_STREAM_DATA,
)


@dataclass
class DeviceStatus:
    """Current device execution status."""

    status: str  # "idle", "running", "completed", "stopped"
    running: bool


@dataclass
class ExecResult:
    """Result from Wasm module execution."""

    ok: bool
    logs: list[str]
    error: str | None = None


class MiloDevice:
    """High-level interface for a single MILO receiver.

    Wraps a `MiloTransport` and provides typed methods for all MILO-Link
    opcodes including the extended control protocol.
    """

    def __init__(self, transport: MiloTransport, name: str = "unnamed"):
        self._transport = transport
        self.name = name
        self.manifest: dict | None = None

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    def discover(self, timeout: float = 5.0) -> dict | None:
        """Request device manifest."""
        self.manifest = self._transport.request_discovery(timeout=timeout)
        return self.manifest

    def push(self, wasm_bytes: bytes, timeout: float = 120.0) -> ExecResult:
        """Push Wasm bytecode and wait for execution result."""
        self._transport.write_frame(OP_BYTECODE_PUSH, wasm_bytes)
        opcode, payload = self._transport.read_frame(timeout=timeout)
        if opcode == OP_EXEC_RESULT:
            data = json.loads(payload)
            return ExecResult(
                ok=data.get("ok", False),
                logs=data.get("logs", []),
                error=data.get("error"),
            )
        raise RuntimeError(f"unexpected opcode 0x{opcode:02x}")

    def stop(self) -> dict:
        """Stop the currently running Wasm module."""
        self._transport.write_frame(OP_STOP)
        opcode, payload = self._transport.read_frame(timeout=5.0)
        if opcode == OP_STATUS_RESPONSE:
            return json.loads(payload)
        return {"stopped": True}

    def query_status(self, timeout: float = 5.0) -> DeviceStatus:
        """Query device execution status."""
        self._transport.write_frame(OP_QUERY_STATUS)
        opcode, payload = self._transport.read_frame(timeout=timeout)
        if opcode == OP_STATUS_RESPONSE:
            data = json.loads(payload)
            return DeviceStatus(
                status=data.get("status", "unknown"),
                running=data.get("running", False),
            )
        raise RuntimeError(f"unexpected opcode 0x{opcode:02x}")

    def set_param(self, slot: int, value: int) -> None:
        """Set a shared parameter slot on the device.

        The running Wasm module can read this via `get_param(slot)`.
        """
        payload = struct.pack(">II", slot, value)
        self._transport.write_frame(OP_SET_PARAM, payload)

    def hot_swap(self, wasm_bytes: bytes, timeout: float = 120.0) -> ExecResult:
        """Stop current execution and immediately start new bytecode."""
        self._transport.write_frame(OP_HOT_SWAP, wasm_bytes)
        opcode, payload = self._transport.read_frame(timeout=timeout)
        if opcode == OP_EXEC_RESULT:
            data = json.loads(payload)
            return ExecResult(
                ok=data.get("ok", False),
                logs=data.get("logs", []),
                error=data.get("error"),
            )
        raise RuntimeError(f"unexpected opcode 0x{opcode:02x}")

    def close(self) -> None:
        """Close the underlying transport."""
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
