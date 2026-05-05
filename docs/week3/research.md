# Week 3 Deep Research: Transport, Multi-Core, Flashing, and Competitive Analysis

## 1. Firmware Flashing Methods

### 1.1 USB vs UART — What's the Difference?

| Aspect | UART | USB (CDC) |
|--------|------|-----------|
| **Wires** | 2 (Tx/Rx) — simple async | 2 (D+/D-) — differential, complex protocol |
| **Clock** | No clock wire; relies on matched baud rates | Host-scheduled, token-based polling |
| **Bridge chip** | Needs external USB-to-UART (CP2102, CH340) | Native on ESP32-C3/S3 (built-in USB-JTAG) |
| **Reliability** | Always works — independent of app state | Depends on software stack; crashes can kill the port |
| **Latency** | Deterministic, constant-rate streaming | Variable, polled (USB frames every 1ms) |
| **Speed** | Typically 115200–921600 baud (max ~1 Mbps) | 12 Mbps (Full Speed) or 480 Mbps (High Speed) |
| **Flashing** | Standard bootloader entry via DTR/RTS strapping | May need special boot mode; port can change after reset |
| **Debugging** | Rock-solid — works even if app crashes | Port disappears if firmware corrupts USB stack |

**For LIAL:** The ESP32-C3 Super Mini uses native USB-JTAG (no bridge chip). The Raspberry Pi Pico uses USB mass storage (UF2) for flashing and USB CDC for serial. Both approaches bypass UART entirely for development. However, UART remains the escape hatch when USB firmware is bricked.

**Key insight:** USB is faster for data transfer (LIAL-Link frames), but UART is more robust for initial flashing and crash recovery. LIAL should support both as transport layers — USB for normal operation, UART as fallback.

### 1.2 WiFi OTA (Over-The-Air) Flashing

**How it works:**
1. Device firmware includes a secondary bootloader with a TCP/HTTP server
2. Host sends new firmware binary over WiFi to device
3. Device writes to alternate flash partition (A/B scheme)
4. Device reboots into new firmware; if it fails, rolls back to previous

**ESP32 (ESP-IDF):**
- Native dual-partition support (`ota_0` / `ota_1` + `otadata`)
- `esp_https_ota` component handles download + verification + swap
- Partition table: `factory` (fallback) + `ota_0` + `ota_1`
- Supports HTTPS for security (prevents MITM injection)

**Raspberry Pi Pico W:**
- No native A/B partition support
- Requires custom bootloader like `picowota` (stays resident in flash)
- Bootloader receives firmware over WiFi, writes to application area
- Less robust than ESP32's approach (no automatic rollback)

**For LIAL:** WiFi OTA updates the *receiver firmware itself* (the Wasm runtime + transport + alphabet). This is different from pushing Wasm bytecode (which is the normal LIAL-Link operation). We need both:
- **Firmware OTA:** Update the receiver binary (rare, for bug fixes)
- **Wasm push:** Normal operation — push logic over WiFi/TCP (frequent)

The receiver needs enough flash for dual partitions:
- ESP32-C3: 4MB flash → factory (1MB) + ota_0 (1.5MB) + ota_1 (1.5MB) ✓
- Pico: 2MB flash → tighter, but feasible with bootloader (~64KB) + app (~900KB) × 2

### 1.3 BLE Flashing (DFU — Device Firmware Update)

**How it works:**
1. Device advertises a DFU service UUID over BLE
2. Host connects, sends firmware in chunks via GATT characteristics
3. Device assembles chunks, verifies checksum (MD5/SHA), writes to flash
4. Reboots into new firmware

**Characteristics:**
- Slow: BLE 4.2 max throughput ~100 KB/s practical, BLE 5.0 up to ~300 KB/s
- Range: 10–100m depending on environment
- No WiFi infrastructure needed (works in the field)
- Works even when device has no WiFi (nRF52, ESP32-C3 in BLE-only mode)

