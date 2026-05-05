//! Raspberry Pi Pico (RP2040) HAL assembly.
//!
//! Peripheral map:
//!   GPIO 25 — onboard LED (directly driven, no PWM in this initial config)
//!   GPIO 26 — ADC channel 0 (potentiometer or analog input)
//!   GPIO 4  — I2C0 SDA
//!   GPIO 5  — I2C0 SCL

use crate::embedded_hal_adapter::{
    DelayAdapter, DynAdc, DynDelay, DynI2c, DynPin, EmbeddedHalAdapter, LogSink,
    OutputPinAdapter,
};
use crate::LialHardware;
use alloc::boxed::Box;
use rp2040_hal::adc::{Adc, AdcPin};
use rp2040_hal::gpio::bank0::{Gpio4, Gpio5, Gpio25, Gpio26};
use rp2040_hal::gpio::{FunctionI2C, FunctionSio, FunctionSioOutput, Pin, PullDown, PullNone, PullUp, SioInput};
use rp2040_hal::i2c::I2C;
use rp2040_hal::pac;
use rp2040_hal::Timer;

use core::sync::atomic::{AtomicPtr, Ordering};
use core::ptr::null_mut;

static TIMER_PTR: AtomicPtr<Timer> = AtomicPtr::new(null_mut());

/// USB poll callback: `fn(ctx)` called during delays to keep USB alive.
static USB_POLL_FN: AtomicPtr<()> = AtomicPtr::new(null_mut());
static USB_POLL_CTX: AtomicPtr<()> = AtomicPtr::new(null_mut());

pub type UsbPollFn = unsafe fn(*mut ());

/// Register a USB poll callback. Called periodically during `delay_ms`.
/// # Safety
/// `ctx` must remain valid until `clear_usb_poll` is called.
pub unsafe fn set_usb_poll(f: UsbPollFn, ctx: *mut ()) {
    USB_POLL_CTX.store(ctx, Ordering::Release);
    USB_POLL_FN.store(f as *mut (), Ordering::Release);
}

pub fn clear_usb_poll() {
    USB_POLL_FN.store(null_mut(), Ordering::Release);
    USB_POLL_CTX.store(null_mut(), Ordering::Release);
}

fn poll_usb_if_available() {
    let f = USB_POLL_FN.load(Ordering::Acquire);
    if !f.is_null() {
        let ctx = USB_POLL_CTX.load(Ordering::Acquire);
        unsafe {
            let func: UsbPollFn = core::mem::transmute(f);
            func(ctx);
        }
    }
}

fn rp2040_uptime_us() -> u64 {
    let ptr = TIMER_PTR.load(Ordering::Relaxed);
    if ptr.is_null() {
        0
    } else {
        unsafe { (*ptr).get_counter().ticks() }
    }
}

fn silent_log(_msg: &str) {}

/// I2C bus recovery: toggles SCL via GPIO to unstick a slave holding SDA low,
/// then clears any abort state on the I2C0 peripheral.
fn i2c_bus_recover() {
    unsafe {
        let io_bank = &*pac::IO_BANK0::ptr();
        let sio = &*pac::SIO::ptr();

        // Override GP5 (SCL) to SIO function for manual GPIO control
        io_bank.gpio(5).gpio_ctrl().write(|w| w.funcsel().sio());
        sio.gpio_oe_set().write(|w| w.bits(1 << 5));

        // Override GP4 (SDA) to SIO for reading
        io_bank.gpio(4).gpio_ctrl().write(|w| w.funcsel().sio());
        sio.gpio_oe_clr().write(|w| w.bits(1 << 4));

        // Toggle SCL up to 9 times to clock out stuck slave
        for _ in 0..9 {
            sio.gpio_out_clr().write(|w| w.bits(1 << 5));
            cortex_m::asm::delay(665); // ~5us
            sio.gpio_out_set().write(|w| w.bits(1 << 5));
            cortex_m::asm::delay(665);
            if sio.gpio_in().read().bits() & (1 << 4) != 0 {
                break;
            }
        }

        // Generate STOP: SDA low→high while SCL high
        sio.gpio_oe_set().write(|w| w.bits(1 << 4));
        sio.gpio_out_clr().write(|w| w.bits(1 << 4));
        cortex_m::asm::delay(665);
        sio.gpio_out_set().write(|w| w.bits(1 << 5));
        cortex_m::asm::delay(665);
        sio.gpio_out_set().write(|w| w.bits(1 << 4));
        cortex_m::asm::delay(665);

        // Restore pins to I2C function
        io_bank.gpio(4).gpio_ctrl().write(|w| w.funcsel().i2c());
        io_bank.gpio(5).gpio_ctrl().write(|w| w.funcsel().i2c());

        // Clear any abort state (read IC_CLR_TX_ABRT to clear)
        let i2c0 = &*pac::I2C0::ptr();
        let _ = i2c0.ic_clr_tx_abrt().read();

        // Disable and re-enable to flush FIFOs without losing config
        i2c0.ic_enable().write(|w| w.enable().disabled());
        cortex_m::asm::delay(133);
        i2c0.ic_enable().write(|w| w.enable().enabled());
    }
}

