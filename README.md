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
