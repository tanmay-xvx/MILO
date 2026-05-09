#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn gpio_set(pin: u32, state: u32);
    fn delay_ms(ms: u32);
    fn log_msg(ptr: u32, len: u32);
}

static MSG: &[u8] = b"Blinking LED on GPIO 25";

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        log_msg(MSG.as_ptr() as u32, MSG.len() as u32);
        for _ in 0..5 {
            gpio_set(25, 1);
            delay_ms(500);
            gpio_set(25, 0);
            delay_ms(500);
        }
    }
}
