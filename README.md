# LIAL (LLM IoT Abstraction Layer)

> "Silicon as a Service for Agentic Systems"

LIAL is an ultra-lightweight hardware abstraction layer that turns any microcontroller into a programmable extension of an LLM's reasoning engine via JIT-compiled WebAssembly. You describe what you want in natural language, the LLM writes firmware, and the device executes it — all in seconds.

## Demo

```
  you → blink the led 5 times with 300ms intervals
  Generating code …

  ┌─ Generated Code ─────────────────────────────────
  │ #[unsafe(no_mangle)]
  │ pub extern "C" fn run_logic() {
  │     unsafe {
  │         for _ in 0..5 {
  │             lial_gpio_set(5, 1);
  │             lial_delay_ms(300);
  │             lial_gpio_set(5, 0);
  │             lial_delay_ms(300);
  │         }
  │     }
  │ }
  └─────────────────────────────────────────────────

  Compiling to wasm …
  Compiled OK — 618 bytes
  Pushing to ESP32 …
  Running on device …
  ✓ Execution finished.
```

## Architecture

```
Host (Python)                              Receiver (Rust, on ESP32-C3)
┌──────────────────────┐                  ┌──────────────────────────────┐
│ Natural language      │                  │ LialRuntime<Esp32C3Hal>      │
│ → GPT-4o             │                  │ ├─ wasmi wasm interpreter    │
│ → Rust → wasm compile│   LIAL-Link      │ ├─ 6 syscall bindings       │
│ → USB serial push    │◄──(USB serial)──►│ ├─ gas metering (1M fuel)   │
│                       │   binary frames  │ └─ GPIO, delay, I2C, log   │
└──────────────────────┘                  └──────────────────────────────┘
```

## Prerequisites

- **Rust** (nightly + stable) with two targets:
  - `wasm32-unknown-unknown` — for compiling wasm drivers
  - `riscv32imc-unknown-none-elf` — for building ESP32-C3 firmware
- **Python 3.10+** with `openai` and `pyserial`
- **espflash** — for flashing firmware to the ESP32-C3
- An **ESP32-C3** board connected via USB

```bash
# Rust targets
rustup target add wasm32-unknown-unknown
rustup toolchain install nightly

# Python deps
pip install openai pyserial

# ESP32 flash tool
cargo install espflash
```

## Quick Start

### 1. Flash the Receiver firmware onto the ESP32-C3

The receiver is the Rust firmware that runs on the microcontroller. It embeds a WebAssembly interpreter and listens for wasm programs over USB serial.

```bash
cd lial-receiver

# Build (requires nightly for build-std)
cargo +nightly build --release \
  --target riscv32imc-unknown-none-elf \
  --features esp32c3 --no-default-features

# Flash (replace port if different on your machine)
espflash flash --port /dev/cu.usbmodem101 \
  target/riscv32imc-unknown-none-elf/release/lial-receiver
```

The `.cargo/config.toml` in `lial-receiver/` already configures the linker scripts, `build-std`, and `portable-atomic` flags needed for the ESP32-C3 build. You should not need to pass extra flags beyond what's shown above.

After flashing, the ESP32 boots, sends a LIAL-Link discovery frame, and waits for wasm programs.

### 2. Run the Host

The host is a Python CLI that takes your natural-language instructions, asks GPT-4o to write Rust firmware, compiles it to wasm, and pushes it to the ESP32 over USB serial.

```bash
cd lial-host

export OPENAI_API_KEY="sk-..."

python3 lial_host.py                          # auto-detects serial port
python3 lial_host.py --port /dev/cu.usbmodem101  # or specify explicitly
```

Then type a task:

```
  you → blink the led on pin 5 three times
```

The host will:
1. Send your prompt to GPT-4o along with the device's hardware manifest
2. Show you the generated Rust code
3. Compile it to a ~600 byte wasm binary (memory-constrained to 64KB for the ESP32)
4. Push the binary to the ESP32 over USB serial
5. Wait for the result and display it

If compilation fails, the host sends the error back to the LLM and retries up to 2 times.

You can send multiple tasks in a row — the ESP32 loops back to waiting after each execution.

### 3. Laptop-only testing (no hardware needed)

You can also test the full pipeline on your laptop using the mock backend:

