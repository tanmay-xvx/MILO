# Week 2 Action Plan: Universal Hardware Abstraction

**Goal:** Replace per-board `LialHardware` implementations with a single generic `embedded-hal` adapter, so adding support for any new board requires zero LIAL code changes.

## Context

In Week 1, the `LialHardware` trait was introduced with two concrete implementations:

- `LaptopMock` -- prints to terminal, simulates hardware
- `Esp32C3Hal` -- direct `esp-hal` calls for real GPIO, UART, I2C on ESP32-C3

This works, but every new board (STM32, Raspberry Pi, ESP32-S3, etc.) would require writing another ~30-line `impl LialHardware for NewBoard`. The `embedded-hal` ecosystem already standardizes these operations across all boards.

## The Generic `embedded-hal` Adapter

Replace board-specific impls with a single generic struct:

```rust
use embedded_hal::digital::{OutputPin, InputPin};
use embedded_hal::delay::DelayNs;
use embedded_hal::i2c::I2c;

pub struct EmbeddedHalAdapter<P, D, I> {
    pins: BTreeMap<u32, P>,   // pin number -> GPIO pin
    delay: D,
    i2c: I,
    log_sink: fn(&str),       // pluggable log output
}

impl<P, D, I> LialHardware for EmbeddedHalAdapter<P, D, I>
where
    P: OutputPin + InputPin,
    D: DelayNs,
    I: I2c,
{
    fn gpio_set(&mut self, pin: u32, state: u32) {
        if let Some(p) = self.pins.get_mut(&pin) {
            if state == 1 { p.set_high().ok(); } else { p.set_low().ok(); }
        }
    }

    fn gpio_get(&mut self, pin: u32) -> u32 {
        self.pins.get_mut(&pin)
            .map(|p| if p.is_high().unwrap_or(false) { 1 } else { 0 })
            .unwrap_or(0)
    }

    fn delay_ms(&mut self, ms: u32) {
        self.delay.delay_ms(ms);
    }

    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        self.i2c.write_read(addr, tx, rx).map(|_| 0).unwrap_or(-1)
    }

    // ... etc
}
```

With this, supporting a new board becomes:

```rust
// ESP32-S3 -- zero LIAL-specific code
let pins = /* esp-hal GPIO pins */;
let delay = /* esp-hal delay */;
let i2c = /* esp-hal I2C */;
let hw = EmbeddedHalAdapter::new(pins, delay, i2c, uart_log);
let runtime = LialRuntime::new(hw, Some(1_000_000));
```

## Week 2 Tasks

- **Replace `Esp32C3Hal`** with `EmbeddedHalAdapter` instantiated with `esp-hal` peripherals
- **Replace `LaptopMock`** with `EmbeddedHalAdapter` instantiated with software-simulated pins (or keep `LaptopMock` as a lightweight testing-only option)
- **Test on a second board** (if available) to validate true portability -- e.g., ESP32-S3 or STM32
- **Add SVD/Manifest auto-generation** -- parse the device's peripheral description to auto-generate the Hardware Manifest instead of hardcoding it
- **CBOR serialization** -- upgrade LIAL-Link from raw length-prefixed frames to proper CBOR encoding
- **Multi-device support** -- Host orchestrator can manage connections to multiple receivers simultaneously

## Dependencies

- `embedded-hal = "1.0"` (trait definitions)
- Board HALs that implement `embedded-hal` traits (e.g., `esp-hal`, `stm32-hal`, `rppal`)
