# Week 1 Changelog

Changes made during Week 1 development on the `week1` branch.

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
