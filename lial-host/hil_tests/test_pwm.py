"""PWM fade test.

Ramps duty cycle on the LED pin from 0 -> 100% -> 0 over ~3 seconds. Visual
verification: the LED should smoothly brighten then dim. Also asserts the
start / done log frames are emitted.
"""

from hil_test import HilTest, hil_test


@hil_test
def pwm_led_fade(hil: HilTest) -> None:
    rust = """
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() {
            unsafe {
                let start = b"pwm fade start";
                lial_log(start.as_ptr() as u32, start.len() as u32);

                let mut d: u32 = 0;
                while d <= 10000 {
                    lial_pwm_set(5, d);
                    lial_delay_ms(30);
                    d += 200;
                }
                while d > 0 {
                    lial_pwm_set(5, d);
                    lial_delay_ms(30);
                    d -= 200;
                }
                lial_pwm_set(5, 0);

                let done = b"pwm fade done";
                lial_log(done.as_ptr() as u32, done.len() as u32);
            }
        }
    """
    result = hil.run(rust, compile_timeout_s=60)
    hil.assert_ok(result)
    hil.assert_log(result, "pwm fade start")
    hil.assert_log(result, "pwm fade done")
