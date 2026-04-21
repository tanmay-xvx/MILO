#!/usr/bin/env python3
"""
LIAL Host -- Interactive orchestrator that connects to an ESP32 running the
LIAL Receiver, asks an LLM to write firmware from natural language, compiles
it to wasm, pushes it over USB-serial, and reports the result.

Subcommands:
    run       (default) open serial port and enter the LLM prompt
    download  fetch LIAL receiver firmware for a board family
    init      auto-detect connected boards and flash LIAL firmware

Usage:
    export OPENAI_API_KEY=sk-...
    python lial_host.py                                # auto-detect + run
    python lial_host.py run --port /dev/cu.usbmodem101
    python lial_host.py download --board esp32c3
    python lial_host.py init --dry-run
"""

import argparse
import glob
import json
import os
import re
import struct
import sys
import time

import serial

# Ensure this directory is on the path before importing our own submodules.
_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from lial_commands import download as download_cmd
from lial_commands import init as init_cmd

# ── LIAL-Link protocol ─────────────────────────────────────────────────
OP_DISCOVERY = 0x01
OP_BYTECODE_PUSH = 0x02
OP_EXEC_RESULT = 0x03

MAX_COMPILE_RETRIES = 2


def _make_frame(opcode: int, payload: bytes) -> bytes:
    return struct.pack(">BI", opcode, len(payload)) + payload


def _read_frame(ser: serial.Serial, timeout: float = 30.0):
    """Read one LIAL-Link frame.  Returns (opcode, payload) or raises."""
    deadline = time.time() + timeout
    buf = b""
    while len(buf) < 5:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError(f"header timeout ({len(buf)}/5 bytes)")
        ser.timeout = left
        chunk = ser.read(5 - len(buf))
        if chunk:
            buf += chunk

    opcode = buf[0]
    plen = struct.unpack(">I", buf[1:5])[0]

    payload = b""
    while len(payload) < plen:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError(f"payload timeout ({len(payload)}/{plen})")
        ser.timeout = left
        chunk = ser.read(plen - len(payload))
        if chunk:
            payload += chunk

    return opcode, payload


# ── LLM integration ────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a firmware engineer writing Rust code for an embedded device via the LIAL framework.

