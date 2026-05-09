# MILO: Modular Interface for LLM-IoT Operations

**Project Manifesto & Technical Specification v1.0**

**Version:** 0.1.0-Beta | **Status:** Active Development (Week 3)

MILO is a high-performance, ultra-lightweight hardware abstraction layer designed to turn any silicon — from a $2 ESP32 to a sophisticated industrial PLC — into a native, programmable extension of an LLM's reasoning engine.

Unlike traditional IoT protocols that rely on static, pre-defined function calls (e.g., `TurnOnLED`), MILO treats hardware as raw compute and I/O resources that the LLM can program dynamically via Just-In-Time (JIT) bytecode execution.

## 1. Core Vision

The current bottleneck in "Agentic IoT" is the **API Wall**. An LLM can only do what a human developer previously coded into a tool. If a device has a sensor the developer didn't expose, the LLM is blind to it.

MILO breaks this wall by moving the "Intelligence-to-Hardware" boundary from the **Application Layer** down to the **Instruction Layer**.

## 2. Project Objectives

- **Universal Portability:** Run on any OS (RTOS, Linux, Bare Metal) with a footprint < 100 KB.
- **Dynamic Capability Discovery:** Devices shouldn't need "drivers" on the host. They describe their own register maps via a Hardware Manifest.
- **Zero-Latency Execution:** Move logic execution to the Edge. The LLM ships a complete control loop (e.g., a PID controller) rather than ping-ponging individual commands.
- **Silicon-Agnostic Code:** Use WebAssembly (Wasm) as the "Universal Machine Code" to ensure the same LLM-generated logic runs on ARM, Xtensa, or RISC-V.

## 3. Technical Requirements

### A. The Target Side (The "Receiver")

The MILO Receiver must be installed on the IoT device.

- **Environment:** Rust (`no_std`) / `wasmi` interpreter.
- **Role:** The **Execution Sandbox** and **Hardware Reporter**.
- **Functions:**
  - **Manifest Exposure:** On boot or discovery, the Receiver generates and sends a Hardware Manifest (JSON/CBOR) to the Host describing available pins, buses, peripherals, and memory limits. This is how the LLM learns what the device can do — no host-side drivers required.
  - **The "Atomic Alphabet":** A manually coded System Call Table mapping generic Wasm calls to physical hardware (GPIO, I2C, SPI, PWM).
  - **Bytecode Execution:** Receives `.wasm` modules from the Host, instantiates them in the sandboxed runtime, links the Alphabet, and runs logic locally.
- **Safety Guards:**
  - **Memory Isolation:** Bytecode cannot access host memory.
  - **Instruction Metering:** Prevents infinite loops via "Gas" limits.
  - **Peripheral Whitelisting:** Only boot-time configured hardware addresses are accessible.
  - **Watchdog:** Hardware watchdog terminates hung execution.
- **Runtime Alternatives:** `wasm3` or `WAMR` for C/C++ targets.
- **Connectivity:** TCP/IP (Wired/Localhost), UDP/IP (Wi-Fi), GATT Services (BLE), Serial (UART).
- **Language:** Rust (`no_std`) for maximum efficiency. C++ as a fallback for legacy targets.

### B. The Host Side (The "Root Instance")

The Root Instance resides on a more powerful machine (Laptop/Server/Gateway).

- **Orchestrator:** A Python-based engine that manages LLM context and device state.
- **JIT Pipeline:** An automated toolchain (LLVM/Clang) that converts LLM-generated C/Rust code into `.wasm` blobs on the fly.
- **Manifest Parser:** A utility to ingest SVD (System View Description) files and translate them into a "Hardware Grammar" the LLM understands.

## 4. System Architecture: The "MILO Stack"

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| **Cognitive** | LLM (GPT-4o / Claude / Gemini) | Reasoning, logic generation, and error correction. |
| **Translation** | MILO-Host (Python) | Compiles code to Wasm and manages the Binary Frame. |
| **Transport** | MILO-Link (CBOR/TCP/UDP) | Extremely low-overhead binary transport. |
| **Execution** | MILO-Runtime (Wasm) | Runs the logic in a safe, sandboxed environment. |
| **Physical** | Hardware Registers | The actual silicon pins and peripherals. |

### Transport Protocol (MILO-Link)

- **Serialization:** CBOR (Concise Binary Object Representation) for minimal overhead.
- **Channels:** TCP/IP (Wired/Localhost), UDP/IP (Wi-Fi), GATT (BLE), Serial (UART).
- **OpCodes:**
  - `0x01`: Identity/Discovery (Device -> Host).
  - `0x02`: Bytecode Push (Host -> Device).
  - `0x03`: Execution Log/Feedback (Device -> Host).

### The "Atomic Alphabet" (System Call Table)

A restricted set of hardware primitives exposed to the Wasm sandbox. The LLM combines these "letters" to write complex hardware "sentences."

| Category | Function | Signature |
|----------|----------|-----------|
| GPIO | `gpio_set` | `(pin: u32, state: u32)` |
| GPIO | `gpio_get` | `(pin: u32) -> u32` |
| Timing | `delay_ms` | `(ms: u32)` |
| Timing | `get_uptime_us` | `() -> u64` |
| I2C Bus | `i2c_transfer` | `(addr, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` |
| PWM | `pwm_set` | `(channel: u32, duty: u32)` — duty 0–10000 |
| ADC | `adc_read` | `(channel: u32) -> u32` — 12-bit, 0–4095 |
| SPI Bus | `spi_transfer` | `(bus, tx_ptr, tx_len, rx_ptr, rx_len) -> i32` |
| UART | `uart_write` | `(bus: u32, ptr: u32, len: u32) -> i32` |
| UART | `uart_read` | `(bus, ptr, len, timeout_ms) -> i32` |
| Logging | `log_msg` | `(ptr: u32, len: u32)` |

