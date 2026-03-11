// lial_std.rs
use embedded_hal::digital::v2::OutputPin;

pub enum LialError {
    InvalidPin,
    ExecutionTimeout,
}

// This is our "Manual Alphabet"
pub fn lial_gpio_set<P: OutputPin>(pin: &mut P, state: bool) {
    if state {
        let _ = pin.set_high();
    } else {
        let _ = pin.set_low();
    }
}