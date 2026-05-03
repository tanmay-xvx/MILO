# Week 2 Changelog

Changes made during Week 2 development.

---

## Board-Agnostic Receiver + Alphabet Expansion — 2026-03-19

### Added

#### Host — firmware delivery
- **`board_registry.py`** — `BoardFamily` / `DetectedDevice` dataclasses, USB
  VID/PID → family map for ESP32, RP2040, AVR, STM32, SAMD, and
  `enumerate_usb_devices()` helper.
- **`flash_backends/`** — `FlashBackend` ABC with concrete backends:
  - `esp32.py` wraps `esptool` (chip detection, offset-aware `.bin` flash).
  - `rp2040.py` uses UF2 drag-drop with a `picotool reboot` fallback.
  - `avr.py` wraps `avrdude` for ATmega variants.
  - `stm32.py` wraps `dfu-util` (USB DFU) and `stm32loader` (UART bootloader).
- **`lial_commands/download.py`** — `lial download` CLI: fetches
  `manifest.json`, presents an interactive variant picker, SHA-256 verifies
  the downloaded binary.
- **`lial_commands/init.py`** — `lial init` CLI: enumerates USB, probes for a
  LIAL discovery frame, offers to flash the right backend with the right
  image from the manifest.
- **`_maybe_autoflash`** in `lial_host.py` — runs `lial init` at startup if a
  blank board of a known family is detected; suppressible via
  `--no-autoflash`.

#### Host — CI/release
- **`.github/workflows/build-receiver.yml`** — matrix-style build (ESP32-C3
  today, RP2040 + ESP32-S3 stubbed) plus pytest-on-PR.
- **`.github/workflows/publish-manifest.yml`** — on release: downloads every
  firmware artifact, runs `scripts/generate_manifest.py`, uploads
  `manifest.json` back to the release.
- **`scripts/generate_manifest.py`** — walks an artifacts directory, emits a
  versioned JSON manifest with per-variant `sha256`, `size_bytes`,
  `download_url`, and `flash_instructions`.

#### Receiver — board-agnostic core
- **`embedded_hal_adapter.rs`** — `EmbeddedHalAdapter` + object-safe traits
  `DynPin`, `DynPwm`, `DynAdc`, `DynI2c`, `DynSpi`, `DynUart`, `DynDelay`
  with blanket impls from `embedded-hal 1.0`. Builder API (`pin()`,
  `pwm()`, `adc()`, `i2c()`, `spi()`, `uart()`, `delay()`, `uptime_fn()`,
  `log_sink()`) keeps board modules declarative.
- **`scan_i2c_bus()`** — boot-time probe of 0x08..=0x77 using a 1-byte read
  to surface physically-present slaves in the discovery manifest.
- **`manifest.rs`** — JSON manifest builder with typed `Capabilities`,
  `ManifestHeader`, `GpioCapability`, `PwmCapability`, `AdcCapability`,
  `I2cCapability`, `SpiCapability`, `UartCapability`, plus the canonical
  `ALPHABET` constant.

#### Receiver — Alphabet expansion
- `LialHardware` gained `pwm_set`, `adc_read`, `spi_transfer`, `uart_write`,
  `uart_read` (all with safe default impls to preserve backwards compat).
- Wasm bindings for all five new syscalls registered in
  `LialRuntime::register_syscalls`.
- `LaptopMock` implements the new syscalls with debug logging and
  deterministic / loopback behaviour.

#### Host — LLM + compiler
- `lial_compiler.py` template now declares all 11 syscalls so the LLM's
  generated code links.
- `SYSTEM_PROMPT` rewritten to be capability-aware: the model is told
  explicitly to consult `device_info.capabilities.*` before picking any
  pin / channel / bus, and to refuse (via `lial_log`) when a required
  peripheral is missing.

#### HIL + tests
- **`hil_test.py`** — `HilTest` harness: opens a serial port, reads the
  discovery frame, compiles Rust snippets via `lial_compiler`, pushes
  them as `OP_BYTECODE_PUSH` frames, parses the result JSON, and exposes
  `assert_ok` / `assert_log` / `assert_log_matching`. Auto-discovers
  `@hil_test`-decorated functions under `hil_tests/`.
- **`hil_tests/test_gpio.py`**, **`test_pwm.py`**, **`test_adc.py`**,
  **`test_uart.py`** — one per syscall; each is a real Rust snippet that
  exercises the peripheral on hardware.
- Unit tests added for `board_registry`, every flash backend,
  `lial download`, `lial init`, `scripts/generate_manifest.py`, the
  `EmbeddedHalAdapter` (including `scan_i2c_bus`), and the manifest
  builder.

### Changed
- `Esp32C3Hal` is now a thin wrapper around `EmbeddedHalAdapter`; all
  `LialHardware` methods delegate. The struct still owns the LIAL-Link
  USB-Serial-JTAG halves.