fn i2c_do_transfer<I: embedded_hal::i2c::I2c>(i2c: &mut I, addr: u8, tx: &[u8], rx: &mut [u8]) -> Result<(), I::Error> {
    use embedded_hal::i2c::Operation;
    match (tx.is_empty(), rx.is_empty()) {
        (false, false) => {
            let mut ops = [Operation::Write(tx), Operation::Read(rx)];
            i2c.transaction(addr, &mut ops)
        }
        (false, true) => i2c.write(addr, tx),
        (true, false) => i2c.read(addr, rx),
        (true, true) => Ok(()),
    }
}

/// I2C wrapper with automatic bus recovery and retry on failure.
struct RecoveringI2c<I> {
    inner: I,
}

impl<I: embedded_hal::i2c::I2c> DynI2c for RecoveringI2c<I> {
    fn transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32 {
        if i2c_do_transfer(&mut self.inner, addr, tx, rx).is_ok() {
            return 0;
        }
        // First attempt failed — recover bus and retry once
        i2c_bus_recover();
        if i2c_do_transfer(&mut self.inner, addr, tx, rx).is_ok() {
            return 0;
        }
        -1
    }
}

/// Bundles the RP2040 ADC peripheral with a specific pin so the `DynAdc`
/// trait (which takes no arguments) can perform a one-shot read.
pub struct Rp2040AdcChannel {
    pub adc: Adc,
    pub pin: AdcPin<Pin<Gpio26, FunctionSio<SioInput>, PullNone>>,
}

impl DynAdc for Rp2040AdcChannel {
    fn read(&mut self) -> u32 {
        use embedded_hal_0_2::adc::OneShot as _;
        let raw: u16 = self.adc.read(&mut self.pin).unwrap_or(0);
        raw as u32
    }

    fn resolution_bits(&self) -> u8 {
        12
    }
}

/// RP2040 hardware abstraction — pure hardware, no transport.
pub struct Rp2040Hal<'d> {
    adapter: EmbeddedHalAdapter<'d>,
}

impl<'d> Rp2040Hal<'d> {
    pub fn new(
        led_pin: Pin<Gpio25, FunctionSioOutput, PullDown>,
        i2c: I2C<
            pac::I2C0,
            (
                Pin<Gpio4, FunctionI2C, PullUp>,
                Pin<Gpio5, FunctionI2C, PullUp>,
            ),
        >,
        adc_channel: Rp2040AdcChannel,
        timer: &'static Timer,
    ) -> Self {
        TIMER_PTR.store(timer as *const Timer as *mut Timer, Ordering::Relaxed);

        let adapter = EmbeddedHalAdapter::builder()
            .pin(25, Box::new(OutputPinAdapter(led_pin)) as Box<dyn DynPin + 'd>)
            .i2c(0, Box::new(RecoveringI2c { inner: i2c }))
            .adc(26, Box::new(adc_channel))
            .delay(Box::new(Rp2040Delay) as Box<dyn DynDelay + 'd>)
            .uptime_fn(rp2040_uptime_us)
            .log_sink(silent_log as LogSink)
            .build();

        Self { adapter }
    }

    pub fn adapter(&self) -> &EmbeddedHalAdapter<'d> {
        &self.adapter
    }

    pub fn adapter_mut(&mut self) -> &mut EmbeddedHalAdapter<'d> {
        &mut self.adapter
    }
}

impl<'d> LialHardware for Rp2040Hal<'d> {
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

struct Rp2040Delay;

impl DynDelay for Rp2040Delay {
    fn delay_ms(&mut self, ms: u32) {
        let ptr = TIMER_PTR.load(Ordering::Relaxed);
        if ptr.is_null() {
            cortex_m::asm::delay(ms * 133_000);
            return;
        }
        let timer = unsafe { &*ptr };
        let start = timer.get_counter().ticks();
        let target = start + (ms as u64) * 1000;
        while timer.get_counter().ticks() < target {
            poll_usb_if_available();
            cortex_m::asm::delay(133_000); // ~1ms between polls
        }
    }
}
