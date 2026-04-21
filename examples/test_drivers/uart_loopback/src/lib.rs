#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn lial_uart_write(bus: u32, ptr: u32, len: u32) -> i32;
    fn lial_uart_read(bus: u32, ptr: u32, len_max: u32, timeout_ms: u32) -> i32;
    fn lial_delay_ms(ms: u32);
    fn lial_log(ptr: u32, len: u32);
}

static TX: &[u8] = b"PING";
static OK: &[u8] = b"uart_loopback ok";
static FAIL: &[u8] = b"uart_loopback fail";

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let _ = lial_uart_write(1, TX.as_ptr() as u32, TX.len() as u32);
        lial_delay_ms(10);

        let mut rx = [0u8; 8];
        let n = lial_uart_read(1, rx.as_mut_ptr() as u32, rx.len() as u32, 100);

        if n == TX.len() as i32 && &rx[..TX.len()] == TX {
            lial_log(OK.as_ptr() as u32, OK.len() as u32);
        } else {
            lial_log(FAIL.as_ptr() as u32, FAIL.len() as u32);
        }
    }
}
