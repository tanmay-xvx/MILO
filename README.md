# LIAL (LLM IoT Abstraction Layer)

> "Silicon as a Service for Agentic Systems"

LIAL is an ultra-lightweight hardware abstraction layer that turns any microcontroller into a programmable extension of an LLM's reasoning engine via JIT-compiled WebAssembly. You describe what you want in natural language, the LLM writes firmware, and the device executes it — all in seconds.

## Demo

```
  you → read the potentiometer and display the value on the OLED
  Generating code …

  ┌─ Generated Code ─────────────────────────────────
  │ #[unsafe(no_mangle)]
  │ pub extern "C" fn run_logic() {
  │     unsafe {
  │         // Read ADC
  │         let value = lial_adc_read(0);
  │         // Init OLED, clear screen, render digits
  │         // ... (font bitmap rendering)
  │         // Set LED brightness proportional to pot
  │         lial_pwm_set(5, value * 10000 / 4095);
  │     }
  │ }
  └─────────────────────────────────────────────────

  Compiling to wasm …
  Compiled OK — 1,247 bytes
  Pushing to ESP32 …
  Running on device …
  ✓ Execution finished.
  Device logs: ['ADC=2048', 'Display updated']
```

## Architecture

```
Host (Python)                              Receiver (Rust, on ESP32-C3)
┌──────────────────────┐                  ┌──────────────────────────────┐
│ Natural language      │                  │ LialRuntime<Esp32C3Hal>      │
│ → GPT-4o             │                  │ ├─ wasmi wasm interpreter    │
│ → Rust → wasm compile│   LIAL-Link      │ ├─ 11 syscall bindings      │
│ → USB serial push    │◄──(USB serial)──►│ ├─ gas metering (500M fuel) │
│                       │   binary frames  │ ├─ GPIO, PWM, ADC, I2C     │
│ lial init (auto-     │                  │ ├─ SPI, UART, delay, log   │
│   detect + flash)    │                  │ └─ EmbeddedHalAdapter      │
└──────────────────────┘                  └──────────────────────────────┘
```

## Prerequisites

- **Rust** (nightly + stable) with two targets:
  - `wasm32-unknown-unknown` — for compiling wasm drivers
  - `riscv32imc-unknown-none-elf` — for building ESP32-C3 firmware
- **Python 3.10+** with `openai`, `pyserial`, `esptool`
- **espflash** — for flashing firmware to ESP32 boards
- An **ESP32-C3** board connected via USB

```bash
# Rust targets
rustup target add wasm32-unknown-unknown
rustup toolchain install nightly

# Python deps
pip install openai pyserial esptool

# ESP32 flash tool
cargo install espflash
```

## Quick Start

### Option 1: Flash from GitHub Release (no Rust toolchain needed)

```bash
cd lial-host

# Auto-detect and flash a connected ESP32
python lial_cli.py init --port /dev/cu.usbmodem3101 -y

# Or download firmware manually
python lial_cli.py download --board esp32c3
```

`lial init` will:
1. Scan USB ports and identify the board by VID/PID
2. Probe the chip variant with `espflash` (or `esptool`)
3. Download the matching firmware binary from the GitHub release
4. Flash it to the device

### Option 2: Build from Source

```bash
cd lial-receiver

# Build (requires nightly for build-std)
cargo +nightly build --release \
  --target riscv32imc-unknown-none-elf \
  --features esp32c3 --no-default-features

# Flash
espflash flash --port /dev/cu.usbmodem3101 \
  target/riscv32imc-unknown-none-elf/release/lial-receiver
```

### Run the Host

```bash
cd lial-host
export OPENAI_API_KEY="sk-..."

python3 lial_host.py                             # auto-detects serial port
python3 lial_host.py --port /dev/cu.usbmodem3101  # or specify explicitly
```

Then type a task:

```
  you → blink the led on pin 5 three times
  you → read the potentiometer and show the value on the OLED
  you → fade the LED brightness up and down using PWM
```

The host will:
1. Send your prompt to GPT-4o along with the device's hardware manifest
2. Show you the generated Rust code
3. Compile it to a wasm binary (memory-constrained to 64 KB, 8 KB stack)
4. Push the binary to the ESP32 over USB serial
5. Wait for the result and display it

If compilation fails, the host sends the error back to the LLM and retries up to 2 times.

### Laptop-only testing (no hardware needed)

```bash
cd lial-receiver
cargo build
cargo run -- ../examples/test_drivers/blink_led/target/wasm32-unknown-unknown/release/blink_led.wasm

# Or with gas metering
cargo run -- --fuel 100000 <path-to-wasm>

# Host in subprocess mode
cd ../lial-host
python3 lial_host.py --subprocess "../lial-receiver/target/debug/lial-receiver --stdin"
```

### Run tests

```bash
cd lial-receiver
cargo test

# HIL tests (requires hardware connected)
cd ../lial-host
python3 hil_test.py --port /dev/cu.usbmodem3101
```

## The Atomic Alphabet (11 Syscalls)

Every wasm driver communicates with hardware through these functions:

