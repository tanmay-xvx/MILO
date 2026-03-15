use crate::LialHardware;
use std::time::Instant;

pub struct LaptopMock {
    start_time: Instant,
}

impl LaptopMock {
    pub fn new() -> Self {
        Self {
            start_time: Instant::now(),
        }
    }
}

impl Default for LaptopMock {
    fn default() -> Self {
        Self::new()
    }
}

impl LialHardware for LaptopMock {
    fn gpio_set(&mut self, pin: u32, state: u32) {
        eprintln!(
            " [GPIO] {} -> {}",
            pin,
            if state != 0 { "ON" } else { "OFF" }
        );
    }

    fn gpio_get(&mut self, pin: u32) -> u32 {
        eprintln!(" [GPIO] read {pin} -> 0");
        0
    }

    fn delay_ms(&mut self, ms: u32) {
        eprintln!(" [TIMER] {ms}ms");
        std::thread::sleep(std::time::Duration::from_millis(ms as u64));
    }

    fn get_uptime_us(&self) -> u64 {
        self.start_time.elapsed().as_micros() as u64
    }

    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        eprintln!(
            " [I2C] addr={addr:#04x} tx={} rx={}",
            tx.len(),
            rx.len()
        );
        0
    }

    fn log(&mut self, message: &str) {
        eprintln!(" [LOG] {message}");
    }
}
