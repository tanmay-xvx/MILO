# Week 3 — Implementation record and plan validation

This document serves two purposes:

1. **Technical record** — what was built, how, and how to reproduce it.
2. **Plan validation** — mapping every phase of the **Week 3 Phased Build** plan to the codebase, with an honest **done / partial / not done** status.

The canonical phased build specification (5 phases + dependency graph + file/dependency tables) lives in **`docs/week3/misc/buildplan.md`** (version-controlled copy). Narrative and deltas may also appear in `docs/week3/plan.md`.

---

## Executive summary

| Phase | Theme | Plan intent | Overall status |
|-------|--------|-------------|----------------|
| **1** | Transport abstraction | Pluggable LIAL-Link I/O on host + receiver | **Done** (with intentional file layout differences) |
| **2** | Raspberry Pi Pico | wasmi + `Rp2040Hal` + USB serial end-to-end | **Done** (Pico uses a dedicated loop instead of shared `main_loop`) |
| **3** | WiFi transport | TCP + mDNS, wireless push | **Partial** (host CLI + discovery + TCP client; receiver WiFi transport is a **stub**) |
| **4** | Extended protocol + dual-core | Opcodes 0x04–0x09, executor, params, Core 0/1 split | **Partial** (ESP32 `main_loop`: most control opcodes + validation + `lial_get_param`; **Pico: no extended opcodes in USB loop**; dual-core **not** driving Core 1) |
| **5** | Multi-device, MCP, safety | Registry, MCP, validation, watchdog, whitelist | **Partial** (registry + MCP scaffold + **import validation** on ESP path; multi-device LLM routing / parallel asyncio / watchdog kick **incomplete or missing**) |

---

## Phase 1: Transport abstraction (foundation)

**Plan goal:** Decouple transport from hardware; same framing over pluggable links.

### Plan checklist vs implementation

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| Define `LialTransport` on receiver | **Done** | Trait lives in `lial-receiver/src/transport.rs` (not `link.rs`). Methods: `read_frame` / `write_frame` returning `Result<Frame, LinkError>`, matching the plan’s intent. |
| Extract USB serial into dedicated struct | **Done** | `EmbeddedIoTransport<W, R>` wraps any `embedded_io::Read` + `Write` pair. ESP32 uses it with USB Serial JTAG `split()`. Plan named `UsbSerialTransport`; same role. |
| Separate `Esp32C3Hal` from transport | **Done** | `main.rs` `esp_entry`: `let mut transport = EmbeddedIoTransport::new(tx, rx);` and `let mut hal = Esp32C3Hal::new(...)`. HAL no longer owns the link. |
| Refactor `main_loop` to `main_loop<T: LialTransport, H: LialHardware>` | **Done** | `lial-receiver/src/lib.rs`: `pub fn main_loop<T: transport::LialTransport, H: LialHardware + 'static>(...)`. ESP32 entry calls `lial_receiver::main_loop(&mut transport, hal, &manifest_json)`. |
| `StdioTransport` (std feature) | **Done** | `transport.rs`, `#[cfg(feature = "std")]`, laptop/mock path in `main.rs`. |
| Host `transport.py` ABC | **Done** | `lial-host/transport.py`: `LialTransport`, `SerialTransport`, `TcpTransport`, opcode constants aligned with `link.rs`. |
| Move serial framing out of `lial_host.py` | **Done** | `lial_host.py` imports `SerialTransport`, `TcpTransport`, `LialTransport`; `_open_transport()` selects serial vs WiFi. |
| **Deliverable:** ESP build unchanged for users | **Done** | `--features esp32c3` path still uses shared `main_loop` + `EmbeddedIoTransport`. |

**Deviations from plan file names:** The plan listed `transport_usb.rs` and a separate `transport_tcp.py`. The repo uses **`transport.rs`** (receiver) and **`TcpTransport` inside `transport.py`** (host). Behavior matches the plan; only file boundaries differ.

---

## Phase 2: Raspberry Pi Pico port

**Plan goal:** wasmi + LIAL on RP2040; Wasm blink on real hardware; same host over USB serial.

