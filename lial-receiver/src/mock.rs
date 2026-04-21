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

    fn pwm_set(&mut self, channel: u32, duty_0_10000: u32) {
        let pct = duty_0_10000 as f32 / 100.0;
        eprintln!(" [PWM] ch{channel} -> {pct:.2}%");
    }

    fn adc_read(&mut self, channel: u32) -> u32 {
        // Deterministic ~half-scale reading (2048 on a 12-bit ADC) so tests
        // can make assertions.
        eprintln!(" [ADC] ch{channel} -> 2048");
        2048
    }

    fn spi_transfer(&mut self, bus: u32, tx: &[u8], rx: &mut [u8]) -> i32 {
        eprintln!(
            " [SPI] bus={bus} tx={} rx={}",
            tx.len(),
            rx.len()
        );
        // Loopback-style: first N bytes of tx echo into rx.
        let n = core::cmp::min(tx.len(), rx.len());
        rx[..n].copy_from_slice(&tx[..n]);
        0
    }

    fn uart_write(&mut self, bus: u32, data: &[u8]) -> i32 {
        eprintln!(" [UART] bus={bus} write {} bytes", data.len());
        data.len() as i32
    }

    fn uart_read(&mut self, bus: u32, buf: &mut [u8], timeout_ms: u32) -> i32 {
        eprintln!(
            " [UART] bus={bus} read (cap {}, timeout {}ms) -> 0",
            buf.len(),
            timeout_ms
        );
        0
    }
}
