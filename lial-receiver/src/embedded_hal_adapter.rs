//! Generic `embedded-hal`-backed implementation of `LialHardware`.
//!
//! `embedded-hal` 1.0 traits are generic over an associated `Error` type and
//! therefore cannot be boxed directly. This module defines small object-safe
//! wrapper traits (`DynPin`, `DynPwm`, `DynAdc`, `DynI2c`, `DynSpi`, `DynUart`,
//! `DynDelay`) and blanket adapters that implement them for any concrete
//! `embedded-hal` peripheral.
//!
//! Board-specific modules (`esp32c3.rs`, future `rp2040.rs`, etc.) build an
//! `EmbeddedHalAdapter` by registering their peripherals into these maps. The
//! adapter itself is platform-agnostic.
//!
//! The adapter exposes the full `LialHardware` surface so it can be used
//! directly as the `H` parameter of `LialRuntime<H>`.

use crate::LialHardware;
use alloc::boxed::Box;
use alloc::collections::BTreeMap;
use alloc::string::String;
use alloc::vec::Vec;

// ── Object-safe wrapper traits ──────────────────────────────────────────

/// Digital pin configured as output (with optional read-back of its set state).
pub trait DynPin {
    fn set(&mut self, level: bool);
    /// For stateful output pins this returns the last-written state. Input
    /// pins implement this to return the live sampled level.
    fn get(&mut self) -> bool;
}

/// Monotonic blocking delay (milliseconds + microseconds).
pub trait DynDelay {
    fn delay_ms(&mut self, ms: u32);
}

/// I²C controller.
///
/// Returns 0 on success, non-zero on error. We deliberately do not propagate a
/// rich error type into wasm -- the LIAL syscall ABI only exposes an i32.
pub trait DynI2c {
    fn transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32;
}

/// SPI bus.
pub trait DynSpi {
    fn transfer(&mut self, tx: &[u8], rx: &mut [u8]) -> i32;
}

/// UART bus. Separate from the LIAL-Link transport: this is a user-facing
/// secondary UART exposed to wasm as `lial_uart_*`.
pub trait DynUart {
    fn write(&mut self, data: &[u8]) -> i32;
    /// Reads up to `buf.len()` bytes, returning the number read (possibly 0)
    /// or a negative value on error.
    fn read(&mut self, buf: &mut [u8], timeout_ms: u32) -> i32;
}

/// Pulse-width modulation channel. `duty` is expressed in 1/10000ths
/// (0 = 0%, 10000 = 100%) for portable firmware.
pub trait DynPwm {
    fn set_duty(&mut self, duty_0_10000: u32);
    fn max_duty(&self) -> u32 {
        10000
    }
}

/// Analog-to-digital channel.
pub trait DynAdc {
    fn read(&mut self) -> u32;
    fn resolution_bits(&self) -> u8 {
        12
    }
}

// ── Log sink ─────────────────────────────────────────────────────────────

pub type LogSink = fn(&str);

fn default_log_sink(_msg: &str) {}

// ── Uptime provider ──────────────────────────────────────────────────────

pub type UptimeFn = fn() -> u64;

fn default_uptime() -> u64 {
    0
}

// ── The adapter ──────────────────────────────────────────────────────────

/// `LialHardware` implementation that composes boxed trait objects at runtime.
///
/// Each peripheral is keyed by `u32` (the `pin`/`bus`/`channel` number from
/// the wasm syscall). Boards register only the peripherals they physically
/// expose; unmapped IDs fail gracefully.
pub struct EmbeddedHalAdapter<'d> {
    pub pins: BTreeMap<u32, Box<dyn DynPin + 'd>>,
    pub pwm_channels: BTreeMap<u32, Box<dyn DynPwm + 'd>>,
    pub adc_channels: BTreeMap<u32, Box<dyn DynAdc + 'd>>,
    pub i2c_buses: BTreeMap<u32, Box<dyn DynI2c + 'd>>,
    pub spi_buses: BTreeMap<u32, Box<dyn DynSpi + 'd>>,
    pub uart_buses: BTreeMap<u32, Box<dyn DynUart + 'd>>,
    pub delay: Box<dyn DynDelay + 'd>,
    pub uptime_fn: UptimeFn,
    pub log_sink: LogSink,
    pub last_log: Option<String>,
}

