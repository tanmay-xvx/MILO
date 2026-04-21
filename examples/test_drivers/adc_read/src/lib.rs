#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }

unsafe extern "C" {
    fn lial_adc_read(channel: u32) -> u32;
    fn lial_delay_ms(ms: u32);
    fn lial_log(ptr: u32, len: u32);
}

fn format_u32(value: u32, buf: &mut [u8]) -> usize {
    if value == 0 {
        if buf.is_empty() { return 0; }
        buf[0] = b'0';
        return 1;
    }
    let mut n = value;
    let mut tmp = [0u8; 12];
    let mut i = 0;
    while n > 0 {
        tmp[i] = b'0' + (n % 10) as u8;
        n /= 10;
        i += 1;
    }
    let out_len = core::cmp::min(i, buf.len());
    for j in 0..out_len {
        buf[j] = tmp[i - 1 - j];
    }
    out_len
}

#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let mut sum: u32 = 0;
        for _ in 0..10 {
            sum += lial_adc_read(0);
            lial_delay_ms(5);
        }
        let avg = sum / 10;

        let mut buf = [0u8; 32];
        let prefix = b"adc_avg=";
        let plen = prefix.len();
        buf[..plen].copy_from_slice(prefix);
        let dlen = format_u32(avg, &mut buf[plen..]);
        lial_log(buf.as_ptr() as u32, (plen + dlen) as u32);
    }
}