**For LIAL:** BLE DFU is useful for:
- Field firmware updates (no router needed)
- Battery-powered devices where WiFi is too power-hungry
- Phone-as-host scenarios (mobile app pushes firmware)

For normal Wasm push operations, BLE is viable because Wasm binaries are tiny (~1-4 KB). A 2KB Wasm module transfers in <0.5s over BLE.

### 1.4 Flashing Method Summary for LIAL

| Method | Use Case | Speed | Infrastructure | LIAL Role |
|--------|----------|-------|----------------|-----------|
| USB (UF2/espflash) | Initial flash, development | Fast | Cable only | `lial init` |
| UART | Crash recovery, legacy boards | Medium | Cable + bridge | Fallback |
| WiFi OTA | Remote firmware updates | Fast | WiFi network | `lial ota` |
| BLE DFU | Field updates, phone-as-host | Slow | None | Future |
| WiFi TCP | Wasm push (normal operation) | Fast | WiFi network | `lial push` |
| BLE GATT | Wasm push (battery devices) | Adequate | None | `lial push --ble` |

---

## 2. Multi-Core Architecture

### 2.1 Device Landscape

| Device | Cores | Architecture | LIAL Impact |
|--------|-------|--------------|-------------|
| ESP32-C3 | 1 | RISC-V 160MHz | Current target. No concurrency concerns. |
| RP2040 (Pico) | 2 | ARM Cortex-M0+ 133MHz | Can dedicate one core to Wasm, one to I/O |
| ESP32-S3 | 2 | Xtensa LX7 240MHz | FreeRTOS tasks, pin to core |
| ESP32 (original) | 2 | Xtensa LX6 240MHz | Same as S3, legacy |
| STM32H7 | 1-2 | ARM Cortex-M7+M4 | Asymmetric cores |
| RP2350 (Pico 2) | 2 | ARM Cortex-M33 / RISC-V Hazard3 | Switchable ISA! |

### 2.2 Single-Core Strategy (ESP32-C3, Current)

On a single core, everything runs cooperatively:
1. Transport loop reads frames
2. Wasm runtime executes (blocking — uses fuel for preemption)
3. Results written back

**The problem:** While Wasm executes, the transport is blocked. If Wasm runs for 500ms, no new frames are processed.

**Current mitigation:** Fuel budget (500M instructions ≈ ~2-5 seconds max).

### 2.3 Dual-Core Strategy (RP2040, ESP32-S3)

**Recommended architecture:**

```
┌─────────────────────────────────────────┐
│              Core 0: I/O                 │
│  ┌───────────────────────────────────┐  │
│  │  Transport (TCP/BLE/USB)          │  │
│  │  Frame parsing                     │  │
│  │  Manifest generation               │  │
│  │  Heartbeat / watchdog kick         │  │
│  │  Sensor streaming (opcode 0x04)    │  │
│  └───────────────────────────────────┘  │
├─────────────────────────────────────────┤
│              Core 1: Execution           │
│  ┌───────────────────────────────────┐  │
│  │  wasmi runtime                     │  │
│  │  Wasm module instantiation         │  │
│  │  Alphabet syscall dispatch         │  │
│  │  Fuel metering                     │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
         ↕ (FIFO / shared queue)
```

**RP2040 specifics:**
- `multicore_launch_core1()` in C SDK, or `cortex_m::multicore` in Rust
- Hardware FIFO between cores (32-bit words, 8 entries deep)
- Shared SRAM (264KB total) — both cores see the same memory
- No hardware cache coherence needed (both cores share the bus)
- Use spinlocks (hardware-provided, 32 available) for mutual exclusion

**ESP32-S3 specifics:**
- FreeRTOS with `xTaskCreatePinnedToCore(wasm_task, "wasm", 8192, NULL, 5, NULL, 1)`
- Core 0: WiFi/BLE stack + transport
- Core 1: Wasm execution
- FreeRTOS queues for inter-core communication
- Important: WiFi stack MUST run on Core 0 (Espressif requirement)

### 2.4 Benefits of Dual-Core for LIAL

