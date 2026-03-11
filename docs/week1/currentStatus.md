# Week 1 Current Status

**Branch:** `week1` | **Started:** 2026-03-12

---

## Completed

- Project architecture and spec finalized (`project.md` manifesto v1.0)
- Receiver compiles and runs on laptop with `wasmi` 1.0.9
- Mock blink driver (600 bytes, `wasm32-unknown-unknown`) executes successfully
- 2 of 6 Alphabet syscalls implemented: `lial_gpio_set`, `lial_delay_ms`
- `lial_compiler.py` skeleton exists (C string -> wasm via Clang)
- Weekly docs structure established (`docs/week1/`)

## In Progress

_(nothing yet -- week 1 development starts with Days 1-2: Receiver Library)_

## Blocked

_(none)_

## Next Up

- **Days 1-2:** Extract `LialRuntime` into `lib.rs` with all 6 Alphabet stubs, gas metering, `LialError` enum, and unit tests
- **Day 3:** TCP transport (Receiver listens on `127.0.0.1:9100`, sends manifest, receives wasm, sends result)
- **Day 4:** Fix JIT compiler to use system `clang` or Rust `cdylib` pipeline
- **Days 5-6:** Host orchestrator with TCP client, LLM prompt template, compile-push pipeline
- **Day 7:** End-to-end integration and polish
