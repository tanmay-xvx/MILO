# LIAL (LLM IoT Abstraction Layer)

> "Silicon as a Service for Agentic Systems"

LIAL is an ultra-lightweight hardware abstraction layer that turns microcontrollers — today **ESP32-C3** and **Raspberry Pi Pico (RP2040)** — into a programmable extension of an LLM's reasoning engine via JIT-compiled WebAssembly. You describe what you want in natural language, the LLM writes firmware, and the device executes it — often in seconds.

## Demo

```
  you → read the potentiometer and display the value on the OLED
  Generating code …

  ┌─ Generated Code ─────────────────────────────────
  │ #[unsafe(no_mangle)]
  │ pub extern "C" fn run_logic() {
  │     unsafe {
  │         // ADC channel comes from discovery manifest (e.g. 0 on ESP32-C3, 26 on Pico)
  │         let value = lial_adc_read(0);
  │         // SSD1306: init, clear all pages, bitmap digits — see host system prompt
  │         lial_pwm_set(5, value * 10000 / 4095);
  │     }
  │ }
  └─────────────────────────────────────────────────

  Compiling to wasm …
  Compiled OK — 1,247 bytes
  Pushing to device …
  Running on device …
  ✓ Execution finished.
  Device logs: ['ADC=2048', 'Display updated']
```

## Architecture

```
Host (Python)                         Receiver (Rust — ESP32-C3 or RP2040 Pico)
┌──────────────────────┐             ┌─────────────────────────────────────┐
│ Natural language      │             │ LialRuntime + board HAL             │
│ → GPT-4o             │             │ ├─ wasmi (sandboxed wasm)            │
│ → Rust → wasm        │  LIAL-Link  │ ├─ syscall bindings + gas (“fuel”)   │
│ → serial / optional   │  frames     │ ├─ GPIO, PWM, ADC, I2C, SPI, UART   │
│    TCP (Wi‑Fi WIP)   │◄───────────►│ └─ EmbeddedHalAdapter               │
│ lial init (ESP32)    │             │ Transport: USB-serial JTAG, USB CDC │
└──────────────────────┘             └─────────────────────────────────────┘
```

## Prerequisites

- **Rust** (nightly + stable) with targets:
  - `wasm32-unknown-unknown` — wasm drivers
  - `riscv32imc-unknown-none-elf` — ESP32-C3 firmware
  - `thumbv6m-none-eabi` — **Raspberry Pi Pico** firmware (requires nightly **build-std** — see `.cargo/config.toml`)
- **Python 3.10+** with `openai`, `pyserial`, `esptool` (optional: `zeroconf` for host mDNS / `--transport wifi`)
- **espflash** — ESP32 flashing
- **picotool** or **elf2uf2-rs** — Pico UF2 flashing (`cargo install elf2uf2-rs`)

```bash
rustup target add wasm32-unknown-unknown
rustup target add riscv32imc-unknown-none-elf
rustup target add thumbv6m-none-eabi
rustup toolchain install nightly

pip install openai pyserial esptool

cargo install espflash
cargo install elf2uf2-rs   # optional; Pico build also supports probe-rs
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

### Option 2a: Build ESP32-C3 from source

```bash
cd lial-receiver

cargo +nightly build --release \
  --target riscv32imc-unknown-none-elf \
  --features esp32c3 --no-default-features

espflash flash --port /dev/cu.usbmodem3101 \
  target/riscv32imc-unknown-none-elf/release/lial-receiver
```

### Option 2b: Build Raspberry Pi Pico (RP2040) from source

```bash
cd lial-receiver

cargo +nightly build --release \
  --target thumbv6m-none-eabi \
  --no-default-features \
  --features rp2040

# UF2 via elf2uf2-rs (BOOTSEL → drag, or use -d when USB drive mounted)
elf2uf2-rs target/thumbv6m-none-eabi/release/lial-receiver lial-pico.uf2

