# Week 3 Plan: Transport + Multi-Device + Feedback + Safety + DX

**Goal:** Untether devices from USB, enable multi-device orchestration, close the agentic feedback loop, add production guardrails, and build the developer-facing tools that make LIAL accessible.

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

## Week 3 Tasks Summary

| Task | Layer | Effort | Priority |
|------|-------|--------|----------|
| SVD parser + manifest generator | 3b | Medium | 1 (highest) |
| SVD file collection (ESP32-C3, RP2040, STM32F4) | 3b | Small | 1 |
| Runtime auto-discovery expansion | 3b | Small | 2 |
| Wi-Fi transport (TCP socket) | 4 | Medium | 1 (highest) |
| Transport abstraction trait (`LialTransport`) | 4 | Medium | 1 |
| BLE + Wi-Fi device discovery | 4 | Medium | 2 |
| BLE transport (GATT) | 4 | Medium | 3 |
| CBOR serialization | 4 | Small | 3 |
| Device registry + LLM device routing | 5 | Medium | 2 |
| Parallel execution + device groups | 5 | Medium | 3 |
| Bidirectional streaming (opcode `0x04`) | 6 | Medium | 2 |
| LLM-in-the-loop agentic cycle | 6 | Medium | 3 |
| Persistent programs + hot-swap | 6 | Medium | 3 |
| Peripheral whitelisting + wasm validation | 7 | Small | 2 |
| Hardware watchdog + rate limiting | 7 | Small | 3 |
| OTA firmware updates + TLS | 7 | Medium | 4 |
| `lial` CLI tool | 8 | Medium | 3 |
| MCP server | 8 | Medium | 3 |
| Upstream wasmi patches | 8 | Small | 4 |

### Priority Order Within Week 3

1. **SVD-driven manifests + auto-discovery** -- Eliminates hand-coded capability lists; scales to any board.
2. **Wi-Fi transport + transport abstraction** -- Untethers the device. Enables everything else.
2. **Bidirectional streaming + device discovery** -- Enables sensor reading and fleet visibility.
3. **Multi-device orchestration + LLM-in-the-loop** -- The agentic loop across multiple devices.
4. **Safety hardening** -- Whitelisting, watchdog, wasm validation.
5. **DX** -- CLI tool, MCP server, upstream patches.

---

## What's Done After Week 3

If Week 2 and Week 3 are completed, LIAL will have:
- Any supported board flashable with one command or auto-detected on plug-in
- Board-agnostic hardware abstraction via `embedded-hal`
- ~12 syscalls covering GPIO, I2C, SPI, PWM, ADC, UART, delay, uptime, log
- Rich auto-generated hardware manifests from SVD + runtime probing
- USB, Wi-Fi, and BLE transport options
- Multi-device orchestration with LLM device routing
- Bidirectional sensor streaming with LLM-in-the-loop adaptation
- Hot-swap of running programs
- Peripheral whitelisting and hardware watchdog safety
- A standalone CLI tool and an MCP server for agent integration
