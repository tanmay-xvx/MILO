#!/usr/bin/env python3
"""Hardware-in-the-loop (HIL) test harness for LIAL receiver firmware.

Compiles per-syscall Rust snippets to wasm, pushes them to a real ESP32-C3
(or any board running LIAL), collects the on-device log output, and asserts
against it. Designed to run against physical hardware; there is no mocking
here -- that's what `lial-receiver/tests/` covers.

Usage:
    python3 hil_test.py --port /dev/cu.usbmodem101           # run every test
    python3 hil_test.py --port /dev/cu.usbmodem101 -k pwm    # only tests with "pwm" in the name

Each test is a function in `lial-host/hil_tests/*.py` decorated with
`@hil_test`. The decorator registers the function; it receives a `HilTest`
instance with:
    hil.run(rust_body)       -> result dict from the device
    hil.assert_ok(result)    -> raise if device reports !ok
    hil.assert_log(result, substr)  -> raise if substr not in logs
"""

from __future__ import annotations

import argparse
import glob
import importlib
import inspect
import json
import os
import pathlib
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

import serial

_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from lial_compiler import compile_rust_to_wasm

OP_DISCOVERY = 0x01
OP_BYTECODE_PUSH = 0x02
OP_EXEC_RESULT = 0x03


# ── wire format helpers (duplicated from lial_host so this harness is self-contained) ──

def _make_frame(opcode: int, payload: bytes) -> bytes:
    return struct.pack(">BI", opcode, len(payload)) + payload


def _read_frame(ser: serial.Serial, timeout: float):
    deadline = time.time() + timeout
    buf = b""
    while len(buf) < 5:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError("header timeout")
        ser.timeout = max(0.01, left)
        chunk = ser.read(5 - len(buf))
        if chunk:
            buf += chunk

    opcode = buf[0]
    plen = struct.unpack(">I", buf[1:5])[0]

    payload = b""
    while len(payload) < plen:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError("payload timeout")
        ser.timeout = max(0.01, left)
        chunk = ser.read(plen - len(payload))
        if chunk:
            payload += chunk

    return opcode, payload


# ── registry ─────────────────────────────────────────────────────────────

@dataclass
class HilResult:
    ok: bool
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    raw: dict | None = None


class HilAssertionError(AssertionError):
    pass


class HilTest:
    def __init__(self, port: str, baud: int = 115200, exec_timeout: float = 30.0):
        self.port = port
        self.baud = baud
        self.exec_timeout = exec_timeout
        self._ser: serial.Serial | None = None
        self._manifest: dict | None = None

    def __enter__(self) -> "HilTest":
        self._ser = serial.Serial(self.port, self.baud, timeout=5)
        time.sleep(0.3)
        self._ser.reset_input_buffer()
        self._manifest = self._try_discovery()
        return self

    def __exit__(self, *_):
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    @property
    def manifest(self) -> dict | None:
        return self._manifest

    def _try_discovery(self) -> dict | None:
        try:
            opcode, payload = _read_frame(self._ser, timeout=3.0)
            if opcode == OP_DISCOVERY:
                return json.loads(payload)
        except (TimeoutError, json.JSONDecodeError):
            pass
        return None

    def run(self, rust_body: str, compile_timeout_s: float = 60.0) -> HilResult:
        """Compile a Rust snippet, push it, read back the result."""
        wasm = compile_rust_to_wasm(rust_body)
        self._ser.reset_input_buffer()
        self._ser.write(_make_frame(OP_BYTECODE_PUSH, wasm))
        self._ser.flush()

        opcode, payload = _read_frame(self._ser, timeout=self.exec_timeout)
        if opcode != OP_EXEC_RESULT:
            return HilResult(ok=False, error=f"unexpected opcode 0x{opcode:02x}")
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as e:
            return HilResult(ok=False, error=f"invalid result JSON: {e}")

        return HilResult(
            ok=bool(raw.get("ok")),
            logs=[str(x) for x in raw.get("logs", [])],
            error=raw.get("error"),
            raw=raw,
        )

    # ── assertions ────────────────────────────────────────────────────
    @staticmethod
    def assert_ok(result: HilResult) -> None:
        if not result.ok:
            raise HilAssertionError(f"device reported failure: {result.error!r}")

    @staticmethod
    def assert_log(result: HilResult, substring: str) -> None:
        if not any(substring in log for log in result.logs):
            raise HilAssertionError(
                f"expected log containing {substring!r}; got {result.logs!r}"
            )

    @staticmethod
    def assert_log_matching(result: HilResult, predicate: Callable[[str], bool], hint: str = "predicate") -> None:
        if not any(predicate(log) for log in result.logs):
            raise HilAssertionError(f"no log satisfied {hint}; got {result.logs!r}")


_TESTS: list[tuple[str, Callable[[HilTest], None]]] = []


def hil_test(fn: Callable[[HilTest], None]):
    """Decorator -- registers `fn` as a HIL test."""
    _TESTS.append((fn.__name__, fn))
    return fn


# ── runner ────────────────────────────────────────────────────────────────

def _discover_port() -> str | None:
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        m = glob.glob(pattern)
        if m:
            return m[0]
    return None


def _load_all_tests() -> None:
    tests_dir = pathlib.Path(_HOST_DIR) / "hil_tests"
    if not tests_dir.is_dir():
        return
    for f in sorted(tests_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        module_name = f"hil_tests.{f.stem}"
        importlib.import_module(module_name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="Serial port (auto-detected if omitted)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("-k", dest="filter", help="Only run tests whose name contains this substring")
    ap.add_argument("--list", action="store_true", help="List registered tests and exit")
    args = ap.parse_args()

    _load_all_tests()

    if args.list:
        for name, _ in _TESTS:
            print(name)
        return 0

    if not _TESTS:
        print("  no HIL tests registered; drop files into lial-host/hil_tests/", file=sys.stderr)
        return 1

    port = args.port or _discover_port()
    if not port:
        print("  no serial port found; use --port", file=sys.stderr)
        return 1

    print(f"  HIL: port={port} baud={args.baud}")

    passed = 0
    failed = 0
    skipped = 0

    with HilTest(port, args.baud) as hil:
        if hil.manifest:
            print(f"  device: {hil.manifest.get('board') or hil.manifest.get('device')}")
        else:
            print("  device: (no discovery frame received)")
        print()

        for name, fn in _TESTS:
            if args.filter and args.filter not in name:
                skipped += 1
                continue
            print(f"  -> {name} ...", end=" ", flush=True)
            try:
                fn(hil)
                print("PASS")
                passed += 1
            except HilAssertionError as e:
                print(f"FAIL ({e})")
                failed += 1
            except Exception:
                print("ERROR")
                traceback.print_exc()
                failed += 1

    print()
    print(f"  total: {passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    # When run as a script, Python loads this file as `__main__`. The
    # per-syscall scripts under `hil_tests/` do `from hil_test import ...`,
    # which reloads this file as a second module. The decorator then writes
    # to that second module's `_TESTS`, and `__main__` sees an empty list.
    # Alias ourselves in sys.modules so both names resolve to the same object.
    sys.modules.setdefault("hil_test", sys.modules[__name__])
    sys.exit(main())
