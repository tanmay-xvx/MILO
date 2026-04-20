#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn lial_gpio_set(pin: u32, state: u32);
    fn lial_delay_ms(ms: u32);
    fn lial_log(ptr: u32, len: u32);
}

static MSG: &[u8] = b"Blinking LED on GPIO 5";

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        lial_log(MSG.as_ptr() as u32, MSG.len() as u32);
        for _ in 0..5 {
            lial_gpio_set(5, 1);
            lial_delay_ms(500);
            lial_gpio_set(5, 0);
            lial_delay_ms(500);
        }
    }
}
