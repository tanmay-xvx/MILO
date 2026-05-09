# LIAL — Complete Project Walkthrough

A file-by-file, function-by-function guide to the entire LIAL codebase. Written for deep understanding — every file is explained, with caveats and gaps called out explicitly.

**Last updated:** Week 3 (post PR #5 + #6 merge)

---

## Table of Contents

1. [Build & Configuration](#1-build--configuration)
2. [Receiver — Core Library (`lib.rs`)](#2-receiver--core-library)
3. [Receiver — Wire Protocol (`link.rs`)](#3-receiver--wire-protocol)
4. [Receiver — Transport Layer (`transport.rs`, `transport_wifi.rs`)](#4-receiver--transport-layer)
5. [Receiver — Discovery Manifest (`manifest.rs`)](#5-receiver--discovery-manifest)
6. [Receiver — Hardware Abstraction (`embedded_hal_adapter.rs`)](#6-receiver--hardware-abstraction)
7. [Receiver — Board HALs (`esp32c3.rs`, `rp2040.rs`)](#7-receiver--board-hals)
8. [Receiver — Execution Engine (`executor.rs`, `executor_dual.rs`)](#8-receiver--execution-engine)
9. [Receiver — Safety (`validation.rs`)](#9-receiver--safety)
10. [Receiver — Mock (`mock.rs`)](#10-receiver--mock)
11. [Receiver — Entry Points (`main.rs`)](#11-receiver--entry-points)
12. [Host — Transport (`transport.py`)](#12-host--transport)
13. [Host — Interactive LLM Loop (`lial_host.py`)](#13-host--interactive-llm-loop)
14. [Host — Wasm Compiler (`lial_compiler.py`)](#14-host--wasm-compiler)
15. [Host — Device Abstraction (`lial_device.py`, `device_registry.py`)](#15-host--device-abstraction)
16. [Host — Discovery (`discovery.py`)](#16-host--discovery)
17. [Host — MCP Server (`mcp_server.py`)](#17-host--mcp-server)
18. [Host — CLI & Flashing (`lial_cli.py`, `board_registry.py`, flash backends)](#18-host--cli--flashing)
19. [Host — Testing (`hil_test.py`, `serial_push.py`)](#19-host--testing)
20. [Patches (wasmi forks)](#20-patches)
21. [Examples](#21-examples)
22. [Summary: What Works, What's Partial, What's Missing](#22-summary)

---

## 1. Build & Configuration

### `lial-receiver/Cargo.toml`

The receiver is a single Rust crate with **feature-gated** board support:

```
[features]
default = ["std"]                    # laptop mock — pulls wasmi/std, serde_json/std
std = ["wasmi/std", "serde_json/std"]
esp32c3 = [dep:esp-hal, dep:esp-alloc, dep:portable-atomic, dep:esp-bootloader-esp-idf, dep:nb]
esp32c3-wifi = ["esp32c3", dep:esp-wifi, dep:smoltcp]
rp2040 = [dep:rp2040-hal, dep:rp2040-boot2, dep:cortex-m, dep:cortex-m-rt, dep:embedded-alloc,
          dep:usb-device, dep:usbd-serial, dep:portable-atomic, dep:embedded-hal-0-2]
```

**Key dependencies:** `wasmi 1.0.9` (patched, via `[patch.crates-io]`), `serde_json` (alloc-only), `embedded-hal 1.0`, `embedded-hal-nb`, `embedded-io`.

**Caveat:** You must use `--no-default-features` when building for embedded targets, otherwise `wasmi/std` is pulled in and the build fails on `thumbv6m` / `riscv32imc`.

### `lial-receiver/.cargo/config.toml`

Two target configs:

- **`riscv32imc-unknown-none-elf`** (ESP32-C3): sets `portable_atomic_unsafe_assume_single_core`, links with `-Tlinkall.x`.
- **`thumbv6m-none-eabi`** (Pico): sets `--nmagic`, links with `-Tlink.x`. Runner is `elf2uf2-rs -d`.

Also enables **`build-std = ["core", "alloc"]`** and **`build-std-features = ["compiler-builtins-mem"]`** under `[unstable]` — required because Pico needs nightly `build-std`.

### `lial-receiver/memory.x`

Linker script for RP2040:

```
MEMORY {
    BOOT2 : ORIGIN = 0x10000000, LENGTH = 0x100
    FLASH : ORIGIN = 0x10000100, LENGTH = 2048K - 0x100
    RAM   : ORIGIN = 0x20000000, LENGTH = 264K
}
```

Only used when building with `--target thumbv6m-none-eabi`.

### `.gitignore`

Ignores `__pycache__/`, `*.py[cod]`, `build/`, `*.o`, `*.wasm`, `.env`, `target/`, and `*.uf2` (firmware images built locally).

---

## 2. Receiver — Core Library

### `lial-receiver/src/lib.rs` (~524 lines)

This is the heart of LIAL. It defines:

#### `LialHardware` trait (lines ~33–65)

The contract every board HAL must implement:

| Method | Required? | Purpose |
|--------|-----------|---------|
| `gpio_set(pin, state)` | Yes | Digital output |
| `gpio_get(pin) -> u32` | Yes | Digital input |
| `delay_ms(ms)` | Yes | Blocking delay |
| `get_uptime_us() -> u64` | Yes | Microsecond timer |
| `i2c_transfer(addr, tx, rx) -> i32` | Yes | I2C read/write |
| `log(message)` | Yes | UTF-8 log message |
| `pwm_set(channel, duty)` | No (default no-op) | PWM duty 0–10000 |
| `adc_read(channel) -> u32` | No (default 0) | ADC raw value |
| `spi_transfer(bus, tx, rx) -> i32` | No (default -1) | SPI |
| `uart_write(bus, data) -> i32` | No (default -1) | UART TX |
| `uart_read(bus, buf, timeout) -> i32` | No (default -1) | UART RX |

**Caveat:** `spi_transfer`, `uart_write`, `uart_read` have default impls that return `-1` (error). No board HAL actually implements them yet — they exist so the Wasm ABI doesn't break if an LLM-generated driver calls them.

#### `LialError` enum (lines ~68–85)

`ModuleInvalid`, `MissingExport`, `FuelExhausted`, `Trapped`. Implements `Display`; implements `std::error::Error` only under `#[cfg(feature = "std")]`.

#### `HostState<H>` struct (lines ~90–94)

Passed into the wasmi `Store`. Holds `hw: H` (hardware), `memory: Option<Memory>` (Wasm linear memory), and `logs: Vec<String>` (captured log messages).

#### `LialRuntime<H>` struct (lines ~96–99)

The Wasm execution engine. Fields: `engine: Engine`, `store: Store<HostState<H>>`, `linker: Linker<HostState<H>>`.

#### `LialRuntime::new()` (lines ~101–)

- Creates a wasmi `Config` with fuel metering enabled.
- Wraps all 12 syscalls as `Func::wrap(...)` closures that pull `HostState` from the `Caller`, read/write Wasm linear memory, and delegate to the `LialHardware` trait.
- Each syscall reads pointers/lengths from Wasm memory and calls the corresponding `hw.*` method.

**Syscalls registered in the linker:**

| Wasm import name | Rust closure logic |
|------------------|--------------------|
| `lial_gpio_set` | `hw.gpio_set(pin, state)` |
| `lial_gpio_get` | `hw.gpio_get(pin)` → returns u32 |
| `lial_delay_ms` | `hw.delay_ms(ms)` |
| `lial_get_uptime_us` | `hw.get_uptime_us()` → returns u64 (split into two u32 for Wasm) |
| `lial_i2c_transfer` | Reads tx from Wasm mem, calls `hw.i2c_transfer`, writes rx back |
| `lial_log` | Reads UTF-8 string from Wasm mem, calls `hw.log`, pushes to `logs` |
| `lial_pwm_set` | `hw.pwm_set(channel, duty)` |
| `lial_adc_read` | `hw.adc_read(channel)` → returns u32 |
| `lial_spi_transfer` | Reads tx, calls `hw.spi_transfer`, writes rx |
| `lial_uart_write` | Reads data, calls `hw.uart_write` |
| `lial_uart_read` | Calls `hw.uart_read`, writes to Wasm mem |
| `lial_get_param` | `executor::get_param(slot)` — reads atomic param slot |

**Caveat — `lial_get_uptime_us`:** Returns a u64 but Wasm only has i32/i64. The implementation packs into two i32 return values via a multi-value return hack that uses a pointer — see the closure for details.

#### `LialRuntime::execute()` (lines ~)

1. Compiles the Wasm bytecode into a `Module`.
2. Instantiates it with the linker (links all syscalls).
3. Grabs the `"memory"` export and stores it in `HostState`.
4. Looks up the `run_logic` export (or the provided entry name).
5. Adds fuel if configured.
6. Calls the Wasm function.
7. Returns `Ok(logs)` or `Err(LialError)`.

#### `LialRuntime::into_hardware()` (line ~)

Consumes the runtime and returns the `H` — used to reclaim ownership of peripherals after Wasm execution (important because the Pico loop re-uses `hal` across multiple runs).

#### `main_loop()` (lines ~103–217)

Generic function: `fn main_loop<T: LialTransport, H: LialHardware + 'static>(transport, hal, manifest_json) -> !`

Infinite loop that:
1. Reads a frame from transport.
2. Dispatches by opcode:
   - **`0x01` Discovery** → responds with manifest JSON.
   - **`0x02` Bytecode Push** → validates Wasm imports via `validation::validate_wasm_imports`, then submits to `SingleCoreExecutor`, polls result, sends JSON response.
   - **`0x05` Stop** → calls `executor.stop()`, responds with `{"stopped":true}`.
   - **`0x06` Query** → responds with `{"status":"...", "running":...}`.
   - **`0x08` SetParam** → parses 8 bytes (slot u32 BE + value u32 BE), writes to `executor::PARAM_SLOTS`.
   - **`0x09` HotSwap** → validates, stops current, submits new bytecode.
   - Unknown → responds with error JSON.

**Caveat:** `OP_STREAM_DATA` (0x04) has no dedicated handler — falls through to "unknown opcode". The Pico entry point does **not** use `main_loop` at all (see §11).

#### Module declarations

Conditionally compiles board modules:
- `#[cfg(feature = "esp32c3")] pub mod esp32c3;`
- `#[cfg(feature = "rp2040")] pub mod rp2040;`
- `#[cfg(feature = "std")] pub mod mock;`
- Always: `embedded_hal_adapter`, `executor`, `link`, `manifest`, `transport`, `validation`

---

## 3. Receiver — Wire Protocol

### `lial-receiver/src/link.rs` (~105 lines)

Defines LIAL-Link v0.1 framing.

#### Constants (lines 12–20)

```rust
OP_DISCOVERY:      0x01
OP_BYTECODE_PUSH:  0x02
OP_EXEC_RESULT:    0x03
OP_STREAM_DATA:    0x04
OP_STOP:           0x05
OP_QUERY_STATUS:   0x06
OP_STATUS_RESPONSE:0x07
OP_SET_PARAM:      0x08
OP_HOT_SWAP:       0x09
```

#### `Frame` struct

```rust
pub struct Frame { pub opcode: u8, pub payload: Vec<u8> }
```

`Frame::new(opcode, payload)` and `Frame::serialize() -> Vec<u8>` (prepends `[opcode, len_be_4bytes]`).

#### `LinkError` enum

`Io(String)`, `UnexpectedOpcode { expected, got }`, `ConnectionClosed`.

#### `#[cfg(feature = "std")]` helpers

`read_frame(reader) -> Result<Frame, LinkError>` and `write_frame(writer, frame) -> Result<(), LinkError>` — used by `StdioTransport` and laptop mock. Not available on embedded.

---

## 4. Receiver — Transport Layer

### `lial-receiver/src/transport.rs` (~90 lines)

#### `LialTransport` trait (line 10)

```rust
pub trait LialTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError>;
    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError>;
}
```

#### `EmbeddedIoTransport<W, R>` (line 18)

Generic transport over any `embedded_io::Write + Read` pair. Used by ESP32 (USB Serial JTAG `split()`) and could work for any embedded UART.

- `read_exact_bytes(&mut self, buf)` — spins until all bytes read.
- `read_frame()` — reads 5-byte header, parses opcode + length, reads payload.
- `write_frame()` — serializes frame and writes with `write_all`.

#### `StdioTransport` (line 65, `#[cfg(feature = "std")]`)

Wraps `stdin`/`stdout` locks. Delegates to `link::read_frame` / `link::write_frame`. Used by the laptop mock path.

### `lial-receiver/src/transport_wifi.rs` (~64 lines)

#### `WifiTcpTransport` struct

Has `rx_buf`, `tx_buf` (1536 bytes each), and `connected: bool`.

**THIS IS A STUB.** Both `read_frame()` and `write_frame()` return `Err(LinkError::Io("wifi transport not yet active"))`.

The plan was for `esp-wifi` + `smoltcp` socket polling. The struct and trait impl exist for compilation, but no real TCP I/O happens.

---

## 5. Receiver — Discovery Manifest

### `lial-receiver/src/manifest.rs` (~120 lines)

Builds the JSON hardware manifest that the receiver sends to the host on `OP_DISCOVERY`.

#### Structs

- **`ManifestHeader`**: `board`, `family`, `firmware_version`, `ram_kb`, `flash_kb`, `max_wasm_memory_kb`, `max_wasm_stack_kb`, `fuel_default`.
- **`Capabilities`**: aggregates `GpioCapability`, `PwmCapability`, `AdcCapability`, `I2cCapability` vec, `SpiCapability` vec, `UartCapability` vec. Derives `Default`.
- **`GpioCapability`**: `pins: Vec<u32>`.
- **`PwmCapability`**: `pins: Vec<u32>`, `resolution_bits: u32`.
- **`AdcCapability`**: `pins: Vec<u32>`, `resolution_bits: u32`, `vref_mv: u32`.
- **`I2cCapability`**: `bus_id`, `sda_pin`, `scl_pin`, `devices_present: Vec<u32>`.
- **`SpiCapability`**: `bus_id`, `mosi_pin`, `miso_pin`, `sck_pin`.
- **`UartCapability`**: `bus_id`, `tx_pin`, `rx_pin`.

#### `build(header, caps) -> String`

Hand-constructs JSON using `serde_json::json!` macro. Returns the serialized string. This is what gets sent as the `OP_DISCOVERY` payload.

**Why hand-built JSON?** `serde_json` with `alloc` (no `std`) doesn't support `#[derive(Serialize)]` without `serde_derive`, which was avoided to keep the dependency tree small for `no_std`.

---

## 6. Receiver — Hardware Abstraction

### `lial-receiver/src/embedded_hal_adapter.rs` (~661 lines)

The board-agnostic adapter layer. Any board that can provide `embedded-hal` 1.0 trait objects can use this.

#### Dynamic dispatch traits (object-safe wrappers)

| Trait | Wraps | Method |
|-------|-------|--------|
| `DynPin` | `OutputPin` + `InputPin` | `set_high()`, `set_low()`, `is_high()` |
| `DynPwm` | `SetDutyCycle` | `set_duty(duty_0_10000)` — scales to hardware resolution |
| `DynAdc` | board-specific | `read_raw() -> u32` |
| `DynI2c` | `I2c` | `transfer(addr, tx, rx) -> i32` |
| `DynSpi` | `SpiDevice` | `transfer(tx, rx) -> i32` |
| `DynUart` | Read + Write | `write(data) -> i32`, `read(buf, timeout_ms) -> i32` |
| `DynDelay` | `DelayNs` | `delay_ms(ms)` |

#### Adapter structs

- **`PinAdapter<P>`** — wraps any `OutputPin + InputPin` → `DynPin`.
- **`PwmAdapter<P>`** — wraps any `SetDutyCycle` → `DynPwm`. Scales `duty_0_10000` to `max_duty_cycle()`.
- **`I2cAdapter<I>`** — wraps any `embedded_hal::i2c::I2c` → `DynI2c`.
- **`SpiAdapter<S>`** — wraps `SpiDevice` → `DynSpi`.
- **`UartAdapter<R, W>`** — wraps `Read + Write` → `DynUart`.
- **`DelayAdapter<D>`** — wraps `DelayNs` → `DynDelay`.

#### `EmbeddedHalAdapter` struct

The main adapter. Holds:
- `pins: BTreeMap<u32, Box<dyn DynPin>>` — registered GPIO pins
- `pwm_channels: BTreeMap<u32, Box<dyn DynPwm>>` — PWM channels
- `adc_channels: BTreeMap<u32, Box<dyn DynAdc>>` — ADC channels
- `i2c_buses: BTreeMap<u32, Box<dyn DynI2c>>` — I2C buses
- `spi_buses: BTreeMap<u32, Box<dyn DynSpi>>` — SPI buses
- `uart_buses: BTreeMap<u32, Box<dyn DynUart>>` — UART buses
- `delay: Box<dyn DynDelay>` — delay provider
- `logs: Vec<String>` — captured log messages

**Registration methods:** `register_pin(id, pin)`, `register_pwm(id, pwm)`, `register_adc(id, adc)`, `register_i2c(bus, i2c)`, etc.

**Implements `LialHardware`:** Each syscall method looks up the peripheral by ID in the BTreeMap. If not found, it silently does nothing (returns 0 or -1). This is by design — the LLM may try to use a pin that doesn't exist.

#### `scan_i2c_bus(bus_id) -> Vec<u32>` (line ~)

Probes I2C addresses 0x08–0x77 with a 1-byte write, then read fallback. Returns a vec of addresses that ACK'd.

**Caveat:** On Pico, this is **not called** at boot because scanning can leave the SSD1306 in a bad state. The manifest hard-codes `0x3C` instead.

---

## 7. Receiver — Board HALs

### `lial-receiver/src/esp32c3.rs` (~100 lines)

#### `Esp32C3Hal` struct

A thin wrapper around `EmbeddedHalAdapter`. Constructor:

```rust
pub fn new(led_pwm: Box<dyn DynPwm>, i2c: I2c, adc: Adc, adc_pin: AdcPin) -> Self
```

Registers:
- Pin **5** as PWM channel (via `PwmAdapter`).
- I2C bus **0** (via `I2cAdapter` wrapping `esp-hal` I2C).
- ADC channel **0** (via a custom `Esp32C3AdcChannel` struct implementing `DynAdc`).
- Delay via `esp_hal::delay::Delay`.

Delegates all `LialHardware` methods to the inner `EmbeddedHalAdapter`.

**Caveat:** `gpio_set(5, ...)` routes through **PWM** (sets duty 0 or 10000), not a raw GPIO pin. This is because GPIO 5 on the ESP32 bench is wired through LEDC.

### `lial-receiver/src/rp2040.rs` (~300 lines)

Much more complex due to USB CDC polling and I2C recovery.

#### `Rp2040Hal` struct

Wraps `EmbeddedHalAdapter`. Constructor:

```rust
pub fn new(led: Pin<..., PushPullOutput>, i2c: I2C, adc_channel: Rp2040AdcChannel, timer: &'static Timer) -> Self
```

Registers:
- Pin **25** (onboard LED) via `PinAdapter`.
- I2C bus **0** via **`RecoveringI2c`** (not raw I2C — see below).
- ADC channel **26** (GPIO 26) via `Rp2040AdcChannel`.
- Delay via `Rp2040Delay` (timer-based, with USB polling).

#### `Rp2040AdcChannel` struct

Bundles `Adc` + `AdcPin` from `rp2040-hal`. Implements `DynAdc` using `embedded_hal_0_2::adc::OneShot` (the 0.2 version of embedded-hal, because rp2040-hal's ADC only implements the old `OneShot` trait, not embedded-hal 1.0).

#### `Rp2040Delay` struct

Uses the hardware timer (`TIMER_PTR` static) for microsecond-accurate delays. During delays, it calls **`poll_usb_if_available()`** every ~1 ms.

#### USB polling statics

```rust
static USB_POLL_FN: AtomicUsize = ...;   // function pointer
static USB_POLL_CTX: AtomicUsize = ...;  // context pointer
```

- `set_usb_poll(fn, ctx)` — registers a callback to poll USB during delays.
- `clear_usb_poll()` — removes the callback.
- `poll_usb_if_available()` — calls the callback if set.

This is how the Pico keeps USB CDC alive while Wasm runs `lial_delay_ms(5000)` — without it, the host would see the connection drop.

#### `i2c_bus_recover()` function

Manual I2C bus recovery via GPIO bit-banging:
1. Overrides GP5 (SCL) and GP4 (SDA) to SIO function.
2. Toggles SCL up to 9 times to clock out a stuck slave.
3. Generates a STOP condition (SDA low→high while SCL high).
4. Restores pins to I2C function.
5. Clears abort state (`IC_CLR_TX_ABRT`), disables/re-enables peripheral to flush FIFOs.

**Important:** Does NOT reconfigure timing registers. An earlier version did (`speed().fast()`) which caused 100 kHz vs 400 kHz mismatch and alternating failures.

#### `RecoveringI2c<I>` struct

Wraps any `embedded_hal::i2c::I2c`. On transfer failure:
1. Calls `i2c_bus_recover()`.
2. Retries the transfer once.
3. Returns 0 on success, -1 if retry also fails.

---

## 8. Receiver — Execution Engine

### `lial-receiver/src/executor.rs` (~178 lines)

#### `PARAM_SLOTS: [AtomicU32; 8]`

Eight shared parameter slots. Wasm reads them via `lial_get_param(slot)`. The host writes them via `OP_SET_PARAM` (opcode 0x08).

#### `get_param(slot) -> u32` / `set_param(slot, value)`

Simple atomic load/store (Relaxed ordering).

#### `ExecResult` struct

`ok: bool`, `logs: Vec<String>`, `error: Option<String>`.

#### `ExecStatus` enum

`Idle`, `Running`, `Completed`, `Stopped`.

#### `LialExecutor` trait

```rust
pub trait LialExecutor {
    type Hardware: LialHardware;
    fn submit(&mut self, bytecode: &[u8]);
    fn poll_result(&mut self) -> Option<ExecResult>;
    fn is_running(&self) -> bool;
    fn stop(&mut self);
    fn status(&self) -> ExecStatus;
}
```

#### `SingleCoreExecutor<H>`

Blocking implementation:
- `submit()` → creates `LialRuntime`, calls `execute()`, stores result. All happens inline.
- `poll_result()` → returns the stored result (always available immediately since `submit` blocks).
- `stop()` → sets status to `Stopped` (can't actually interrupt a blocking run).
- Reclaims hardware via `runtime.into_hardware()` after each execution.

**Caveat:** `stop()` is a no-op for blocking runs — it only affects the `status()` return. A running Wasm module can't be interrupted mid-execution in single-core mode.

### `lial-receiver/src/executor_dual.rs` (~173 lines)

#### `DualCoreExecutor<H>`

Designed for RP2040 Core 0/1 split. Has `core1_available: bool`.

**When `core1_available = false`** (current state): falls back to **single-core inline execution** (same as `SingleCoreExecutor`).

**When `core1_available = true`** (NOT YET WIRED): would use:
- `EXEC_STATE: AtomicU8` — 0=idle, 1=pending, 2=running, 3=done
- `STOP_FLAG: AtomicBool` — cooperative stop signal
- `RESULT_READY: AtomicBool`
- FIFO commands: `CMD_EXECUTE (0x01)`, `CMD_STOP (0x02)`
- FIFO responses: `RSP_DONE (0x10)`, `RSP_ERROR (0x11)`

**Status:** Infrastructure is coded. Core 1 spawn (via `rp2040-hal::multicore::Multicore`) is NOT done in `main.rs`. The Pico entry doesn't use `DualCoreExecutor` at all — it uses `LialRuntime` directly.

---

## 9. Receiver — Safety

### `lial-receiver/src/validation.rs` (~182 lines)

#### `ALLOWED_IMPORTS` array

The complete Alphabet whitelist:

```rust
["lial_gpio_set", "lial_gpio_get", "lial_delay_ms", "lial_get_uptime_us",
 "lial_i2c_transfer", "lial_log", "lial_pwm_set", "lial_adc_read",
 "lial_spi_transfer", "lial_uart_write", "lial_uart_read", "lial_get_param"]
```

#### `ValidationResult` struct

`valid: bool`, `rejected_imports: Vec<String>`.

#### `validate_wasm_imports(wasm_bytes) -> ValidationResult`

Hand-parses the Wasm binary format:
1. Checks magic number (`\x00asm`).
2. Walks sections until section ID 2 (import section).
3. For each import: reads module name, field name, and descriptor type.
4. If it's a function import (`kind == 0x00`) and the field name is **not** in `ALLOWED_IMPORTS`, adds it to `rejected_imports`.

Also includes `read_leb128_u32()` helper for Wasm binary encoding.

**Caveat:** Only validates on the **ESP32 `main_loop` path**. The Pico USB loop does NOT call `validate_wasm_imports` before executing Wasm.

Has `#[cfg(test)]` unit tests for `read_leb128_u32`.

---

## 10. Receiver — Mock

### `lial-receiver/src/mock.rs` (~60 lines, `#[cfg(feature = "std")]`)

#### `LaptopMock` struct

Implements `LialHardware` for laptop testing:
- `gpio_set` / `gpio_get` → prints to stdout, stores state in a `HashMap`.
- `delay_ms` → `std::thread::sleep`.
- `get_uptime_us` → `Instant::now().elapsed()`.
- `i2c_transfer` → prints "[I2C] ..." and returns 0.
- `log` → prints "[LOG] ...".
- `adc_read` → returns random value `rand::random::<u32>() % 4096`.

Used by the `std` entry point in `main.rs` for testing without hardware.

---

## 11. Receiver — Entry Points

### `lial-receiver/src/main.rs` (~568 lines)

Three conditional entry points, all in `main.rs`:

#### ESP32-C3 (`#[cfg(feature = "esp32c3")] mod esp_entry`, lines ~1–128)

1. `esp_alloc::heap_allocator!(size: 200 * 1024)` — 200 KB heap.
2. Inits LEDC PWM on GPIO 5 (10-bit, 5 kHz).
3. Inits I2C0 on GPIO 8 (SDA) / GPIO 9 (SCL).
4. Inits ADC1 on GPIO 2.
5. Inits USB Serial JTAG → splits into `(rx, tx)`.
6. Creates `EmbeddedIoTransport::new(tx, rx)`.
7. Creates `Esp32C3Hal::new(...)`.
8. Calls `hal.adapter_mut().scan_i2c_bus(0)` to discover I2C devices.
9. Builds manifest (board `"esp32c3"`, pins `[5]`, ADC channel `[0]`, I2C bus 0 with scan results).
10. Calls **`lial_receiver::main_loop(&mut transport, hal, &manifest_json)`** — enters the shared generic loop.

#### Raspberry Pi Pico (`#[cfg(feature = "rp2040")] mod pico_entry`, lines ~130–416)

1. `LlffHeap` with 150 KB static heap.
2. Inits clocks, watchdog, SIO, GPIO pins.
3. GPIO 25 → push-pull output (onboard LED).
4. I2C0 on GPIO 4 (SDA) / GPIO 5 (SCL) at **100 kHz** with pull-ups.
5. ADC on GPIO 26 → `Rp2040AdcChannel`.
6. Creates `Rp2040Hal::new(...)`.
7. Hard-codes I2C devices as `[0x3C]` (no bus scan).
8. Builds manifest (board `"rp2040-pico"`, pins `[25]`, ADC channel `[26]`, I2C bus 0 with `0x3C`).
9. USB CDC setup: `UsbBusAllocator`, `SerialPort`, `UsbDeviceBuilder` with VID/PID `0x2E8A:0x000A`, serial `"LIAL-PICO-001"`.
10. **DOES NOT** call `main_loop`. Instead runs its own loop:

```
loop {
    usb_dev.poll(&mut [&mut serial]);
    serial.read(&mut read_buf);
    // accumulate into frame_buf
    // parse frames when complete
    // handle OP_DISCOVERY → usb_write_all(manifest)
    // handle OP_BYTECODE_PUSH:
    //   - set_usb_poll callback
    //   - LialRuntime::new + execute
    //   - clear_usb_poll
    //   - usb_write_all(result)
    // else → error response
}
```

**Key differences from `main_loop`:**
- Manual USB CDC read/write (can't use `EmbeddedIoTransport` because USB CDC needs `usb_dev.poll()` interleaved).
- **No Wasm import validation** before execute.
- **No extended opcodes** (stop, query, set param, hot-swap — only discovery + bytecode push).
- `usb_write_all()` helper for chunked 64-byte USB writes with polling.
- USB poll callback registered around Wasm execution.

#### Laptop/std (`#[cfg(feature = "std")] fn main()`, lines ~420–568)

1. Creates `LaptopMock` hardware.
2. Builds manifest with mock values.
3. Two modes:
   - **`--stdin`** → creates `StdioTransport`, calls `main_loop`.
   - **File argument** → reads `.wasm` file, executes directly via `LialRuntime`.
4. Supports `--fuel <n>` for gas metering.

---

## 12. Host — Transport

### `lial-host/transport.py` (~168 lines)

#### Constants (lines 12–20)

Mirrored from `link.rs`: `OP_DISCOVERY` through `OP_HOT_SWAP`.

#### `LialTransport` ABC (line 23)

```python
class LialTransport(ABC):
    def read_frame(self, timeout=30.0) -> tuple[int, bytes]
    def write_frame(self, opcode: int, payload: bytes = b"")
    def close()
    def is_connected -> bool
    def request_discovery(timeout=5.0, retries=3) -> dict | None   # convenience
    def push_bytecode(wasm_bytes, timeout=120.0) -> dict            # convenience
```

`request_discovery` retries 3 times with 0.5 s backoff.

#### `SerialTransport` (line 76)

Wraps `pyserial`. On init: opens port, sleeps **1 s** (critical for Pico USB enumeration), clears input buffer.

- `read_frame`: reads 5-byte header, then payload, with deadline-based timeout.
- `write_frame`: packs `>BI` + payload, writes, flushes.

#### `TcpTransport` (line 121)

Wraps a TCP socket. Same frame protocol over TCP. Used with `--transport wifi --ip`.

**Caveat:** No reconnection logic. If the socket drops, you need to restart.

---

## 13. Host — Interactive LLM Loop

### `lial-host/lial_host.py` (~516 lines)

The main user-facing program.

#### `SYSTEM_PROMPT` (lines 50–243)

A massive prompt (~240 lines) sent to GPT-4o. Contains:
- All 12 syscall signatures with usage notes.
- Capability-aware pin selection rules (use `device_info` JSON).
- `#[unsafe(no_mangle)]` syntax requirement (Rust 2024 edition).
- Memory constraints (64 KB, 8 KB stack, max ~200 byte arrays).
- **Complete SSD1306 driver reference**: init sequence (26 bytes), display clear procedure (page-by-page), `DIGIT_FONT` (10 entries × 5 bytes), `LETTER_FONT` (26 entries × 5 bytes), concrete rendering examples.
- Formatted with `{device_info}` placeholder filled at runtime.

**Caveat:** The system prompt is very long. Token cost per request is high. Could be optimized by only including SSD1306 sections when I2C is present.

#### `_call_openai(messages) -> str` (line 247)

Uses `openai.OpenAI()` client, model `gpt-4o`, temperature 0.2.

#### `_extract_rust(text) -> str` (line 258)

Strips markdown fences from LLM response to get raw Rust code.

#### `_detect_port() -> str | None` (line ~270)

Globs for `/dev/cu.usbmodem*` or `/dev/ttyUSB*` or `/dev/ttyACM*`.

#### `_maybe_autoflash(port)` (line ~280)

If the serial port looks like a blank ESP32 (specific VID/PID), runs `lial init` automatically.

#### `_open_transport(args) -> LialTransport | None` (line 313)

Based on `--transport` flag:
- **`serial`** (default): auto-detect or use `--port`, create `SerialTransport`.
- **`wifi`**: use `--ip` or run `discover_mdns()`, create `TcpTransport`.

#### `cmd_run(args)` (line 345)

The main interactive loop:
1. Opens transport, requests discovery.
2. Falls back to ESP32 defaults if discovery fails.
3. Formats `SYSTEM_PROMPT` with `device_info` JSON.
4. Loop: read user input → send to GPT-4o → extract Rust → compile → push → display result.
5. On compile failure: sends error back to LLM, retries up to 2 times.

#### CLI setup (line ~477)

Subcommands: `run`, `download`, `init`. Default is `run`.
Flags: `--port`, `--baud`, `--transport`, `--ip`, `--tcp-port`, `--no-autoflash`.

---

## 14. Host — Wasm Compiler

### `lial-host/lial_compiler.py` (~120 lines)

#### `compile_rust_to_wasm(code: str) -> bytes`

1. Creates a temp directory with a Cargo project.
2. Writes `Cargo.toml` targeting `cdylib`, `panic = "abort"`, memory settings.
3. Writes `src/lib.rs` with:
   - `#![no_std]` + panic handler
   - `unsafe extern "C"` block declaring all 12 syscall imports
   - The user's code (which must contain `pub extern "C" fn run_logic()`)
4. Runs `cargo build --release --target wasm32-unknown-unknown`.
5. Reads the `.wasm` output.
6. Returns raw bytes.

**Caveat:** Wasm is compiled with `-C link-arg=--initial-memory=65536 -C link-arg=--max-memory=65536 -C link-arg=-z -C link-arg=stack-size=8192`. These are hard-coded and match what `LialRuntime` expects. If you change them here, you must change the runtime too.

---

## 15. Host — Device Abstraction

### `lial-host/lial_device.py` (~128 lines)

#### `LialDevice` class

High-level wrapper around a `LialTransport`. Methods:

| Method | Opcode used | Notes |
|--------|-------------|-------|
| `discover()` | 0x01 | Returns manifest dict |
| `push(wasm_bytes)` | 0x02 → waits for 0x03 | Returns `ExecResult` |
| `stop()` | 0x05 → waits for 0x07 | Returns dict |
| `query_status()` | 0x06 → waits for 0x07 | Returns `DeviceStatus` |
| `set_param(slot, value)` | 0x08 | Packs as 8 bytes BE |
| `hot_swap(wasm_bytes)` | 0x09 → waits for 0x03 | Returns `ExecResult` |
| `close()` | — | Closes transport |

**Caveat:** All methods are **synchronous**. The plan called for an async class; this is sync with optional asyncio usage elsewhere.

### `lial-host/device_registry.py` (~144 lines)

#### `DeviceRegistry` class

Manages multiple named devices:
- `register(name, transport)` → creates `LialDevice`, runs discovery, stores.
- `register_serial(name, port)` / `register_tcp(name, host, port)` — convenience.
- `push_to(name, wasm)` → push to one device.
- `push_to_all(wasm)` → **sequential** push to all connected devices.
- `stop_all()` / `query_all()` / `close_all()`.
- `get_manifests_summary()` → JSON string of all manifests (for multi-device LLM prompts).

**Caveat:** `push_to_all` is sequential, not `asyncio.gather` as the plan specified.

---

## 16. Host — Discovery

### `lial-host/discovery.py` (~91 lines)

#### `DiscoveredDevice` dataclass

`name`, `host`, `port`, `board`, `family`, `firmware_version`, `transport`, `properties`.

#### `discover_mdns(timeout=5.0) -> list[DiscoveredDevice]`

Uses the `zeroconf` package (optional — gracefully handles `ImportError`) to browse `_lial._tcp.local.` services. Returns a list of devices found within the timeout.

**Caveat:** Only useful when a receiver actually advertises via mDNS. Currently NO receiver does this (the WiFi transport is a stub). So this function will always return an empty list against real hardware.

#### `discover_ble(timeout=5.0) -> list[DiscoveredDevice]`

Stub that prints "BLE discovery not yet implemented" and returns `[]`.

---

## 17. Host — MCP Server

### `lial-host/mcp_server.py` (~302 lines)

Exposes LIAL as Model Context Protocol tools for any MCP-compatible LLM agent.

#### Tool definitions (`MCP_TOOLS` list, lines ~158–253)

Six tools with JSON Schema input definitions:

| Tool | Arguments | Action |
|------|-----------|--------|
| `lial_devices` | (none) | List all registered devices |
| `lial_push` | `device`, `code` | Compile Rust → Wasm, push to device |
| `lial_stop` | `device` | Stop execution |
| `lial_query` | `device` or `"all"` | Query status |
| `lial_set_param` | `device`, `slot`, `value` | Set parameter slot |
| `lial_hot_swap` | `device`, `code` | Stop + recompile + push |

#### `handle_tool_call(tool_name, arguments) -> dict`

Routes to the appropriate `tool_*` function.

#### `__main__` (lines 261–302)

Minimal **stdio JSON-RPC** server loop:
- Reads one JSON line from stdin.
- Dispatches `tools/list` → returns `MCP_TOOLS`.
- Dispatches `tools/call` → calls `handle_tool_call`.
- Writes JSON response to stdout.

**Caveat:** This is a barebones JSON-RPC loop, not a full MCP SDK integration. It should work with MCP clients that use stdio transport, but hasn't been tested with a real Cursor/Claude MCP config.

---

## 18. Host — CLI & Flashing

### `lial-host/lial_cli.py` (~40 lines)

Unified CLI entry point. Parses subcommands (`init`, `download`, `run`) and dispatches to `lial_commands/init.py`, `lial_commands/download.py`, or `lial_host.py`.

### `lial-host/board_registry.py` (~80 lines)

Maps USB VID/PID pairs to board families:
- `(0x303A, 0x1001)` → `esp32c3` (ESP-JTAG)
- `(0x10C4, 0xEA60)` → `esp32c3` (CP2102)
- `(0x2E8A, 0x000A)` → `rp2040` (Pico)
- etc.

Also includes `detect_boards() -> list` which scans serial ports and matches against the registry.

### `lial-host/flash_backends/`

| File | Board | Tool | Status |
|------|-------|------|--------|
| `esp32.py` | ESP32 family | `espflash` (preferred), `esptool` fallback | **Working** |
| `rp2040.py` | Pico | UF2 copy to mounted volume | **Scaffold** |
| `avr.py` | AVR | `avrdude` | **Stub** |
| `stm32.py` | STM32 | `dfu-util` | **Stub** |

### `lial-host/lial_commands/`

- **`download.py`**: Downloads firmware from GitHub release manifest. Supports private repos via `gh api`.
- **`init.py`**: Auto-detect + flash flow. Calls `detect_boards()`, downloads firmware, invokes flash backend.

---

## 19. Host — Testing

### `lial-host/hil_test.py` (~120 lines)

Hardware-in-the-loop test harness:
1. Opens serial transport.
2. For each test in `hil_tests/`: reads a `.rs` snippet, compiles to Wasm, pushes to device, checks logs against expected output.
3. Reports pass/fail.

### `lial-host/serial_push.py` (~50 lines)

Low-level diagnostic tool: opens serial port, sends a raw `.wasm` file as `OP_BYTECODE_PUSH`, reads the response. Useful for debugging transport issues without the LLM loop.

### `lial-host/hil_tests/`

Individual test scripts (Rust snippets) for:
- `gpio_blink.rs` — toggle a pin
- `adc_read.rs` — read ADC, log value
- `pwm_fade.rs` — ramp PWM duty
- `i2c_scan.rs` — scan I2C bus, log found addresses
- (others may exist)

---

## 20. Patches

### `patches/` directory

Four crate forks applied via `[patch.crates-io]` in `Cargo.toml`:

| Crate | Key changes | Why |
|-------|-------------|-----|
| `wasmi` | `Arc` → `Rc`, remove `Send + Sync` bounds | ESP32-C3 is single-core RISC-V with no hardware atomics |
| `wasmi_core` | `#[cfg(feature = "std")] extern crate std;` gating | Allow `no_std` compilation |
| `wasmi_collections` | Same pattern as core | |
| `wasmparser` | Minimal `no_std` compat | |

All four have their own `Cargo.toml` with `default = ["std"]` and `std = []` feature gating. The receiver uses them with `default-features = false`.

**Note:** `portable-atomic` is used in the dependency graph (not just direct code) to provide atomic operations on `thumbv6m` and `riscv32imc`.

---

## 21. Examples

### `examples/test_drivers/blink_led/`

A standalone `cdylib` crate targeting `wasm32-unknown-unknown`:

```rust
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        lial_gpio_set(25, 1);
        lial_delay_ms(500);
        lial_gpio_set(25, 0);
        lial_delay_ms(500);
    }
}
```

Uses GPIO **25** (Pico onboard LED). Previously used GPIO 5 (ESP32 external LED) and was updated in Week 3.

---

## 22. Summary: What Works, What's Partial, What's Missing

### Fully working (tested on hardware)

| Feature | ESP32-C3 | Pico |
|---------|----------|------|
| Discovery manifest | Yes | Yes |
| Wasm execution (`run_logic`) | Yes | Yes |
| GPIO set/get | Yes | Yes (pin 25) |
| PWM | Yes (LEDC) | No (not registered) |
| ADC | Yes (channel 0) | Yes (channel 26) |
| I2C (SSD1306 OLED) | Yes | Yes (with RecoveringI2c) |
| USB serial transport | Yes (JTAG) | Yes (CDC) |
| LLM → compile → push → result | Yes | Yes |
| Wasm import validation | Yes | **No** |
| Extended opcodes (stop/query/param/swap) | Yes | **No** |

### Partial / scaffolding

| Feature | What exists | What's missing |
|---------|-------------|----------------|
| WiFi transport | `WifiTcpTransport` struct + host `TcpTransport` | Real `esp-wifi` init, TCP socket polling, mDNS advertise |
| Dual-core execution | `DualCoreExecutor` struct + atomics | Core 1 spawn, FIFO mailbox, wiring in `pico_entry` |
| MCP server | 6 tool definitions, stdio JSON-RPC loop | Testing with real MCP client, device pre-registration |
| Device registry | Register, push, stop, query | Parallel push (`asyncio.gather`), multi-device LLM routing |
| mDNS discovery (host) | `discover_mdns()` with `zeroconf` | No device advertises yet |
| SPI / UART syscalls | Registered in Wasm linker, default -1 | No board HAL implements them |
| BLE discovery | Stub function | Everything |

### Not implemented at all

| Feature | Notes |
|---------|-------|
| Peripheral pin whitelisting | Syscalls don't check if pin is in manifest |
| Watchdog feeding | `Watchdog` initialized on Pico but never fed |
| `OP_STREAM_DATA` (0x04) | Constant defined, no handler anywhere |
| OTA firmware update | Researched but not built |
| CBOR serialization | Manifest is JSON; LIAL spec mentions CBOR |
| Multi-device LLM prompt | `get_manifests_summary()` exists; `lial_host.py` uses one device |
| Pico uses `main_loop` | Pico has its own USB loop; doesn't benefit from shared dispatch |

---

*This document covers the full LIAL codebase as of the `week3` branch merged to `main`.*
