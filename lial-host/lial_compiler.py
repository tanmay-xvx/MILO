"""
LIAL JIT Compiler -- Compiles Rust source code into wasm32-unknown-unknown bytecode
suitable for the LIAL receiver (including memory-constrained targets like ESP32-C3).
"""

import os
import subprocess
import tempfile

RUST_LIB_TEMPLATE = """\
#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {{ loop {{}} }}

unsafe extern "C" {{
    fn lial_gpio_set(pin: u32, state: u32);
    fn lial_gpio_get(pin: u32) -> u32;
    fn lial_delay_ms(ms: u32);
    fn lial_get_uptime_us() -> u64;
    fn lial_i2c_transfer(addr: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
    fn lial_log(ptr: u32, len: u32);
    fn lial_pwm_set(channel: u32, duty_0_10000: u32);
    fn lial_adc_read(channel: u32) -> u32;
    fn lial_spi_transfer(bus: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
    fn lial_uart_write(bus: u32, ptr: u32, len: u32) -> i32;
    fn lial_uart_read(bus: u32, ptr: u32, len_max: u32, timeout_ms: u32) -> i32;
}}

{body}
"""

CARGO_TOML = """\
[package]
name = "lial_driver"
version = "0.1.0"
edition = "2024"

[lib]
crate-type = ["cdylib"]

[profile.release]
opt-level = "z"
lto = true
"""

CARGO_CONFIG = """\
[target.wasm32-unknown-unknown]
rustflags = [
    "-C", "link-arg=--initial-memory=65536",
    "-C", "link-arg=--max-memory=65536",
    "-C", "link-arg=-z",
    "-C", "link-arg=stack-size=4096",
]
"""


def compile_rust_to_wasm(body: str) -> bytes:
    """
    Compile a Rust function body into a wasm binary.

    The body should contain at minimum:
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() { ... }

    The compiler wraps it with #![no_std], panic handler, and extern declarations.

    Returns raw .wasm bytes.
    Raises RuntimeError with the compiler stderr on failure.
    """
    with tempfile.TemporaryDirectory(prefix="lial_") as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        cargo_dir = os.path.join(tmpdir, ".cargo")
        os.makedirs(src_dir)
        os.makedirs(cargo_dir)

        with open(os.path.join(tmpdir, "Cargo.toml"), "w") as f:
            f.write(CARGO_TOML)

        with open(os.path.join(cargo_dir, "config.toml"), "w") as f:
            f.write(CARGO_CONFIG)

        with open(os.path.join(src_dir, "lib.rs"), "w") as f:
            f.write(RUST_LIB_TEMPLATE.format(body=body))

        result = subprocess.run(
            ["cargo", "build", "--target", "wasm32-unknown-unknown", "--release"],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)

        wasm_path = os.path.join(
            tmpdir, "target", "wasm32-unknown-unknown", "release", "lial_driver.wasm"
        )
        with open(wasm_path, "rb") as f:
            wasm_bytes = f.read()

        assert wasm_bytes[:4] == b"\x00asm", "Compiler produced invalid wasm"
        return wasm_bytes


if __name__ == "__main__":
    test_body = '''
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"hello from compiler test";
        lial_log(msg.as_ptr() as u32, msg.len() as u32);
        for _ in 0..3 {
            lial_gpio_set(5, 1);
            lial_delay_ms(200);
            lial_gpio_set(5, 0);
            lial_delay_ms(200);
        }
    }
}
'''
    print("Testing Rust -> wasm pipeline...")
    wasm = compile_rust_to_wasm(test_body)
    print(f"Success: {len(wasm)} bytes")
