# Week 3 Plan: Transport + Multi-Board + Multi-Core + Wireless Control + DX

**Goal:** Untether devices from USB, prove cross-platform portability on Raspberry Pi Pico, establish dual-core execution architecture, enable wireless runtime control, and build the MCP server that differentiates LIAL from ESP-Claw.

## Prerequisites (Week 2 -- Done)

Week 2 delivered:
- Firmware delivery pipeline (CI binaries, `lial download`, `lial init` USB auto-detection, host auto-flash)
- Generic `embedded-hal` adapter with dynamic pin mapping
- Expanded Alphabet (~10-12 syscalls: GPIO, delay, uptime, I2C, SPI, PWM, ADC, UART, log)
- Hardware-in-the-loop test harness (GPIO, ADC, PWM pass on ESP32-C3 Super Mini)
- LEDC hardware PWM with fallback to plain GPIO
- LLM system prompt with SSD1306 font table, memory constraints, capability-aware pin selection
- Interactive `lial_host.py` end-to-end: natural language -> Rust Wasm -> compile -> push -> execute -> result

---

## Layer 3b: SVD-Driven Manifests + Auto-Discovery

**Problem:** Hardware capabilities are currently hand-coded in `main.rs` for each board. Adding a new board means manually listing every pin, bus, and peripheral. SVD files already describe all of this in a machine-readable format.

### SVD Parsing (Host-Side)

Parse ARM CMSIS SVD (System View Description) XML files on the host to extract:
- All GPIO pins and their alternate functions (PWM, I2C, SPI, UART, ADC)
- Peripheral register blocks (LEDC, I2C0/1, SPI0/1/2, UART0/1, ADC1/2)
- Memory map (RAM, flash sizes)

SVD files are widely available for ESP32, STM32, nRF52, RP2040, etc.

```
svd/esp32c3.svd  -->  parse  -->  {
  gpio: [0..21],
  i2c: [{bus: 0, sda_options: [1,3,5,...], scl_options: [2,4,6,...]}],
  adc: [{channel: 0, pins: [0,1,2,3,4]}, ...],
  pwm: {channels: 6, resolution_max: 14},
  ...
}
```

### Auto-Discovery at Boot (Receiver-Side)

Complement SVD data with runtime probing on the receiver:
- **I2C bus scan** (already implemented -- detects SSD1306 at 0x3C, etc.)
- **ADC self-test** (read known reference voltage)
- **GPIO direction detection** (which pins are connected)

The receiver sends the runtime probe results alongside the static SVD-derived capabilities.

### Manifest Generation Pipeline

```
[SVD XML] --parse--> [static capabilities]
                          +
[boot-time probing] ---> [runtime discoveries]
                          =
                    [full manifest JSON]
```

### SVD Source Management

- Ship SVD files in `svd/` directory (one per chip family)
- `lial download --board esp32c3` also fetches the matching SVD
- `lial init` uses SVD to generate the manifest that gets baked into firmware

### Tasks

| Task | Effort | Priority |
|------|--------|----------|
| SVD parser (Python, extract GPIO/I2C/SPI/ADC/PWM/UART) | Medium | 1 |
| SVD file collection (ESP32-C3, RP2040, STM32F4) | Small | 1 |
| Manifest generator from SVD output | Medium | 2 |
| Runtime auto-discovery expansion (ADC self-test, GPIO probe) | Small | 3 |

---

## Layer 4: Transport Beyond USB Serial

**Problem:** USB serial means the host laptop must be physically plugged into the device. Rules out remote operation and multi-device setups.

### Wi-Fi Transport

ESP32-C3 has Wi-Fi built in. The receiver listens on a TCP socket; the host connects over the network. Same LIAL-Link framing, just over TCP instead of serial. This is the biggest unlock for practical use.

### BLE Transport

For battery-powered devices or phones-as-hosts. LIAL-Link frames over GATT characteristics. Lower bandwidth than Wi-Fi but works without network infrastructure.

### CBOR Serialization

Replace the current raw `[opcode: u8][payload_len: u32 BE][payload: bytes]` framing with proper CBOR encoding. Benefits:
- Self-describing types
- Schema evolution (add fields without breaking old receivers)
- Compression for large manifests

### Transport Abstraction in the Receiver

A `LialTransport` trait (analogous to `LialHardware`) with `fn read_frame()` and `fn write_frame()`, implemented for USB serial, TCP, and BLE. The main loop becomes transport-agnostic:

```rust
trait LialTransport {
    fn read_frame(&mut self) -> Result<Frame, TransportError>;
    fn write_frame(&mut self, frame: &Frame) -> Result<(), TransportError>;
}
```

### BLE + Wi-Fi Device Discovery (Layer 1b Extension)