1. **Non-blocking transport:** Core 0 keeps receiving frames (new Wasm push, stop command) while Core 1 executes
2. **Hot-swap:** Core 0 receives new Wasm, signals Core 1 to stop, then Core 1 starts new module
3. **Sensor streaming:** Core 0 continuously pushes sensor data (opcode 0x04) while Core 1 runs control logic
4. **Watchdog safety:** Core 0 kicks the watchdog independently; if Core 1 hangs, Core 0 can reset it
5. **Real-time guarantees:** Separate execution from I/O means neither starves the other

### 2.5 Implementation Plan for LIAL

```rust
// The LialRuntime stays generic — doesn't know about cores
pub struct LialRuntime<H: LialHardware> { ... }

// The main loop becomes core-aware on dual-core targets:
// Core 0:
fn core0_main() {
    let transport = WifiTransport::new(...);
    loop {
        let frame = transport.read_frame();
        match frame.opcode {
            0x02 => WASM_QUEUE.push(frame.payload), // send to core 1
            0x05 => WASM_QUEUE.push(StopSignal),    // stop execution
            _ => { ... }
        }
    }
}

// Core 1:
fn core1_main() {
    loop {
        let bytecode = WASM_QUEUE.pop(); // blocks until available
        let mut runtime = LialRuntime::new(hardware);
        runtime.execute(&bytecode);
        RESULT_QUEUE.push(runtime.result());
    }
}
```

On single-core, both loops run cooperatively in one thread (current behavior). The abstraction is:

```rust
trait LialExecutor {
    fn submit_wasm(&mut self, bytecode: &[u8]);
    fn poll_result(&mut self) -> Option<ExecResult>;
    fn is_running(&self) -> bool;
    fn stop(&mut self);
}

// SingleCoreExecutor: runs inline, blocking
// DualCoreExecutor: sends to Core 1 via queue
```

---

## 3. Wireless Runtime Control (Host Controlling Receiver)

### 3.1 The Vision

The host should be able to:
1. Push Wasm modules wirelessly (already planned)
2. **Stop** a running module remotely
3. **Hot-swap** a module while one is running
4. **Query** real-time status (is it running? fuel remaining? last log?)
5. **Stream** sensor data back to host continuously
6. **Adjust parameters** of a running module without full replacement

### 3.2 Control Protocol (Extended LIAL-Link)

Current opcodes: `0x01` Discovery, `0x02` Bytecode Push, `0x03` Exec Result

**Proposed additions:**

| OpCode | Direction | Name | Payload |
|--------|-----------|------|---------|
| 0x04 | Device→Host | Streaming Data | CBOR sensor readings |
| 0x05 | Host→Device | Stop Execution | (empty) |
| 0x06 | Host→Device | Query Status | (empty) |
| 0x07 | Device→Host | Status Response | `{running, fuel_remaining, uptime_us}` |
| 0x08 | Host→Device | Set Parameter | `{key, value}` — shared memory slot |
| 0x09 | Host→Device | Hot-Swap | New wasm bytecode (replaces running) |

### 3.3 Persistent TCP Connection Architecture

```
┌─────────────┐         TCP (persistent)         ┌──────────────┐
│   Host      │◄────────────────────────────────►│   Receiver   │
│  (Python)   │   WiFi, port 9100                │  (ESP32/Pico)│
│             │                                   │              │
│ LLM context │   ┌──── Frames ─────────────┐   │ Wasm runtime │
│ Compiler    │   │ 0x02: Push bytecode      │   │ Alphabet     │
│ Device reg  │   │ 0x05: Stop               │   │ Transport    │
│             │   │ 0x04: Sensor stream ←    │   │              │
└─────────────┘   └─────────────────────────┘   └──────────────┘
```

**Connection management:**
- Receiver boots → connects to WiFi → registers via mDNS (`_lial._tcp.local.`)
- Host discovers via mDNS or manual IP
- Persistent TCP socket (no HTTP overhead)
- Heartbeat every 5s (both directions) — detect disconnection in <10s
- On disconnect: receiver enters safe state (stops Wasm, waits for reconnect)

