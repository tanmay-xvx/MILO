# Week 3 — Implementation Record

This document is the technical companion to the Week 3 pull request. It describes **what shipped**, **why**, and **how to reproduce** builds and hardware tests. It is written for reviewers and future maintainers.

## Goals (from Week 3 planning)

Week 3 broadened LIAL beyond the ESP32-C3 reference board:

1. **Second silicon family** — Prove the same Wasm + syscall model on **Raspberry Pi Pico (RP2040)** (`thumbv6m-none-eabi`).
2. **Transport abstraction** — Decouple LIAL-Link framing from any single physical link (USB serial today; TCP / future BLE).
3. **Execution model groundwork** — Introduce a **`LialExecutor`** abstraction so single-core (blocking) and dual-core strategies can coexist.
4. **Wireless and tooling scaffolding** — Wi-Fi transport hooks on ESP32, **mDNS discovery** stubs, **device registry**, and an **MCP server** skeleton for agent-facing workflows.
5. **Protocol surface** — Reserve **opcodes 0x04–0x09** for streaming, stop, status, parameters, and hot-swap (receiver handling may be partial; host and docs align on names).
6. **Research and context** — Captured flashing methods, multi-core patterns, and competitive notes in `research.md` and `context.md`.

## 1. Raspberry Pi Pico (RP2040) receiver

### 1.1 Feature flag and build

The receiver crate gains a Cargo feature **`rp2040`** that pulls in:

- `rp2040-hal`, `rp2040-boot2`, `cortex-m`, `cortex-m-rt`
- `embedded-alloc` (linked-list first-fit heap)
- `usb-device`, `usbd-serial` (USB CDC to the host)
- `portable-atomic` (required by the dependency graph on Cortex-M0+)
- `embedded-hal` 0.2 as **`embedded-hal-0-2`** (for `Adc::read` / `OneShot` on the RP2040 ADC path)

**Important:** firmware for embedded targets must **not** enable the crate’s default `std` feature (which turns on `wasmi/std` and breaks `thumbv6m-none-eabi`). Build with:

```bash
cd lial-receiver
cargo build --release --no-default-features --features rp2040 --target thumbv6m-none-eabi
```

`elf2uf2-rs` is configured as the **runner** in `.cargo/config.toml` for `thumbv6m-none-eabi`. A **custom `memory.x`** is used so linking matches the Pico’s RAM layout when using `build-std`.

### 1.2 Boot and memory

- **`rp2040-boot2`**: the standard W25Q080 second-stage bootloader is linked via `#[unsafe(link_section = ".boot2")]`.
- **Heap**: a static `LlffHeap` backs `alloc` (Vec, String, Box) for manifest JSON and framing buffers on the Pico path.

### 1.3 Pinout and discovery manifest (reference wiring)

| Function | GPIO | Notes |
|----------|------|--------|
| Onboard LED | 25 | Digital output via `lial_gpio_set` |
| I2C0 SDA | 4 | SSD1306 (typ. `0x3C`) — external pull-ups as usual |
| I2C0 SCL | 5 | |
| ADC (pot) | 26 | Registered as **ADC channel `26`** in the manifest so drivers use `lial_adc_read(26)` |

The discovery manifest identifies the board as **`rp2040-pico`** and lists **I2C `0x3C`** without relying on a boot-time I2C scan (see §1.6).

### 1.4 USB CDC transport and large frames

The Pico talks LIAL-Link over **USB CDC serial** (not UART-to-USB adapter). Practical issues addressed:

- **Chunked writes (`usb_write_all`)**: A single logical frame (especially the JSON discovery response) can exceed one USB packet. Writes loop until all bytes are accepted, **polling `usb_dev`** between chunks so the stack can transmit.
- **Post-open delay and discovery retries (host)**: After opening the serial port, the host waits **1 s** and clears RX buffers; `request_discovery` retries **3×** with backoff to avoid races with USB enumeration.
- **USB during Wasm delays**: While Wasm runs, `lial_delay_ms` must still **poll USB**. Otherwise the CDC connection can stall or drop from the host’s perspective. The firmware registers a **temporary poll callback** (`rp2040::set_usb_poll` / `clear_usb_poll`) around `LialRuntime::execute` so delays keep the device connected.

### 1.5 `Rp2040Hal` (`rp2040.rs`)

- Implements **`LialHardware`** using the same dynamic peripheral pattern as ESP32 (`DynGpio`, `DynI2c`, `DynDelay`, etc.).
- **ADC**: `Rp2040AdcChannel` bundles `Adc` + `AdcPin` and implements **`DynAdc`** using `embedded_hal_0_2::adc::OneShot`.
- **Delay**: Timer-backed microseconds, with periodic USB poll when a callback is registered.
- **I2C reliability (`RecoveringI2c`)**:
  - I2C runs at **100 kHz** (reduced from 400 kHz for marginal wiring / SSD1306 robustness).
  - On transfer failure, **`i2c_bus_recover`** bit-bangs SCL/SDA via SIO to release a stuck slave, restores I2C pin functions, clears abort state, and briefly toggles `ic_enable` to flush FIFOs **without** re-hacking timing registers (an earlier approach reset the peripheral with mismatched 100 kHz vs “fast” mode and caused alternating success/failure).
  - **`RecoveringI2c`** retries the transfer once after recovery.

### 1.6 SSD1306 and “static noise” (LLM-generated drivers)

Symptom: I2C **succeeds** but the OLED shows **snow** across the panel.

**Cause:** SSD1306 GDDRAM is **undefined at power-on**. Wasm that only paints a few columns on page 0 leaves the rest of the framebuffer random — visually “noise.”

**Mitigations:**