Extends the Week 2 USB-only discovery with wireless transports:
- **BLE scanning** (`bleak` library) -- LIAL receivers advertise a custom service UUID; the advertisement payload includes `family`, `variant`, and firmware version
- **Wi-Fi/mDNS** (`zeroconf` library) -- receivers register `_lial._tcp.local.` on boot, advertising chip type, firmware version, and available peripherals

These only discover already-flashed receivers (cannot flash blank devices over wireless). BLE works for ESP32, nRF52, STM32WB. Wi-Fi works for ESP32, Pico W, any board with a Wi-Fi module.

### New Python Dependencies (Transport)

| Package | Purpose |
|---------|---------|
| `bleak` | BLE scanning + GATT OTA |
| `zeroconf` | mDNS service discovery |

---

## Layer 5: Multi-Device Orchestration

**Problem:** The host talks to one device at a time over one serial port. Real scenarios involve multiple devices.

### Device Registry

The host maintains a table of connected receivers, each with its manifest, transport handle, and status. Devices are identified by a unique ID (MAC address, user-assigned name).

```python
devices = {
    "kitchen-esp": {
        "transport": TCPTransport("192.168.1.47", 9100),
        "manifest": {"pins": {...}, "i2c_devices": [...]},
        "status": "idle",
    },
    "garage-esp": {
        "transport": BLETransport("AA:BB:CC:DD:EE:FF"),
        "manifest": {"pins": {...}, "i2c_devices": [...]},
        "status": "running",
    },
}
```

### LLM Device Routing

When the user says "turn on the kitchen light and read the garage temperature," the LLM needs to know which device controls the kitchen light and which has the temperature sensor. The host provides all manifests in the LLM context; the LLM outputs targeted instructions:

```json
[
  {"device": "kitchen-esp", "code": "...gpio_set(5, 1)..."},
  {"device": "garage-esp", "code": "...i2c_transfer(0x48, ...)..."}
]
```

### Parallel Execution

Push wasm to multiple devices simultaneously, collect results, present a unified view to the user.

### Device Groups and Scenes

"All lights off" compiles one wasm program, pushes it to N devices in parallel.

---

## Layer 6: Feedback Loop and Iteration

**Problem:** Today it's fire-and-forget: push wasm, get one result, done. Real tasks require reading sensors and adapting.

### Bidirectional Streaming

A new LIAL-Link opcode (`0x04 Streaming Data`) where the device sends periodic readings while the wasm runs. The receiver pushes frames like:

```json
{"opcode": "0x04", "payload": {"temperature": 23.5, "timestamp_us": 1234567}}
```

### LLM-in-the-Loop

The host receives sensor data, feeds it back to the LLM, the LLM decides to push a new wasm module. This is the agentic loop:

```
Sensor reads 25C -> host forwards to LLM -> LLM says "above target, switch to cooling"
-> host compiles new wasm -> pushes to device -> relay turns on
```

### Persistent Programs

Currently, wasm runs once and the device waits for the next push. For long-running tasks (thermostat, motion detector), the wasm needs to run indefinitely -- with fuel top-ups or no fuel limit -- until the host sends a stop/replace command.

### Hot-Swap

Push a new wasm module while the old one is running. The receiver gracefully stops the old module and starts the new one, without rebooting. Enables real-time logic updates ("actually, make it ramp up slowly instead of switching instantly").

---

## Layer 7: Safety and Production Hardening

**Problem:** The current system is a demo. Production use with LLM-generated code running on hardware needs serious guardrails.

### Peripheral Whitelisting

The manifest declares which pins/buses the wasm is allowed to touch. The runtime rejects syscalls to non-whitelisted resources. Prevents the LLM from accidentally toggling a pin connected to something dangerous.

### Hardware Watchdog

If wasmi hangs (unlikely with fuel, but defense in depth), the hardware watchdog reboots the device. ESP32-C3 has this built in, just needs enabling.

### Wasm Validation

Before executing, verify the module doesn't import anything outside the Alphabet. Reject modules with unexpected imports.

### Rate Limiting

Prevent the host from flooding the device with rapid wasm pushes that could cause instability.

### OTA Firmware Updates

Update the receiver firmware itself (not the wasm drivers) over the air, so you don't need USB access to patch bugs in the receiver.

### TLS on Wi-Fi Transport

Encrypt LIAL-Link frames when running over TCP. Prevents eavesdropping and injection on the local network.

---

## Layer 8: Developer Experience and Ecosystem

**Problem:** Running LIAL requires cloning a repo, patching wasmi, building with nightly Rust, and manually flashing. Not accessible.

### `lial` CLI Tool

A single command-line tool that replaces the Python scripts for users who don't want to use the LLM:

```bash
lial flash --board esp32c3       # flash receiver firmware
lial connect                     # connect to a device
lial push blink.wasm             # push a wasm program
lial devices                     # list connected devices
```