### 3.4 WiFi vs BLE for Runtime Control

| Aspect | WiFi TCP | BLE GATT |
|--------|----------|----------|
| Bandwidth | 1-10 Mbps | 100-300 KB/s |
| Latency | ~5-20ms RTT | ~30-100ms RTT |
| Range | Whole building (router) | 10-50m line of sight |
| Power | High (always connected) | Low (can sleep between events) |
| Infrastructure | Requires WiFi router | None (direct connection) |
| Max payload | Unlimited (TCP stream) | 512 bytes per characteristic (BLE 5.0) |
| Concurrent clients | Many (TCP server) | ~3-7 (BLE spec limit) |

**For LIAL:**
- WiFi TCP for "tethered wireless" scenarios (home automation, lab)
- BLE for battery-powered, phone-as-host, or field deployment
- The `LialTransport` trait abstracts this — runtime doesn't care

### 3.5 Shared Parameter Slots (Remote Adjustment)

For cases where the host wants to tweak a running Wasm module without replacing it:

```rust
// In the receiver, expose a shared memory region:
static PARAM_SLOTS: [AtomicU32; 8] = [...];

// New alphabet syscall:
fn lial_get_param(slot: u32) -> u32;

// Wasm code reads parameters:
let threshold = lial_get_param(0); // host can change this live
if temperature > threshold { ... }
```

The host sends `0x08 Set Parameter {slot: 0, value: 28}` — no recompilation, no restart, instant effect.

---

## 4. Raspberry Pi Pico — Porting LIAL

### 4.1 RP2040 Specifications

| Spec | Value | vs ESP32-C3 |
|------|-------|-------------|
| CPU | Dual-core ARM Cortex-M0+ @ 133MHz | Single RISC-V @ 160MHz |
| SRAM | 264 KB (4×64KB + 2×4KB banks) | 400 KB |
| Flash | 2 MB external QSPI (up to 16MB) | 4 MB internal |
| WiFi | None (Pico W has CYW43439) | Built-in 802.11 b/g/n |
| BLE | None (Pico W has BLE 5.2) | Built-in BLE 5.0 |
| USB | Native USB 1.1 (device + host) | USB-JTAG/Serial |
| ADC | 3 channels, 12-bit, 500 ksps | 2 channels, 12-bit |
| PWM | 16 channels (8 slices × 2) | 6 LEDC channels |
| I2C | 2 controllers | 1 controller |
| SPI | 2 controllers | 2 controllers (1 used for flash) |
| PIO | 2 × 4 state machines (programmable I/O!) | None |
| Power | ~25mA active, <1mA dormant | ~80mA active (WiFi) |
| Price | ~$4 (Pico), ~$6 (Pico W) | ~$2-3 |
| Flashing | UF2 drag-and-drop, SWD, picotool | USB-JTAG, espflash |
| Rust target | `thumbv6m-none-eabi` | `riscv32imc-unknown-none-elf` |

### 4.2 Memory Budget for Wasm on Pico

```
Total SRAM: 264 KB
─────────────────────────────────
Stack (both cores):        16 KB
Firmware code/data:        80 KB  (conservative estimate)
WiFi driver (Pico W):     ~50 KB  (CYW43 firmware loaded to SRAM)
───────────────────────────────────
Available for Wasm:       ~118 KB
  - wasmi interpreter:    ~40 KB  (code + internal state)
  - Wasm linear memory:    64 KB  (as configured today)
  - Frame buffers/misc:   ~14 KB
```

This is tighter than ESP32-C3 (400KB SRAM) but feasible. The 64KB Wasm memory limit we already enforce works perfectly here.

**If we need more room:**
- Execute Wasm code from flash (XIP — Execute In Place) on RP2040
- Reduce Wasm memory to 32KB for simple programs
- Use the PIO state machines to offload I/O (frees CPU time)

### 4.3 Porting Checklist

