use crate::LialHardware;
use esp_hal::gpio::Output;
use esp_hal::time::{Duration, Instant};

pub struct Esp32C3Hal<'d, W: embedded_io::Write, R: embedded_io::Read> {
    led_pin: Output<'d>,
    writer: W,
    reader: R,
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> Esp32C3Hal<'d, W, R> {
    pub fn new(led_pin: Output<'d>, writer: W, reader: R) -> Self {
        Self { led_pin, writer, reader }
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
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> LialHardware for Esp32C3Hal<'d, W, R> {
    fn gpio_set(&mut self, _pin: u32, state: u32) {
        if state != 0 {
            self.led_pin.set_high();
        } else {
            self.led_pin.set_low();
        }
    }

    fn gpio_get(&mut self, _pin: u32) -> u32 {
        if self.led_pin.is_set_high() { 1 } else { 0 }
    }

    fn delay_ms(&mut self, ms: u32) {
        let start = Instant::now();
        while start.elapsed() < Duration::from_millis(ms as u64) {}
    }

    fn get_uptime_us(&self) -> u64 {
        Instant::now().duration_since_epoch().as_micros()
    }

    fn i2c_transfer(&mut self, _addr: u8, _tx: &[u8], _rx: &mut [u8]) -> i32 {
        -1
    }

    fn log(&mut self, _message: &str) {
        // Don't write to serial -- log messages are captured in HostState.logs
        // and returned in the LIAL-Link result frame. Writing raw text here
        // would corrupt the binary protocol.
    }
}
