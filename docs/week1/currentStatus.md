# Week 1 Current Status

**Branch:** `week1` | **Started:** 2026-03-12 | **Updated:** 2026-03-15

---

## Completed

- Project architecture and spec finalized (`project.md` manifesto v1.0)
- **Receiver library refactored** with `LialHardware` trait, `LialRuntime<H>` generic executor, `LialError` enum
- All 6 Alphabet syscalls implemented: `gpio_set`, `gpio_get`, `delay_ms`, `get_uptime_us`, `i2c_transfer`, `log`
- Gas metering (fuel) fully working -- infinite loops are safely terminated
- `LaptopMock` backend: GPIO prints, real delays, Instant-based uptime, I2C stub, log capture
- `Esp32C3Hal` stub: structurally complete, pending wasmi atomics fix
- LIAL-Link v0.1 frame protocol: binary frames over stdin/stdout (and ready for serial)
- `--stdin` pipe mode: Receiver acts as a subprocess, host talks via LIAL-Link frames
- **4 integration tests passing:** happy_path, missing_export, fuel_exhaustion, bad_module
- 3 wasm test fixtures: mock_driver (blink), infinite_loop (gas test), no_export (missing export)
- **JIT compiler rewritten:** Rust cdylib pipeline generates ~500-byte wasm from source code
- **Host orchestrator created:** LIALLink class, LLM integration (OpenAI + Anthropic), CLI loop
- **Full laptop E2E verified:** Host compiles Rust -> wasm -> pushes via stdin pipe -> Receiver executes -> result returned
- ESP32-C3 toolchain installed: `riscv32imc-unknown-none-elf` target, `espup`, `espflash`

## Blocked

- **ESP32-C3 compilation:** wasmi 1.0.9 uses `alloc::sync::Arc` which requires atomics not available on riscv32imc. See wasmi-labs/wasmi#738. Resolution paths documented in `esp32c3.rs`.

## Deferred to Week 2

- Generic `embedded-hal` adapter (replaces per-board `LialHardware` impls)
- SVD/manifest auto-generation
- CBOR serialization (upgrading from raw length-prefixed frames)
- Multi-device support

## Architecture

```
Host (MacBook)                          Receiver (laptop-mock or ESP32-C3)
┌─────────────────────┐                ┌──────────────────────────────┐
│ lial_host.py        │                │ lial-receiver                │
│ ├─ LLM Prompter     │                │ ├─ LialRuntime<H>           │
│ ├─ lial_compiler.py │  LIAL-Link     │ ├─ LialHardware trait       │
│ └─ LIALLink class   │◄──(stdin)──────│ ├─ LaptopMock / Esp32C3Hal │
│                      │  0x01/02/03    │ └─ link.rs frame I/O       │
└─────────────────────┘                └──────────────────────────────┘
```
