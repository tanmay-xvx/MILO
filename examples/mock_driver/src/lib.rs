// The "Alphabet" imports from our Receiver
unsafe extern "C" {
    fn lial_gpio_set(pin: u32, state: u32);
    fn lial_delay_ms(ms: u32);
}

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    for _ in 0..3 {
        unsafe {
            lial_gpio_set(5, 1);
            lial_delay_ms(500);
            lial_gpio_set(5, 0);
            lial_delay_ms(500);
        }
    }
}
