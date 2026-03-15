# Week 1 Changelog

Changes made during Week 1 development.

---

## ESP32-C3 End-to-End — 2026-03-15

### Added

- **wasmi patches** — Local forks of 4 crates in `patches/` enabling wasmi on `riscv32imc-unknown-none-elf`:
  - `wasmi`: `Arc`→`Rc`, all `Send+Sync` bounds removed, `portable-atomic` for `AtomicU32`.
  - `wasmi_core`: `Arc`→`Rc` in `fuel.rs` and `func_type.rs`.
  - `wasmi_collections`: `Arc<str>`→`Rc<str>` in string interner.
  - `wasmparser`: `Arc`→`Rc`, `core::sync::atomic`→`portable_atomic`.
- **`Esp32C3Hal`** — Real hardware implementation: GPIO output via `esp_hal::gpio::Output`, busy-wait delay via `esp_hal::time::Instant`, generic over `embedded_io::Read/Write` for USB Serial JTAG.
- **Dual-target `main.rs`** — `#[esp_hal::main]` for ESP32-C3, standard `fn main()` for laptop. `#![no_std]` / `#![no_main]` gated behind `esp32c3` feature.
- **`esp-bootloader-esp-idf`** — `esp_app_desc!()` macro for ESP-IDF bootloader image format.
- **`blink_led` test driver** — 618-byte wasm binary, 64KB memory, blinks GPIO5 five times.
- **`serial_push.py`** — Python tool for pushing pre-built wasm over USB serial.
- **`lial_host.py` rewrite** — Interactive CLI: serial auto-detect, GPT-4o, compile-error retry (2 attempts), progress messages during compile/push/execute.
- **`lial_compiler.py` rewrite** — Rust body → wasm with `--initial-memory=65536 --max-memory=65536 -z stack-size=4096`.
- **`.cargo/config.toml`** — `portable_atomic_unsafe_assume_single_core`, `-Tlinkall.x`, `build-std = ["core", "alloc"]`.
- **`LialRuntime::into_hardware()`** — Returns the `LialHardware` impl so the ESP32 main loop can reuse peripherals.

### Changed

- **`lial_log` signature** — `(ptr: u32)` → `(ptr: u32, len: u32)` across receiver `lib.rs`, compiler template, system prompt, and blink_led example.
- **`Cargo.toml`** — Added `embedded-io`, `portable-atomic`, `esp-bootloader-esp-idf`. `[patch.crates-io]` for 4 patched crates. `esp-hal` now includes `"unstable"` feature for UsbSerialJtag.
- **`Esp32C3Hal::log()`** — Changed to no-op to prevent raw text from corrupting the binary LIAL-Link protocol on the serial line. Logs are captured in `HostState.logs` and returned in the JSON result frame.

### Fixed

- **wasmi atomics blocker** — Patched all 4 crates to eliminate `alloc::sync::Arc` and `core::sync::atomic` CAS operations, using `alloc::rc::Rc` and `portable_atomic` instead.
- **Wasm memory overflow** — Default Rust wasm binaries request 17 pages (1.1MB) of linear memory. Constrained to 1 page (64KB) via linker flags.
- **Serial protocol corruption** — `log()` was writing plain text to USB serial during wasm execution, breaking LIAL-Link frame parsing on the host side.
- **Linker script resolution** — ESP32-C3 build needed `-Tlinkall.x` in rustflags (not `-Tlink.x` from riscv-rt which expects `REGION_TEXT`).

### Verified

- ESP32-C3 (revision v0.4), 4MB flash, `riscv32imc-unknown-none-elf`
- Firmware: 902KB flash footprint (21.8%)
- Heap: 200KB for wasmi runtime
- Wasm execution: blink_led driver (5 cycles × 500ms = 5s), result returned as `{"ok":true,"logs":["Blinking LED on GPIO 5"]}`

---

## Hardware Pivot Implementation — 2026-03-15

### Added

- **`lial-receiver/src/lib.rs`** — Core library: `LialHardware` trait, `LialRuntime<H>`, `LialError`, `HostState<H>`, gas metering.
- **`lial-receiver/src/mock.rs`** — `LaptopMock` with GPIO print, `thread::sleep` delay, `Instant` uptime, stderr logging.
- **`lial-receiver/src/link.rs`** — LIAL-Link v0.1: `Frame`, `read_frame`/`write_frame`, `LinkError`.
- **`lial-receiver/tests/integration.rs`** — 4 tests: happy_path, missing_export, fuel_exhaustion, bad_module.
- **Test fixtures** — `infinite_loop`, `no_export`.
- **Host orchestrator** and **JIT compiler** initial versions.

### Changed

- **`main.rs`** — Rewritten as CLI wrapper: `--fuel N`, `<wasm_path>`, `--stdin`.
- **`mock.rs`** — All output via `eprintln!` to keep stdout clean for binary protocol.

### Removed

- **`lial_std.rs`** — Dead code replaced by `LialHardware` trait.

---

## Pre-Week-1 Baseline (carried from `dev`)

### Fixed

- wasmi API mismatch (`instantiate_and_start()`).
- mock_driver.wasm wrong target (1.4MB wasip1 → 600B wasm32-unknown-unknown).
- Rust 2024 edition compatibility.

### Milestone

First successful end-to-end laptop handshake: 600-byte wasm → wasmi → 3-cycle GPIO blink.
