# LIAL — Week 3 Starting Context

## What is LIAL?

LIAL (LLM IoT Abstraction Layer) lets an LLM control any microcontroller via WebAssembly. The user types a natural language command, GPT-4o generates Rust code, the host compiles it to a ~1 KB Wasm binary, pushes it over USB serial to the device, and the device executes it — all in seconds.

## What exists today (end of Week 2)

### Receiver (Rust, `no_std`, runs on ESP32-C3)
- **`LialHardware` trait** with 11 syscalls: `gpio_set`, `gpio_get`, `delay_ms`, `get_uptime_us`, `i2c_transfer`, `pwm_set`, `adc_read`, `spi_transfer`, `uart_write`, `uart_read`, `log`.
- **`EmbeddedHalAdapter`** — board-agnostic adapter with object-safe dynamic dispatch (`DynPwm`, `DynAdc`, `DynI2c`, `DynSpi`, `DynUart`, `DynDelay`, `DynPin`). Adding a new board = instantiate the adapter with that board's `embedded-hal` peripherals.
- **`Esp32C3Hal`** — thin factory over `EmbeddedHalAdapter`:
  - GPIO 5: external LED via LEDC hardware PWM (10-bit, 5 kHz). `gpio_set(5,...)` routes through PWM.
  - GPIO 2: potentiometer via ADC1 (12-bit).
  - GPIO 8/9: I2C0 SDA/SCL driving SSD1306 OLED at 0x3C.
- **`LialRuntime<H>`** — wasmi-based Wasm executor. 64 KB memory, 8 KB stack, 500M fuel.
- **`manifest.rs`** — structured JSON discovery manifest (capabilities, pin lists, I2C scan, memory limits, syscall alphabet).
- **LIAL-Link protocol** — `[opcode: u8][len: u32 BE][payload]`. Opcodes: 0x01 Discovery, 0x02 Bytecode Push, 0x03 Exec Result.
- **wasmi patches** — `patches/` has 4 crate forks (wasmi, wasmi_core, wasmi_collections, wasmparser) replacing `Arc`→`Rc` and using `portable-atomic` for ESP32-C3's single-core RISC-V.

### Host (Python)
- **`lial_host.py`** — interactive CLI: reads discovery manifest, sends prompt + `device_info` to GPT-4o, compiles Rust→Wasm, pushes over serial, displays result. Retries compilation errors up to 2x.
- **`lial_compiler.py`** — generates a Cargo project, compiles to `wasm32-unknown-unknown` with 64 KB memory / 8 KB stack constraints.
- **`lial_cli.py`** — unified CLI entry point: `init` and `download` subcommands.
- **`lial init`** — auto-detects USB boards by VID/PID (`board_registry.py`), probes chip variant, downloads firmware from GitHub release manifest, flashes via `espflash` (preferred) or `esptool` (fallback). Private repo fallback via `gh api`.
- **`flash_backends/`** — `esp32.py` (espflash + esptool), `rp2040.py` (UF2), `avr.py` (avrdude), `stm32.py` (dfu-util).
- **`hil_test.py`** — HIL harness: compiles Rust snippets, pushes to real hardware, asserts on returned logs. Tests in `hil_tests/`.
- **System prompt** includes: SSD1306 font tables (digits + letters as 5-byte bitmaps), memory warnings, `#[unsafe(no_mangle)]` syntax, ban on `cfg!()` and `format!`.

### Release
- **`v0.1.0-beta`** on GitHub — merged ESP32-C3 firmware `.bin` + `manifest.json`.
- Wipe-and-reflash verified: `espflash erase-flash` → `lial init` → downloads → flashes → LIAL v0.1.0 running.

### HIL test results (all on ESP32-C3 Super Mini)
| Test | Status |
|------|--------|
| GPIO (blink pin 5) | Pass |
| ADC (potentiometer) | Pass |
| PWM (LED brightness) | Pass |
| I2C / OLED | Pass |
| UART (loopback) | Skip (no jumper) |

### Hardware on the bench
- ESP32-C3 Super Mini (OceanLabz, rev v0.4), USB JTAG at `/dev/cu.usbmodem3101`
- Raspberry Pi Pico (with headers, not yet used)
- External LED + 330 Ω on GPIO 5
- 10 kΩ potentiometer on GPIO 2
- SSD1306 0.96" OLED on I2C (GPIO 8/9)
- TMP102 I2C breakout (no headers soldered — excluded)

## Key file paths
- Receiver: `lial-receiver/src/{lib.rs, main.rs, esp32c3.rs, embedded_hal_adapter.rs, manifest.rs, link.rs}`
- Host: `lial-host/{lial_host.py, lial_compiler.py, lial_cli.py, board_registry.py, hil_test.py}`
- Flash: `lial-host/flash_backends/{__init__.py, esp32.py}`
- CLI: `lial-host/lial_commands/{init.py, download.py}`
- Plans: `docs/week3/misc/buildplan.md` (phased build spec in git), `docs/week3/plan.md` (narrative)
- Repo: `git@github.com:tanmay-xvx/LIAL.git` (private), branch `week3`

## What Week 3 covers (see `docs/week3/misc/buildplan.md` for phased spec; `docs/week3/plan.md` for narrative)
1. **Raspberry Pi Pico port** — compile wasmi for `thumbv6m-none-eabi`, create `Rp2040Hal`, prove Wasm execution on ARM Cortex-M0+. Validates "any silicon" promise.
2. **Dual-core execution model** — transport on Core 0, Wasm on Core 1. `LialExecutor` trait with single/dual-core impls.
3. **Wi-Fi transport + `LialTransport` trait** — untether from USB; LIAL-Link over persistent TCP.
4. **Extended control protocol** — opcodes 0x04-0x09 for stop, hot-swap, query, parameter set, streaming.
5. **Wireless runtime control** — host can stop/swap/parameterize running modules remotely via Python async API.
6. **SVD-driven manifests + auto-discovery** — parse SVD XML to auto-generate capability manifests.
7. **BLE + Wi-Fi device discovery** — mDNS and BLE scanning for already-flashed receivers.
8. **Multi-device orchestration** — device registry, LLM device routing, parallel execution.
9. **MCP server** — expose LIAL as MCP tool (key differentiator vs ESP-Claw).
10. **Safety hardening** — peripheral whitelisting, watchdog, Wasm validation.

## Research (see `docs/week3/research.md`)
- Firmware flashing methods (USB vs UART vs WiFi OTA vs BLE DFU)
- Multi-core architecture patterns (single/dual/multi-core Wasm execution)
- Wireless persistent connection design (TCP, heartbeat, mDNS)
- LIAL vs ESP-Claw competitive analysis and differentiation strategy
