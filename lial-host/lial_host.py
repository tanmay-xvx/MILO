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

# Ensure this directory is on the path before importing our own submodules.
_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from lial_commands import download as download_cmd
from lial_commands import init as init_cmd
from transport import (
    LialTransport,
    SerialTransport,
    TcpTransport,
    OP_DISCOVERY,
    OP_BYTECODE_PUSH,
    OP_EXEC_RESULT,
)

MAX_COMPILE_RETRIES = 2


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
- If a GPIO/PWM/ADC pin is NOT listed in `device_info`, log a message and
  do nothing -- do not fabricate pin numbers.
- For I2C: if the user explicitly names a device (e.g. SSD1306, BME280),
  use its well-known address even if `devices_present` is empty -- the boot
  scan can miss devices. Only bail if no I2C bus exists at all.

Rules:
- Write ONLY the function body. Do NOT write `extern` declarations, `#![no_std]`,
  or panic handlers -- those are injected by the compiler wrapper.
- Your code MUST contain exactly one function:
    #[unsafe(no_mangle)]
    pub extern "C" fn run_logic() {{ ... }}
  IMPORTANT: Use `#[unsafe(no_mangle)]`, NOT `#[no_mangle]`. The latter will
  not compile.
- All syscall calls must be in `unsafe {{ }}` blocks.
- To log a message:  let msg = b"hello"; lial_log(msg.as_ptr() as u32, msg.len() as u32);
- Use only the syscalls listed above. No std, no alloc, no other crates.
- Keep code minimal and correct.
- NEVER use `cfg!(feature = ...)` or `#[cfg(...)]` to check capabilities.
  There are no Cargo features in the Wasm driver. The `device_info` JSON
  below already tells you what peripherals are available -- just use them
  directly. If a peripheral is listed in `device_info`, it is available.
- To convert a u32 number to decimal digits for display or logging, manually
  extract each digit: `b'0' + ((value / divisor) % 10) as u8` for each
  place value. Do NOT use format!, to_string(), or any alloc-based method.

Memory constraints (CRITICAL):
- The Wasm module has only 64 KB total memory and an 8 KB stack.
- NEVER allocate arrays larger than ~200 bytes on the stack.
- To clear the SSD1306 screen, clear it one page at a time (8 pages, 128 cols
  each). For each page, send a cursor-set command, then 0x40 + 128 zero bytes
  (129 bytes total per page). Example:
    for page in 0..8 {{
        let cursor: [u8; 4] = [0x00, 0xB0 | page, 0x00, 0x10];
        lial_i2c_transfer(0x3C, cursor.as_ptr() as u32, 4, 0, 0);
        let zeros = [0u8; 129]; // zeros[0] should actually be 0x40
        // build: let mut row = [0u8; 129]; row[0] = 0x40;
        lial_i2c_transfer(0x3C, row.as_ptr() as u32, 129, 0, 0);
    }}
  NEVER use a single 1025-byte array -- it will crash.

SSD1306 OLED driver reference (use ONLY when an I2C device at 0x3C is present):

CRITICAL: The SSD1306 is a GRAPHICAL display with NO built-in character set.
Sending ASCII codes like b'A' (0x41) does NOT display the letter A. Each byte
after the 0x40 control byte represents 8 vertical pixels in one column.
To display a character you MUST send its 5-byte bitmap from the font table below.

Protocol:
- Commands:  prepend control byte 0x00 before command bytes.
- Data:      prepend control byte 0x40 before pixel data bytes.
- All transfers use lial_i2c_transfer(addr, tx_ptr, tx_len, 0, 0).

Init sequence (26 bytes, MUST send before any data writes or display will stay blank):
  [0x00, 0xAE, 0xD5,0x80, 0xA8,0x3F, 0xD3,0x00, 0x40,
   0x8D,0x14, 0x20,0x00, 0xA1, 0xC8, 0xDA,0x12,
   0x81,0xCF, 0xD9,0xF1, 0xDB,0x40, 0xA4, 0xA6, 0xAF]
  ALWAYS send this init sequence at the start of run_logic() when using the SSD1306.

