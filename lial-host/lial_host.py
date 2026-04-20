#!/usr/bin/env python3
"""
LIAL Host -- Interactive orchestrator that connects to an ESP32 running the
LIAL Receiver, asks an LLM to write firmware from natural language, compiles
it to wasm, pushes it over USB-serial, and reports the result.

Usage:
    export OPENAI_API_KEY=sk-...
    python lial_host.py                          # auto-detect serial port
    python lial_host.py --port /dev/cu.usbmodem101
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


# ── Main loop ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LIAL Host")
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    # Locate compiler
    host_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, host_dir)
    from lial_compiler import compile_rust_to_wasm

    # Resolve serial port
    port = args.port or _detect_port()
    if not port:
        print("No serial port found. Plug in the ESP32 or pass --port.")
        sys.exit(1)

    print(f"  Opening {port} @ {args.baud} baud …")
    ser = serial.Serial(port, args.baud, timeout=5)
    time.sleep(0.3)
    ser.reset_input_buffer()

    # Device manifest -- try reading discovery, fall back to hardcoded
    manifest = _try_read_discovery(ser, timeout=3.0)
    if manifest:
        device = manifest.get("device", "unknown")
        pins = manifest.get("pins", [])
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
    print("╔══════════════════════════════════════════════════════╗")
    print("║          LIAL Host  ·  type a task, hit enter       ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Conversation history (keeps context for retries)
    history: list[dict] = [system_msg]

    try:
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

            # Fresh conversation per task (keep system prompt)
            history = [system_msg, {"role": "user", "content": user_input}]

            # ── Step 1: ask LLM ─────────────────────────────────
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

            # ── Step 2: compile (with retry) ────────────────────
            wasm_bytes = None
            for attempt in range(1 + MAX_COMPILE_RETRIES):
                print(f"  Compiling to wasm …" + (f" (retry {attempt})" if attempt else ""))
                try:
                    wasm_bytes = compile_rust_to_wasm(code)
                    break
                except RuntimeError as e:
                    error_text = str(e)
                    # Only show the last ~20 lines of compiler output
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

            # ── Step 3: push to device ──────────────────────────
            print("  Pushing to ESP32 …")
            ser.reset_input_buffer()
            ser.write(_make_frame(OP_BYTECODE_PUSH, wasm_bytes))
            ser.flush()
            print("  Running on device …")

            # ── Step 4: wait for result ─────────────────────────
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


if __name__ == "__main__":
    main()
