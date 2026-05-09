#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn pwm_set(channel: u32, duty_0_10000: u32);
    fn delay_ms(ms: u32);
    fn log_msg(ptr: u32, len: u32);
}

static START: &[u8] = b"pwm fade start";
static DONE: &[u8] = b"pwm fade done";

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        log_msg(START.as_ptr() as u32, START.len() as u32);
        let mut d: u32 = 0;
        while d <= 10000 {
            pwm_set(5, d);
            delay_ms(15);
            d += 500;
        }
        pwm_set(5, 0);
        log_msg(DONE.as_ptr() as u32, DONE.len() as u32);
    }
}