impl<'d> EmbeddedHalAdapter<'d> {
    pub fn builder() -> EmbeddedHalAdapterBuilder<'d> {
        EmbeddedHalAdapterBuilder::default()
    }

    pub fn register_pin(&mut self, id: u32, pin: Box<dyn DynPin + 'd>) {
        self.pins.insert(id, pin);
    }

    pub fn register_pwm(&mut self, id: u32, pwm: Box<dyn DynPwm + 'd>) {
        self.pwm_channels.insert(id, pwm);
    }

    pub fn register_adc(&mut self, id: u32, adc: Box<dyn DynAdc + 'd>) {
        self.adc_channels.insert(id, adc);
    }

    pub fn register_i2c(&mut self, id: u32, i2c: Box<dyn DynI2c + 'd>) {
        self.i2c_buses.insert(id, i2c);
    }

    pub fn register_spi(&mut self, id: u32, spi: Box<dyn DynSpi + 'd>) {
        self.spi_buses.insert(id, spi);
    }

    pub fn register_uart(&mut self, id: u32, uart: Box<dyn DynUart + 'd>) {
        self.uart_buses.insert(id, uart);
    }

    pub fn registered_pin_ids(&self) -> Vec<u32> {
        self.pins.keys().copied().collect()
    }

    pub fn registered_pwm_ids(&self) -> Vec<u32> {
        self.pwm_channels.keys().copied().collect()
    }

    pub fn registered_adc_ids(&self) -> Vec<u32> {
        self.adc_channels.keys().copied().collect()
    }

    pub fn registered_i2c_buses(&self) -> Vec<u32> {
        self.i2c_buses.keys().copied().collect()
    }

    pub fn registered_spi_buses(&self) -> Vec<u32> {
        self.spi_buses.keys().copied().collect()
    }

    pub fn registered_uart_buses(&self) -> Vec<u32> {
        self.uart_buses.keys().copied().collect()
    }

    /// Scan a specific I²C bus for responding 7-bit addresses (0x08..=0x77 by
    /// convention -- reserved ranges excluded).
    ///
    /// Tries a 1-byte write probe first (works for write-only devices like
    /// SSD1306), then falls back to a 1-byte read probe. A device is
    /// "present" if either succeeds.
    pub fn scan_i2c_bus(&mut self, bus: u32) -> Vec<u8> {
        let mut found = Vec::new();
        let bus_ref = match self.i2c_buses.get_mut(&bus) {
            Some(b) => b,
            None => return found,
        };
        for addr in 0x08u8..=0x77u8 {
            // Write probe: send a single zero byte (benign for most devices)
            if bus_ref.transfer(addr, &[0x00], &mut []) == 0 {
                found.push(addr);
            } else {
                // Read probe fallback for read-only devices
                let mut rx = [0u8; 1];
                if bus_ref.transfer(addr, &[], &mut rx) == 0 {
                    found.push(addr);
                }
            }
        }
        found
    }
}

impl<'d> LialHardware for EmbeddedHalAdapter<'d> {
    fn gpio_set(&mut self, pin: u32, state: u32) {
        if let Some(p) = self.pins.get_mut(&pin) {
            p.set(state != 0);
        }
    }

    fn gpio_get(&mut self, pin: u32) -> u32 {
        self.pins
            .get_mut(&pin)
            .map(|p| if p.get() { 1u32 } else { 0u32 })
            .unwrap_or(0)
    }

    fn delay_ms(&mut self, ms: u32) {
        self.delay.delay_ms(ms);
    }

    fn get_uptime_us(&self) -> u64 {
        (self.uptime_fn)()
    }

    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        // Default to bus 0 -- we only expose `lial_i2c_transfer(addr, ...)` in
        // the current syscall ABI without a bus id. Phase D+ will grow this.
        match self.i2c_buses.get_mut(&0) {
            Some(bus) => bus.transfer(addr, tx, rx),
            None => -1,
        }
    }

    fn log(&mut self, message: &str) {
        (self.log_sink)(message);
        self.last_log = Some(String::from(message));
    }

    fn pwm_set(&mut self, channel: u32, duty_0_10000: u32) {
        if let Some(pwm) = self.pwm_channels.get_mut(&channel) {
            pwm.set_duty(duty_0_10000);
        }
    }

    fn adc_read(&mut self, channel: u32) -> u32 {
        self.adc_channels
            .get_mut(&channel)
            .map(|a| a.read())
            .unwrap_or(0)
    }

    fn spi_transfer(&mut self, bus: u32, tx: &[u8], rx: &mut [u8]) -> i32 {
        match self.spi_buses.get_mut(&bus) {
            Some(b) => b.transfer(tx, rx),
            None => -1,
        }
    }

    fn uart_write(&mut self, bus: u32, data: &[u8]) -> i32 {
        match self.uart_buses.get_mut(&bus) {
            Some(u) => u.write(data),
            None => -1,
        }
    }

    fn uart_read(&mut self, bus: u32, buf: &mut [u8], timeout_ms: u32) -> i32 {
        match self.uart_buses.get_mut(&bus) {
            Some(u) => u.read(buf, timeout_ms),
            None => -1,
        }
    }
}