### Plan checklist vs implementation

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| **2a** `rp2040` feature in `Cargo.toml` | **Done** | Feature includes `rp2040-hal`, `rp2040-boot2`, `cortex-m`, `cortex-m-rt`, `embedded-alloc`, `usb-device`, `usbd-serial`, `portable-atomic`, `embedded-hal-0-2` (ADC `OneShot`). |
| **2a** `thumbv6m-none-eabi` + `build-std` | **Done** | `lial-receiver/.cargo/config.toml`: `[target.thumbv6m-none-eabi]` rustflags + `build-std = ["core", "alloc"]` under `[unstable]`. **`memory.x`** added for link script. |
| **2a** wasmi on M0+ | **Done** | Build with **`cargo build --release --no-default-features --features rp2040 --target thumbv6m-none-eabi`** so `wasmi/std` is not enabled. Patches under `patches/` still apply where needed for the dependency graph. |
| **2b** `Rp2040Hal` (`rp2040.rs`) | **Done** | Same `EmbeddedHalAdapter` pattern as ESP32: GPIO 25, I2C0 on GP4/GP5, ADC on GP26; **`RecoveringI2c`** + bus recovery; timer-based delay with optional USB poll. |
| **2b** `transport_usb_pico.rs` | **Not as specified** | No separate file. **USB CDC** framing is implemented **inline** in `main.rs` (`pico_entry`): read buffer accumulation, frame parse, `usb_write_all` for chunked TX. Functionally equivalent to a dedicated module; refactor possible later. |
| **2c** Pico `main` entry | **Done** | `#[cfg(feature = "rp2040")] mod pico_entry`: `cortex_m_rt::entry`, `embedded_alloc` heap, clocks, pins, I2C @ 100 kHz, ADC, USB CDC + `usbd-serial`. |
| **2c** Call `main_loop(transport, hal)` | **Partial** | Pico does **not** use `lib::main_loop`. It uses a **local infinite loop** (USB poll, discovery, bytecode dispatch) so **`usb_write_all`**, **chunked discovery responses**, and **USB poll during `lial_delay_ms`** can be wired without changing the generic `LialTransport` trait for embedded USB. ESP32 keeps the shared `main_loop`. |
| **2d** Blink Wasm test | **Done** (reported) | Onboard LED GPIO **25**; example `examples/test_drivers/blink_led` updated. Host `lial_host.py run --port ...` works over CDC. |
| **2d** HIL on Pico (`hil_test.py`) | **Not verified in this doc** | Plan called for wiring HIL against Pico; confirm separately in CI or manual HIL docs. |
| **Deliverable:** same host, no `LialRuntime`/`LialHardware` change | **Done** | Syscall surface unchanged; drivers stay `wasm32-unknown-unknown`. |

**Extra work (not in short plan text but required for real bring-up):** Boot2 section, USB enumeration race mitigation (host 1 s settle + discovery retries), I2C robustness (no SSD1306 probe at boot on Pico, hard-coded `0x3C` in manifest), OLED prompt updates (full GDDRAM clear after init).

---

## Phase 3: WiFi transport (ESP32-C3 wireless)

**Plan goal:** LIAL-Link over TCP; mDNS; host `--transport wifi`.

### Plan checklist vs implementation

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| WiFi init in ESP `esp_entry` | **Not done** | No WiFi/AP join in the main ESP entry path in-repo as a default build. |
| `transport_wifi.rs` / TCP server :9100 | **Partial** | **`lial-receiver/src/transport_wifi.rs`** exists behind feature **`esp32c3-wifi`**. `WifiTcpTransport::read_frame` / `write_frame` return **placeholder errors** (“wifi transport not yet active”). **Not production TCP.** |
| Receiver mDNS `_lial._tcp.local` | **Not done** | Not implemented on device. |
| Dual-transport (WiFi + USB) | **Not done** | — |
| Host `TcpTransport` | **Done** | In **`lial-host/transport.py`** (plan suggested `transport_tcp.py`). |
| Host mDNS discovery | **Partial** | **`lial-host/discovery.py`**: `discover_mdns()` using `zeroconf` for `_lial._tcp.local.` when installed. Useful when a **real** receiver advertises; today the receiver does not advertise. |
| `lial_host.py --transport wifi` / `--ip` | **Done** | `_open_transport()`: `--transport wifi`, optional `--ip`, else mDNS; `--tcp-port` default 9100. |
| **Deliverable:** push Wasm over WiFi | **Not met end-to-end** | Requires completing `WifiTcpTransport` + `esp-wifi` / socket polling in `main` and mDNS on device. |

---

## Phase 4: Extended control protocol + dual-core

