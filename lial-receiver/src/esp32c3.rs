//! ESP32-C3 HAL assembly.
//!
//! `Esp32C3Hal` is now a thin factory that builds an `EmbeddedHalAdapter`
//! from the concrete peripherals provided by `esp-hal`. It also owns the
//! LIAL-Link serial transport (USB-Serial-JTAG reader + writer), which is
//! kept distinct from the hardware abstraction: link I/O is a transport
//! concern, not a `LialHardware` concern.
//!
//! Why this split matters: every future board (RP2040, STM32, ...) gets its
//! own board module that assembles its own `EmbeddedHalAdapter`, but they all
//! share the same `LialHardware` impl (the one in `embedded_hal_adapter.rs`).
//! Boards differ only in pin maps and transport wiring.

use crate::embedded_hal_adapter::{
    DelayAdapter, DynDelay, EmbeddedHalAdapter, LogSink, OutputPinAdapter,
};
use crate::LialHardware;
use alloc::boxed::Box;
use esp_hal::delay::Delay;
use esp_hal::gpio::Output;
use esp_hal::time::Instant;

/// Returns microseconds since system boot, monotonic.
fn esp_uptime_us() -> u64 {
    Instant::now().duration_since_epoch().as_micros()
}

/// Silent log sink: on-device log messages are captured by `HostState.logs`
/// and returned in the LIAL-Link result frame. Writing raw text to the USB
/// JTAG here would corrupt the binary protocol.
fn silent_log(_msg: &str) {}

/// ESP32-C3 hardware + link transport.
///
/// `W` and `R` are the UsbSerialJtag TX/RX halves. The inner `adapter` owns
/// the LED (and anything else we register) and implements `LialHardware`.
pub struct Esp32C3Hal<'d, W: embedded_io::Write, R: embedded_io::Read> {
    adapter: EmbeddedHalAdapter<'d>,
    writer: W,
    reader: R,
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> Esp32C3Hal<'d, W, R> {
    /// Construct with a single LED pin on GPIO 5 (the only pin the reference
    /// DevKitC-02 board has wired). Additional pins land as Phase D expands
    /// the Alphabet.
    pub fn new(led_pin: Output<'d>, writer: W, reader: R) -> Self {
        let adapter = EmbeddedHalAdapter::builder()
            .pin(5, Box::new(OutputPinAdapter(led_pin)))
            .delay(Box::new(DelayAdapter(Delay::new())))
            .uptime_fn(esp_uptime_us)
            .log_sink(silent_log as LogSink)
            .build();

        Self {
            adapter,
            writer,
            reader,
        }
    }

    pub fn write_bytes(&mut self, data: &[u8]) {
        let _ = self.writer.write_all(data);
    }

    pub fn read_exact(&mut self, buf: &mut [u8]) {
        let mut pos = 0;
        while pos < buf.len() {
            match self.reader.read(&mut buf[pos..]) {
                Ok(n) if n > 0 => pos += n,
                _ => {}
            }
        }
    }

    /// Access the inner adapter (for discovery-manifest introspection, etc.).
    pub fn adapter(&self) -> &EmbeddedHalAdapter<'d> {
        &self.adapter
    }
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> LialHardware for Esp32C3Hal<'d, W, R> {
    fn gpio_set(&mut self, pin: u32, state: u32) {
        self.adapter.gpio_set(pin, state);
    }

    fn gpio_get(&mut self, pin: u32) -> u32 {
        self.adapter.gpio_get(pin)
    }

    fn delay_ms(&mut self, ms: u32) {
        self.adapter.delay_ms(ms);
    }

    fn get_uptime_us(&self) -> u64 {
        self.adapter.get_uptime_us()
    }

    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        self.adapter.i2c_transfer(addr, tx, rx)
    }

    fn log(&mut self, message: &str) {
        self.adapter.log(message);
    }
}

/// Fallback delay that spin-waits using `esp_hal::time::Instant` -- used by
/// code paths that want delays without the full `Delay` struct (currently
/// unused, but kept for future sub-millisecond precision).
pub struct InstantDelay;

impl DynDelay for InstantDelay {
    fn delay_ms(&mut self, ms: u32) {
        let start = Instant::now();
        while start.elapsed() < esp_hal::time::Duration::from_millis(ms as u64) {}
    }
}