- ESP32-C3 `main.rs` builds its discovery manifest from
  `lial_receiver::manifest::build` instead of the hand-rolled JSON string
  literal it used in Week 1.
- Laptop `main.rs` (`--stdin`) similarly emits a structured manifest.
- `lial_host.py` restructured into `run`, `download`, `init` subparsers
  via `argparse`.

### Fixed
- `hil_test.py` running as `__main__` double-loaded itself when
  `hil_tests/*` did `from hil_test import ...`; the decorator registered
  tests against the second module copy. Fixed by aliasing
  `sys.modules["hil_test"]` to `sys.modules["__main__"]` in the script
  entry point.
- `tests/test_init_flow.py` was patching the wrong attribute
  (`lial_commands.init.serial.Serial`) for a lazily-imported `serial`
  module; now patches `serial.Serial` directly.

### Dependencies
- `lial-receiver/Cargo.toml`: added `embedded-hal = "1.0"`,
  `embedded-hal-nb = "1.0"`.
- `lial-host/requirements.txt`: added `esptool`, `pyusb`.

### Deferred to Week 3 / hardware-dependent
- RP2040 port via `rp-hal` (needs Raspberry Pi Pico).
- ESP32-S3 port via `esp-hal`.
- OTA update path — today `lial init` covers first-flash only.

---

## E2E Hardware Testing + Release Pipeline — 2026-05-04

### Added

- **`lial_cli.py`** — Unified CLI entry point (`python lial_cli.py init`,
  `python lial_cli.py download`). Registers `init` and `download` subcommands.
- **Private repo download fallback** (`download.py`) — When plain URL returns
  403/404, falls back to `gh api` with token auth. Handles both
  `/releases/download/TAG/` and `/releases/latest/download/` URL patterns.
- **`espflash`-preferred flash backend** (`flash_backends/esp32.py`) — Prefers
  `espflash` over `esptool` for probe (`board-info`) and flash (`write-bin`
  for .bin, `flash` for ELF). Falls back to `esptool` when `espflash` is
  unavailable.
- **LEDC hardware PWM** on GPIO 5 in `main.rs` — 10-bit resolution, 5 kHz via
  `esp-hal::ledc`. Timer leaked with `Box::leak` for `'static` lifetime.
  `PwmAdapter` wraps the LEDC channel into `DynPwm`.
- **ADC1** on GPIO 2 via `Esp32AdcAdapter` implementing `DynAdc` (12-bit).
- **I2C0** on GPIO 8/9 via `I2cAdapter` for SSD1306 OLED at 0x3C.
- **`release-manifest.json`** — Machine-readable index for `lial init` /
  `lial download` firmware distribution.
- **`v0.1.0-beta` GitHub release** — First pre-release with merged ESP32-C3
  firmware binary (bootloader + partitions + app) and `manifest.json`.
- **LLM system prompt additions** (`lial_host.py`):
  - Complete SSD1306 5×8 font tables (digits 0–9, letters A–Z).
  - Concrete rendering examples.
  - Memory constraint warnings.
  - Ban on `cfg!()` macros, `format!`, raw ASCII display data.
  - `#[unsafe(no_mangle)]` syntax enforcement.
- **Week 3 plan Layer 3b** — SVD-driven manifest generation and auto-discovery.

### Changed

- **`Esp32C3Hal::new`** — Now accepts `Box<dyn DynPwm>` instead of `Output`
  for GPIO 5. Registers PWM channel instead of GPIO pin.
- **`gpio_set(5, ...)`** — Routes through `pwm_set(5, ...)` for backward
  compatibility (full duty = ON, zero = OFF).
- **Wasm stack** — 4 KB → 8 KB (`lial_compiler.py`).
- **Execution timeout** — 30s → 120s (`lial_host.py`).
- **Fuel budget** — 50M → 500M (`main.rs`).
- **Discovery protocol** — Host sends active request frame instead of
  passive wait.
- **Manifest URL** — Points to `tanmay-xvx/LIAL` with `v0.1.0-beta` tag.
- **Firmware image format** — Merged image via `espflash save-image --merge`
  (bootloader + partition table + app).

### Fixed

- **OLED garbage output** — LLM sent raw ASCII instead of font bitmaps.
- **Wasm OOB crash** — 1025-byte stack array caused overflow; enforced
  page-by-page clearing.
- **Fuel exhaustion** — Complex OLED tasks exceeded 50M fuel.
- **`cfg!()` in Wasm** — Prompt now bans compile-time feature checks.
- **LED flickering** — Changed LEDC from 14-bit/1 kHz to 10-bit/5 kHz.
- **USB JTAG post-flash boot** — `esptool` didn't reset properly; `espflash`
  now preferred.
- **Private repo 404** — Added `gh api` fallback with token auth.

### Dependencies

- `lial-receiver/Cargo.toml`: added `nb = "1"` (optional, for ADC).
- `lial-host`: `esptool` added to venv for probe fallback.