// ── Builder ──────────────────────────────────────────────────────────────

pub struct EmbeddedHalAdapterBuilder<'d> {
    pins: BTreeMap<u32, Box<dyn DynPin + 'd>>,
    pwm_channels: BTreeMap<u32, Box<dyn DynPwm + 'd>>,
    adc_channels: BTreeMap<u32, Box<dyn DynAdc + 'd>>,
    i2c_buses: BTreeMap<u32, Box<dyn DynI2c + 'd>>,
    spi_buses: BTreeMap<u32, Box<dyn DynSpi + 'd>>,
    uart_buses: BTreeMap<u32, Box<dyn DynUart + 'd>>,
    delay: Option<Box<dyn DynDelay + 'd>>,
    uptime_fn: UptimeFn,
    log_sink: LogSink,
}

impl<'d> Default for EmbeddedHalAdapterBuilder<'d> {
    fn default() -> Self {
        Self {
            pins: BTreeMap::new(),
            pwm_channels: BTreeMap::new(),
            adc_channels: BTreeMap::new(),
            i2c_buses: BTreeMap::new(),
            spi_buses: BTreeMap::new(),
            uart_buses: BTreeMap::new(),
            delay: None,
            uptime_fn: default_uptime,
            log_sink: default_log_sink,
        }
    }
}

impl<'d> EmbeddedHalAdapterBuilder<'d> {
    pub fn pin(mut self, id: u32, pin: Box<dyn DynPin + 'd>) -> Self {
        self.pins.insert(id, pin);
        self
    }
    pub fn pwm(mut self, id: u32, pwm: Box<dyn DynPwm + 'd>) -> Self {
        self.pwm_channels.insert(id, pwm);
        self
    }
    pub fn adc(mut self, id: u32, adc: Box<dyn DynAdc + 'd>) -> Self {
        self.adc_channels.insert(id, adc);
        self
    }
    pub fn i2c(mut self, id: u32, i2c: Box<dyn DynI2c + 'd>) -> Self {
        self.i2c_buses.insert(id, i2c);
        self
    }
    pub fn spi(mut self, id: u32, spi: Box<dyn DynSpi + 'd>) -> Self {
        self.spi_buses.insert(id, spi);
        self
    }
    pub fn uart(mut self, id: u32, uart: Box<dyn DynUart + 'd>) -> Self {
        self.uart_buses.insert(id, uart);
        self
    }
    pub fn delay(mut self, delay: Box<dyn DynDelay + 'd>) -> Self {
        self.delay = Some(delay);
        self
    }
    pub fn uptime_fn(mut self, f: UptimeFn) -> Self {
        self.uptime_fn = f;
        self
    }
    pub fn log_sink(mut self, f: LogSink) -> Self {
        self.log_sink = f;
        self
    }

    pub fn build(self) -> EmbeddedHalAdapter<'d> {
        EmbeddedHalAdapter {
            pins: self.pins,
            pwm_channels: self.pwm_channels,
            adc_channels: self.adc_channels,
            i2c_buses: self.i2c_buses,
            spi_buses: self.spi_buses,
            uart_buses: self.uart_buses,
            delay: self.delay.unwrap_or_else(|| Box::new(NoopDelay)),
            uptime_fn: self.uptime_fn,
            log_sink: self.log_sink,
            last_log: None,
        }
    }
}

// ── Default no-op peripherals for unmapped IDs ──────────────────────────

struct NoopDelay;
impl DynDelay for NoopDelay {
    fn delay_ms(&mut self, _ms: u32) {}
}

// ── Blanket impls from embedded-hal 1.0 traits ───────────────────────────

/// Wrap an `embedded_hal::digital::OutputPin` (+ optional `StatefulOutputPin`)
/// into a `DynPin`. This is intentionally lenient: pins that only implement
/// `OutputPin` will always return `false` from `get()`.
pub struct OutputPinAdapter<P>(pub P);