The device exposes these syscalls (imported via `unsafe extern "C"`):
  fn lial_gpio_set(pin: u32, state: u32);   // 1 = HIGH, 0 = LOW
  fn lial_gpio_get(pin: u32) -> u32;        // returns 1 or 0
  fn lial_delay_ms(ms: u32);                // blocking delay
  fn lial_get_uptime_us() -> u64;           // microseconds since boot
  fn lial_i2c_transfer(addr: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
  fn lial_log(ptr: u32, len: u32);          // log a UTF-8 message (pointer + byte length)
  fn lial_pwm_set(channel: u32, duty_0_10000: u32);   // duty in 1/10000ths (0 = 0%, 10000 = 100%)
  fn lial_adc_read(channel: u32) -> u32;    // returns raw ADC value (0..resolution)
  fn lial_spi_transfer(bus: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
  fn lial_uart_write(bus: u32, ptr: u32, len: u32) -> i32;
  fn lial_uart_read(bus: u32, ptr: u32, len_max: u32, timeout_ms: u32) -> i32;

Capability-aware pin/channel/bus selection:
- The `device_info` JSON below is the *source of truth* for what this physical
  board supports. Read it carefully before you pick any pin, channel, or bus.
- Only use IDs that appear in:
    capabilities.gpio.pins        -> valid `pin` arg for lial_gpio_set/lial_gpio_get
    capabilities.pwm.pins         -> valid `channel` arg for lial_pwm_set
    capabilities.adc.pins         -> valid `channel` arg for lial_adc_read
    capabilities.i2c[*].bus_id    -> valid I2C bus (today the ABI still uses
                                     addr only, but bus_id tells you wiring)
    capabilities.i2c[*].devices_present
                                  -> 7-bit addresses that ACK'd the boot scan;
                                     prefer these over guessed addresses
    capabilities.spi[*].bus_id    -> valid `bus` arg for lial_spi_transfer
    capabilities.uart[*].bus_id   -> valid `bus` arg for lial_uart_write / read
- Unknown IDs are silently ignored on-device (they do nothing), so a driver
  that picks an unlisted pin will appear to run but achieve nothing. Always
  verify against `device_info` first.
- For PWM, always pass `duty_0_10000` in 1/10000ths (so 50% is 5000). The
  receiver rescales to the board's actual timer resolution
  (`capabilities.pwm.resolution_bits`).
- For ADC, raw values range 0..(2^resolution_bits - 1). Convert to millivolts
  with: `mv = raw * vref_mv / ((1 << resolution_bits) - 1)` if needed.
- If the requested peripheral is NOT present in `device_info`, respond with
  a `lial_log` message stating the requirement and do nothing else -- do not
  fabricate pin numbers.

Rules:
- Write ONLY the function body. Do NOT write `extern` declarations, `#![no_std]`,
  or panic handlers -- those are injected by the compiler wrapper.
- Your code MUST contain exactly one function:
    #[unsafe(no_mangle)]
    pub extern "C" fn run_logic() {{ ... }}
- All syscall calls must be in `unsafe {{ }}` blocks.
- To log a message:  let msg = b"hello"; lial_log(msg.as_ptr() as u32, msg.len() as u32);
- Use only the syscalls listed above. No std, no alloc, no other crates.
- Keep code minimal and correct.

Device info:
{device_info}
"""


def _call_openai(messages: list[dict]) -> str:
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.2,
    )
    return resp.choices[0].message.content


def _extract_rust(text: str) -> str:
    """Pull the Rust code out of an LLM response, stripping markdown fences."""
    for pat in [r"```rust\s*\n(.*?)```", r"```\s*\n(.*?)```"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text.strip()


# ── Serial helpers ──────────────────────────────────────────────────────

def _detect_port() -> str | None:
    """Try to find the ESP32 USB-serial-JTAG port."""
    for pattern in ["/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"]:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def _try_read_discovery(ser: serial.Serial, timeout=3.0):
    """Attempt to read a discovery frame; returns dict or None."""
    try:
        opcode, payload = _read_frame(ser, timeout=timeout)
        if opcode == OP_DISCOVERY:
            return json.loads(payload)
    except (TimeoutError, json.JSONDecodeError):
        pass
    return None


def _maybe_autoflash(port: str | None, enabled: bool) -> None:
    """Run `lial init --yes` on startup if we see a blank board of a known family.

    Only runs when there is at least one candidate blank device and `--port` is
    not pinned to an already-LIAL device.
    """
    if not enabled:
        return
    try:
        from board_registry import enumerate_usb_devices
    except ImportError:
        return

    devices = enumerate_usb_devices()
    if port:
        devices = [d for d in devices if d.port == port]

    blank = [d for d in devices if d.candidate_families and not init_cmd._probe_lial_firmware(d.port)]
    if not blank:
        return

    print("  detected blank board(s); running `lial init` ...")
    ns = argparse.Namespace(
        port=port,
        yes=False,
        manifest_url=download_cmd.DEFAULT_MANIFEST_URL,
        cache_dir=str(download_cmd.DEFAULT_CACHE_DIR),
        dry_run=False,
    )
    init_cmd.run(ns)


# ── `run` subcommand (interactive LLM loop) ─────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    from lial_compiler import compile_rust_to_wasm

    _maybe_autoflash(args.port, enabled=not args.no_autoflash)

    port = args.port or _detect_port()
    if not port:
        print("No serial port found. Plug in the ESP32, run `lial init`, or pass --port.")
        return 1

    print(f"  Opening {port} @ {args.baud} baud …")
    ser = serial.Serial(port, args.baud, timeout=5)
    time.sleep(0.3)
    ser.reset_input_buffer()

    try:
        manifest = _try_read_discovery(ser, timeout=3.0)
        if manifest:
            device = manifest.get("device") or manifest.get("board") or "unknown"
            pins = manifest.get("pins") or manifest.get("capabilities", {}).get("gpio", {}).get("pins", [])
            print(f"  Device : {device}")
            print(f"  Pins   : {pins}")
        else:
            manifest = {
                "device": "esp32c3",
                "pins": [5],
                "buses": {"i2c": []},
                "memory_kb": 320,
            }
            print("  Device : esp32c3 (discovery missed, using defaults)")
            print("  Pins   : [5]")

        device_info = json.dumps(manifest, indent=2)
        system_msg = {"role": "system", "content": SYSTEM_PROMPT.format(device_info=device_info)}

        print()
        print("  LIAL Host  ·  type a task, hit enter")
        print()

        history: list[dict] = [system_msg]

        while True:
            try:
                user_input = input("  you → ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Bye.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break

            history = [system_msg, {"role": "user", "content": user_input}]

            print("  Generating code …")
            try:
                raw = _call_openai(history)
            except Exception as e:
                print(f"  LLM error: {e}\n")
                continue

            history.append({"role": "assistant", "content": raw})
            code = _extract_rust(raw)
            print()
            print("  ┌─ Generated Code ─────────────────────────────")
            for line in code.splitlines():
                print(f"  │ {line}")
            print("  └─────────────────────────────────────────────")
            print()

            wasm_bytes = None
            for attempt in range(1 + MAX_COMPILE_RETRIES):
                print(f"  Compiling to wasm …" + (f" (retry {attempt})" if attempt else ""))
                try:
                    wasm_bytes = compile_rust_to_wasm(code)
                    break
                except RuntimeError as e:
                    error_text = str(e)
                    short = "\n".join(error_text.strip().splitlines()[-20:])
                    print(f"  Compile error:\n{short}\n")

                    if attempt < MAX_COMPILE_RETRIES:
                        print("  Asking LLM to fix …")
                        fix_msg = (
                            f"The code failed to compile. Here is the error:\n\n"
                            f"```\n{error_text}\n```\n\n"
                            f"Please output the corrected complete function body."
                        )
                        history.append({"role": "user", "content": fix_msg})
                        try:
                            raw = _call_openai(history)
                        except Exception as ex:
                            print(f"  LLM error: {ex}\n")
                            break
                        history.append({"role": "assistant", "content": raw})
                        code = _extract_rust(raw)
                        print()
                        print("  ┌─ Revised Code ──────────────────────────────")
                        for line in code.splitlines():
                            print(f"  │ {line}")
                        print("  └─────────────────────────────────────────────")
                        print()

            if wasm_bytes is None:
                print("  Could not compile after retries. Try rephrasing.\n")
                continue

            print(f"  Compiled OK — {len(wasm_bytes)} bytes")

            print("  Pushing to ESP32 …")
            ser.reset_input_buffer()
            ser.write(_make_frame(OP_BYTECODE_PUSH, wasm_bytes))
            ser.flush()
            print("  Running on device …")

            try:
                opcode, payload = _read_frame(ser, timeout=30.0)
                if opcode == OP_EXEC_RESULT:
                    result = json.loads(payload)
                    if result.get("ok"):
                        logs = result.get("logs", [])
                        print("  ✓ Execution finished.")
                        if logs:
                            print(f"  Device logs: {logs}")
                    else:
                        print(f"  ✗ Device error: {result.get('error', 'unknown')}")
                else:
                    print(f"  Unexpected frame: opcode=0x{opcode:02x}")
            except TimeoutError:
                print("  Timed out waiting for device response.")

            print()
    finally:
        ser.close()
    return 0


def _add_run_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("run", help="Interactive LLM -> wasm -> device loop (default)")
    p.add_argument("--port", help="Serial port (auto-detected if omitted)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--no-autoflash", action="store_true", help="Skip startup board detection / flashing")
    p.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LIAL Host")
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_run_parser(subparsers)
    download_cmd.add_parser(subparsers)
    init_cmd.add_parser(subparsers)

    # Treat `lial_host.py` with no subcommand (or only flags) as `run` for
    # backward compat with pre-subcommand invocation.
    argv_list = list(sys.argv[1:] if argv is None else argv)
    known_subs = {"run", "download", "init"}
    top_level_help = {"-h", "--help"}
    if not argv_list or (
        argv_list[0] not in known_subs and argv_list[0] not in top_level_help
    ):
        argv_list = ["run", *argv_list]

    args = parser.parse_args(argv_list)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
