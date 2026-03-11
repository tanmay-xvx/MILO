# LIAL: The LLM IoT Abstraction Layer

**Project Manifesto & Technical Specification v1.0**

**Version:** 0.1.0-Alpha | **Status:** Active Development (3-Week POC)

LIAL (pronounced "Lyle") is a high-performance, ultra-lightweight hardware abstraction layer designed to turn any silicon — from a $2 ESP32 to a sophisticated industrial PLC — into a native, programmable extension of an LLM's reasoning engine.

Unlike traditional IoT protocols that rely on static, pre-defined function calls (e.g., `TurnOnLED`), LIAL treats hardware as raw compute and I/O resources that the LLM can program dynamically via Just-In-Time (JIT) bytecode execution.

## 1. Core Vision

The current bottleneck in "Agentic IoT" is the **API Wall**. An LLM can only do what a human developer previously coded into a tool. If a device has a sensor the developer didn't expose, the LLM is blind to it.

LIAL breaks this wall by moving the "Intelligence-to-Hardware" boundary from the **Application Layer** down to the **Instruction Layer**.

## 2. Project Objectives

- **Universal Portability:** Run on any OS (RTOS, Linux, Bare Metal) with a footprint < 100 KB.
- **Dynamic Capability Discovery:** Devices shouldn't need "drivers" on the host. They describe their own register maps via a Hardware Manifest.
- **Zero-Latency Execution:** Move logic execution to the Edge. The LLM ships a complete control loop (e.g., a PID controller) rather than ping-ponging individual commands.
- **Silicon-Agnostic Code:** Use WebAssembly (Wasm) as the "Universal Machine Code" to ensure the same LLM-generated logic runs on ARM, Xtensa, or RISC-V.

## 3. Technical Requirements

### A. The Target Side (The "Receiver")

The LIAL Receiver must be installed on the IoT device.

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

## 4. System Architecture: The "LIAL Stack"

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| **Cognitive** | LLM (GPT-4o / Claude / Gemini) | Reasoning, logic generation, and error correction. |
| **Translation** | LIAL-Host (Python) | Compiles code to Wasm and manages the Binary Frame. |
| **Transport** | LIAL-Link (CBOR/TCP/UDP) | Extremely low-overhead binary transport. |
| **Execution** | LIAL-Runtime (Wasm) | Runs the logic in a safe, sandboxed environment. |
| **Physical** | Hardware Registers | The actual silicon pins and peripherals. |

### Transport Protocol (LIAL-Link)

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
| GPIO | `lial_gpio_set` | `(pin: u32, state: u32)` |
| GPIO | `lial_gpio_get` | `(pin: u32) -> u32` |
| Timing | `lial_delay_ms` | `(ms: u32)` |
| Timing | `lial_get_uptime_us` | `() -> u64` |
| I2C Bus | `lial_i2c_transfer` | `(addr: u8, tx_buf: *u8, tx_len: u32, rx_buf: *u8, rx_len: u32) -> i32` |
| Logging | `lial_log` | `(message: *const char)` |

The alphabet must stay **under 15 functions** to ensure portability across all targets.

## 5. Security & Safety (The "Sandbox")

Direct hardware access by an AI is inherently risky. LIAL solves this via **Capability-Based Security**:

1. **Memory Isolation:** The Wasm runtime cannot access any memory outside its allotted heap.
2. **Peripheral Whitelisting:** The LIAL Receiver only allows the Wasm module to call specific hardware addresses defined in the boot-time configuration.
3. **Instruction Metering (Gas):** A configurable fuel budget prevents infinite loops. Exhausting the budget terminates execution immediately.
4. **Watchdog Enforcement:** Any LLM-generated loop that hangs the processor is automatically terminated by the hardware watchdog.
5. **No Direct Pointers:** Wasm modules must never access hardware memory directly — all access goes through the Alphabet.

## 6. Efficiency Model

The power of LIAL is quantified by its reduction in Network Round Trips (\(N\)).

In standard MCP/tool-use, for every "if/then" hardware decision, data must travel to the LLM and back:

\[
T_{\text{MCP}} = N \times (t_{\text{network}} + t_{\text{inference}})
\]

In LIAL, the entire logic is pushed once:

\[
T_{\text{LIAL}} = t_{\text{compile}} + t_{\text{push}} + t_{\text{local\_exec}}
\]

For any control loop with \(N > 1\) iterations, LIAL dominates because \(t_{\text{local\_exec}} \ll t_{\text{network}}\).

## 7. Execution Flow

1. **Discovery:** Receiver sends a Hardware Manifest (e.g., "I have an LED on Pin 5, a temperature sensor on I2C 0x48").
2. **Prompting:** User asks: "Blink the light three times slowly."
3. **Generation:** Host sends Task + Manifest to the LLM. LLM writes a C/Rust function using the LIAL Alphabet.
4. **Compilation:** Host JIT-compiles that code into a tiny `.wasm` file (~400 bytes).
5. **Transfer:** Host pushes the bytecode via LIAL-Link.
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

## 9. 3-Week Action Plan

### Week 1: Foundations & The "Alphabet"

- Build a Rust Receiver using `wasmi`.
- Manually link "Atomic Letters" (`gpio_set`, `delay_ms`).
- Successfully execute a local `.wasm` payload via `fs::read`.

### Week 2: Intelligence & The "Manifest"

- Build the Python Host orchestration.
- Implement the String -> WASM JIT pipeline.
- Automate LLM prompting with hardware context.

### Week 3: Connectivity & Safety

- Implement LIAL-Link over UDP.
- Add Instruction Metering (Gas) to prevent device hangs.
- Full "User Prompt to Silicon Action" loop.