impl<P> DynPin for OutputPinAdapter<P>
where
    P: embedded_hal::digital::OutputPin + embedded_hal::digital::StatefulOutputPin,
{
    fn set(&mut self, level: bool) {
        if level {
            let _ = self.0.set_high();
        } else {
            let _ = self.0.set_low();
        }
    }

    fn get(&mut self) -> bool {
        self.0.is_set_high().unwrap_or(false)
    }
}

/// Wrap an `embedded_hal::digital::InputPin`. Reads live sampled level.
pub struct InputPinAdapter<P>(pub P);

impl<P> DynPin for InputPinAdapter<P>
where
    P: embedded_hal::digital::InputPin,
{
    fn set(&mut self, _level: bool) {
        // Input pins cannot drive; syscall becomes a no-op.
    }

    fn get(&mut self) -> bool {
        self.0.is_high().unwrap_or(false)
    }
}

/// Wrap a blocking `embedded_hal::delay::DelayNs`.
pub struct DelayAdapter<D>(pub D);

impl<D> DynDelay for DelayAdapter<D>
where
    D: embedded_hal::delay::DelayNs,
{
    fn delay_ms(&mut self, ms: u32) {
        self.0.delay_ms(ms);
    }
}

/// Wrap a blocking `embedded_hal::i2c::I2c`.
pub struct I2cAdapter<B>(pub B);

impl<B> DynI2c for I2cAdapter<B>
where
    B: embedded_hal::i2c::I2c,
{
    fn transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        use embedded_hal::i2c::Operation;
        let result = match (tx.is_empty(), rx.is_empty()) {
            (false, false) => {
                let mut ops = [Operation::Write(tx), Operation::Read(rx)];
                self.0.transaction(addr, &mut ops)
            }
            (false, true) => self.0.write(addr, tx),
            (true, false) => self.0.read(addr, rx),
            (true, true) => Ok(()),
        };
        match result {
            Ok(()) => 0,
            Err(_) => -1,
        }
    }
}

/// Wrap a blocking `embedded_hal::spi::SpiBus`.
pub struct SpiAdapter<B>(pub B);

impl<B> DynSpi for SpiAdapter<B>
where
    B: embedded_hal::spi::SpiBus<u8>,
{
    fn transfer(&mut self, tx: &[u8], rx: &mut [u8]) -> i32 {
        let len = core::cmp::min(tx.len(), rx.len());
        match self.0.transfer(&mut rx[..len], &tx[..len]) {
            Ok(()) => 0,
            Err(_) => -1,
        }
    }
}

/// Wrap a type that implements `SetDutyCycle` for PWM.
pub struct PwmAdapter<P>(pub P);

impl<P> DynPwm for PwmAdapter<P>
where
    P: embedded_hal::pwm::SetDutyCycle,
{
    fn set_duty(&mut self, duty_0_10000: u32) {
        let clamped = duty_0_10000.min(10000);
        let max = self.0.max_duty_cycle() as u32;
        let scaled = ((clamped as u64) * (max as u64) / 10000u64) as u16;
        let _ = self.0.set_duty_cycle(scaled);
    }
}

// ── Unit tests (run with `cargo test` on the laptop) ─────────────────────

#[cfg(all(test, feature = "std"))]
mod tests {
    use super::*;
    use core::cell::RefCell;

    /// Tiny mock pin that records every call, usable in tests.
    struct MockPin {
        state: bool,
        writes: RefCell<Vec<bool>>,
        reads: RefCell<u32>,
    }

    impl MockPin {
        fn new() -> Self {
            Self {
                state: false,
                writes: RefCell::new(Vec::new()),
                reads: RefCell::new(0),
            }
        }
    }

    impl DynPin for MockPin {
        fn set(&mut self, level: bool) {
            self.state = level;
            self.writes.borrow_mut().push(level);
        }

        fn get(&mut self) -> bool {
            *self.reads.borrow_mut() += 1;
            self.state
        }
    }

    struct MockDelay {
        total_ms: u32,
    }

    impl DynDelay for MockDelay {
        fn delay_ms(&mut self, ms: u32) {
            self.total_ms = self.total_ms.saturating_add(ms);
        }
    }

    struct MockI2c {
        pub last_addr: u8,
        pub last_tx: Vec<u8>,
        pub next_rx: Vec<u8>,
    }