**Plan goal:** Opcodes 0x04–0x09, `LialExecutor`, parameter slots, optional Core 1 Wasm on Pico, host `LialDevice` API.

### Opcodes and executor (receiver)

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| Constants `OP_STREAM_DATA` … `OP_HOT_SWAP` in `link.rs` | **Done** | `lial-receiver/src/link.rs` and `lial-host/transport.py` aligned. |
| `LialExecutor` + `SingleCoreExecutor` | **Done** | `lial-receiver/src/executor.rs`: `submit`, `poll_result`, `is_running`, `stop`, `status`; `SingleCoreExecutor` runs Wasm **inline** (same as pre-planned behavior). |
| `PARAM_SLOTS` + `lial_get_param` | **Done** | `executor.rs`: `AtomicU32` slots; **`lib.rs`** registers **`lial_get_param`** in the Wasm linker. |
| `main_loop` handles stop / query / set param / hot-swap | **Done** | `lib.rs` `main_loop`: **`OP_STOP`**, **`OP_QUERY_STATUS`**, **`OP_SET_PARAM`** (8-byte BE payload), **`OP_HOT_SWAP`** (after validation). **`OP_STREAM_DATA`**: no dedicated branch → falls through to **unknown opcode** JSON error. |
| Wasm validation before execute / hot-swap | **Done (ESP path)** | `validation::validate_wasm_imports` on **`OP_BYTECODE_PUSH`** and **`OP_HOT_SWAP`** inside **`main_loop`**. Parses Wasm import section; only **Alphabet** names allowed (includes `lial_get_param`). |
| **Pico path:** extended opcodes + validation | **Not done** | Pico `pico_entry` handles **discovery** and **bytecode push** only; **no** `validate_wasm_imports` call before `LialRuntime::execute`. Extended opcodes **not** dispatched on USB loop. |
| `DualCoreExecutor` (Core 0 link, Core 1 Wasm) | **Partial** | **`lial-receiver/src/executor_dual.rs`**: structure and **`LialExecutor`** impl exist; **`core1_available`** path falls back to **single-core** execution; Core 1 FIFO/spawn **not** integrated in `main.rs`. |
| **Deliverable:** Pico transport Core 0, Wasm Core 1 | **Not met** | Pico still runs transport + Wasm on the same core (with USB polling in delay). |

### Host `LialDevice` (plan: async)

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| `lial_device.py` with `push`, `stop`, `query_status`, `set_param`, hot-swap | **Done** | Synchronous wrapper over `LialTransport` (plan said “async class”; implementation is **sync** + optional `asyncio` usage elsewhere). |
| `stream_subscribe` / streaming | **Partial** | `OP_STREAM_DATA` exists as constant; **no** full streaming protocol loop wired on device + host in a documented way. |

---

## Phase 5: Multi-device, MCP, safety

**Plan goal:** Device registry, MCP tools, Wasm validation, peripheral whitelist, watchdog.

### Checklist vs implementation

| Plan item | Status | How it was done |
|-----------|--------|-----------------|
| `device_registry.py` | **Done** | Register serial/TCP, `list_devices`, `push_to`, **`push_to_all`** (sequential), `stop_all`, `query_all`, `get_manifests_summary`. |
| LLM routing: `[{"device": "name", "code": "..."}]` | **Not done** | Interactive `lial_host.py` still targets **one** manifest per session; registry is for **programmatic** / MCP-style use. |
| Parallel push `asyncio.gather` | **Partial** | **`push_to_all` is sequential**, not parallel. Docstring mentions parallel; implementation does not use `gather`. |
| `mcp_server.py` | **Done (scaffold)** | **`__main__`**: minimal **stdio JSON-RPC** loop (`tools/list`, `tools/call`) — no `mcp` PyPI package required. Tool handlers route to `DeviceRegistry` + `lial_compiler`. Wire from Cursor/Claude via MCP config pointing at this script. |
| Wasm validation (Alphabet only) | **Done (ESP `main_loop`)** | `validation.rs` + hook in `main_loop`. **Pico path:** bypass — see Phase 4. |
| Peripheral whitelisting (manifest pins) | **Not done** | Syscalls still rely on HAL; no central “reject GPIO not in manifest” layer documented here. |
| Hardware watchdog (ESP + Pico kick) | **Partial** | Pico init creates **`Watchdog`** from HAL but **feeding** from transport loop is **not** described as mandatory in the bring-up path; ESP RWDT kick **not** verified here. |

---

