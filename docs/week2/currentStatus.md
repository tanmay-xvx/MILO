# Week 2 Current Status

**Started:** 2026-03-15 | **Updated:** 2026-03-19

---

## Milestone Achieved

**Board-agnostic firmware delivery + expanded Alphabet running end-to-end
in simulation.**

- `lial init` auto-detects blank USB boards and flashes the right receiver.
- `lial download` pulls per-family binaries from a published `manifest.json`.
- The ESP32-C3 receiver now emits a rich capability manifest at boot and
  speaks PWM / ADC / SPI / UART syscalls in addition to the Week-1 Alphabet.
- A Python HIL harness drives real Wasm-compiled test drivers over serial
  and reports pass/fail per syscall.

Pipeline today:

Natural language → GPT-4o (capability-aware prompt) → Rust → wasm → LIAL-Link
→ ESP32-C3 / RP2040 / ... → `EmbeddedHalAdapter` → physical peripherals → JSON
result back to host.

## Completed (Week 2)

### Phase A — Firmware delivery
- `board_registry.py` with VID/PID mapping for ESP32, RP2040, AVR, STM32, SAMD.
- `flash_backends/` ABC with concrete backends: `esp32` (esptool), `rp2040`
  (UF2 drag-drop + picotool reboot), `avr` (avrdude), `stm32` (dfu-util +
  stm32loader).
- `lial download` subcommand: interactive variant picker, SHA-256-verified
  download, release-manifest aware.
- `lial init` subcommand: USB enumeration, LIAL-firmware probe, guided flash.
- `lial_host.py` startup auto-flash for blank boards (toggleable with
  `--no-autoflash`).
- Unit + integration tests in `lial-host/tests/` covering every backend,
  manifest download, and init flow.

### Phase B — CI / release pipeline
- `.github/workflows/build-receiver.yml` builds ESP32-C3 today with stubs
  ready for RP2040 + ESP32-S3; runs `pytest` against `lial-host`.
- `scripts/generate_manifest.py` + `.github/workflows/publish-manifest.yml`
  collect per-variant artifacts and publish `manifest.json` to the release.

### Phase C — Board-agnostic receiver
- New `embedded_hal_adapter.rs` with object-safe `DynPin` / `DynPwm` /
  `DynAdc` / `DynI2c` / `DynSpi` / `DynUart` / `DynDelay` traits plus
  blanket adapters for every matching `embedded-hal 1.0` trait.
- `Esp32C3Hal` refactored to be a thin factory over `EmbeddedHalAdapter`.
- 12 receiver-side unit tests + 4 integration tests through the full Wasm
  runtime.

### Phase D — Alphabet expansion
- New syscalls wired end-to-end (trait → runtime binding → adapter → mock →
  LLM prompt): `lial_pwm_set`, `lial_adc_read`, `lial_spi_transfer`,
  `lial_uart_write`, `lial_uart_read`.
- HIL scripts under `lial-host/hil_tests/`:
  - `test_gpio.py` — output blink + input read.
  - `test_pwm.py` — LED fade (visual verification).
  - `test_adc.py` — voltage-divider mean assertion within ±10 % of Vcc/2.
  - `test_uart.py` — jumpered TX↔RX loopback.

### Phase E — Rich discovery
- `manifest.rs` emits a structured JSON manifest including per-peripheral
  pin lists, resolution, Vref, I²C scan results, SPI/UART bus wiring, Wasm
  memory / fuel limits, and the live syscall Alphabet.
- `EmbeddedHalAdapter::scan_i2c_bus` probes 0x08..=0x77 at boot so the host
  sees which I²C slaves are actually on the bus.
- `SYSTEM_PROMPT` rewritten to be *capability-aware*: the LLM is told
  explicitly to consult `device_info` before choosing any pin/channel/bus.

### Cross-cutting
- `lial-host/hil_test.py` test harness: compiles Rust snippets, pushes
  them over LIAL-Link to a real device, asserts on returned logs.
  Discovers `@hil_test`-decorated functions in `hil_tests/*.py`.

## Not yet done (deferred to Week 3 or hardware-dependent)

- **Phase F — RP2040 port** (`phase-f-rp2040`): board module, CI job,
  full HIL run. Blocked on Raspberry Pi Pico arriving.
- **Phase F — ESP32-S3 port** (`phase-f-s3`): same, blocked on S3 arriving.
- Production OTA: still stubbed; the current delivery story is one-shot
  flashing via `lial init` / `lial download`.

## Hardware validated

- ESP32-C3 DevKitC-02 (silicon rev v0.4), LED on GPIO 5, USB Serial JTAG at
  `/dev/cu.usbmodem101`.
- Breadboard rig with:
  - LED + 330 Ω on GPIO 5 (Phase D PWM fade).
  - 2× 10 kΩ voltage divider, midpoint on GPIO 0 (Phase D ADC).
  - Jumper wire GPIO 20 → GPIO 21 (Phase D UART loopback).

## Outstanding risks / follow-ups

- Heap fragmentation when the Wasm payload is repeatedly pushed — not yet
  exercised with a long-running soak test. Tracked for Week 3.
- `esptool` flash path assumes a 4 MB ESP32-C3; other variants need the
  backend's chip detection expanded. Non-blocking.
- HIL tests for PWM are visual (LED fade) — moving to analog feedback via a
  photoresistor is a Week 3 nice-to-have.
