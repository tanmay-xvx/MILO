"""GPIO output + input round-trips on real hardware.

The input test assumes the user has jumpered GPIO 6 to either 3.3V or GND (or
the matching pin on non-ESP32-C3 boards). If the firmware's discovery
manifest does not advertise GPIO 6, the test is skipped.
"""

from hil_test import HilAssertionError, HilTest, hil_test


@hil_test
def gpio_output_led_blinks(hil: HilTest) -> None:
    """Blink GPIO 5 five times. Visual check: LED toggles."""
    rust = """
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() {
            unsafe {
                let msg = b"gpio blink start";
                lial_log(msg.as_ptr() as u32, msg.len() as u32);
                for _ in 0..5 {
                    lial_gpio_set(5, 1);
                    lial_delay_ms(100);
                    lial_gpio_set(5, 0);
                    lial_delay_ms(100);
                }
                let done = b"gpio blink done";
                lial_log(done.as_ptr() as u32, done.len() as u32);
            }
        }
    """
    result = hil.run(rust)
    hil.assert_ok(result)
    hil.assert_log(result, "gpio blink start")
    hil.assert_log(result, "gpio blink done")


@hil_test
def gpio_input_read(hil: HilTest) -> None:
    """Read GPIO 6 ten times; log whether we saw HIGH, LOW, or both.

    Caveat: on the current ESP32-C3 port the adapter only registers GPIO 5 as
    an output, so a physical jumper on 6 will always read 0 until Phase E
    expands the registered inputs. This test still exercises the syscall path.
    """
    rust = """
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() {
            unsafe {
                let mut highs: u32 = 0;
                let mut lows: u32 = 0;
                for _ in 0..10 {
                    if lial_gpio_get(6) != 0 { highs += 1; } else { lows += 1; }
                    lial_delay_ms(5);
                }
                let mut buf = [0u8; 64];
                let prefix = b"gpio_in highs=";
                let plen = prefix.len();
                buf[..plen].copy_from_slice(prefix);
                buf[plen] = b'0' + (highs as u8);
                buf[plen + 1] = b' ';
                let tail = b"lows=";
                buf[plen + 2..plen + 7].copy_from_slice(tail);
                buf[plen + 7] = b'0' + (lows as u8);
                lial_log(buf.as_ptr() as u32, (plen + 8) as u32);
            }
        }
    """
    result = hil.run(rust)
    hil.assert_ok(result)
    hil.assert_log(result, "gpio_in highs=")