1. **Rust target:** `thumbv6m-none-eabi` (Cortex-M0+)
2. **HAL crate:** `rp2040-hal` (implements `embedded-hal` traits)
3. **Allocator:** `embedded-alloc` or `linked-list-allocator` (wasmi needs alloc)
4. **Atomics:** Cortex-M0+ supports native atomics (no `portable-atomic` patches needed!)
5. **USB transport:** `usb-device` + `usbd-serial` crates for USB CDC
6. **WiFi (Pico W):** `cyw43` crate + `embassy-net` for TCP/IP
7. **Flashing:** UF2 format via `elf2uf2-rs` or `probe-rs`
8. **Dual-core:** `rp2040-hal::multicore` for Core 1 launch

### 4.4 wasmi on RP2040 — Key Differences from ESP32-C3

| Concern | ESP32-C3 | RP2040 |
|---------|----------|--------|
| Atomics | No hardware atomics → `portable-atomic` patches | Native atomics on M0+ (with `LDREX`/`STREX`) |
| wasmi patches | 4 forked crates (Arc→Rc, portable-atomic) | May work without patches! (needs testing) |
| Allocation | `esp-alloc` | `embedded-alloc` |
| Float support | Hardware single-precision | Software float (M0+ has no FPU) |
| Flash access | Internal, fast | External QSPI via XIP cache (slightly slower) |

**Critical test:** Compile wasmi for `thumbv6m-none-eabi` without the ESP32 patches and see if it links. If it does, RP2040 is easier than ESP32-C3.

### 4.5 Pico W Wireless Stack

The CYW43439 chip on Pico W provides:
- WiFi 802.11 b/g/n (2.4GHz)
- BLE 5.2

In Rust, the stack is:
- `cyw43` crate: WiFi/BLE driver
- `embassy-net`: TCP/IP stack (async, no RTOS needed)
- `embassy-executor`: Async runtime

This means LIAL on Pico W can use async/await instead of FreeRTOS — potentially cleaner code than ESP32.

---

## 5. LIAL vs ESP-Claw: Competitive Analysis

### 5.1 What is ESP-Claw?

ESP-Claw is Espressif's official "Chat Coding" AI agent framework. It puts the LLM agent *on the device* — the ESP32 itself calls OpenAI/Claude APIs, parses responses, and executes Lua scripts locally.

### 5.2 Architecture Comparison

| Dimension | LIAL | ESP-Claw |
|-----------|------|----------|
| **Where LLM runs** | Host (laptop/server) | On-device (ESP32 calls API directly) |
| **Execution language** | WebAssembly (compiled from Rust/C) | Lua scripts |
| **Min hardware** | ESP32-C3 (400KB SRAM, no PSRAM) | ESP32-S3 + 8MB PSRAM + 8MB Flash |
| **Isolation** | Wasm sandbox (memory-safe by construction) | Lua VM (no formal memory isolation) |
| **Offline capable** | Yes — Wasm runs without network | No — requires LLM API calls per interaction |
| **Latency** | One compile+push, then local at silicon speed | Every action requires LLM API roundtrip |
| **Language** | Rust `no_std` (receiver), Python (host) | C (ESP-IDF), Lua (scripts) |
| **Code generation** | LLM generates Rust/C → compiled to Wasm | LLM generates Lua → interpreted directly |
| **Safety** | Gas metering + watchdog + peripheral whitelisting | No gas metering; relies on Lua VM limits |
| **Multi-device** | Host orchestrates many receivers | Each device is independent agent |
| **Board support** | Any `embedded-hal` board (Pico, STM32, ESP32, nRF) | ESP32-S3/C3 only (Espressif ecosystem) |
| **Transport** | USB/WiFi/BLE (abstracted) | WiFi only (for API calls) |
| **Memory per interaction** | ~1-4 KB Wasm binary | 8KB ring buffer for streaming JSON |
| **MCP support** | Planned (host-side MCP server) | Built-in (client + server) |
| **Long-term memory** | Not yet (host-side) | On-device structured memory |