```bash
# Build the receiver (std mode, default)
cd lial-receiver
cargo build

# Run a pre-built wasm driver directly
cargo run -- ../examples/test_drivers/blink_led/target/wasm32-unknown-unknown/release/blink_led.wasm

# Or with gas metering (limits to N wasm instructions)
cargo run -- --fuel 100000 <path-to-wasm>

# Run the host in subprocess mode (no serial, no ESP32)
cd ../lial-host
export OPENAI_API_KEY="sk-..."
python3 lial_host.py --subprocess "../lial-receiver/target/debug/lial-receiver --stdin"
```

### 4. Run tests

```bash
cd lial-receiver
cargo test
```

All 4 integration tests should pass: `happy_path`, `missing_export`, `fuel_exhaustion`, `bad_module`.

## The Atomic Alphabet (6 Syscalls)

Every wasm driver communicates with hardware through exactly these 6 functions:

| Syscall | Signature | Purpose |
|---------|-----------|---------|
| `lial_gpio_set` | `(pin: u32, state: u32)` | Set GPIO pin HIGH (1) or LOW (0) |
| `lial_gpio_get` | `(pin: u32) -> u32` | Read GPIO pin state |
| `lial_delay_ms` | `(ms: u32)` | Blocking delay in milliseconds |
| `lial_get_uptime_us` | `() -> u64` | Microseconds since boot |
| `lial_i2c_transfer` | `(addr, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | I2C read/write |
| `lial_log` | `(ptr: u32, len: u32)` | Log a UTF-8 message (pointer + byte length) |

## Project Structure

```
LIAL/
├── lial-receiver/              # Rust firmware (runs on ESP32-C3 or laptop)
│   ├── src/
│   │   ├── lib.rs              # LialHardware trait, LialRuntime<H>, syscall bindings
│   │   ├── mock.rs             # LaptopMock — prints GPIO, uses thread::sleep
│   │   ├── esp32c3.rs          # Esp32C3Hal — real GPIO, delay, USB serial I/O
│   │   ├── link.rs             # LIAL-Link v0.1 binary frame protocol
│   │   └── main.rs             # Dual entry: #[esp_hal::main] or std fn main()
│   ├── tests/integration.rs    # 4 integration tests
│   └── .cargo/config.toml      # ESP32-C3 build config (linker, build-std, atomics)
│
├── lial-host/                  # Python host orchestrator
│   ├── lial_host.py            # Interactive CLI: LLM → compile → push → result
│   ├── lial_compiler.py        # Rust body → wasm (64KB memory, 4KB stack)
│   ├── serial_push.py          # Low-level serial test tool
│   └── requirements.txt        # openai, pyserial
│
├── patches/                    # Local forks for ESP32-C3 compatibility
│   ├── wasmi/                  # Arc→Rc, Send+Sync removal, portable-atomic
│   ├── wasmi_core/             # Arc→Rc in fuel.rs, func_type.rs
│   ├── wasmi_collections/      # Arc→Rc in string interner
│   └── wasmparser/             # Arc→Rc, portable-atomic for AtomicUsize
│
├── examples/test_drivers/
│   ├── blink_led/              # 5-blink wasm driver (618 bytes)
│   ├── infinite_loop/          # Gas metering test fixture
│   └── no_export/              # Missing export test fixture
│
└── docs/
    ├── changelog.md            # Project-wide changelog
    └── week1/                  # Week 1 development docs
```

## LIAL-Link Protocol

Binary framing over any byte stream (USB serial, stdin/stdout):

```
[opcode: u8][payload_len: u32 big-endian][payload: bytes]
```

| Opcode | Direction | Purpose |
|--------|-----------|---------|
| `0x01` | Receiver → Host | Discovery — JSON hardware manifest |
| `0x02` | Host → Receiver | Bytecode Push — raw wasm bytes |
| `0x03` | Receiver → Host | Execution Result — JSON `{"ok":true,"logs":[...]}` |

## wasmi Patches

The ESP32-C3 (`riscv32imc`) lacks hardware atomics, but `wasmi` and its dependencies use `alloc::sync::Arc` and `core::sync::atomic` operations. The `patches/` directory contains local forks of 4 crates that replace `Arc` with `Rc`, remove `Send + Sync` bounds, and use `portable-atomic` for CAS operations. These patches are applied via `[patch.crates-io]` in `lial-receiver/Cargo.toml` and are transparent — the std (laptop) build uses them too and all tests pass.

## Development Tracking

See `docs/` for changelogs and weekly development docs.