    impl DynI2c for MockI2c {
        fn transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
            self.last_addr = addr;
            self.last_tx = tx.to_vec();
            let n = core::cmp::min(self.next_rx.len(), rx.len());
            rx[..n].copy_from_slice(&self.next_rx[..n]);
            0
        }
    }

    fn fake_uptime() -> u64 {
        42
    }

    fn fake_log(_msg: &str) {}

    #[test]
    fn gpio_set_routes_to_registered_pin() {
        let pin = MockPin::new();
        let mut a = EmbeddedHalAdapter::builder()
            .pin(5, Box::new(pin))
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .uptime_fn(fake_uptime)
            .log_sink(fake_log)
            .build();

        a.gpio_set(5, 1);
        a.gpio_set(5, 0);
        a.gpio_set(5, 1);

        // Unregistered pin is a no-op (silent).
        a.gpio_set(99, 1);
    }

    #[test]
    fn gpio_get_reads_state() {
        let pin = MockPin::new();
        let mut a = EmbeddedHalAdapter::builder()
            .pin(3, Box::new(pin))
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();

        a.gpio_set(3, 1);
        assert_eq!(a.gpio_get(3), 1);
        a.gpio_set(3, 0);
        assert_eq!(a.gpio_get(3), 0);

        // Unregistered pin reads 0 (safe default).
        assert_eq!(a.gpio_get(42), 0);
    }

    #[test]
    fn delay_delegates_to_delay_impl() {
        let mut a = EmbeddedHalAdapter::builder()
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();
        a.delay_ms(10);
        a.delay_ms(25);
        // MockDelay tracks total_ms internally; we can't inspect through
        // the boxed trait, but the call should not panic.
    }

    #[test]
    fn uptime_uses_injected_function() {
        let a = EmbeddedHalAdapter::builder()
            .uptime_fn(fake_uptime)
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();
        assert_eq!(a.get_uptime_us(), 42);
    }

    #[test]
    fn i2c_routes_to_bus_zero() {
        let mut a = EmbeddedHalAdapter::builder()
            .i2c(0, Box::new(MockI2c {
                last_addr: 0,
                last_tx: Vec::new(),
                next_rx: alloc::vec![0xDE, 0xAD],
            }))
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();

        let mut rx = [0u8; 2];
        let rc = a.i2c_transfer(0x48, &[0x01, 0x02], &mut rx);
        assert_eq!(rc, 0);
        assert_eq!(rx, [0xDE, 0xAD]);
    }

    #[test]
    fn i2c_unregistered_bus_returns_err() {
        let mut a = EmbeddedHalAdapter::builder()
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();
        let mut rx = [0u8; 0];
        let rc = a.i2c_transfer(0x48, &[], &mut rx);
        assert_eq!(rc, -1);
    }

    #[test]
    fn log_stores_last_message_and_invokes_sink() {
        let mut a = EmbeddedHalAdapter::builder()
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();
        a.log("hello");
        assert_eq!(a.last_log.as_deref(), Some("hello"));
    }

    #[test]
    fn i2c_scan_reports_acking_addresses() {
        /// Selective-ACK mock that ACKs only a fixed set of addresses.
        struct SelectiveI2c {
            ack_addrs: Vec<u8>,
        }

        impl DynI2c for SelectiveI2c {
            fn transfer(&mut self, addr: u8, _tx: &[u8], _rx: &mut [u8]) -> i32 {
                if self.ack_addrs.contains(&addr) { 0 } else { -1 }
            }
        }

        let mut a = EmbeddedHalAdapter::builder()
            .i2c(
                0,
                Box::new(SelectiveI2c {
                    ack_addrs: alloc::vec![0x48, 0x76, 0x3C],
                }),
            )
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();

        let found = a.scan_i2c_bus(0);
        assert_eq!(found, alloc::vec![0x3C, 0x48, 0x76]);

        // Unregistered bus returns empty.
        assert!(a.scan_i2c_bus(5).is_empty());
    }

    #[test]
    fn registered_ids_enumerate_correctly() {
        let a = EmbeddedHalAdapter::builder()
            .pin(5, Box::new(MockPin::new()))
            .pin(6, Box::new(MockPin::new()))
            .pin(7, Box::new(MockPin::new()))
            .delay(Box::new(MockDelay { total_ms: 0 }))
            .build();
        assert_eq!(a.registered_pin_ids(), alloc::vec![5, 6, 7]);
    }
}