### 5.3 Where LIAL Wins

1. **Runs on $2 hardware:** ESP-Claw needs expensive ESP32-S3 + 8MB PSRAM (~$8-15). LIAL runs on a bare ESP32-C3 ($2) or Raspberry Pi Pico ($4).

2. **True sandboxing:** Wasm provides formal memory isolation. LLM-generated code literally cannot access memory outside its 64KB heap. Lua has no such guarantee — a malicious or buggy script can corrupt device state.

3. **Offline-first execution:** Once Wasm is pushed, the device runs autonomously forever (or until fuel runs out). ESP-Claw needs WiFi + API access for every decision.

4. **Performance:** Compiled Wasm is 10-100x faster than interpreted Lua. For control loops (PID, signal processing), this matters.

5. **Language-agnostic:** LIAL accepts any language that compiles to Wasm (Rust, C, AssemblyScript, TinyGo). ESP-Claw is locked to Lua.

6. **Board-agnostic:** LIAL works on any `embedded-hal` device. ESP-Claw is locked to Espressif chips.

7. **Deterministic resource limits:** Gas metering provides hard guarantees on execution time. Lua has no equivalent — a tight loop can hang the device.

8. **Multi-device orchestration:** LIAL's host can manage fleets of heterogeneous devices. ESP-Claw devices are islands.

### 5.4 Where ESP-Claw Wins (and What LIAL Should Learn)

1. **Zero-infrastructure setup:** ESP-Claw needs only the ESP32 + WiFi — no host computer running. LIAL requires a host.
   - **Counter:** This is a feature trade-off, not a bug. The host enables multi-device orchestration, compilation, and richer LLM context.
   - **Learn:** Consider a "standalone mode" where the receiver has a pre-loaded set of Wasm modules it can cycle through based on simple triggers (button press, sensor threshold).

2. **MCP integration (already shipping):** ESP-Claw is an MCP server/client today.
   - **Learn:** Prioritize the LIAL MCP server. This is the "Silicon as a Service" endpoint that makes LIAL accessible to any MCP-compatible agent.

3. **On-device memory:** ESP-Claw stores user preferences, routines, and interaction history locally.
   - **Learn:** Add a small persistent storage syscall (`lial_storage_read`/`lial_storage_write`) so Wasm modules can persist state across reboots.

4. **Chat interface (Telegram/WeChat):** End-user friendly, no technical setup.
   - **Learn:** LIAL's MCP server achieves the same goal (any chat interface with MCP support becomes the UI).

5. **Streaming JSON parsing:** Efficient handling of large LLM responses in <8KB.
   - **Not relevant for LIAL:** LIAL doesn't parse LLM responses on-device. The host handles all LLM interaction.

### 5.5 LIAL's Unique Differentiators (Things ESP-Claw Cannot Do)

1. **Compile-once, run-anywhere:** The same `.wasm` binary runs on ESP32-C3, Pico, STM32, nRF52 — zero changes. ESP-Claw Lua scripts depend on Espressif's HAL.

2. **Sub-millisecond control loops:** Compiled Wasm can run a PID controller at 10kHz+. Lua interpretation adds ~100μs per operation minimum.

3. **Formal verification potential:** Wasm's structured format enables static analysis (no computed jumps, typed stack). You can prove properties about LLM-generated code before execution.

4. **Edge federation:** A host can orchestrate 100 heterogeneous devices simultaneously. ESP-Claw scales linearly (each device is independent).

5. **The "push once" model:** For a thermostat, LIAL pushes a ~2KB control loop and the device runs for months autonomously. ESP-Claw needs WiFi + cloud API for every temperature check.

---

## 6. Wireless Control — Making LIAL Truly Untethered

### 6.1 The Full Wireless Stack

