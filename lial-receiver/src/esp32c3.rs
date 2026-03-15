/// ESP32-C3 hardware implementation using esp-hal.
///
/// BLOCKER: wasmi 1.0.9 uses `alloc::sync::Arc` internally, which requires
/// `target_has_atomic = "ptr"`. The ESP32-C3 target (riscv32imc) lacks hardware
/// atomics, so `alloc::sync` is not available even with `-Z build-std`.
///
/// Resolution paths:
///   1. Patch wasmi to conditionally use `Rc` instead of `Arc` (PR upstream)
///   2. Use `portable-atomic` + critical-section to emulate atomics (needs
///      wasmi to adopt portable-atomic-util::Arc)
///   3. Wait for wasmi to land no-atomics support (wasmi-labs/wasmi#738)
///
/// Target: riscv32imc-unknown-none-elf
/// GPIO 8: Onboard LED (active-low on most ESP32-C3 devkits)
/// GPIO 5: External test LED
///
/// This module compiles when the `esp32c3` feature is enabled.
/// The trait implementation below is structurally complete -- once the wasmi
/// blocker is resolved, it needs only peripheral initialization in `new()`.
use crate::LialHardware;

pub struct Esp32C3Hal {
    // Will hold: esp_hal GPIO output/input pins, Delay, I2C master, UART
    _placeholder: (),
}

impl Esp32C3Hal {
    pub fn new() -> Self {
        Self { _placeholder: () }
    }
}

impl LialHardware for Esp32C3Hal {
    fn gpio_set(&mut self, _pin: u32, _state: u32) {
        // esp_hal::gpio::Output::set_high / set_low based on pin number
        // Requires a pin registry mapping u32 -> concrete Output pin
    }

    fn gpio_get(&mut self, _pin: u32) -> u32 {
        // esp_hal::gpio::Input::is_high -> 1/0
        0
    }

    fn delay_ms(&mut self, _ms: u32) {
        // esp_hal::delay::Delay::delay_millis(ms)
    }

    fn get_uptime_us(&self) -> u64 {
        // esp_hal::time::Instant::now().duration_since_epoch().as_micros()
        0
    }

    fn i2c_transfer(&mut self, _addr: u8, _tx: &[u8], _rx: &mut [u8]) -> i32 {
        // esp_hal::i2c::master::I2c::write_read(addr, tx, rx)
        -1
    }

    fn log(&mut self, _message: &str) {
        // Write to UART0 (same USB-serial used for flashing)
    }
}