CRITICAL: After sending the init sequence, you MUST clear the entire display before
writing any data. The display RAM contains random noise at power-on. If you skip this
step, the entire screen will show static/noise except the few bytes you write.
Clear the screen page by page (8 pages):
  for page in 0..8u8 {{
      let cursor: [u8; 4] = [0x00, 0xB0 | page, 0x00, 0x10];
      unsafe {{ lial_i2c_transfer(0x3C, cursor.as_ptr() as u32, 4, 0, 0); }}
      let mut row = [0u8; 129];
      row[0] = 0x40;
      unsafe {{ lial_i2c_transfer(0x3C, row.as_ptr() as u32, 129, 0, 0); }}
  }}
  This clears all 8 pages (1024 pixels). ALWAYS do this after init, before writing text.
  When updating the display in a loop (e.g. live ADC values), clear only the pages you
  write to, by sending 128 zero-bytes before re-drawing that page's content.

Set cursor to (page, col):
  [0x00, 0xB0 | page, col & 0x0F, 0x10 | (col >> 4)]

DIGIT FONT (use this EXACT array in your code for digits 0-9):
  const DIGIT_FONT: [[u8; 5]; 10] = [
      [0x3E,0x51,0x49,0x45,0x3E], // 0
      [0x00,0x42,0x7F,0x40,0x00], // 1
      [0x42,0x61,0x51,0x49,0x46], // 2
      [0x21,0x41,0x45,0x4B,0x31], // 3
      [0x18,0x14,0x12,0x7F,0x10], // 4
      [0x27,0x45,0x45,0x45,0x39], // 5
      [0x3C,0x4A,0x49,0x49,0x30], // 6
      [0x01,0x71,0x09,0x05,0x03], // 7
      [0x36,0x49,0x49,0x49,0x36], // 8
      [0x06,0x49,0x49,0x29,0x1E], // 9
  ];

LETTER FONT (use for A-Z):
  const LETTER_FONT: [[u8; 5]; 26] = [
      [0x7E,0x11,0x11,0x11,0x7E], // A
      [0x7F,0x49,0x49,0x49,0x36], // B
      [0x3E,0x41,0x41,0x41,0x22], // C
      [0x7F,0x41,0x41,0x22,0x1C], // D
      [0x7F,0x49,0x49,0x49,0x41], // E
      [0x7F,0x09,0x09,0x09,0x01], // F
      [0x3E,0x41,0x49,0x49,0x7A], // G
      [0x7F,0x08,0x08,0x08,0x7F], // H
      [0x00,0x41,0x7F,0x41,0x00], // I
      [0x20,0x40,0x41,0x3F,0x01], // J
      [0x7F,0x08,0x14,0x22,0x41], // K
      [0x7F,0x40,0x40,0x40,0x40], // L
      [0x7F,0x02,0x0C,0x02,0x7F], // M
      [0x7F,0x04,0x08,0x10,0x7F], // N
      [0x3E,0x41,0x41,0x41,0x3E], // O
      [0x7F,0x09,0x09,0x09,0x06], // P
      [0x3E,0x41,0x51,0x21,0x5E], // Q
      [0x7F,0x09,0x19,0x29,0x46], // R
      [0x46,0x49,0x49,0x49,0x31], // S
      [0x01,0x01,0x7F,0x01,0x01], // T
      [0x3F,0x40,0x40,0x40,0x3F], // U
      [0x1F,0x20,0x40,0x20,0x1F], // V
      [0x3F,0x40,0x38,0x40,0x3F], // W
      [0x63,0x14,0x08,0x14,0x63], // X
      [0x07,0x08,0x70,0x08,0x07], // Y
      [0x61,0x51,0x49,0x45,0x43], // Z
  ];

SPACE = [0x00,0x00,0x00,0x00,0x00]

HOW TO RENDER A NUMBER (e.g. ADC value 1234) on the OLED:
  1. Extract each digit: thousands=1, hundreds=2, tens=3, units=4
  2. Build a buffer: [0x40, ...DIGIT_FONT[1], 0x00, ...DIGIT_FONT[2], 0x00, ...DIGIT_FONT[3], 0x00, ...DIGIT_FONT[4]]
     That is 1 control byte + 4*(5 font bytes + 1 spacer) = 25 bytes.
  3. Set cursor, then send the buffer.

