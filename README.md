# LIAL (LLM IoT Abstraction Layer)

> "Silicon as a Service for Agentic Systems"

LIAL is an ultra-lightweight hardware abstraction layer that turns any silicon into a programmable extension of an LLM's reasoning engine via JIT-compiled WebAssembly.

## Core Philosophy

1. **Atomic Alphabet** — We don't code "TurnOnLight"; we expose `lial_gpio_set`. Six syscalls cover GPIO, timing, I2C, and logging.
2. **Execution Locality** — Logic runs on-device in a sandboxed wasm interpreter, not in the cloud.
3. **Safety First** — Memory isolation, gas metering, peripheral whitelisting, watchdog protection.

## Architecture

```
Host (Python)                              Receiver (Rust)
┌──────────────────────┐                  ┌────────────────────────────┐
│ User Prompt           │                  │ LialRuntime<H: LialHardware>│
│ → LLM (GPT-4o/Claude)│                  │ ├─ wasmi wasm interpreter  │
│ → JIT Compiler        │   LIAL-Link     │ ├─ 6 syscall bindings     │
│ → LIALLink transport  │◄───(serial)────►│ ├─ gas metering (fuel)    │
│                        │   0x01/02/03    │ └─ LaptopMock | Esp32C3Hal│
└──────────────────────┘                  └────────────────────────────┘
```

## Current Specs

- **Runtime:** `wasmi` 1.0.9 (Rust WebAssembly interpreter, `no_std` capable)
- **Transport:** LIAL-Link v0.1 — `[opcode: u8][len: u32 BE][payload]` over Serial/stdin
- **Compiler:** Rust cdylib pipeline targeting `wasm32-unknown-unknown` (~500 byte binaries)
- **Host:** Python 3.10+ with OpenAI / Anthropic LLM support

## Quick Start

### Prerequisites

- Rust toolchain (stable) with `wasm32-unknown-unknown` target
- Python 3.10+

```bash
rustup target add wasm32-unknown-unknown
pip install openai anthropic pyserial
```

### Build the Receiver

```bash
cd lial-receiver
cargo build
```

### Run a Wasm Driver Directly

```bash
cargo run -- ../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm
```

### Run with Gas Metering

```bash
cargo run -- --fuel 100000 ../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm
```

### Use the Host Orchestrator (subprocess mode)

```bash
cd lial-host
export OPENAI_API_KEY="your-key"  # or ANTHROPIC_API_KEY
python lial_host.py --subprocess "../lial-receiver/target/debug/lial-receiver --stdin"
```

Then type a natural language task like "Blink LED on pin 5 three times".

### Run Tests

```bash
cd lial-receiver
cargo test
```

## The Atomic Alphabet (6 Syscalls)

| Syscall | Signature | Purpose |
|---------|-----------|---------|
| `lial_gpio_set` | `(pin: u32, state: u32)` | Set GPIO pin high/low |
| `lial_gpio_get` | `(pin: u32) -> u32` | Read GPIO pin state |
| `lial_delay_ms` | `(ms: u32)` | Sleep for milliseconds |
| `lial_get_uptime_us` | `() -> u64` | Microsecond uptime counter |
| `lial_i2c_transfer` | `(addr, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | I2C read/write |
| `lial_log` | `(ptr: u32)` | Log a null-terminated string |

## Project Structure

```
LIAL/
├── lial-receiver/          # Rust wasm runtime + hardware abstraction
│   ├── src/
│   │   ├── lib.rs          # LialHardware trait, LialRuntime<H>, LialError
│   │   ├── mock.rs         # LaptopMock implementation
│   │   ├── esp32c3.rs      # ESP32-C3 stub (blocked on wasmi atomics)
│   │   ├── link.rs         # LIAL-Link v0.1 frame protocol
│   │   └── main.rs         # CLI: --stdin, --fuel, <wasm_path>
│   └── tests/
│       └── integration.rs  # 4 integration tests
├── lial-host/              # Python host orchestrator
│   ├── lial_host.py        # LLM + compile + push pipeline
│   ├── lial_compiler.py    # JIT: Rust source -> wasm bytes
│   └── requirements.txt
├── examples/
│   ├── mock_driver/        # 3-blink test driver (cdylib)
│   └── test_drivers/       # infinite_loop, no_export fixtures
└── docs/
    ├── changelog.md        # Project-wide changelog
    └── week1/              # Week 1 development docs
```

## Development Tracking

See `docs/week1/` for the current week's action plan, changelog, and status.