### MCP Server

Expose LIAL as an MCP (Model Context Protocol) tool server so any MCP-compatible LLM agent (Cursor, Claude Desktop, custom agents) can program hardware as just another tool call. This is the "Silicon as a Service" endpoint.

```json
{
  "tool": "lial_push",
  "arguments": {
    "device": "kitchen-esp",
    "code": "pub extern \"C\" fn run_logic() { ... }"
  }
}
```

### Upstream wasmi Patches

Get the `Arc`->`Rc` / `portable-atomic` changes merged upstream into the `wasmi` crate so the `patches/` directory can be removed and users just `cargo add wasmi`.

---

---

## Layer 9: Raspberry Pi Pico Port + Multi-Core

**Problem:** LIAL only runs on ESP32-C3 (single-core RISC-V). Proving it works on RP2040 (dual-core ARM) validates the "any silicon" promise and unlocks dual-core architecture.

### Pico Porting (RP2040)

The RP2040 is architecturally different from ESP32-C3:
- Dual-core ARM Cortex-M0+ @ 133MHz (vs single-core RISC-V @ 160MHz)
- 264 KB SRAM (vs 400 KB) — tighter but feasible with 64KB Wasm limit
- Native atomics (no `portable-atomic` patches needed!)
- External QSPI flash with XIP (Execute In Place)
- Rust target: `thumbv6m-none-eabi`
- HAL: `rp2040-hal` (implements `embedded-hal` traits — same as ESP32-C3 adapter)

### wasmi on RP2040

Key hypothesis: wasmi may compile clean without our 4-crate patch set because Cortex-M0+ has native atomics. If true, RP2040 is *easier* than ESP32-C3.

Memory budget:
```
Total SRAM: 264 KB
  Stack (both cores):      16 KB
  Firmware:                80 KB
  WiFi (Pico W):          ~50 KB
  Available for Wasm:    ~118 KB
    wasmi interpreter:    ~40 KB
    Wasm linear memory:    64 KB
    Buffers:              ~14 KB
```

### Dual-Core Execution Model

```
Core 0 (I/O):                    Core 1 (Execution):
┌────────────────────┐           ┌────────────────────┐
│ Transport loop     │           │ wasmi runtime      │
│ Frame parse/send   │  ←FIFO→  │ Wasm instantiate   │
│ mDNS/heartbeat     │           │ Alphabet dispatch  │
│ Sensor streaming   │           │ Fuel metering      │
│ Watchdog kick      │           │                    │
└────────────────────┘           └────────────────────┘
```

Benefits:
- Non-blocking transport (receive stop/swap while executing)
- Hot-swap without reboot
- Concurrent sensor streaming + control logic
- Independent watchdog management

### LialExecutor Trait

```rust
trait LialExecutor {
    fn submit_wasm(&mut self, bytecode: &[u8]);
    fn poll_result(&mut self) -> Option<ExecResult>;
    fn is_running(&self) -> bool;
    fn stop(&mut self);
}

// SingleCoreExecutor: runs inline (ESP32-C3)
// DualCoreExecutor: sends to Core 1 via FIFO (RP2040, ESP32-S3)
```

### Tasks

| Task | Effort | Priority |
|------|--------|----------|
| Compile wasmi for `thumbv6m-none-eabi` (test if patches needed) | Small | 1 |
| Create `Rp2040Hal` factory (like `Esp32C3Hal`) | Medium | 1 |
| Basic blink via Wasm on Pico (USB transport) | Medium | 1 |
| Dual-core split (transport on Core 0, Wasm on Core 1) | Medium | 2 |
| WiFi transport on Pico W (`cyw43` + `embassy-net`) | Large | 3 |
| HIL tests on Pico (GPIO, PWM, I2C) | Medium | 2 |

---

## Layer 10: Extended Control Protocol (Wireless Runtime Control)

**Problem:** The host can only push Wasm and receive results. It cannot stop, swap, query, or adjust a running module remotely.

### Extended LIAL-Link OpCodes

| OpCode | Direction | Name | Payload |
|--------|-----------|------|---------|
| 0x01 | Dev→Host | Discovery | Manifest JSON |
| 0x02 | Host→Dev | Bytecode Push | Wasm binary |
| 0x03 | Dev→Host | Exec Result | Return value + logs |
| 0x04 | Dev→Host | Streaming Data | CBOR sensor readings |
| 0x05 | Host→Dev | Stop Execution | (empty) |
| 0x06 | Host→Dev | Query Status | (empty) |
| 0x07 | Dev→Host | Status Response | `{running, fuel_left, uptime}` |
| 0x08 | Host→Dev | Set Parameter | `{slot: u8, value: u32}` |
| 0x09 | Host→Dev | Hot-Swap | New Wasm binary |

