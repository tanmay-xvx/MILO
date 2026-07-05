# Porting MILO to a New Board

MILO's runtime, protocol, validation, signing, and syscall ABI are
board-agnostic. Bringing up a new MCU means implementing **one trait** and
**one entry point** — everything above the HAL is reused unchanged. The
ESP32-C3 and RP2040 ports are the two worked examples; this guide is the
recipe they both follow.

## What you reuse (don't touch)

- `MiloRuntime` / `main_loop_with_policy` — wasm execution, opcode dispatch,
  the four security gates.
- `engine/link.rs`, `validation.rs`, `signing.rs`, `manifest.rs` — protocol,
  import whitelist, Ed25519 verification, discovery manifest.
- The 12-syscall ABI and the host, MCP server, and demos.

## What you implement

### 1. A Cargo feature and dependencies

In `receiver/Cargo.toml`:

```toml
[features]
mynewboard = ["dep:mynewboard-hal", "dep:cortex-m-rt", "dep:portable-atomic",
              "portable-atomic/critical-section"]

[dependencies]
mynewboard-hal = { version = "…", optional = true }
```

If the core has no native atomics (like the RISC-V C3), pull in
`portable-atomic` — and read the note in `.cargo/config.toml` about the
`critical-section` vs `unsafe-assume-single-core` conflict before you wire it.

### 2. The `MiloHardware` implementation

Two options, in increasing order of effort:

- **Use `EmbeddedHalAdapter`** (recommended). If your board's HAL implements
  the `embedded-hal` 1.0 traits (`OutputPin`, `InputPin`, `PwmPin`, `I2c`,
  `SpiBus`), wrap each peripheral in the adapter's `DynPin` / `DynPwm` /
  `DynAdc` / `DynI2c` boxes and register them by id. The adapter already
  routes every syscall, and you inherit **capability-denial logging** for free.
  This is what the RP2040 port does (`targets/rp2040/mod.rs`, ~60 lines of
  glue).

- **Implement `MiloHardware` directly** if your HAL is unusual. Twelve methods,
  each mapping a syscall to your peripheral. Defaults are provided for
  `pwm_set`, `adc_read`, `spi_transfer`, `uart_write`, `uart_read`, so a
  minimal board only writes `gpio_set/get`, `delay_ms`, `get_uptime_us`,
  `i2c_transfer`, and `log`.

```rust
impl MiloHardware for MyBoardHal {
    fn gpio_set(&mut self, pin: u32, state: u32) { /* … */ }
    fn gpio_get(&mut self, pin: u32) -> u32 { /* … */ }
    fn delay_ms(&mut self, ms: u32) { /* … */ }
    fn get_uptime_us(&self) -> u64 { /* … */ }
    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 { /* … */ }
    fn log(&mut self, message: &str) { /* your transport / RTT */ }
    // pwm_set / adc_read / spi_transfer / uart_* — override as supported
}
```

### 3. The entry point in `main.rs`

Gate a module on your feature (copy the `rp2040` block as a template):

1. Init clocks, heap (`embedded-alloc` for Cortex-M), peripherals.
2. Build a `SimHal`-free `MyBoardHal` from real peripherals.
3. Assemble a `MiloTransport` — `EmbeddedIoTransport` over your USB/UART, or a
   custom loop if your USB stack needs polling (see the Pico's chunked CDC
   writer).
4. Build the discovery manifest with your **real** pins/channels/buses. The
   manifest is the source of truth the LLM reads — list only what physically
   exists.
5. Read a compile-time trusted key with `option_env!("MILO_TRUSTED_KEY")` for
   signed-only deployments (optional).
6. Call `main_loop_with_policy(&mut transport, exec, &manifest, policy)`.

### 4. Linker + target config

Add a `[target.<triple>]` block to `receiver/.cargo/config.toml` with the
board's link args. Keep any board-specific `memory.x` **out of the crate root**
— lld resolves `INCLUDE memory.x` from the cwd, and a root copy shadows another
target's generated one (this exact bug bit the Pico↔ESP32 build; see
`build.rs`). Put it under `ld/<board>/` and add the search path in `build.rs`.

### 5. CI + releases

Add a matrix row to `.github/workflows/build-receiver.yml`
(`variant`/`family`/`target`/`features`/`artifact_ext`). The workflow builds
the image, packages it (`.bin` via espflash, `.uf2` via elf2uf2-rs), records
size + sha256, and uploads it — a flashable artifact per board per commit.

## Bring-up checklist

- [ ] `cargo +nightly build --release --target <triple> --features <board> --no-default-features` links clean
- [ ] Discovery returns a manifest whose pins match the wiring
- [ ] `blink_led` example drives the onboard LED
- [ ] `adc_read` / `pwm_set` map to real peripherals (or are absent from the manifest)
- [ ] An out-of-manifest syscall logs `denied: …` rather than misbehaving
- [ ] Fuel exhaustion returns cleanly (push `examples/test_drivers/infinite_loop`)
- [ ] CI matrix row builds and uploads an artifact

## Why the board matrix matters

Every board that speaks MILO-Link runs the *same* wasm drivers, the same MCP
tools, the same LLM prompt. Board coverage and the driver corpus compound:
each new board inherits every driver ever written, and each new driver works on
every board. That product is the moat — not any single port.
