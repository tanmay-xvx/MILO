"""ADC read test.

Reads ADC channel 0 ten times, computes the mean, and asserts the value is
within the valid 12-bit range (0-4095). With a potentiometer connected,
the reading depends on the knob position.
"""

from hil_test import HilAssertionError, HilTest, hil_test


@hil_test
def adc_voltage_divider(hil: HilTest) -> None:
    rust = """
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

                // format avg as decimal
                let mut n = avg;
                if n == 0 {
                    buf[plen] = b'0';
                    lial_log(buf.as_ptr() as u32, (plen + 1) as u32);
                    return;
                }
                let mut tmp = [0u8; 12];
                let mut i = 0usize;
                while n > 0 {
                    tmp[i] = b'0' + (n % 10) as u8;
                    n /= 10;
                    i += 1;
                }
                let mut out_len = 0usize;
                while out_len < i {
                    buf[plen + out_len] = tmp[i - 1 - out_len];
                    out_len += 1;
                }
                lial_log(buf.as_ptr() as u32, (plen + out_len) as u32);
            }
        }
    """
    result = hil.run(rust)
    hil.assert_ok(result)
    hil.assert_log(result, "adc_avg=")

    for log in result.logs:
        if log.startswith("adc_avg="):
            raw = log.split("=", 1)[1]
            try:
                avg = int(raw)
            except ValueError:
                raise HilAssertionError(f"couldn't parse ADC value from {log!r}")
            if not (0 <= avg <= 4095):
                raise HilAssertionError(
                    f"ADC avg {avg} outside valid 12-bit range [0, 4095]"
                )
            return

    raise HilAssertionError("no adc_avg log found")
