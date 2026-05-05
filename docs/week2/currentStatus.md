# Week 2 Current Status

**Started:** 2026-03-15 | **Updated:** 2026-05-04

---

## Milestone Achieved

**Full end-to-end hardware testing with LEDC PWM, ADC, I2C OLED, and
firmware distribution from GitHub Releases.**

- `lial init` auto-detects blank USB boards, downloads firmware from
  a GitHub release, and flashes via `espflash` (preferred) or `esptool`.
- `lial download` pulls per-family binaries from a published `manifest.json`.
- The ESP32-C3 receiver runs hardware LEDC PWM (10-bit, 5 kHz), 12-bit ADC,
  and I2C master with SSD1306 OLED display.
- LLM system prompt includes complete SSD1306 font tables, memory constraints,
  and capability-aware pin selection.
- A Python HIL harness drives real Wasm-compiled test drivers over serial
  and reports pass/fail per syscall.
- First beta release (`v0.1.0-beta`) published on GitHub with merged firmware
  binary and release manifest.

Pipeline today:

Natural language → GPT-4o (capability-aware prompt) → Rust → wasm → LIAL-Link
→ ESP32-C3 → `EmbeddedHalAdapter` (LEDC PWM + ADC + I2C) → physical peripherals
→ JSON result back to host.

## Completed (Week 2)

### Phase A — Firmware delivery
- `board_registry.py` with VID/PID mapping for ESP32, RP2040, AVR, STM32, SAMD.
- `flash_backends/` ABC with concrete backends: `esp32` (espflash + esptool
  fallback), `rp2040` (UF2 drag-drop + picotool reboot), `avr` (avrdude),
  `stm32` (dfu-util + stm32loader).
- `lial download` subcommand: interactive variant picker, SHA-256-verified
  download, release-manifest aware.
- `lial init` subcommand: USB enumeration, LIAL-firmware probe, guided flash.
- `lial_cli.py` unified CLI entry point.
- Private repo download fallback via `gh api` with token authentication.

### Phase B — CI / release pipeline
- `.github/workflows/build-receiver.yml` builds ESP32-C3 with stubs for
  RP2040 + ESP32-S3; runs `pytest` against `lial-host`.
- `v0.1.0-beta` GitHub release with merged firmware image and `manifest.json`.
- `release-manifest.json` with sha256, size, flash instructions per variant.

### Phase C — Board-agnostic receiver
- `embedded_hal_adapter.rs` with object-safe `DynPin` / `DynPwm` / `DynAdc` /
  `DynI2c` / `DynSpi` / `DynUart` / `DynDelay` traits plus blanket adapters.
- `Esp32C3Hal` refactored: accepts `DynPwm` for GPIO 5, I2C bus 0, ADC channel 0.
- `gpio_set(5, ...)` routes through PWM for backward compatibility.
- `PwmAdapter` wraps esp-hal LEDC channel into `DynPwm`.

### Phase D — Alphabet expansion + hardware peripherals
- **LEDC hardware PWM** on GPIO 5: 10-bit resolution, 5 kHz, via `esp-hal::ledc`.
  Timer leaked with `Box::leak` for `'static` lifetime requirement.
- **ADC1** on GPIO 2: 12-bit potentiometer reading via `Esp32AdcAdapter`.
- **I2C0** on GPIO 8 (SDA) / GPIO 9 (SCL): SSD1306 OLED at address 0x3C.
- Syscalls wired end-to-end: `lial_pwm_set`, `lial_adc_read`, `lial_spi_transfer`,
  `lial_uart_write`, `lial_uart_read`.
- Wasm fuel budget: 500M. Stack: 8 KB. Memory: 64 KB.

### Phase E — Rich discovery + LLM prompt
- `manifest.rs` emits structured JSON with per-peripheral pin lists, resolution,
  I2C scan results, Wasm memory/fuel limits, and the syscall Alphabet.
- `SYSTEM_PROMPT` rewritten with:
  - Complete SSD1306 5x8 font tables (digits 0–9, letters A–Z).
  - Concrete rendering examples (how to display "42" on the OLED).
  - Memory constraint warnings (never > 200 bytes on stack, page-by-page clearing).
  - Ban on `cfg!()` macros, `format!`, `to_string()`.
  - `#[unsafe(no_mangle)]` syntax enforcement.

### Phase F — E2E testing + release
- HIL test harness validates GPIO, ADC, PWM, I2C on real hardware.
- Wipe-and-reflash cycle verified: `espflash erase-flash` → `lial init` detects
  blank board → downloads from GitHub release → flashes → device boots with
  LIAL v0.1.0.

## Hardware validated

- ESP32-C3 Super Mini (OceanLabz, silicon rev v0.4), USB JTAG at
  `/dev/cu.usbmodem3101`.
- Breadboard rig:
  - External LED + 330 Ω on GPIO 5 (PWM brightness control).
  - 10 kΩ potentiometer wiper on GPIO 2 (ADC, 12-bit).
  - SSD1306 0.96" OLED on I2C0 (GPIO 8 SDA, GPIO 9 SCL, address 0x3C).
  - 4.7 kΩ pull-ups on SDA/SCL (built into OLED module).

## HIL Test Results

| Test | Status | Notes |
|------|--------|-------|
| GPIO (blink pin 5) | Pass | Routed through PWM full/zero duty |
| ADC (potentiometer) | Pass | 12-bit, 0–4095 range |
| PWM (LED brightness) | Pass | 10-bit LEDC, 5 kHz |
| I2C / OLED | Pass | SSD1306 text rendering with font bitmaps |
| UART (loopback) | Skip | No TX↔RX jumper wired |

## Not yet done (deferred to Week 3)

- **RP2040 port**: blocked on Raspberry Pi Pico setup.
- **SVD-driven manifests**: planned for Week 3 Layer 3b.
- **Wi-Fi / BLE transport**: planned for Week 3 Layer 4.
- **Production OTA**: `lial init` covers first-flash; OTA is Week 3.

## Outstanding risks / follow-ups

- Heap fragmentation on repeated Wasm pushes — not soak-tested yet.
- ADC potentiometer reports only min/max at the extremes; intermediate
  values work but wiring quality affects noise. Worth adding smoothing.
- `esptool` flash path doesn't reliably reset USB JTAG devices after flash;
  `espflash` preferred. `esptool` kept as fallback for systems without Rust.