- Host **system prompt** (`lial_host.py`): After the 26-byte init, the model is instructed to **clear all 8 pages** (129 bytes per page: `0x40` + 128 zeros) within stack limits, and to redraw responsibly in loops.
- Firmware: **No boot-time I2C scan** on Pico for the OLED path — scanning some displays can put them in an odd state; the manifest instead documents **`0x3C`**.

### 1.7 ESP32-C3 unchanged path

The existing `esp32c3` module and `esp_hal` entry remain the primary reference; minor edits may appear for shared types or transport Wi-Fi module gating. The **shared `main_loop`** in `lib.rs` still drives ESP32-style transports that implement `transport::LialTransport`.

## 2. Shared receiver architecture

### 2.1 `main_loop` + `LialExecutor`

`lib.rs` exposes **`main_loop`** for boards whose transport implements **`transport::LialTransport`** (e.g. ESP32 with `EmbeddedIoTransport`). It uses:

- **`SingleCoreExecutor`** (`executor.rs`) — constructs `LialRuntime`, runs Wasm, returns JSON result.
- **`PARAM_SLOTS`** — atomic `u32` slots for future `OP_SET_PARAM` / `lial_get_param` wiring.

### 2.2 Dual-core placeholder (`executor_dual.rs`)

**`DualCoreExecutor` / `LialExecutor` trait** sketch how Core 0 could service the link while Core 1 runs Wasm. The Pico **entry** currently uses a **dedicated USB loop** and direct `LialRuntime` invocation (simpler and sufficient for bring-up); the executor split remains available for RP2040 `multicore` integration later.

### 2.3 `transport.rs` (no_std)

Board-side framing helpers and `LialTransport` trait for `embedded-io` streams remain the single source of frame layout **[opcode u8][len u32 BE][payload]**.

### 2.4 `transport_wifi.rs` (ESP32, feature-gated)

Wi-Fi / TCP scaffolding for **unplugged** operation lives behind **`esp32c3-wifi`** (optional feature). It is not required for Pico bring-up.

### 2.5 `validation.rs`

Hooks for stricter Wasm module checks (for example constraints aligned with Week 3 safety goals) — integrate with the executor path as hardening matures.

### 2.6 `link.rs` opcode constants

Extended constants for future control plane messages:

- `0x04` `OP_STREAM_DATA`
- `0x05` `OP_STOP`
- `0x06` `OP_QUERY_STATUS`
- `0x07` `OP_STATUS_RESPONSE`
- `0x08` `OP_SET_PARAM`
- `0x09` `OP_HOT_SWAP`

Receiver dispatch for every opcode is not assumed complete; names are shared between Rust and Python.

### 2.7 `embedded_hal_adapter.rs`

Shared adapter logic; I2C scan behavior may differ per board (Pico manifest avoids scan; ESP32 may still scan).

## 3. Python host

### 3.1 `transport.py`

- **`LialTransport`** ABC: `read_frame`, `write_frame`, `close`, `is_connected`.
- **`SerialTransport`**: `pyserial`, 1 s settle time, `request_discovery` with retries, `push_bytecode`.
- **`TcpTransport`**: framing for future Wi-Fi receivers.
- Opcode constants match `link.rs`.

### 3.2 `lial_host.py`

- Imports transports from **`transport`** instead of inlining serial logic.
- **`SYSTEM_PROMPT`** enriched for SSD1306: init sequence, **mandatory full clear**, digit/letter fonts, bitmap-only rendering rules.
- Relaxed wording: if the user names a device (e.g. SSD1306), use known **0x3C** even when `devices_present` is empty (Pico).

### 3.3 Device orchestration and MCP (scaffolding)

| Module | Role |
|--------|------|
| `device_registry.py` | Named devices → transport + manifest |
| `lial_device.py` | Thin wrapper: push Wasm, query capabilities |
| `discovery.py` | **mDNS** discovery for `_lial._tcp.local.` (requires `zeroconf`; stub BLE hook) |
| `mcp_server.py` | MCP tool stubs: list devices, push, stop, query, set param, hot-swap |

These align with the Week 3 theme: **agents** and **multi-device** workflows without locking LIAL to one IDE or one transport.

## 4. Examples

- **`examples/test_drivers/blink_led`**: GPIO **25** for Pico onboard LED (was a different pin for ESP32).

## 5. Documentation artifacts

| File | Purpose |
|------|---------|
| `docs/week3/plan.md` | Phased Week 3 plan (reference; may evolve after review) |
| `docs/week3/research.md` | Flashing, USB vs UART, OTA, multi-core, LIAL vs ESP-Claw notes |
| `docs/week3/context.md` | Snapshot of pre–Week 3 repo state and file map |

## 6. Verified hardware scenarios (reported)

On **Pico** with this branch:

- Discovery returns **`rp2040-pico`** and gpio **`[25]`**.
- **Blink** onboard LED via generated Wasm.
- **ADC** on **GPIO 26** / channel **26** with live updates.
- **SSD1306** on I2C0 (`SDA=4`, `SCL=5`): stable display after init + **full clear** + bitmap digits.

Flashing: **`picotool load`** of the built ELF/UF2 is more reliable than drag-drop on some macOS setups.

## 7. Known follow-ups

- Wire **`DualCoreExecutor`** to RP2040 `multicore` and a mailbox for **non-blocking** runs.
- Implement full receiver-side handling for **opcodes 0x04–0x09** where not already present.
- Harden **`validation.rs`** and connect to push path.
- Publish UF2/bin artifacts via release workflow (build artifacts are **gitignored**: `*.uf2`).

---

*Last updated: Week 3 PR preparation — implementation summary for reviewers.*
