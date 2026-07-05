#!/usr/bin/env python3
"""
MILO Host -- Interactive orchestrator that connects to an MCU running the
MILO Receiver, asks an LLM to write firmware from natural language, compiles
it to wasm, pushes it over USB-serial, and reports the result.

Subcommands:
    run       (default) open serial port and enter the LLM prompt
    download  fetch MILO receiver firmware for a board family
    init      auto-detect connected boards and flash MILO firmware

Usage:
    export OPENAI_API_KEY=sk-...
    python cli.py                                      # auto-detect + run
    python cli.py run --port /dev/cu.usbmodem101
    python cli.py download --board esp32c3
    python cli.py init --dry-run
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

from flash import download as download_cmd
from flash import init_cmd
from core.transport import (
    MiloTransport,
    SerialTransport,
    SubprocessTransport,
    TcpTransport,
    OP_DISCOVERY,
    OP_BYTECODE_PUSH,
    OP_EXEC_RESULT,
)

MAX_COMPILE_RETRIES = 2


# ── LLM integration ────────────────────────────────────────────────────

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "system_prompt.txt")
with open(_PROMPT_PATH) as _f:
    SYSTEM_PROMPT = _f.read()


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
    """Run `milo init` on startup if we see a blank board of a known family.

    Only runs when there is at least one candidate blank device and `--port` is
    not pinned to an already-MILO device.
    """
    if not enabled:
        return
    try:
        from devices.boards import enumerate_usb_devices
    except ImportError:
        return

    devices = enumerate_usb_devices()
    if port:
        devices = [d for d in devices if d.port == port]

    blank = [d for d in devices if d.candidate_families and not init_cmd._probe_milo_firmware(d.port)]
    if not blank:
        return

    print("  detected blank board(s); running `milo init` ...")
    ns = argparse.Namespace(
        port=port,
        yes=False,
        manifest_url=download_cmd.DEFAULT_MANIFEST_URL,
        cache_dir=str(download_cmd.DEFAULT_CACHE_DIR),
        dry_run=False,
    )
    init_cmd.run(ns)


# ── `run` subcommand (interactive LLM loop) ─────────────────────────────

def _open_transport(args: argparse.Namespace) -> MiloTransport | None:
    """Open the appropriate transport based on CLI flags."""
    subprocess_cmd = getattr(args, "subprocess", None)
    if subprocess_cmd:
        print(f"  Spawning receiver subprocess: {subprocess_cmd}")
        return SubprocessTransport(subprocess_cmd)

    transport_type = getattr(args, "transport", "serial")

    if transport_type == "wifi":
        ip = getattr(args, "ip", None)
        if not ip:
            from devices.discovery import discover_mdns
            print("  Scanning for MILO devices via mDNS …")
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
        print("No serial port found. Plug in the device, run `milo init`, or pass --port.")
        return None

    print(f"  Opening {port} @ {args.baud} baud …")
    return SerialTransport(port, args.baud)


def cmd_run(args: argparse.Namespace) -> int:
    from core.compiler import compile_rust_to_wasm

    _maybe_autoflash(
        args.port,
        enabled=not args.no_autoflash and not getattr(args, "subprocess", None),
    )

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
        print("  MILO Host  ·  type a task, hit enter")
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
    p.add_argument("--subprocess", metavar="CMD",
                   help='Talk to a local receiver process instead of hardware, '
                        'e.g. --subprocess "../receiver/target/debug/milo-receiver --stdin"')
    p.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    from core import keygen_cmd

    parser = argparse.ArgumentParser(description="MILO Host")
    subparsers = parser.add_subparsers(dest="subcommand")
    _add_run_parser(subparsers)
    download_cmd.add_parser(subparsers)
    init_cmd.add_parser(subparsers)
    keygen_cmd.add_parser(subparsers)

    # Treat `cli.py` with no subcommand (or only flags) as `run` for
    # backward compat with pre-subcommand invocation.
    argv_list = list(sys.argv[1:] if argv is None else argv)
    known_subs = {"run", "download", "init", "keygen"}
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
