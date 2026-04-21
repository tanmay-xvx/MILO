"""Secondary-UART loopback test.

On the ESP32-C3 DevKitC, wire GPIO 20 -> GPIO 21 with a short jumper so the
UART1 TX pin feeds directly into UART1 RX. The driver writes "PING", reads
up to 4 bytes back with a 100 ms timeout, and asserts equality.

If the jumper is not present, the test will FAIL -- this is intentional, so
it serves as a wiring-integrity check.
"""

from hil_test import HilAssertionError, HilTest, hil_test


@hil_test
def uart_loopback(hil: HilTest) -> None:
    rust = """
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() {
            unsafe {
                let tx = b"PING";
                let _ = lial_uart_write(1, tx.as_ptr() as u32, tx.len() as u32);
                lial_delay_ms(10);

                let mut rx = [0u8; 8];
                let n = lial_uart_read(1, rx.as_mut_ptr() as u32, rx.len() as u32, 100);

                if n == tx.len() as i32 && rx[0] == b'P' && rx[1] == b'I' && rx[2] == b'N' && rx[3] == b'G' {
                    let ok = b"uart_loopback ok";
                    lial_log(ok.as_ptr() as u32, ok.len() as u32);
                } else {
                    let fail = b"uart_loopback fail";
                    lial_log(fail.as_ptr() as u32, fail.len() as u32);
                }
            }
        }
    """
    result = hil.run(rust)
    hil.assert_ok(result)
    if not any("uart_loopback ok" in log for log in result.logs):
        raise HilAssertionError(
            f"UART loopback failed; logs: {result.logs}. "
            "Did you jumper TX (GPIO 20) to RX (GPIO 21)?"
        )
