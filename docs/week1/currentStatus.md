# Week 1 Current Status

**Started:** 2026-03-12 | **Updated:** 2026-03-15

---

## Milestone Achieved

**Full end-to-end pipeline working on real hardware:**

Natural language → GPT-4o → Rust → wasm (618 bytes) → USB serial → ESP32-C3 → wasmi interpreter → GPIO5 LED blinks → JSON result returned to host.

## Completed

- Project architecture and spec finalized (`project.md` manifesto v1.0)
- **Receiver library** with `LialHardware` trait, `LialRuntime<H>` generic executor, `LialError` enum
- All 6 Alphabet syscalls: `gpio_set`, `gpio_get`, `delay_ms`, `get_uptime_us`, `i2c_transfer`, `log`
- Gas metering (1M fuel per execution) — infinite loops are safely terminated
- `LaptopMock` backend: GPIO prints, real delays, Instant-based uptime, I2C stub, log capture
- **`Esp32C3Hal` fully working**: real GPIO output, busy-wait delay, USB Serial JTAG I/O
- LIAL-Link v0.1 binary frame protocol over USB serial and stdin/stdout
- **4 integration tests passing**: happy_path, missing_export, fuel_exhaustion, bad_module
- 4 wasm test fixtures: mock_driver, blink_led, infinite_loop, no_export
- **JIT compiler**: Rust body → wasm with 64KB memory constraint and 4KB stack
- **Host orchestrator**: serial auto-detect, GPT-4o, compile-error retry (2 retries), clear status UX
- **wasmi patches**: 4 crates patched (Arc→Rc, Send+Sync removal, portable-atomic) enabling `riscv32imc-unknown-none-elf` build
- ESP32-C3 firmware: 902KB flash footprint, 200KB heap, boots in <1 second
- **Verified on real hardware**: ESP32-C3 (revision v0.4), LED on GPIO5, USB Serial JTAG at `/dev/cu.usbmodem101`

## Previously Blocked — Now Resolved

- ~~**ESP32-C3 compilation:** wasmi 1.0.9 uses `alloc::sync::Arc` which requires atomics not available on riscv32imc.~~ **Fixed** by patching wasmi, wasmi_core, wasmi_collections, and wasmparser in `patches/`.

## Deferred to Week 2

- Generic `embedded-hal` adapter (replaces per-board `LialHardware` impls)
- SVD/manifest auto-generation
- CBOR serialization (upgrading from raw length-prefixed frames)
- Multi-device support

## Architecture

```
Host (MacBook)                          Receiver (ESP32-C3)
┌─────────────────────┐                ┌──────────────────────────────┐
│ lial_host.py        │                │ lial-receiver (no_std)       │
│ ├─ GPT-4o           │                │ ├─ LialRuntime<Esp32C3Hal>  │
│ ├─ lial_compiler.py │  LIAL-Link     │ ├─ wasmi interpreter        │
│ └─ pyserial         │◄─(USB serial)─►│ ├─ 6 syscall bindings       │
│                      │  binary frames │ └─ UsbSerialJtag I/O        │
└─────────────────────┘                └──────────────────────────────┘
```