| Syscall | Signature | Purpose |
|---------|-----------|---------|
| `lial_gpio_set` | `(pin: u32, state: u32)` | Set GPIO pin HIGH (1) or LOW (0) |
| `lial_gpio_get` | `(pin: u32) -> u32` | Read GPIO pin state |
| `lial_delay_ms` | `(ms: u32)` | Blocking delay in milliseconds |
| `lial_get_uptime_us` | `() -> u64` | Microseconds since boot |
| `lial_i2c_transfer` | `(addr, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | I2C read/write |
| `lial_pwm_set` | `(channel: u32, duty: u32)` | Set PWM duty (0–10000 = 0–100%) |
| `lial_adc_read` | `(channel: u32) -> u32` | Read ADC channel (12-bit, 0–4095) |
| `lial_spi_transfer` | `(bus, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | SPI read/write |
| `lial_uart_write` | `(bus: u32, ptr: u32, len: u32) -> i32` | Write to UART bus |
| `lial_uart_read` | `(bus, ptr, len, timeout_ms) -> i32` | Read from UART bus |
| `lial_log` | `(ptr: u32, len: u32)` | Log a UTF-8 message |

## Project Structure

```
LIAL/
├── lial-receiver/              # Rust firmware (runs on ESP32-C3 or laptop)
│   ├── src/
│   │   ├── lib.rs              # LialHardware trait, LialRuntime<H>, syscall bindings
│   │   ├── mock.rs             # LaptopMock — prints GPIO, uses thread::sleep
│   │   ├── esp32c3.rs          # Esp32C3Hal — LEDC PWM, ADC, I2C, USB serial I/O
│   │   ├── embedded_hal_adapter.rs  # Board-agnostic adapter (DynPwm, DynAdc, etc.)
│   │   ├── link.rs             # LIAL-Link v0.1 binary frame protocol
│   │   ├── manifest.rs         # Discovery manifest builder
│   │   └── main.rs             # Dual entry: #[esp_hal::main] or std fn main()
│   ├── release-manifest.json   # Release index for lial init / lial download
│   ├── tests/integration.rs    # Integration tests
│   └── .cargo/config.toml      # ESP32-C3 build config
│
├── lial-host/                  # Python host orchestrator
│   ├── lial_cli.py             # Unified CLI: lial init, lial download
│   ├── lial_host.py            # Interactive CLI: LLM → compile → push → result
│   ├── lial_compiler.py        # Rust body → wasm (64 KB memory, 8 KB stack)
│   ├── serial_push.py          # Low-level serial test tool
│   ├── hil_test.py             # Hardware-in-the-loop test harness
│   ├── hil_tests/              # Per-peripheral HIL test scripts
│   ├── board_registry.py       # USB VID/PID → board family mapping
│   ├── flash_backends/         # Per-family flash tools (espflash, UF2, avrdude, etc.)
│   └── lial_commands/          # CLI subcommands (init, download)
│
├── patches/                    # Local forks for ESP32-C3 compatibility
│   ├── wasmi/                  # Arc→Rc, Send+Sync removal, portable-atomic
│   ├── wasmi_core/
│   ├── wasmi_collections/
│   └── wasmparser/
│
├── examples/test_drivers/      # Pre-built wasm test fixtures
│
└── docs/
    ├── changelog.md            # Project-wide changelog
    ├── week1/                  # Week 1: core runtime, ESP32-C3, serial protocol
    ├── week2/                  # Week 2: board-agnostic adapter, firmware delivery, E2E testing
    └── week3/                  # Week 3: SVD manifests, Wi-Fi transport, multi-device
```

## LIAL-Link Protocol

Binary framing over any byte stream (USB serial, stdin/stdout):

```
[opcode: u8][payload_len: u32 big-endian][payload: bytes]
```

| Opcode | Direction | Purpose |
|--------|-----------|---------|
| `0x01` | Bidirectional | Discovery — JSON hardware manifest |
| `0x02` | Host → Receiver | Bytecode Push — raw wasm bytes |
| `0x03` | Receiver → Host | Execution Result — JSON `{"ok":true,"logs":[...]}` |

## Hardware Tested

| Component | GPIO | Protocol | Status |
|-----------|------|----------|--------|
| External LED (330 Ω) | 5 | LEDC PWM (10-bit, 5 kHz) | Working |
| Potentiometer (10 kΩ) | 2 | ADC1 (12-bit) | Working |
| SSD1306 OLED (0.96") | 8 (SDA), 9 (SCL) | I2C @ 0x3C | Working |

## wasmi Patches

The ESP32-C3 (`riscv32imc`) lacks hardware atomics, but `wasmi` uses `Arc` and `core::sync::atomic`. The `patches/` directory contains forks of 4 crates that replace `Arc` with `Rc`, remove `Send + Sync` bounds, and use `portable-atomic` for CAS operations. Applied via `[patch.crates-io]` in `Cargo.toml`.

## Releases

| Version | Tag | Contents |
|---------|-----|----------|
| v0.1.0-beta | `v0.1.0-beta` | ESP32-C3 merged firmware binary + manifest.json |

## Development Tracking

See `docs/` for changelogs and weekly development docs.

- `docs/week1/` — Receiver library, serial transport, JIT compiler, ESP32-C3 deployment
- `docs/week2/` — Board-agnostic adapter, firmware delivery, E2E hardware testing, LEDC PWM
- `docs/week3/` — SVD-driven manifests, Wi-Fi transport, multi-device orchestration