```
Phase 1: Initial Flash (must be wired)
  └── USB cable → lial init → firmware installed

Phase 2: Normal Operation (wireless)
  ├── Discovery: mDNS / BLE advertising
  ├── Connection: TCP persistent socket (port 9100)
  ├── Wasm Push: Host compiles + sends over TCP
  ├── Execution: Device runs locally
  ├── Results: Device sends back over TCP
  ├── Streaming: Continuous sensor data (opcode 0x04)
  └── Control: Stop/swap/query over same TCP connection

Phase 3: Firmware Updates (wireless, rare)
  └── WiFi OTA: Host pushes new receiver firmware
```

### 6.2 What "Host Controls Receiver from Code" Means

The Python host maintains full programmatic control:

```python
class LialDevice:
    async def push(self, wasm_bytes: bytes) -> ExecResult:
        """Push and execute Wasm module."""
    
    async def stop(self) -> None:
        """Stop currently running module."""
    
    async def hot_swap(self, wasm_bytes: bytes) -> None:
        """Replace running module without reboot."""
    
    async def set_param(self, slot: int, value: int) -> None:
        """Set runtime parameter (live, no recompile)."""
    
    async def query_status(self) -> DeviceStatus:
        """Get running state, fuel remaining, uptime."""
    
    async def stream_subscribe(self) -> AsyncIterator[SensorData]:
        """Subscribe to sensor data stream."""
    
    async def ota_update(self, firmware: bytes) -> None:
        """Push new receiver firmware (rare)."""
```

This API works identically whether the device is on USB, WiFi, or BLE — the `LialTransport` trait handles the difference.

### 6.3 Async Architecture (Host Side)

```python
import asyncio

async def agentic_loop(device: LialDevice, llm: LLMClient):
    # Subscribe to sensor stream
    async for reading in device.stream_subscribe():
        # Feed to LLM
        response = await llm.complete(
            f"Temperature is {reading.temp}°C. Target is 22°C. "
            f"Current state: {reading.state}. What should I do?"
        )
        
        if response.action == "compile_new":
            wasm = await compile(response.code)
            await device.hot_swap(wasm)
        elif response.action == "adjust_param":
            await device.set_param(response.slot, response.value)
```

---

## 7. Key Decisions for Week 3

### 7.1 Transport Priority

1. **WiFi TCP** (ESP32-C3, Pico W) — highest value, enables everything
2. **USB CDC** (current, keep working) — development fallback
3. **BLE GATT** — later, for battery devices and phone-as-host

### 7.2 Pico Testing Priority

1. **Get wasmi compiling** for `thumbv6m-none-eabi` (may not need patches!)
2. **Basic blink** via Wasm on Pico (proves the stack works)
3. **USB transport** (USB CDC serial — same protocol as ESP32)
4. **WiFi transport** (Pico W with `cyw43` + `embassy-net`)
5. **Dual-core** (Wasm on Core 1, transport on Core 0)

### 7.3 ESP-Claw Differentiation Priority

1. **MCP server** — matches ESP-Claw's MCP, but host-side (richer context)
2. **Multi-device** — capability ESP-Claw cannot match architecturally
3. **Persistent storage syscall** — learns from ESP-Claw's on-device memory
4. **Gas metering visibility** — expose fuel stats (ESP-Claw has nothing comparable)
5. **Formal Wasm validation** — reject invalid modules before execution (security)

---

## 8. Open Questions to Resolve

1. **Does wasmi compile clean for `thumbv6m-none-eabi`?** If yes, Pico is much easier than ESP32-C3. If no, what patches are needed?

2. **Embassy vs bare-metal for Pico?** Embassy provides async WiFi but adds complexity. Bare-metal is simpler but means blocking I/O.

3. **How small can we make the receiver?** ESP-Claw needs 8MB PSRAM. LIAL should run in <100KB SRAM total. Can we hit <64KB?

4. **Should LIAL support "standalone mode"?** A device that has pre-loaded Wasm modules and triggers them locally (no host needed after initial push). This would match ESP-Claw's "offline" capability.

5. **What about RP2350 (Pico 2)?** It has ARM Cortex-M33 with hardware FPU and security extensions (TrustZone). Could provide hardware-backed Wasm isolation.
