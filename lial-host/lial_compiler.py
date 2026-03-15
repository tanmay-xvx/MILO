"""
LIAL JIT Compiler -- Compiles C or Rust source code into wasm32-unknown-unknown bytecode.

Compilation strategies (tried in order):
1. Homebrew LLVM clang + wasm-ld  (C code, if wasm-ld is available)
2. Rust cdylib pipeline            (generates a temp crate, builds with cargo)
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOMEBREW_LLVM = Path("/opt/homebrew/opt/llvm/bin")
LIAL_HEADER = """\
// LIAL Atomic Alphabet -- provided by the receiver at link time
extern void lial_gpio_set(int pin, int state);
extern int  lial_gpio_get(int pin);
extern void lial_delay_ms(int ms);
extern unsigned long long lial_get_uptime_us(void);
extern int  lial_i2c_transfer(int addr, const char* tx, int tx_len, char* rx, int rx_len);
extern void lial_log(const char* message);
"""

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
    fn lial_log(ptr: u32);
}}

{body}
"""


def _find_clang():
    """Return clang path if Homebrew LLVM clang is available."""
    homebrew = HOMEBREW_LLVM / "clang"
    if homebrew.exists():
        return str(homebrew)
    system = shutil.which("clang")
    return system


def _find_wasm_ld():
    """Return wasm-ld path if available."""
    homebrew = HOMEBREW_LLVM / "wasm-ld"
    if homebrew.exists():
        return str(homebrew)
    return shutil.which("wasm-ld")


def _has_clang_wasm_support():
    return _find_clang() is not None and _find_wasm_ld() is not None


def compile_c_clang(c_code: str) -> bytes | None:
    """Compile C code to wasm bytes using clang + wasm-ld."""
    clang = _find_clang()
    wasm_ld = _find_wasm_ld()
    if not clang or not wasm_ld:
        return None

    with tempfile.TemporaryDirectory(prefix="lial_") as tmpdir:
        src = os.path.join(tmpdir, "driver.c")
        out = os.path.join(tmpdir, "driver.wasm")

        full_source = LIAL_HEADER + "\n" + c_code
        with open(src, "w") as f:
            f.write(full_source)

        cmd = [
            clang,
            "--target=wasm32",
            "-O3",
            "-nostdlib",
            f"-fuse-ld={wasm_ld}",
            "-Wl,--no-entry",
            "-Wl,--export-all",
            "-Wl,--allow-undefined",
            "-o", out,
            src,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"clang compilation failed:\n{result.stderr}")

        with open(out, "rb") as f:
            return f.read()


def compile_c_via_rust(c_code: str) -> bytes:
    """
    Convert C-style code into equivalent Rust, compile as cdylib to wasm.
    This is the primary path since most systems lack wasm-ld.
    The LLM should generate Rust-compatible code when targeting this compiler.
    """
    with tempfile.TemporaryDirectory(prefix="lial_") as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir)

        cargo_toml = os.path.join(tmpdir, "Cargo.toml")
        with open(cargo_toml, "w") as f:
            f.write(
                '[package]\nname = "lial_driver"\nversion = "0.1.0"\nedition = "2024"\n\n'
                '[lib]\ncrate-type = ["cdylib"]\n\n[dependencies]\n'
            )

        lib_rs = os.path.join(src_dir, "lib.rs")
        with open(lib_rs, "w") as f:
            f.write(RUST_LIB_TEMPLATE.format(body=c_code))

        result = subprocess.run(
            [
                "cargo", "build",
                "--target", "wasm32-unknown-unknown",
                "--release",
            ],
            capture_output=True,
            text=True,
            cwd=tmpdir,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Rust wasm compilation failed:\n{result.stderr}")

        wasm_path = os.path.join(
            tmpdir, "target", "wasm32-unknown-unknown", "release", "lial_driver.wasm"
        )
        with open(wasm_path, "rb") as f:
            return f.read()


def compile_to_bytes(code: str, lang: str = "c") -> bytes:
    """
    Compile source code to wasm bytes.

    Args:
        code: Source code string (C or Rust body)
        lang: "c" for C code (tries clang, falls back to Rust pipeline)
              "rust" for Rust code body (goes directly to Rust pipeline)

    Returns:
        Raw .wasm bytes ready to push to the receiver.
    """
    if lang == "c" and _has_clang_wasm_support():
        result = compile_c_clang(code)
        if result is not None:
            return result

    if lang == "c":
        raise RuntimeError(
            "C compilation requires wasm-ld (not found). "
            "Ask the LLM to generate Rust code instead, or install wasm-ld."
        )

    return compile_c_via_rust(code)


if __name__ == "__main__":
    print(f"clang: {_find_clang()}")
    print(f"wasm-ld: {_find_wasm_ld()}")
    print(f"clang+wasm-ld available: {_has_clang_wasm_support()}")

    test_rust_body = '''
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        for _ in 0..3 {
            lial_gpio_set(5, 1);
            lial_delay_ms(200);
            lial_gpio_set(5, 0);
            lial_delay_ms(200);
        }
    }
}
'''
    print("\nTesting Rust cdylib pipeline...")
    wasm_bytes = compile_to_bytes(test_rust_body, lang="rust")
    print(f"Success! Compiled to {len(wasm_bytes)} bytes of wasm")

    assert wasm_bytes[:4] == b"\x00asm", "Not a valid wasm module"
    print("Valid wasm header confirmed.")