## Plan appendix tables (files and dependencies)

### “New files” table from plan vs repo

| Plan file | Present? | Actual path / note |
|-----------|----------|---------------------|
| `transport.rs` | Yes | `lial-receiver/src/transport.rs` |
| `transport_usb.rs` | No | **`EmbeddedIoTransport`** in `transport.rs` |
| `transport_usb_pico.rs` | No | USB loop in **`main.rs`** (`pico_entry`) |
| `rp2040.rs` | Yes | `lial-receiver/src/rp2040.rs` |
| `transport_wifi.rs` | Yes | Stub `WifiTcpTransport` |
| `executor.rs` | Yes | + `executor_dual.rs` |
| `transport.py` | Yes | Includes `TcpTransport` (no separate `transport_tcp.py`) |
| `lial_device.py` | Yes | |
| `device_registry.py` | Yes | |
| `mcp_server.py` | Yes | |

### Cargo (plan vs actual)

| Plan | Actual |
|------|--------|
| `rp2040-hal`, `cortex-m`, `embedded-alloc`, `usb-*` | As in `Cargo.toml` + **`rp2040-boot2`**, **`portable-atomic`**, **`embedded-hal-0-2`** |
| `esp-wifi` for Phase 3 | Optional feature **`esp32c3-wifi`** with `esp-wifi`, `smoltcp` — **not fully wired to working TCP server** |

### Python (plan vs actual)

| Plan | Actual |
|------|--------|
| `zeroconf` for mDNS | Used optionally in **`discovery.py`** |
| `mcp` SDK | Confirm in your venv if `mcp_server.py` imports it |

---

## Technical reference (cross-cutting)

### Raspberry Pi Pico (build and pins)

- **Build:** `cargo build --release --no-default-features --features rp2040 --target thumbv6m-none-eabi`
- **Boot:** `rp2040-boot2` `.boot2` section; **`memory.x`** + `build-std`.
- **Pins:** LED **25**, I2C0 **SDA 4 / SCL 5**, ADC **GP26** registered as channel **26** in manifest.
- **USB:** CDC; **`usb_write_all`** for large frames; **`set_usb_poll` / `clear_usb_poll`** around `LialRuntime::execute` so `lial_delay_ms` keeps USB alive.
- **I2C:** 100 kHz, **`RecoveringI2c`**, boot manifest uses **`0x3C`** without scan (avoids some OLED oddities).

### ESP32-C3

- **`main_loop`:** discovery, bytecode with **import validation**, stop, query, set param, hot-swap.
- **Transport:** `EmbeddedIoTransport` over USB Serial JTAG.

### Host LLM prompt (SSD1306)

- `lial_host.py` **SYSTEM_PROMPT**: init sequence, **mandatory full display clear** (8×129 bytes per page pattern), bitmap fonts; explicitDevices even if `devices_present` is empty.

### Examples

- **`examples/test_drivers/blink_led`:** GPIO **25** for Pico.

### Related docs

| File | Purpose |
|------|---------|
| **`docs/week3/misc/buildplan.md`** | **Week 3 phased build plan** (phases 1–5, mermaid graph, new-file and dependency tables) — canonical spec in git |
| `docs/week3/plan.md` | Expanded phased narrative in repo (optional overlap) |
| `docs/week3/research.md` | Flashing, transports, multi-core, ESP-Claw notes |
| `docs/week3/context.md` | Pre–Week 3 snapshot |

---

## Verified scenarios (reported on hardware)

- **Pico:** discovery `rp2040-pico`, blink, ADC live read, SSD1306 with init + clear + bitmap digits.
- **ESP32-C3:** existing Week 1–2 path preserved via `main_loop`.

---

## Recommended follow-ups (priority order)

1. **Unify Pico with `main_loop`:** either extend `LialTransport` for USB CDC write polling or share opcode dispatch so Pico gets **validation** + **extended opcodes**.
2. **Complete Phase 3:** real `WifiTcpTransport` + ESP main wiring + optional mDNS advertise.
3. **Dual-core:** spawn Core 1 in `pico_entry`, set `core1_available = true`, mailbox bytecode.
4. **Phase 5:** peripheral whitelist from manifest; watchdog feed; **`push_to_all`** true parallelism; optional multi-manifest LLM mode in `lial_host.py`.

---

*This document is aligned to `docs/week3/misc/buildplan.md` for traceability. Last updated for plan-vs-implementation audit.*
