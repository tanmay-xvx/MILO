//! ESP32-C3 Super Mini HAL assembly.
//!
//! Peripheral map:
//!   GPIO 5  — external LED (PWM via LEDC, falls back to gpio_set routing)
//!   GPIO 2  — potentiometer wiper (ADC1 channel)
//!   GPIO 8  — I2C0 SDA (SSD1306 OLED at 0x3C) — also the onboard blue LED
//!   GPIO 9  — I2C0 SCL

use crate::embedded_hal_adapter::{
    DelayAdapter, DynAdc, DynDelay, DynPwm, EmbeddedHalAdapter, I2cAdapter, LogSink,
};
use crate::LialHardware;
use alloc::boxed::Box;
use esp_hal::analog::adc::{Adc, AdcPin};
use esp_hal::delay::Delay;
use esp_hal::i2c::master::I2c;
use esp_hal::time::Instant;

fn esp_uptime_us() -> u64 {
    Instant::now().duration_since_epoch().as_micros()
}

fn silent_log(_msg: &str) {}

/// esp-hal ADC wrapper implementing `DynAdc`.
pub struct Esp32AdcAdapter<'d> {
    adc: Adc<'d, esp_hal::peripherals::ADC1<'d>, esp_hal::Blocking>,
    pin: AdcPin<esp_hal::peripherals::GPIO2<'d>, esp_hal::peripherals::ADC1<'d>>,
}

impl<'d> DynAdc for Esp32AdcAdapter<'d> {
    fn read(&mut self) -> u32 {
        nb::block!(self.adc.read_oneshot(&mut self.pin)).unwrap_or(0) as u32
    }

    fn resolution_bits(&self) -> u8 {
        12
    }
}

/// ESP32-C3 hardware + link transport.
pub struct Esp32C3Hal<'d, W: embedded_io::Write, R: embedded_io::Read> {
    adapter: EmbeddedHalAdapter<'d>,
    writer: W,
    reader: R,
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> Esp32C3Hal<'d, W, R> {
    pub fn new(
        led_pwm: Box<dyn DynPwm + 'd>,
        i2c: I2c<'d, esp_hal::Blocking>,
        adc: Adc<'d, esp_hal::peripherals::ADC1<'d>, esp_hal::Blocking>,
        adc_pin: AdcPin<esp_hal::peripherals::GPIO2<'d>, esp_hal::peripherals::ADC1<'d>>,
        writer: W,
        reader: R,
    ) -> Self {
        let adapter = EmbeddedHalAdapter::builder()
            .pwm(5, led_pwm)
            .i2c(0, Box::new(I2cAdapter(i2c)))
            .adc(0, Box::new(Esp32AdcAdapter { adc, pin: adc_pin }))
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

    pub fn adapter(&self) -> &EmbeddedHalAdapter<'d> {
        &self.adapter
    }

    pub fn adapter_mut(&mut self) -> &mut EmbeddedHalAdapter<'d> {
        &mut self.adapter
    }
}

impl<'d, W: embedded_io::Write, R: embedded_io::Read> LialHardware for Esp32C3Hal<'d, W, R> {
    fn gpio_set(&mut self, pin: u32, state: u32) {
        if pin == 5 {
            self.adapter.pwm_set(5, if state != 0 { 10000 } else { 0 });
        } else {
            self.adapter.gpio_set(pin, state);
        }
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

    fn pwm_set(&mut self, channel: u32, duty_0_10000: u32) {
        self.adapter.pwm_set(channel, duty_0_10000);
    }

    fn adc_read(&mut self, channel: u32) -> u32 {
        self.adapter.adc_read(channel)
    }

    fn spi_transfer(&mut self, bus: u32, tx: &[u8], rx: &mut [u8]) -> i32 {
        self.adapter.spi_transfer(bus, tx, rx)
    }

    fn uart_write(&mut self, bus: u32, data: &[u8]) -> i32 {
        self.adapter.uart_write(bus, data)
    }

    fn uart_read(&mut self, bus: u32, buf: &mut [u8], timeout_ms: u32) -> i32 {
        self.adapter.uart_read(bus, buf, timeout_ms)
    }
}

pub struct InstantDelay;

impl DynDelay for InstantDelay {
    fn delay_ms(&mut self, ms: u32) {
        let start = Instant::now();
        while start.elapsed() < esp_hal::time::Duration::from_millis(ms as u64) {}
    }
}
