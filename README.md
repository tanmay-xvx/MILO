# LIAL (LLM IoT Abstraction Layer)
> "Silicon as a Service for Agentic Systems"

LIAL is a high-performance, sandboxed hardware abstraction layer that allows LLMs 
to interact with any device using Just-In-Time compiled WebAssembly (Wasm).

## Core Philosophy
1. **Generic Primitives:** We don't code "TurnOnLight"; we code `gpio_write`.
2. **Execution Locality:** Logic runs on the silicon, not in the cloud.
3. **Safety First:** Wasm provides a memory-safe, metered sandbox.

## Current Specs
- **Runtime:** Wasm3 (C++)
- **Transport:** LIAL-Link (CBOR over UDP/BLE)
- **Compiler:** LLVM / Clang (Target: wasm32-wasi)

## 🛠️ Development & Simulation

This project uses **WebAssembly (WASM)** and **WASI** to provide a hardware-agnostic abstraction layer. To simulate the driver execution on a local machine:

### Prerequisites
- **Rust Toolchain**: `rustup target add wasm32-wasip1`
- **Wasmtime Runtime**: [Install here](https://wasmtime.dev/)

### Build the Driver
Compile the Rust source into a WASI-compliant WebAssembly module:
```bash
cargo build --target wasm32-wasip1
```
## Run the Simulation
 - Execute the compiled module using the wasmtime runtime to simulate the LIAL Receiver:
```bash
wasmtime target/wasm32-wasip1/debug/lial-pulse-driver.wasm
```