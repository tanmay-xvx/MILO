"""ADC read test.

Wire a voltage divider from 3.3V to GND (two equal resistors, midpoint to
an ADC-capable pin; on ESP32-C3 use GPIO 0/1/2/3/4). The test reads the
channel 10 times, computes the mean, and asserts it falls within +/-10%
of Vcc/2. With a 12-bit ADC (0-4095) that's roughly 1842 <= avg <= 2253.
"""

from hil_test import HilAssertionError, HilTest, hil_test


ADC_MIN = 1800
ADC_MAX = 2300


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
            if not (ADC_MIN <= avg <= ADC_MAX):
                raise HilAssertionError(
                    f"ADC avg {avg} outside [{ADC_MIN}, {ADC_MAX}] "
                    "(did you wire a 1:1 voltage divider to the ADC pin?)"
                )
            return

    raise HilAssertionError("no adc_avg log found")
