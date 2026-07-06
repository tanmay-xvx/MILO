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
    OP_SIGNED_PUSH,
    OP_SIGNED_SWAP,
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
        # EXEC_RESULT frames that arrived while waiting for something else
        # (a long-running module can finish at any time).
        self._pending_results: list[bytes] = []

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    def _read_routed(self, want_opcode: int, timeout: float) -> bytes:
        """Read frames until `want_opcode` arrives, stashing stray EXEC_RESULTs.

        With a non-blocking executor on the device, an EXEC_RESULT from a
        previously pushed module can interleave with the response to a later
        control request; route it to the pending queue instead of failing.
        """
        import time as _time

        deadline = _time.time() + timeout
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                raise TimeoutError(f"no 0x{want_opcode:02x} frame within {timeout}s")
            opcode, payload = self._transport.read_frame(timeout=remaining)
            if opcode == want_opcode:
                return payload
            if opcode == OP_EXEC_RESULT:
                self._pending_results.append(payload)

    @staticmethod
    def _parse_result(payload: bytes) -> ExecResult:
        data = json.loads(payload)
        return ExecResult(
            ok=data.get("ok", False),
            logs=data.get("logs", []),
            error=data.get("error"),
        )

    def discover(self, timeout: float = 5.0) -> dict | None:
        """Request device manifest."""
        self.manifest = self._transport.request_discovery(timeout=timeout)
        return self.manifest

    def push(self, wasm_bytes: bytes, timeout: float = 120.0) -> ExecResult:
        """Push Wasm bytecode and wait for execution result."""
        self._transport.write_frame(OP_BYTECODE_PUSH, wasm_bytes)
        return self.wait_result(timeout=timeout)

    def push_async(self, wasm_bytes: bytes) -> None:
        """Push bytecode without waiting — collect via wait_result() later.

        Requires a receiver with a non-blocking executor (sim fleet, dual-core)
        for live control; on blocking receivers the result simply queues up.
        """
        self._transport.write_frame(OP_BYTECODE_PUSH, wasm_bytes)

    def push_signed(self, signed_payload: bytes, timeout: float = 120.0) -> ExecResult:
        """Push an Ed25519-signed module (signature||wasm) and wait for result.

        Use `core.signing.sign_wasm(wasm, key)` to build the payload. Required
        by receivers provisioned with MILO_REQUIRE_SIGNED=1.
        """
        self._transport.write_frame(OP_SIGNED_PUSH, signed_payload)
        return self.wait_result(timeout=timeout)

    def push_signed_async(self, signed_payload: bytes) -> None:
        self._transport.write_frame(OP_SIGNED_PUSH, signed_payload)

    def hot_swap_signed(self, signed_payload: bytes, timeout: float = 120.0) -> ExecResult:
        """Hot-swap with an Ed25519-signed module and wait for result."""
        self._transport.write_frame(OP_SIGNED_SWAP, signed_payload)
        return self.wait_result(timeout=timeout)

    def wait_result(self, timeout: float = 120.0) -> ExecResult:
        """Wait for the next execution result (pending queue first)."""
        if self._pending_results:
            return self._parse_result(self._pending_results.pop(0))
        return self._parse_result(self._read_routed(OP_EXEC_RESULT, timeout))

    def stop(self) -> dict:
        """Stop the currently running Wasm module."""
        self._transport.write_frame(OP_STOP)
        payload = self._read_routed(OP_STATUS_RESPONSE, timeout=5.0)
        return json.loads(payload)

    def query_status(self, timeout: float = 5.0) -> DeviceStatus:
        """Query device execution status."""
        self._transport.write_frame(OP_QUERY_STATUS)
        payload = self._read_routed(OP_STATUS_RESPONSE, timeout=timeout)
        data = json.loads(payload)
        return DeviceStatus(
            status=data.get("status", "unknown"),
            running=data.get("running", False),
        )

    def set_param(self, slot: int, value: int) -> None:
        """Set a shared parameter slot on the device.

        The running Wasm module can read this via `get_param(slot)`.
        """
        payload = struct.pack(">II", slot, value)
        self._transport.write_frame(OP_SET_PARAM, payload)

    def hot_swap(self, wasm_bytes: bytes, timeout: float = 120.0) -> ExecResult:
        """Stop current execution and immediately start new bytecode."""
        self._transport.write_frame(OP_HOT_SWAP, wasm_bytes)
        return self.wait_result(timeout=timeout)

    def hot_swap_async(self, wasm_bytes: bytes) -> None:
        """Hot-swap without waiting — collect via wait_result() later."""
        self._transport.write_frame(OP_HOT_SWAP, wasm_bytes)

    def close(self) -> None:
        """Close the underlying transport."""
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
