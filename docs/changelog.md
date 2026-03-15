# LIAL Changelog

All notable changes to this project are documented in this file.
Format follows a simplified [Keep a Changelog](https://keepachangelog.com/) style.

---

## [Week 1 — Hardware Pivot] — 2026-03-15

### Added

- **Receiver library** (`lib.rs`) with `LialHardware` trait (6 syscalls), `LialRuntime<H>` generic executor, `LialError` enum, gas metering via wasmi fuel.
- **LaptopMock** (`mock.rs`) — Full `LialHardware` implementation for laptop development.
- **Esp32C3Hal** (`esp32c3.rs`) — Structural stub, blocked on wasmi atomics issue.
- **LIAL-Link v0.1** (`link.rs`) — Binary frame protocol: `[opcode: u8][len: u32 BE][payload]`, OpCodes 0x01-0x03.
- **stdin pipe mode** — Receiver subprocess communicates via LIAL-Link frames on stdin/stdout.
- **Integration tests** — 4 tests covering happy path, missing export, fuel exhaustion, bad module.
- **Test fixtures** — `infinite_loop` (gas test), `no_export` (missing export test).
- **Host orchestrator** (`lial_host.py`) — LIALLink transport, LLM prompter (OpenAI + Anthropic), CLI loop.
- **JIT compiler rewrite** (`lial_compiler.py`) — Rust cdylib pipeline, `compile_to_bytes()` API, auto-detection of clang/wasm-ld.

### Changed

- **Receiver main.rs** — Rewritten as thin CLI wrapper: `--fuel N`, `<wasm_path>`, `--stdin` modes.
- **Cargo.toml** — Feature flags (`std`, `esp32c3`), optional esp-hal/esp-alloc dependencies.

### Removed

- `lial_std.rs` — Dead code replaced by `LialHardware` trait architecture.

### Known Blocker

- wasmi 1.0.9 requires `alloc::sync::Arc` (atomics) — cannot compile for `riscv32imc-unknown-none-elf`.

---

## [Unreleased] — 2026-03-12

### Fixed

- **wasmi API mismatch in receiver** — Replaced non-existent two-step `linker.instantiate()` + `.start()` with `linker.instantiate_and_start()` to match `wasmi` 1.0.9 API. Receiver now compiles cleanly.
- **mock_driver.wasm wrong target** — The old `examples/mock_driver.wasm` (1.4 MB, `wasm32-wasip1`) bundled the full Rust `std` and required WASI imports the receiver doesn't provide. Replaced with a proper `cdylib` crate build targeting `wasm32-unknown-unknown`, producing a 600-byte binary with only `lial_gpio_set`/`lial_delay_ms` imports.
- **Rust 2024 edition compatibility** — Updated `examples/mock_driver/src/lib.rs` to use `unsafe extern "C"` blocks and `#[unsafe(no_mangle)]` as required by Rust 2024 edition.

### Added

- `examples/mock_driver/Cargo.toml` — Configured as a `cdylib` crate for clean `wasm32-unknown-unknown` builds.
- `.cursor/project.md` — Reformatted with proper Markdown structure (headings, nested lists, code formatting).
- `.cursor/rules/lial-project.mdc` — Cursor rule for project architecture context and safety constraints.
- `docs/changelog.md` — This file.

### Changed

- `lial-receiver/src/main.rs` — Wasm path now points to `../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm`.

### Milestone

First successful end-to-end "Handshake": Receiver loads a 600-byte wasm module, links the Atomic Alphabet syscalls (`gpio_set`, `delay_ms`), and executes a 3-cycle GPIO blink loop on a laptop.

---

## [Day 2] — 2026-03-12 — `a447247`

> Baseline state before the fixes above.

### Status

- Week 1 Goal ("Foundations & The Alphabet"): ~40% complete.
- Receiver compiled but could not instantiate wasm modules due to API mismatch.

### Pending Hurdles

- **API Alignment:** `wasmi` version-specific traits preventing correct function wrapping for the Wasm Linker.
- **Host Function Binding:** Bridge between Rust functions and Wasm execution environment not finalized.
- **Linker Configuration:** macOS-specific `clang`/`lld` paths causing issues for Wasm cross-compilation.
- **Binary Packaging:** Generated `.wasm` files not compatible with the `wasmi` runtime (WASI target vs. `wasm32-unknown-unknown`).

### Accomplishments

- Architectural definition complete (Host / Link / Receiver).
- "Alphabet vs. Phrasebook" strategy established.
- Repository structured as a monorepo with `lial-receiver/`, `lial-host/`, `lial-link/`, `lial-pulse-driver/`, `examples/`.
- `lial_std.h` header defining the Atomic Alphabet (GPIO, timing, I2C, logging).
- `lial_compiler.py` host-side JIT compiler (C string to Wasm via Clang).
- `lial-receiver/src/main.rs` initial implementation with `wasmi` engine, function wrapping, and module loading.

---

## [Day 1] — 2026-03-11 — `458d8e4` .. `f45351c`

### Added

- Initial project architecture (`PROJECT_LIAL.md`).
- `lial_std.h` — Standard header with GPIO, timing, I2C, and logging function declarations.
- `lial_compiler.py` — Python JIT compiler (C string to Wasm via WASI-SDK Clang).
- `lial-pulse-driver/` — Placeholder Rust crate for driver development.
- `.gitignore` — Excludes `target/`, `*.wasm`, `*.o`, `.env`, Python cache.
- `README.md` — Development and simulation instructions.
