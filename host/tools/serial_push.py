#!/usr/bin/env python3
"""Push a wasm binary to MILO receiver over serial and display the result."""

import sys
import struct
import time
import serial

OP_DISCOVERY = 0x01
OP_BYTECODE_PUSH = 0x02
OP_EXEC_RESULT = 0x03

def make_frame(opcode: int, payload: bytes) -> bytes:
    return struct.pack(">BI", opcode, len(payload)) + payload

def read_frame(ser: serial.Serial, timeout: float = 30.0) -> tuple[int, bytes]:
    deadline = time.time() + timeout
    header = b""
    while len(header) < 5:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out reading frame header (got {len(header)}/5 bytes: {header.hex()})")
        ser.timeout = remaining
        chunk = ser.read(5 - len(header))
        if not chunk:
            continue
        header += chunk

    opcode = header[0]
    payload_len = struct.unpack(">I", header[1:5])[0]

    payload = b""
    while len(payload) < payload_len:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out reading payload ({len(payload)}/{payload_len} bytes)")
        ser.timeout = remaining
        chunk = ser.read(payload_len - len(payload))
        if chunk:
            payload += chunk

    return opcode, payload

def main():
    if len(sys.argv) < 2:
        print("Usage: serial_push.py <wasm_file> [serial_port] [baud_rate]")
        sys.exit(1)

    wasm_path = sys.argv[1]
    port = sys.argv[2] if len(sys.argv) > 2 else "/dev/cu.usbmodem101"
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else 115200

    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()

    print(f"Loaded {len(wasm_bytes)} bytes of wasm from {wasm_path}")
    print(f"Connecting to {port} at {baud} baud...")

    ser = serial.Serial(port, baud, timeout=5)
    time.sleep(0.5)  # let the ESP32 boot

    # Drain any boot messages
    ser.reset_input_buffer()

    # Request discovery manifest
    print("Requesting discovery manifest...")
    ser.write(make_frame(OP_DISCOVERY, b""))
    ser.flush()
    try:
        opcode, payload = read_frame(ser, timeout=10.0)
        if opcode == OP_DISCOVERY:
            print(f"Discovery: {payload.decode('utf-8', errors='replace')}")
        else:
            print(f"Unexpected frame: opcode=0x{opcode:02x}, payload={payload[:100]}")
    except TimeoutError as e:
        print(f"No discovery frame received: {e}")
        print("Device may not be running MILO firmware. Proceeding anyway...")

    # Send bytecode push
    frame = make_frame(OP_BYTECODE_PUSH, wasm_bytes)
    print(f"Pushing {len(wasm_bytes)} bytes of wasm...")
    ser.write(frame)
    ser.flush()

    # Wait for execution result
    print("Waiting for execution result...")
    try:
        opcode, payload = read_frame(ser, timeout=120.0)
        if opcode == OP_EXEC_RESULT:
            print(f"Result: {payload.decode('utf-8', errors='replace')}")
        else:
            print(f"Unexpected response: opcode=0x{opcode:02x}")
    except TimeoutError as e:
        print(f"Timed out waiting for result: {e}")

    ser.close()

if __name__ == "__main__":
    main()
