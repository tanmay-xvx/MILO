#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn lial_pwm_set(channel: u32, duty_0_10000: u32);
    fn lial_delay_ms(ms: u32);
    fn lial_log(ptr: u32, len: u32);
}

static START: &[u8] = b"pwm fade start";
static DONE: &[u8] = b"pwm fade done";

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        lial_log(START.as_ptr() as u32, START.len() as u32);
        let mut d: u32 = 0;
        while d <= 10000 {
            lial_pwm_set(5, d);
            lial_delay_ms(15);
            d += 500;
        }
        lial_pwm_set(5, 0);
        lial_log(DONE.as_ptr() as u32, DONE.len() as u32);
    }
}
