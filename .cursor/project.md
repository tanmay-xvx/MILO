# LIAL: LLM IoT Abstraction Layer

**Version:** 0.1.0-Alpha

**Status:** Active Development (3-Week POC)

**Vision:** A secure, high-performance "Nervous System" for LLM agents to interact with physical hardware using JIT-compiled WebAssembly.

## Project Aim

LIAL solves the "Latency vs. Logic" problem in IoT. Instead of sending slow, individual commands over a network, LIAL allows an LLM to generate localized logic (drivers/scripts) on a Host, compile them to WebAssembly (Wasm), and ship them to an IoT device to run at the speed of silicon.

## Technical Architecture

### 1. The LIAL Host (The Brain)

- **Environment:** Python 3.10+ / LLVM Toolchain.
- **Role:** The Orchestrator (Maintained as a `.md`-based specification for Agent consumption). Manages the LLM relationship and the compilation pipeline.
- **Functions:**
  - **Context Injection:** Ingests the Hardware Manifest from the device and teaches the LLM its physical layout.
  - **JIT Compilation:** Converts LLM-generated strings (C/Rust) into `wasm32-unknown-unknown` binaries.
  - **Packaging:** Wraps binaries into LIAL-Link frames for transmission.

### 2. The LIAL-Link (The Nervous System)

- **Protocol:** Binary-first, stateless communication over UDP/BLE/Serial.
- **Serialization:** Uses CBOR (Concise Binary Object Representation) for minimal overhead.
- **OpCodes:**
  - `0x01`: Identity/Discovery (Device → Host).
  - `0x02`: Bytecode Push (Host → Device).
  - `0x03`: Execution Log/Feedback (Device → Host).

### 3. The LIAL Receiver (The Body)

- **Environment:** Rust (`no_std`) / `wasmi` interpreter.
- **Role:** The Execution Sandbox.
- **The "Atomic Alphabet":** A manually coded System Call Table mapping generic Wasm calls to physical hardware (GPIO, I2C, SPI, PWM).
- **Safety Guards:**
  - **Memory Isolation:** Bytecode cannot access host memory.
  - **Instruction Metering:** Prevents infinite loops via "Gas" limits.

## Requirements & Dependencies

### Host Machine (Laptop/Server)

- **Rust Toolchain:** `rustup`, `cargo`, `wasm32-unknown-unknown` target.
- **LLVM:** `clang` (version 15+ recommended) for C-based drivers.
- **Python:** `pyserial`, `cbor2`, `openai` / `anthropic` libraries.
- **WASI-SDK:** Required for standard C library support in Wasm.

### Receiver Machine (Target)

- **Hardware:** ESP32-C3/S3, STM32 (ARM Cortex-M), or Raspberry Pi.
- **Crates:** `wasmi` (with `default-features = false`), `embedded-hal`.

## Execution Flow

1. **Discovery:** Receiver sends a Hardware Manifest (e.g., "I have an LED on Pin 5").
2. **Prompting:** User asks: "Blink the light three times slowly."
3. **Generation:** Host sends Task + Manifest to the LLM. LLM writes a C/Rust function using the LIAL Alphabet.
4. **Compilation:** Host compiles that code into a tiny `.wasm` file (~400 bytes).
5. **Transfer:** Host pushes the bytecode via LIAL-Link.
6. **Execution:** Receiver instantiates the module, links it to physical GPIO, and runs logic locally.

## 3-Week Action Plan

### Week 1: Foundations & The "Alphabet"

- Build a Rust Receiver using `wasmi`.
- Manually link "Atomic Letters" (`gpio_set`, `delay_ms`).
- Successfully execute a local `.wasm` payload via `fs::read`.

### Week 2: Intelligence & The "Manifest"

- Build the Python Host orchestration.
- Implement the String → WASM JIT pipeline.
- Automate LLM prompting with hardware context.

### Week 3: Connectivity & Safety

- Implement LIAL-Link over UDP.
- Add Instruction Metering (Gas) to prevent device hangs.
- Full "User Prompt to Silicon Action" loop.

## Safety Constraints for Agents

- **No Direct Pointers:** Wasm modules must never have direct access to hardware memory addresses.
- **Agnosticism:** The Host must remain platform-agnostic; it only speaks "Wasm."
- **Minimalism:** The `lial_std` alphabet should be kept under 15 functions to ensure portability.