# Or load directly (device in BOOTSEL / picotool discovers RP2)
picotool load target/thumbv6m-none-eabi/release/lial-receiver -f && picotool reboot
```

Reference wiring for the **Pico** build in-tree: onboard LED **GPIO 25**, I2C0 **SDA 4 / SCL 5** (e.g. SSD1306 @ `0x3C`), ADC pot on **GPIO 26** (manifest reports channel **`26`**). Full Week 3 notes: `docs/week3/WEEK3_IMPLEMENTATION.md`.

### Run the Host

```bash
cd lial-host
export OPENAI_API_KEY="sk-..."

python3 lial_host.py                             # auto-detect serial (ESP32 or Pico CDC)
python3 lial_host.py --port /dev/cu.usbmodem3101 # explicit ESP32
python3 lial_host.py --port /dev/cu.usbmodemLIAL_PICO_0011   # Pico (example)

# Experimental: TCP to a future Wi-Fi receiver (requires device advertising/listening)
python3 lial_host.py run --transport wifi --ip 192.168.1.50
```

Then type a task:

```
  you → blink the onboard LED three times        # Pico: often GPIO 25; follow manifest
  you → blink the led on pin 5 three times        # ESP32-C3 external LED
  you → read the potentiometer and show the value on the OLED
  you → fade the LED brightness up and down using PWM