### Shared Parameter Slots

Allow host to tweak running Wasm without recompile:

```rust
static PARAM_SLOTS: [AtomicU32; 8] = [...];
// New alphabet syscall:
fn lial_get_param(slot: u32) -> u32;
```

Host sends `0x08 {slot: 0, value: 28}` → Wasm reads `lial_get_param(0)` → returns 28.

### Persistent TCP Architecture

```
Receiver boots → WiFi connect → mDNS register "_lial._tcp.local."
Host discovers → TCP connect (port 9100) → persistent socket
Heartbeat every 5s → detect disconnect < 10s
On disconnect → receiver enters safe state (stop Wasm, wait)
```

### Python Host API

```python
class LialDevice:
    async def push(self, wasm: bytes) -> ExecResult
    async def stop(self) -> None
    async def hot_swap(self, wasm: bytes) -> None
    async def set_param(self, slot: int, value: int) -> None
    async def query_status(self) -> DeviceStatus
    async def stream_subscribe(self) -> AsyncIterator[SensorData]
    async def ota_update(self, firmware: bytes) -> None
```

### Tasks

| Task | Effort | Priority |
|------|--------|----------|
| Implement opcodes 0x04-0x09 in receiver | Medium | 2 |
| Persistent TCP server on receiver (WiFi) | Medium | 1 |
| mDNS registration (`_lial._tcp.local.`) | Small | 2 |
| Heartbeat + disconnect detection | Small | 2 |
| `LialDevice` async Python class | Medium | 2 |
| Shared parameter slots + `lial_get_param` syscall | Small | 3 |

---

## Week 3 Tasks Summary (Revised)

| Task | Layer | Effort | Priority |
|------|-------|--------|----------|
| Compile wasmi for RP2040, test if patches needed | 9 | Small | 1 (highest) |
| `Rp2040Hal` factory + basic Wasm blink on Pico | 9 | Medium | 1 (highest) |
| `LialTransport` trait + WiFi TCP (ESP32-C3) | 4 | Medium | 1 (highest) |
| Persistent TCP server on receiver | 10 | Medium | 1 (highest) |
| SVD parser + manifest generator | 3b | Medium | 1 |
| Dual-core executor (Core 0 I/O, Core 1 Wasm) | 9 | Medium | 2 |
| Extended opcodes (0x04-0x09) | 10 | Medium | 2 |
| HIL tests on Pico (GPIO, PWM, I2C) | 9 | Medium | 2 |
| mDNS discovery + heartbeat | 10 | Small | 2 |
| BLE + Wi-Fi device discovery | 4 | Medium | 2 |
| Bidirectional streaming (opcode 0x04) | 6 | Medium | 2 |
| Peripheral whitelisting + wasm validation | 7 | Small | 2 |
| Device registry + LLM device routing | 5 | Medium | 3 |
| MCP server (LIAL as MCP tool) | 8 | Medium | 3 |
| `LialDevice` async Python host class | 10 | Medium | 3 |
| WiFi transport on Pico W | 9 | Large | 3 |
| BLE transport (GATT) | 4 | Medium | 3 |
| Persistent programs + hot-swap | 6 | Medium | 3 |
| LLM-in-the-loop agentic cycle | 6 | Medium | 3 |
| WiFi OTA firmware updates | 7 | Medium | 4 |
| Upstream wasmi patches | 8 | Small | 4 |

### Priority Order Within Week 3

1. **Pico port + WiFi transport** — Proves "any silicon" and untethers from USB. The single biggest validation milestone.
2. **Extended control protocol + dual-core** — Makes LIAL a real wireless control system, not just push-and-pray.
3. **Discovery + streaming + safety** — Fleet visibility, sensor feedback, production guardrails.
4. **Multi-device + MCP + agentic loop** — The orchestration and ecosystem layer.
5. **OTA + upstream patches** — Polish and maintenance.

---

## What's Done After Week 3

If Week 2 and Week 3 are completed, LIAL will have:
- **Two proven platforms:** ESP32-C3 (RISC-V) and Raspberry Pi Pico (ARM) — validates portability
- **Dual-core execution** on RP2040/ESP32-S3 (transport + Wasm on separate cores)
- **Wireless operation:** WiFi TCP persistent connection with mDNS discovery
- **Full runtime control:** Stop, swap, query, parameterize running modules remotely
- **Bidirectional sensor streaming** with LLM-in-the-loop adaptation
- **SVD-driven manifests** — no more hand-coding capabilities per board
- **MCP server** — any MCP-compatible agent can program hardware
- **Safety hardening** — whitelisting, watchdog, Wasm validation
- **Clear differentiation from ESP-Claw:** runs on $2 hardware, formal sandboxing, offline-first, multi-device orchestration
