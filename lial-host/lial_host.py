"""
LIAL Host Orchestrator -- Connects to a LIAL Receiver (via serial or subprocess),
prompts an LLM to generate driver code, compiles it to wasm, and pushes it.

Usage:
    python lial_host.py --subprocess "cargo run --manifest-path ../lial-receiver/Cargo.toml -- --stdin"
    python lial_host.py --port /dev/tty.usbserial-XXX

Environment:
    LIAL_LLM_PROVIDER  = "openai" (default) | "anthropic"
    OPENAI_API_KEY     = your key
    ANTHROPIC_API_KEY  = your key
"""

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time

OP_DISCOVERY = 0x01
OP_BYTECODE_PUSH = 0x02
OP_EXEC_RESULT = 0x03

SYSTEM_PROMPT = """\
You are a firmware engineer writing Rust code for an embedded device via the LIAL framework.

The device exposes these syscalls (imported via `unsafe extern "C"`):
  fn lial_gpio_set(pin: u32, state: u32);
  fn lial_gpio_get(pin: u32) -> u32;
  fn lial_delay_ms(ms: u32);
  fn lial_get_uptime_us() -> u64;
  fn lial_i2c_transfer(addr: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
  fn lial_log(ptr: u32);

Rules:
- Write ONLY the function body. Do NOT write `extern` declarations, `#![no_std]`, or panic handlers -- those are provided by the compiler wrapper.
- Your code MUST include exactly one `#[unsafe(no_mangle)] pub extern "C" fn run_logic()` function.
- All syscall calls must be inside `unsafe {{ }}` blocks.
- Use only the syscalls listed above.
- Keep code minimal and correct. No standard library, no allocations.

Device manifest:
{manifest}
"""


class LIALLink:
    """Communicates with a LIAL Receiver over LIAL-Link v0.1 frames."""

    def __init__(self, proc=None, serial_port=None):
        self._proc = proc
        self._serial = serial_port

    @classmethod
    def from_subprocess(cls, cmd: str):
        import shlex
        proc = subprocess.Popen(
            shlex.split(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return cls(proc=proc)

    @classmethod
    def from_serial(cls, port: str, baud: int = 115200):
        import serial
        ser = serial.Serial(port, baud, timeout=30)
        return cls(serial_port=ser)

    def _read_exact(self, n: int) -> bytes:
        if self._proc:
            data = self._proc.stdout.read(n)
        elif self._serial:
            data = self._serial.read(n)
        else:
            raise RuntimeError("No transport configured")
        if len(data) < n:
            raise ConnectionError(f"Short read: expected {n} bytes, got {len(data)}")
        return data

    def _write(self, data: bytes):
        if self._proc:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        elif self._serial:
            self._serial.write(data)
        else:
            raise RuntimeError("No transport configured")

    def read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(5)
        opcode = header[0]
        length = struct.unpack(">I", header[1:5])[0]
        payload = self._read_exact(length) if length > 0 else b""
        return opcode, payload

    def write_frame(self, opcode: int, payload: bytes):
        header = bytes([opcode]) + struct.pack(">I", len(payload))
        self._write(header + payload)

    def read_discovery(self) -> dict:
        opcode, payload = self.read_frame()
        if opcode != OP_DISCOVERY:
            raise RuntimeError(f"Expected discovery (0x01), got 0x{opcode:02x}")
        return json.loads(payload)

    def push_bytecode(self, wasm_bytes: bytes):
        self.write_frame(OP_BYTECODE_PUSH, wasm_bytes)

    def receive_result(self) -> dict:
        opcode, payload = self.read_frame()
        if opcode != OP_EXEC_RESULT:
            raise RuntimeError(f"Expected result (0x03), got 0x{opcode:02x}")
        return json.loads(payload)

    def close(self):
        if self._proc:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        if self._serial:
            self._serial.close()

    def drain_stderr(self) -> str:
        """Read any available stderr (non-blocking) from subprocess."""
        if not self._proc or not self._proc.stderr:
            return ""
        import select
        result = []
        while select.select([self._proc.stderr], [], [], 0.0)[0]:
            chunk = self._proc.stderr.read(4096)
            if not chunk:
                break
            result.append(chunk.decode(errors="replace"))
        return "".join(result)


def generate_driver_openai(prompt: str, manifest: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(manifest=manifest)},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content


def generate_driver_anthropic(prompt: str, manifest: str) -> str:
    from anthropic import Anthropic
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=SYSTEM_PROMPT.format(manifest=manifest),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def generate_driver(prompt: str, manifest: str) -> str:
    provider = os.environ.get("LIAL_LLM_PROVIDER", "openai").lower()
    if provider == "anthropic":
        return generate_driver_anthropic(prompt, manifest)
    return generate_driver_openai(prompt, manifest)


def extract_rust_code(llm_response: str) -> str:
    """Extract Rust code from LLM response, stripping markdown fences."""
    patterns = [
        r"```rust\s*\n(.*?)```",
        r"```\s*\n(.*?)```",
    ]
    for pattern in patterns:
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            return match.group(1).strip()
    return llm_response.strip()


def main():
    parser = argparse.ArgumentParser(description="LIAL Host Orchestrator")
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--port", help="Serial port (e.g. /dev/tty.usbserial-XXX)")
    transport.add_argument("--subprocess", help="Receiver command to spawn as subprocess")
    transport.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    args = parser.parse_args()

    # Lazy import compiler
    sys.path.insert(0, os.path.dirname(__file__))
    from lial_compiler import compile_to_bytes

    print("Connecting to receiver...")
    if args.subprocess:
        link = LIALLink.from_subprocess(args.subprocess)
    else:
        link = LIALLink.from_serial(args.port, args.baud)

    try:
        manifest = link.read_discovery()
        print(f"Device: {manifest.get('device', 'unknown')}")
        print(f"Pins: {manifest.get('pins', [])}")
        print(f"Alphabet: {manifest.get('alphabet', [])}")
        manifest_str = json.dumps(manifest, indent=2)

        print("\nLIAL Host Ready. Type a task for the device (or 'quit' to exit).\n")

        while True:
            try:
                user_input = input("lial> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                break

            print("Asking LLM...")
            try:
                llm_response = generate_driver(user_input, manifest_str)
            except Exception as e:
                print(f"LLM error: {e}")
                continue

            code = extract_rust_code(llm_response)
            print(f"\n--- Generated Code ---\n{code}\n--- End Code ---\n")

            print("Compiling to wasm...")
            try:
                wasm_bytes = compile_to_bytes(code, lang="rust")
            except RuntimeError as e:
                print(f"Compilation failed: {e}")
                print("Retrying with LLM...\n")
                continue

            print(f"Compiled: {len(wasm_bytes)} bytes")
            print("Pushing to receiver...")
            link.push_bytecode(wasm_bytes)

            print("Waiting for result...")
            result = link.receive_result()

            stderr_output = link.drain_stderr()
            if stderr_output:
                print(f"\n--- Receiver Logs ---\n{stderr_output}--- End Logs ---")

            if result.get("ok"):
                print(f"Execution successful. Logs: {result.get('logs', [])}")
            else:
                print(f"Execution failed: {result.get('error', 'unknown')}")

            print()

    finally:
        link.close()


if __name__ == "__main__":
    main()