```

The host will:
1. Send your prompt to GPT-4o along with the device's hardware manifest
2. Show you the generated Rust code
3. Compile it to a wasm binary (memory-constrained to 64 KB, 8 KB stack)
4. Push the binary over **USB serial** (or `--transport wifi` when supported)
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

## The Atomic Alphabet (12 Syscalls)

Every wasm driver communicates with hardware through these functions (all imported from module `env` unless noted):

| Syscall | Signature | Purpose |
|---------|-----------|---------|
| `lial_gpio_set` | `(pin: u32, state: u32)` | Set GPIO pin HIGH (1) or LOW (0) |
| `lial_gpio_get` | `(pin: u32) -> u32` | Read GPIO pin state |
| `lial_delay_ms` | `(ms: u32)` | Blocking delay in milliseconds |
| `lial_get_uptime_us` | `() -> u64` | Microseconds since boot |
| `lial_i2c_transfer` | `(addr, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | I2C read/write |
| `lial_pwm_set` | `(channel: u32, duty: u32)` | Set PWM duty (0–10000 = 0–100%) |
| `lial_adc_read` | `(channel: u32) -> u32` | Read ADC (resolution in manifest; often 12-bit) |
| `lial_spi_transfer` | `(bus, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` | SPI read/write |
| `lial_uart_write` | `(bus: u32, ptr: u32, len: u32) -> i32` | Write to UART bus |
| `lial_uart_read` | `(bus, ptr, len, timeout_ms) -> i32` | Read from UART bus |
| `lial_log` | `(ptr: u32, len: u32)` | Log a UTF-8 message |
| `lial_get_param` | `(slot: u32) -> u32` | Read host-writable parameter slot (0–7); see extended protocol |

## Project Structure

```
LIAL/
├── lial-receiver/              # Rust firmware (ESP32-C3, RP2040 Pico, or std mock)
│   ├── src/
│   │   ├── lib.rs              # LialHardware, LialRuntime, main_loop, syscalls
│   │   ├── mock.rs             # LaptopMock — prints GPIO, uses thread::sleep
│   │   ├── esp32c3.rs          # Esp32C3Hal
│   │   ├── rp2040.rs           # Rp2040Hal (Pico) — Week 3
│   │   ├── transport.rs       # LialTransport, EmbeddedIoTransport, StdioTransport
│   │   ├── executor.rs         # LialExecutor, param slots — Week 3
│   │   ├── transport_wifi.rs  # Wi-Fi TCP stub (esp32c3-wifi feature)
│   │   ├── validation.rs       # Wasm import whitelist
│   │   ├── embedded_hal_adapter.rs
│   │   ├── link.rs             # LIAL-Link opcodes + Frame
│   │   ├── manifest.rs
│   │   └── main.rs             # esp_hal::main, rp2040 entry, or std main
│   ├── memory.x                # Pico RAM layout (thumbv6)
│   ├── release-manifest.json
│   ├── tests/integration.rs
│   └── .cargo/config.toml      # riscv + thumbv6 (build-std for Pico)
│
├── lial-host/
│   ├── lial_cli.py
│   ├── lial_host.py            # LLM loop; --transport serial|wifi
│   ├── transport.py            # SerialTransport, TcpTransport — Week 3
│   ├── lial_device.py / device_registry.py / discovery.py / mcp_server.py
│   ├── lial_compiler.py
│   ├── hil_test.py
│   ├── board_registry.py
│   ├── flash_backends/
│   └── lial_commands/
│
├── patches/                    # wasmi forks (ESP32 + dependency graph)
│   └── …
│
├── examples/test_drivers/
│
└── docs/
    ├── changelog.md
    ├── week1/
    ├── week2/
    └── week3/
        ├── misc/buildplan.md        # Phased Week 3 plan (in-repo)
        ├── WEEK3_IMPLEMENTATION.md # What shipped vs plan
        ├── research.md / context.md / plan.md
```

## LIAL-Link Protocol

Binary framing over any byte stream (USB serial, stdin/stdout):

```
[opcode: u8][payload_len: u32 big-endian][payload: bytes]
```

| Opcode | Direction | Purpose |
|--------|-----------|---------|
| `0x01` | Bidirectional | Discovery — JSON hardware manifest |
| `0x02` | Host → Receiver | Bytecode push — raw wasm |
| `0x03` | Receiver → Host | Execution result — JSON |
| `0x04`–`0x09` | (extended) | Streaming, stop, query/status, set param, hot-swap — **see `link.rs`; ESP32 `main_loop` implements most; Pico path is narrower today** |

Details and gaps: `docs/week3/WEEK3_IMPLEMENTATION.md`.

## Hardware tested

### ESP32-C3 (reference bench)

| Component | GPIO | Protocol | Status |
|-----------|------|----------|--------|
| External LED (330 Ω) | 5 | LEDC PWM (10-bit, 5 kHz) | Working |
| Potentiometer (10 kΩ) | 2 | ADC1 (12-bit); manifest channel `0` | Working |
| SSD1306 OLED (0.96") | 8 (SDA), 9 (SCL) | I2C @ 0x3C | Working |

### Raspberry Pi Pico (Week 3)

| Component | GPIO | Protocol | Status |
|-----------|------|----------|--------|
| Onboard LED | 25 | GPIO | Working |
| Potentiometer | 26 | ADC (manifest channel **26**) | Working |
| SSD1306 OLED | 4 (SDA), 5 (SCL) | I2C @ 0x3C | Working |

## wasmi patches

The **ESP32-C3** (`riscv32imc`) has no native atomics; `wasmi` historically relied on `Arc` and atomics. The `patches/` directory contains forks (`wasmi`, `wasmi_core`, `wasmi_collections`, `wasmparser`) wired via `[patch.crates-io]`.

**RP2040** builds use **`thumbv6m-none-eabi`** with **`--no-default-features`** (no `wasmi/std`). The same patched crates are used where the resolver requires them; **`portable-atomic`** also appears in the Pico feature set for the dependency graph.

## Releases

| Version | Tag | Contents |
|---------|-----|----------|
| v0.1.0-beta | `v0.1.0-beta` | ESP32-C3 merged firmware binary + manifest.json |

## Development tracking

- `docs/week1/` — Core runtime, ESP32-C3, serial protocol  
- `docs/week2/` — Board-agnostic adapter, firmware delivery, E2E testing  
- `docs/week3/misc/buildplan.md` — **Phased Week 3 build plan** (5 phases, in git)  
- `docs/week3/WEEK3_IMPLEMENTATION.md` — **Implementation status** vs that plan (Pico, transport, Wi‑Fi stub, MCP, validation)  
- `docs/week3/research.md`, `context.md`, `plan.md` — Notes and narrative
