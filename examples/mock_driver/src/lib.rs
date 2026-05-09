// The "Alphabet" imports from our Receiver
unsafe extern "C" {
    fn gpio_set(pin: u32, state: u32);
    fn delay_ms(ms: u32);
}

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    for _ in 0..3 {
        unsafe {
            gpio_set(5, 1);
            delay_ms(500);
            gpio_set(5, 0);
            delay_ms(500);
        }
    }
}