The alphabet must stay **under 15 functions** to ensure portability across all targets.

## 5. Security & Safety (The "Sandbox")

Direct hardware access by an AI is inherently risky. MILO solves this via **Capability-Based Security**:

1. **Memory Isolation:** The Wasm runtime cannot access any memory outside its allotted heap.
2. **Peripheral Whitelisting:** The MILO Receiver only allows the Wasm module to call specific hardware addresses defined in the boot-time configuration.
3. **Instruction Metering (Gas):** A configurable fuel budget prevents infinite loops. Exhausting the budget terminates execution immediately.
4. **Watchdog Enforcement:** Any LLM-generated loop that hangs the processor is automatically terminated by the hardware watchdog.
5. **No Direct Pointers:** Wasm modules must never access hardware memory directly — all access goes through the Alphabet.

## 6. Efficiency Model

The power of MILO is quantified by its reduction in Network Round Trips (\(N\)).

In standard MCP/tool-use, for every "if/then" hardware decision, data must travel to the LLM and back:

\[
T_{\text{MCP}} = N \times (t_{\text{network}} + t_{\text{inference}})
\]

In MILO, the entire logic is pushed once:

\[
T_{\text{MILO}} = t_{\text{compile}} + t_{\text{push}} + t_{\text{local\_exec}}
\]

For any control loop with \(N > 1\) iterations, MILO dominates because \(t_{\text{local\_exec}} \ll t_{\text{network}}\).

## 7. Execution Flow

1. **Discovery:** Receiver sends a Hardware Manifest (e.g., "I have an LED on Pin 5, a temperature sensor on I2C 0x48").
2. **Prompting:** User asks: "Blink the light three times slowly."
3. **Generation:** Host sends Task + Manifest to the LLM. LLM writes a C/Rust function using the MILO Alphabet.
4. **Compilation:** Host JIT-compiles that code into a tiny `.wasm` file (~400 bytes).
5. **Transfer:** Host pushes the bytecode via MILO-Link.
6. **Execution:** Receiver instantiates the module, links it to the Alphabet, and runs logic locally at silicon speed.

## 8. Requirements & Dependencies

### Host Machine (Laptop/Server)

- **Rust Toolchain:** `rustup`, `cargo`, `wasm32-unknown-unknown` target.
- **LLVM:** `clang` (version 15+ recommended) for C-based drivers.
- **Python:** `pyserial`, `cbor2`, `openai` / `anthropic` libraries.
- **WASI-SDK:** Required for standard C library support in Wasm.

### Receiver Machine (Target)

- **Hardware:** ESP32-C3/S3, STM32 (ARM Cortex-M), or Raspberry Pi.
- **Crates:** `wasmi` (with `default-features = false`), `embedded-hal`.

## 9. Hardware Abstraction Strategy

The Receiver uses a **`MiloHardware` trait** to abstract all syscalls away from the runtime:

```rust
pub trait MiloHardware {
    fn gpio_set(&mut self, pin: u32, state: u32);
    fn gpio_get(&mut self, pin: u32) -> u32;
    fn delay_ms(&mut self, ms: u32);
    fn get_uptime_us(&self) -> u64;
    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32;
    fn log(&mut self, message: &str);
}
```

`MiloRuntime<H: MiloHardware>` is generic over the hardware backend. The runtime, wasm execution, linker, gas metering, and protocol code are fully board-agnostic.

**Phase 1 (Week 1):** Per-board implementations -- `LaptopMock` for development/testing, `Esp32C3Hal` for real hardware via `esp-hal`.

**Phase 2 (Week 2):** A single generic `EmbeddedHalAdapter<P, D, I>` that accepts any board's `embedded-hal`-compatible peripherals (`OutputPin`, `InputPin`, `DelayNs`, `I2c`). Adding a new board requires zero MILO code changes -- just instantiate the adapter with the board's HAL.

## 10. Current State (as of v0.1.0-beta)

- **11 syscalls** running end-to-end on ESP32-C3 Super Mini.
- **Hardware LEDC PWM** (10-bit, 5 kHz) on GPIO 5 with GPIO fallback.
- **ADC** (12-bit) on GPIO 2 for potentiometer reading.
- **I2C** master on GPIO 8/9 driving SSD1306 OLED with bitmap font rendering.
- **`milo init`** auto-detects boards by VID/PID, downloads firmware from
  GitHub releases (with `gh api` fallback for private repos), flashes via
  `espflash` (preferred) or `esptool`.
- **`v0.1.0-beta`** GitHub release with merged ESP32-C3 firmware binary.
- **LLM system prompt** includes complete SSD1306 font tables, memory
  constraints, and capability-aware peripheral selection.
- **HIL tests** pass for GPIO, ADC, PWM, I2C on real hardware.
- **Board-agnostic `EmbeddedHalAdapter`** with object-safe dynamic dispatch
  (`DynPwm`, `DynAdc`, `DynI2c`, `DynSpi`, `DynUart`, `DynDelay`).
- **Wasm constraints**: 64 KB memory, 8 KB stack, 500M fuel budget.

## 11. Development Tracking

Weekly action plans, changelogs, and status are maintained in `docs/weekN/`.

- `docs/week1/` — Receiver library, serial transport, JIT compiler, ESP32-C3 deployment
- `docs/week2/` — Board-agnostic adapter, firmware delivery, E2E hardware testing, LEDC PWM, release pipeline
- `docs/week3/` — SVD-driven manifests, auto-discovery, Wi-Fi transport, multi-device orchestration
