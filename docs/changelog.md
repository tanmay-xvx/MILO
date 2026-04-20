# LIAL Changelog

All notable changes to this project are documented in this file.
Format follows a simplified [Keep a Changelog](https://keepachangelog.com/) style.

---

## [Week 1 — ESP32-C3 End-to-End] — 2026-03-15

### Added

- **ESP32-C3 firmware working end-to-end.** Full pipeline verified: natural language → GPT-4o → Rust → wasm (618 bytes) → USB serial → ESP32-C3 wasmi interpreter → GPIO5 LED blinks → JSON result returned to host.
- **wasmi patches** (`patches/`) — Local forks of 4 crates enabling wasmi on `riscv32imc-unknown-none-elf`:
  - `wasmi`: `Arc`→`Rc`, all `Send+Sync` bounds removed, `portable-atomic` for `AtomicU32`.
  - `wasmi_core`: `Arc`→`Rc` in `fuel.rs` and `func_type.rs`.
  - `wasmi_collections`: `Arc<str>`→`Rc<str>` in string interner.
  - `wasmparser`: `Arc`→`Rc`, `core::sync::atomic`→`portable_atomic`.
- **`Esp32C3Hal`** (`esp32c3.rs`) — Real hardware implementation using `esp-hal` 1.0: GPIO output, busy-wait delay, `embedded_io::Read/Write` generic over USB Serial JTAG.
- **Dual-target `main.rs`** — `#[esp_hal::main]` for ESP32-C3 (`no_std`), standard `fn main()` for laptop. Controlled by `esp32c3` / `std` feature flags.
- **`esp-bootloader-esp-idf`** integration — `esp_app_desc!()` macro for ESP-IDF bootloader compatibility.
- **`blink_led` test driver** — 618-byte wasm binary that blinks GPIO5 five times, memory-constrained to 1 page (64KB).
- **`serial_push.py`** — Low-level Python tool for pushing pre-built wasm to the ESP32 over serial.
- **`lial_host.py` rewrite** — Interactive CLI with serial auto-detection, GPT-4o integration, compile-error retry loop (up to 2 retries), clear status messages during push and execution.
- **`lial_compiler.py` rewrite** — Generates memory-constrained wasm (64KB initial/max, 4KB stack). Fixed `lial_log` signature to `(ptr: u32, len: u32)`.
- **`.cargo/config.toml`** for ESP32-C3 — `portable_atomic_unsafe_assume_single_core`, `-Tlinkall.x` linker script, `build-std = ["core", "alloc"]`.

### Changed

- **`lial_log` signature** — Changed from `(ptr: u32)` (null-terminated) to `(ptr: u32, len: u32)` (pointer + length) across receiver, compiler, and system prompt.
- **`LialRuntime`** — Added `into_hardware()` method to recover the `LialHardware` impl after execution, enabling the ESP32 main loop to reuse peripherals.
- **`Cargo.toml`** — Added `embedded-io`, `portable-atomic`, `esp-bootloader-esp-idf` dependencies. `[patch.crates-io]` section for all 4 patched crates.

### Fixed

- **ESP32-C3 wasmi build** — Resolved the `alloc::sync::Arc` atomics blocker by patching wasmi, wasmi_core, wasmi_collections, and wasmparser to use `Rc` and `portable-atomic`.
- **Wasm memory overflow on ESP32** — Driver wasm binaries previously requested 17 pages (1MB+) of linear memory. Now constrained to 1 page (64KB) via linker flags in the compiler.
- **Serial protocol corruption** — `Esp32C3Hal::log()` was writing raw text to the same serial line used for binary LIAL-Link frames. Fixed by making `log()` a no-op on ESP32 (messages are captured in `HostState.logs` and returned in the result frame).

---

## [Week 1 — Hardware Pivot] — 2026-03-15

### Added

- **Receiver library** (`lib.rs`) with `LialHardware` trait (6 syscalls), `LialRuntime<H>` generic executor, `LialError` enum, gas metering via wasmi fuel.
- **LaptopMock** (`mock.rs`) — Full `LialHardware` implementation for laptop development.
- **LIAL-Link v0.1** (`link.rs`) — Binary frame protocol: `[opcode: u8][len: u32 BE][payload]`, OpCodes 0x01-0x03.
- **stdin pipe mode** — Receiver subprocess communicates via LIAL-Link frames on stdin/stdout.
- **Integration tests** — 4 tests covering happy path, missing export, fuel exhaustion, bad module.
- **Test fixtures** — `infinite_loop` (gas test), `no_export` (missing export test).

### Changed

- **Receiver main.rs** — Rewritten as thin CLI wrapper: `--fuel N`, `<wasm_path>`, `--stdin` modes.
- **Cargo.toml** — Feature flags (`std`, `esp32c3`), optional esp-hal/esp-alloc dependencies.

### Removed

- `lial_std.rs` — Dead code replaced by `LialHardware` trait architecture.

---

## [Unreleased] — 2026-03-12

### Fixed

- **wasmi API mismatch in receiver** — Replaced non-existent two-step `linker.instantiate()` + `.start()` with `linker.instantiate_and_start()` to match `wasmi` 1.0.9 API.
- **mock_driver.wasm wrong target** — Replaced 1.4 MB `wasm32-wasip1` binary with a 600-byte `wasm32-unknown-unknown` cdylib build.
- **Rust 2024 edition compatibility** — Updated examples to use `unsafe extern "C"` blocks and `#[unsafe(no_mangle)]`.

### Milestone

First successful end-to-end "Handshake": Receiver loads a 600-byte wasm module, links Alphabet syscalls, and executes a 3-cycle GPIO blink loop on a laptop.

---

## [Day 1] — 2026-03-11

### Added

- Initial project architecture, `lial_std.h` header, `lial_compiler.py` JIT compiler, `lial-pulse-driver/` placeholder, `.gitignore`, `README.md`.