Concrete example -- display "42" at page 0, col 0:
  let cursor: [u8; 4] = [0x00, 0xB0, 0x00, 0x10];
  unsafe {{ lial_i2c_transfer(0x3C, cursor.as_ptr() as u32, 4, 0, 0); }}
  let data: [u8; 13] = [
      0x40,                           // control byte
      0x18,0x14,0x12,0x7F,0x10, 0x00, // '4' + spacer
      0x42,0x61,0x51,0x49,0x46, 0x00, // '2' + spacer
  ];
  unsafe {{ lial_i2c_transfer(0x3C, data.as_ptr() as u32, data.len() as u32, 0, 0); }}

HOW TO RENDER A STRING (e.g. "DONE") on the OLED:
  Look up each letter: D=[0x7F,0x41,0x41,0x22,0x1C], O=[0x3E,0x41,0x41,0x41,0x3E], etc.
  Build buffer: [0x40, ...D_font, 0x00, ...O_font, 0x00, ...N_font, 0x00, ...E_font]
  Set cursor, then send.

IMPORTANT RULES:
- NEVER send raw ASCII codes (b'0', b'A', etc.) as display data. They will show
  as garbage. ALWAYS use the 5-byte bitmaps from DIGIT_FONT / LETTER_FONT.
- Each rendered character is 6 columns wide (5 font + 1 spacer).
- 128 columns / 6 = max 21 characters per line.
- 8 pages = 8 lines. Use page 0-7.
- To render a u32 number, extract digits with (value / divisor) % 10, then
  index into DIGIT_FONT for each digit's 5-byte bitmap.
- Only use uppercase letters (convert lowercase to uppercase).

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

def _open_transport(args: argparse.Namespace) -> LialTransport | None:
    """Open the appropriate transport based on CLI flags."""
    transport_type = getattr(args, "transport", "serial")

    if transport_type == "wifi":
        ip = getattr(args, "ip", None)
        if not ip:
            from discovery import discover_mdns
            print("  Scanning for LIAL devices via mDNS …")
            devices = discover_mdns(timeout=3.0)
            if devices:
                dev = devices[0]
                ip = dev.host
                port = dev.port
                print(f"  Found: {dev.name} at {ip}:{port} ({dev.board})")
            else:
                print("  No devices found. Use --ip <address> to specify manually.")
                return None
        else:
            port = getattr(args, "tcp_port", 9100)
        print(f"  Connecting to {ip}:{port} over TCP …")
        return TcpTransport(ip, port)

    port = args.port or _detect_port()
    if not port:
        print("No serial port found. Plug in the ESP32, run `lial init`, or pass --port.")
        return None

    print(f"  Opening {port} @ {args.baud} baud …")
    return SerialTransport(port, args.baud)


def cmd_run(args: argparse.Namespace) -> int:
    from lial_compiler import compile_rust_to_wasm

    _maybe_autoflash(args.port, enabled=not args.no_autoflash)

    transport = _open_transport(args)
    if transport is None:
        return 1

    try:
        manifest = transport.request_discovery(timeout=5.0)
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

            print("  Pushing to device …")
            if hasattr(transport, "reset_input"):
                transport.reset_input()
            transport.write_frame(OP_BYTECODE_PUSH, wasm_bytes)
            print("  Running on device …")

            try:
                opcode, payload = transport.read_frame(timeout=120.0)
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
        transport.close()
    return 0


def _add_run_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("run", help="Interactive LLM -> wasm -> device loop (default)")
    p.add_argument("--port", help="Serial port (auto-detected if omitted)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--transport", choices=["serial", "wifi"], default="serial",
                   help="Transport type (default: serial)")
    p.add_argument("--ip", help="Device IP address (for --transport wifi)")
    p.add_argument("--tcp-port", type=int, default=9100, help="TCP port (default: 9100)")
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
