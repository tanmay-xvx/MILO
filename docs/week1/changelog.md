# Week 1 Changelog

Changes made during Week 1 development on the `week1` branch.

---

## Hardware Pivot Implementation — 2026-03-15

### Added

- **`lial-receiver/src/lib.rs`** — Core runtime library with `LialHardware` trait (6 syscalls), `LialRuntime<H>` generic executor, `LialError` enum, `HostState<H>`, gas metering via wasmi fuel.
- **`lial-receiver/src/mock.rs`** — `LaptopMock` struct implementing all 6 `LialHardware` methods (GPIO print, thread::sleep delay, Instant-based uptime, I2C stub, stderr logging).
- **`lial-receiver/src/esp32c3.rs`** — `Esp32C3Hal` stub implementing `LialHardware` trait. Structurally complete but blocked on wasmi atomics issue (wasmi-labs/wasmi#738).
- **`lial-receiver/src/link.rs`** — LIAL-Link v0.1 frame protocol: `[opcode: u8][len: u32 BE][payload]`, OpCodes 0x01 (Discovery), 0x02 (Bytecode Push), 0x03 (Exec Result). `read_frame`/`write_frame` for std byte streams.
- **`lial-receiver/tests/integration.rs`** — 4 integration tests: happy_path, missing_export, fuel_exhaustion, bad_module. All passing.
- **`examples/test_drivers/infinite_loop/`** — Wasm fixture for gas/fuel exhaustion testing.
- **`examples/test_drivers/no_export/`** — Wasm fixture for missing export testing.
- **`lial-host/lial_host.py`** — Full host orchestrator: `LIALLink` class (subprocess + serial transports), LLM prompter (OpenAI + Anthropic), `extract_rust_code()` for markdown stripping, interactive CLI loop.
- **`lial-host/requirements.txt`** — openai, anthropic, pyserial.
- **`lial-receiver/.cargo/config.toml`** — ESP32-C3 target config with `portable_atomic_unsafe_assume_single_core`.

### Changed

- **`lial-receiver/src/main.rs`** — Complete rewrite: thin CLI wrapper around `LialRuntime<LaptopMock>`. Supports `--fuel N`, `<wasm_path>`, and `--stdin` (LIAL-Link pipe mode).
- **`lial-receiver/Cargo.toml`** — Added `std`/`esp32c3` feature flags, `serde_json` dependency, optional `esp-hal`/`esp-alloc` dependencies.
- **`lial-host/lial_compiler.py`** — Complete rewrite: auto-detects Homebrew LLVM clang + wasm-ld, falls back to Rust cdylib pipeline. New `compile_to_bytes(code, lang)` API. LIAL header and Rust template auto-prepended.
- **`lial-receiver/src/mock.rs`** — All output via `eprintln!` to keep stdout clean for binary frame protocol.

### Removed

- **`lial-receiver/src/lial_std.rs`** — Dead code with unresolvable `embedded_hal` import, replaced by `LialHardware` trait.

### Infrastructure

- Installed `riscv32imc-unknown-none-elf` Rust target.
- Installed `espup` v0.16.0, `espflash` v4.3.0.
- Installed Python packages: openai, anthropic, pyserial.

### Known Blocker

- **wasmi no-atomics:** `wasmi 1.0.9` uses `alloc::sync::Arc` which is unavailable on `riscv32imc-unknown-none-elf` (no hardware atomics). This prevents compiling the receiver for ESP32-C3. Tracked upstream: wasmi-labs/wasmi#738. Resolution: patch wasmi to use `Rc`, or wait for upstream fix.

---

## Pre-Week-1 Baseline (carried from `dev`)

### Fixed

- **wasmi API mismatch in receiver** -- Replaced `linker.instantiate()` + `.start()` with `linker.instantiate_and_start()` to match `wasmi` 1.0.9 API.
- **mock_driver.wasm wrong target** -- Replaced 1.4 MB `wasm32-wasip1` binary with a 600-byte `wasm32-unknown-unknown` cdylib build.
- **Rust 2024 edition compatibility** -- Updated `examples/mock_driver/src/lib.rs` to use `unsafe extern "C"` and `#[unsafe(no_mangle)]`.

### Added

- `examples/mock_driver/Cargo.toml` -- cdylib crate for clean wasm builds.
- `.cursor/project.md` -- Full manifesto v1.0 with LIAL Stack, Alphabet table, security model.
- `.cursor/rules/lial-project.mdc` -- Cursor rule for project context.
- `docs/changelog.md` -- Project-wide changelog.

### Milestone

First successful end-to-end "Handshake": Receiver loads a 600-byte wasm module, links Alphabet syscalls (`gpio_set`, `delay_ms`), and executes a 3-cycle GPIO blink loop on a laptop.
